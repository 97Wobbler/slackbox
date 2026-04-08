"""Slack Fetch MCP Server.

Claude Code에서 Slack 벌크 크롤링을 수행할 수 있는 MCP 서버.
기존 slack_fetch 함수들을 호출하는 얇은 래퍼.

실행: python -m slack_fetch
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp.server import FastMCP

from slack_sdk.errors import SlackApiError

from slack_fetch.config import CrawlerConfig
from slack_fetch.client import create_slack_client
from slack_fetch.channels import collect_channels
from slack_fetch.messages import collect_via_search, collect_via_history
from slack_fetch.threads import collect_threads
from slack_fetch.mentions import collect_mentions
from slack_fetch.rate_limit import rate_wait, handle_rate_limit
from slack_fetch.text_cleaner import (
    SlackTextCleaner,
    ts_to_dt,
    ts_to_str,
    load_user_map_from_threads,
    load_channel_map,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

mcp = FastMCP("slack-fetch-mcp", instructions="Slack 워크스페이스 벌크 크롤링 도구")

# ── 설정/클라이언트 싱글턴 ──────────────────────────────────────

_cfg: CrawlerConfig | None = None
_client = None


def _get_cfg() -> CrawlerConfig:
    global _cfg
    if _cfg is None:
        _cfg = CrawlerConfig.from_env()
        errors = _cfg.validate()
        if errors:
            raise RuntimeError(f"설정 오류: {'; '.join(errors)}")
        _cfg.ensure_dirs()
    return _cfg


def _get_client():
    global _client
    if _client is None:
        _client = create_slack_client(_get_cfg())
    return _client


# ── 헬퍼 ─────────────────────────────────────────────────────────

def _load_channels(cfg: CrawlerConfig) -> list[dict]:
    """channels.json에서 채널 목록 로드."""
    cp = cfg.channels_path()
    if not cp.exists():
        return []
    data = json.loads(cp.read_text(encoding="utf-8"))
    return data.get("channels", [])


def _since_str(days: int) -> str | None:
    """N일 전 날짜를 YYYY-MM-DD 문자열로 반환. days가 0 이하이면 None (기간 제한 없음)."""
    if days <= 0:
        return None
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%d")


def _format_channel_messages_md(
    messages: list[dict], cleaner: SlackTextCleaner, tz: str, cfg: CrawlerConfig
) -> str:
    """메시지 목록을 채널별 그룹핑된 Markdown으로 변환."""
    if not messages:
        return "수집된 메시지가 없습니다."

    messages.sort(key=lambda m: float(m.get("ts", "0")))

    by_channel: dict[str, list[dict]] = defaultdict(list)
    for msg in messages:
        by_channel[msg.get("channel_name", "unknown")].append(msg)

    lines: list[str] = []
    for ch_name, msgs in sorted(by_channel.items()):
        lines.append(f"# #{ch_name} ({len(msgs)}건)\n")
        current_date = ""
        for msg in msgs:
            dt = ts_to_dt(msg["ts"], tz)
            date_str = dt.strftime("%Y-%m-%d")
            time_str = dt.strftime("%H:%M")

            if date_str != current_date:
                lines.append(f"\n## {date_str}\n")
                current_date = date_str

            text = cleaner.clean(msg.get("text", ""))
            thread_note = ""
            if msg.get("thread_ts") and msg.get("reply_count", 0) > 0:
                thread_note = f"  [스레드 {msg['reply_count']}건]"
            lines.append(f"[{time_str}] {text}{thread_note}")

        lines.append("")

    return "\n".join(lines)


def _format_weekly_md(
    messages: list[dict], cleaner: SlackTextCleaner, tz: str, cfg: CrawlerConfig
) -> str:
    """메시지 목록을 주별 그룹핑된 Markdown으로 변환."""
    if not messages:
        return "수집된 메시지가 없습니다."

    messages.sort(key=lambda m: float(m.get("ts", "0")))

    by_week: dict[str, list[dict]] = defaultdict(list)
    for msg in messages:
        dt = ts_to_dt(msg["ts"], tz)
        week_key = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
        by_week[week_key].append(msg)

    lines: list[str] = []
    for week_key in sorted(by_week.keys()):
        msgs = by_week[week_key]
        year, week_num = int(week_key[:4]), int(week_key.split("W")[1])
        week_start = datetime.strptime(f"{year}-W{week_num:02d}-1", "%G-W%V-%u")
        week_end_dt = week_start + timedelta(days=6)

        ch_counts: dict[str, int] = defaultdict(int)
        for msg in msgs:
            ch_counts[msg.get("channel_name", "?")] += 1

        lines.append(f"# {week_key} ({week_start.strftime('%m/%d')}~{week_end_dt.strftime('%m/%d')}) — {len(msgs)}건\n")
        lines.append(f"채널: {', '.join(f'#{c}({n})' for c, n in sorted(ch_counts.items(), key=lambda x: -x[1]))}\n")

        current_date = ""
        for msg in msgs:
            dt = ts_to_dt(msg["ts"], tz)
            date_str = dt.strftime("%Y-%m-%d")
            time_str = dt.strftime("%H:%M")

            if date_str != current_date:
                lines.append(f"\n### {date_str}\n")
                current_date = date_str

            ch = msg.get("channel_name", "?")
            text = cleaner.clean(msg.get("text", ""))
            lines.append(f"[{time_str}] #{ch}: {text}")

        lines.append("\n---\n")

    return "\n".join(lines)


# ── MCP Tools ────────────────────────────────────────────────────

@mcp.tool()
def list_channels() -> str:
    """워크스페이스의 가입된 공개 채널 목록을 반환합니다.

    로컬 캐시(channels.json)가 있으면 캐시를 사용하고,
    없으면 Slack API에서 수집합니다.
    """
    cfg = _get_cfg()
    channels = _load_channels(cfg)

    if not channels:
        client = _get_client()
        channels = collect_channels(client, cfg)

    lines = [f"총 {len(channels)}개 채널:\n"]
    for ch in sorted(channels, key=lambda c: -c.get("num_members", 0)):
        archived = " [archived]" if ch.get("is_archived") else ""
        purpose = f" — {ch['purpose']}" if ch.get("purpose") else ""
        lines.append(f"- #{ch['name']} ({ch.get('num_members', 0)}명){archived}{purpose}")

    return "\n".join(lines)


@mcp.tool()
def crawl_channel(channel: str, days: int = 7) -> str:
    """특정 채널의 최근 N일간 전체 대화를 수집합니다.

    Args:
        channel: 채널 이름 (예: "general") 또는 채널 ID (예: "C01234")
        days: 수집할 기간 (일). 기본값 7일. 0이면 전체 기간 수집.

    수집된 데이터는 로컬 data/ 디렉토리에 저장됩니다.
    """
    cfg = _get_cfg()
    client = _get_client()

    # 채널 목록에서 채널 정보 찾기
    channels = _load_channels(cfg)
    if not channels:
        channels = collect_channels(client, cfg)

    target_ch = None
    for ch in channels:
        if ch["name"] == channel or ch["id"] == channel:
            target_ch = ch
            break

    if not target_ch:
        return f"채널 '{channel}'을 찾을 수 없습니다. list_channels로 확인하세요."

    since = _since_str(days) if days > 0 else None

    # user_id=None → 채널 전체 대화 수집 (사용자 필터 없음)
    try:
        total = collect_via_history(
            client, cfg, [target_ch], since=since, user_id=None
        )
    except SlackApiError as e:
        error_code = e.response.get("error", "unknown_error")
        if error_code in ("token_revoked", "invalid_auth", "not_authed", "account_inactive"):
            return f"Slack 인증 오류 ({error_code}): 토큰이 만료되었거나 무효합니다. 재발급이 필요합니다."
        if error_code == "missing_scope":
            return f"Slack 권한 오류 ({error_code}): 토큰에 필요한 scope가 없습니다. channels:history 권한을 확인하세요."
        if error_code == "channel_not_found":
            return f"Slack API 오류 ({error_code}): 채널 '{channel}'을 찾을 수 없거나 접근 권한이 없습니다."
        if error_code == "not_in_channel":
            return f"Slack API 오류 ({error_code}): 봇이 #{target_ch['name']} 채널에 참여하지 않았습니다."
        return f"Slack API 오류 ({error_code}): {e}"

    period_desc = f"최근 {days}일" if days > 0 else "전체 기간"
    return (
        f"#{target_ch['name']} 채널 {period_desc} 전체 대화 수집 완료.\n"
        f"수집된 메시지: {total}건\n"
        f"저장 위치: {cfg.channel_messages_path(target_ch['id'])}\n"
        f"get_collected_data로 수집 데이터를 조회할 수 있습니다."
    )


@mcp.tool()
def crawl_user(user_id: str, days: int = 30, include_threads: bool = False) -> str:
    """특정 사용자의 최근 N일간 활동을 수집합니다.

    search.messages를 우선 사용하고, 실패 시 conversations.history로 fallback합니다.

    Args:
        user_id: Slack 사용자 ID (예: "U0XXX0X0X0X")
        days: 수집할 기간 (일). 기본값 30일. 0이면 전체 기간 수집.
        include_threads: True이면 메시지 수집 후 자동으로 스레드도 수집합니다.
    """
    cfg = _get_cfg()
    client = _get_client()

    since = _since_str(days) if days > 0 else None

    try:
        total = collect_via_search(client, cfg, since=since, user_id=user_id)
        method = "search.messages"
    except SlackApiError as e:
        error_code = e.response.get("error", "unknown_error")
        if error_code in ("token_revoked", "invalid_auth", "not_authed", "account_inactive"):
            return f"Slack 인증 오류 ({error_code}): 토큰이 만료되었거나 무효합니다. 재발급이 필요합니다."
        if error_code == "missing_scope":
            return f"Slack 권한 오류 ({error_code}): 토큰에 필요한 scope가 없습니다. search:read 권한을 확인하세요."
        logger.warning("search.messages 실패 (%s), conversations.history로 fallback", error_code)
        try:
            channels = _load_channels(cfg)
            if not channels:
                channels = collect_channels(client, cfg)
            total = collect_via_history(client, cfg, channels, since=since, user_id=user_id)
            method = "conversations.history"
        except SlackApiError as e2:
            error_code2 = e2.response.get("error", "unknown_error")
            if error_code2 in ("token_revoked", "invalid_auth", "not_authed", "account_inactive"):
                return f"Slack 인증 오류 ({error_code2}): 토큰이 만료되었거나 무효합니다. 재발급이 필요합니다."
            if error_code2 == "missing_scope":
                return f"Slack 권한 오류 ({error_code2}): 토큰에 필요한 scope가 없습니다."
            return f"Slack API 오류 ({error_code2}): {e2}"
    except Exception as e:
        return f"예기치 않은 오류: {e}"

    period_desc = f"최근 {days}일" if days > 0 else "전체 기간"

    thread_info = ""
    if include_threads:
        try:
            thread_count = collect_threads(client, cfg, user_id=user_id)
            thread_info = f"\n스레드 수집: {thread_count}개 완료"
        except SlackApiError as e:
            error_code = e.response.get("error", "unknown_error")
            thread_info = f"\n스레드 수집 실패 ({error_code}): {e}"

    return (
        f"사용자 {user_id}의 {period_desc} 활동 수집 완료.\n"
        f"수집 방법: {method}\n"
        f"수집된 메시지: {total}건"
        f"{thread_info}"
        + ("" if include_threads else "\n스레드 수집이 필요하면 crawl_threads를 실행하세요.")
    )


@mcp.tool()
def search_messages(query: str, days: int = 30) -> str:
    """키워드로 Slack 메시지를 검색하여 수집합니다.

    Slack search.messages API를 사용하여 임의 검색어로 메시지를 찾습니다.

    Args:
        query: 검색 쿼리. Slack 검색 문법 지원 (예: "배포 in:#general", "from:@홍길동 버그")
        days: 검색 기간 (일). 기본값 30일. 0이면 전체 기간.
    """
    cfg = _get_cfg()
    client = _get_client()

    # 검색 디렉토리 생성
    search_dir = cfg.raw_dir / "search"
    search_dir.mkdir(parents=True, exist_ok=True)

    # 쿼리에 기간 조건 추가
    full_query = query
    since = _since_str(days)
    if since:
        full_query += f" after:{since}"

    # 파일명용 쿼리 sanitize
    sanitized = re.sub(r'[^\w가-힣\s-]', '_', query).strip()
    sanitized = re.sub(r'\s+', '_', sanitized)
    if not sanitized:
        sanitized = "empty_query"
    out_path = search_dir / f"{sanitized}.jsonl"

    # 중복 방지: 기존 파일에서 seen_ts 로드
    seen_ts: set[str] = set()
    if out_path.exists():
        with open(out_path, encoding="utf-8") as ef:
            for line in ef:
                if line.strip():
                    rec = _safe_json_loads(line, out_path)
                    if rec is not None:
                        seen_ts.add(f"{rec['ts']}_{rec.get('channel_id', '')}")

    total = 0
    page = 1

    with open(out_path, "a", encoding="utf-8") as f:
        while True:
            try:
                resp = client.search_messages(
                    query=full_query, sort="timestamp",
                    sort_dir="asc", count=100, page=page
                )
            except SlackApiError as e:
                if e.response.status_code == 429:
                    handle_rate_limit(e)
                    continue
                error_code = e.response.get("error", "unknown_error")
                if error_code in ("token_revoked", "invalid_auth", "not_authed", "account_inactive"):
                    return (
                        f"Slack 인증 오류 ({error_code}): 토큰이 만료되었거나 무효합니다."
                    )
                if error_code == "missing_scope":
                    return (
                        f"Slack 권한 부족 ({error_code}): 필요한 scope를 확인하세요."
                    )
                return f"Slack API 오류 ({error_code}): {e}"

            messages = resp.get("messages", {}).get("matches", [])
            if not messages:
                break

            for msg in messages:
                ts = msg.get("ts", "")
                channel_id = msg.get("channel", {}).get("id", "")
                dedup_key = f"{ts}_{channel_id}"
                if dedup_key in seen_ts:
                    continue
                seen_ts.add(dedup_key)

                record = {
                    "ts": ts,
                    "channel_id": channel_id,
                    "channel_name": msg.get("channel", {}).get("name", ""),
                    "user": msg.get("user") or msg.get("username", ""),
                    "text": msg.get("text", ""),
                    "thread_ts": msg.get("thread_ts"),
                    "reply_count": msg.get("reply_count", 0),
                    "permalink": msg.get("permalink", ""),
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                total += 1

            paging = resp.get("messages", {}).get("paging", {})
            if page >= paging.get("pages", 1):
                break
            page += 1
            rate_wait(1.0)

    period_desc = f"최근 {days}일" if days > 0 else "전체 기간"
    return (
        f"키워드 검색 수집 완료.\n"
        f"검색어: {query}\n"
        f"기간: {period_desc}\n"
        f"수집된 메시지: {total}건 (기존 중복 제외)\n"
        f"저장 경로: {out_path}"
    )


@mcp.tool()
def crawl_threads(
    channel: str = "",
    thread_ts_list: list[str] | None = None,
    user_id: str | None = None,
) -> str:
    """지정된 스레드들의 전체 대화를 수집합니다.

    user_id를 지정하면 해당 사용자의 messages.jsonl에서 thread_ts를 자동 추출하여
    모든 채널의 스레드를 한 번에 수집합니다 (channel 파라미터 무시).

    Args:
        channel: 채널 이름 또는 채널 ID (user_id 미지정 시 필수)
        thread_ts_list: 수집할 스레드의 타임스탬프 목록 (예: ["1717000000.000000"]).
                        비어있거나 미지정 시 user_id의 messages.jsonl에서 자동 추출.
        user_id: Slack 사용자 ID. 지정하면 해당 사용자의 messages.jsonl에서 thread_ts를
                 자동 추출하고 모든 채널의 스레드를 수집합니다.
    """
    cfg = _get_cfg()
    client = _get_client()

    # ── 자동 발견 모드: user_id가 주어지면 collect_threads 호출 ──
    if user_id:
        thread_count = collect_threads(client, cfg, user_id=user_id)
        return (
            f"사용자 {user_id}의 스레드 자동 수집 완료.\n"
            f"수집된 스레드: {thread_count}개\n"
            f"messages.jsonl에서 thread_ts를 자동 추출하여 모든 채널 대상으로 수집했습니다."
        )

    # ── 수동 모드: channel + thread_ts_list 지정 ──
    if not channel:
        return "channel 또는 user_id 중 하나는 반드시 지정해야 합니다."

    if not thread_ts_list:
        return (
            "thread_ts_list가 비어있습니다. "
            "수집할 스레드 타임스탬프를 지정하거나 user_id를 지정하여 자동 발견 모드를 사용하세요."
        )

    # 채널 ID 확인
    channels = _load_channels(cfg)
    channel_id = channel
    channel_name = channel
    for ch in channels:
        if ch["name"] == channel or ch["id"] == channel:
            channel_id = ch["id"]
            channel_name = ch["name"]
            break

    collected = 0
    errors_list: list[str] = []

    threads_dir = cfg.shared_threads_dir
    threads_dir.mkdir(parents=True, exist_ok=True)

    for thread_ts in thread_ts_list:
        key = f"{channel_id}_{thread_ts}"
        out_path = threads_dir / f"{key}.jsonl"

        if out_path.exists():
            collected += 1
            continue

        try:
            resp = client.conversations_replies(
                channel=channel_id, ts=thread_ts, limit=cfg.page_limit
            )
            replies = resp.get("messages", [])

            if replies:
                target_ids = cfg.all_user_ids_set
                with open(out_path, "w", encoding="utf-8") as f:
                    for msg in replies:
                        msg_uid = msg.get("user", "unknown")
                        f.write(json.dumps({
                            "ts": msg.get("ts", ""),
                            "user": msg_uid,
                            "user_name": msg_uid,
                            "text": msg.get("text", ""),
                            "is_target_user": msg_uid in target_ids,
                        }, ensure_ascii=False) + "\n")
                collected += 1

        except SlackApiError as e:
            error_code = e.response.get("error", "unknown_error")
            if error_code in ("token_revoked", "invalid_auth", "not_authed", "account_inactive"):
                return f"Slack 인증 오류 ({error_code}): 토큰이 만료되었거나 무효합니다."
            if error_code == "missing_scope":
                return f"Slack 권한 부족 ({error_code}): 필요한 scope를 확인하세요."
            errors_list.append(f"{thread_ts}: {error_code} - {e}")

    result = f"#{channel_name} 스레드 {collected}/{len(thread_ts_list)}개 수집 완료."
    if errors_list:
        result += f"\n오류: {'; '.join(errors_list)}"
    return result


@mcp.tool()
def crawl_mentions(user_id: str, days: int = 30) -> str:
    """특정 사용자가 멘션된 메시지를 수집합니다.

    다른 사람이 해당 사용자를 @멘션한 메시지를 검색하여 수집합니다.
    본인이 보낸 메시지는 제외됩니다.

    Args:
        user_id: Slack 사용자 ID (예: "U0XXX0X0X0X")
        days: 수집할 기간 (일). 기본값 30일. 0이면 전체 기간 수집.
    """
    cfg = _get_cfg()
    client = _get_client()

    since = _since_str(days) if days > 0 else None

    try:
        total = collect_mentions(client, cfg, since=since, user_id=user_id)
    except SlackApiError as e:
        error_code = e.response.get("error", "unknown_error")
        if error_code in ("token_revoked", "invalid_auth", "not_authed", "account_inactive"):
            return f"Slack 인증 오류 ({error_code}): 토큰이 만료되었거나 무효합니다."
        if error_code == "missing_scope":
            return f"Slack 권한 부족 ({error_code}): 필요한 scope를 확인하세요."
        return f"Slack API 오류 ({error_code}): {e}"

    period_desc = f"최근 {days}일" if days > 0 else "전체 기간"
    return (
        f"사용자 {user_id}의 {period_desc} 멘션 수집 완료.\n"
        f"수집된 멘션: {total}건\n"
        f"저장 위치: {cfg.user_raw_dir(user_id) / 'mentions.jsonl'}"
    )


def _safe_json_loads(line: str, filepath: Path | str = "") -> dict | None:
    """JSON 라인을 안전하게 파싱. 불완전한 라인은 skip하고 None 반환."""
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        logger.warning("불완전한 JSON 라인 스킵 (파일: %s): %s", filepath, line[:120])
        return None


def _load_all_messages(cfg: CrawlerConfig) -> tuple[list[dict], dict[str, int]]:
    """3가지 소스에서 메시지를 로드하고 ts+channel_id 기반 dedup.

    Returns:
        (deduplicated messages list, source counts dict)
    """
    seen: set[str] = set()  # "ts_channel_id"
    all_messages: list[dict] = []
    source_counts = {"user": 0, "channel": 0, "search": 0}

    def _add(msg: dict, source: str) -> None:
        ts = msg.get("ts", "")
        ch_id = msg.get("channel_id", "")
        dedup_key = f"{ts}_{ch_id}"
        if dedup_key in seen:
            return
        seen.add(dedup_key)
        all_messages.append(msg)
        source_counts[source] += 1

    # 1) 사용자별 messages.jsonl — 디렉토리 자동 탐색
    #    U로 시작하는 디렉토리를 모두 스캔 (cfg.target_user_ids에 의존하지 않음)
    if cfg.raw_dir.exists():
        for user_dir in sorted(cfg.raw_dir.iterdir()):
            if not user_dir.is_dir() or not user_dir.name.startswith("U"):
                continue
            mp = user_dir / "messages.jsonl"
            if not mp.exists():
                continue
            with open(mp, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        msg = _safe_json_loads(line, mp)
                        if msg is not None:
                            _add(msg, "user")

    # 2) 채널 전체 대화: data/raw/channels/*/messages.jsonl
    channels_dir = cfg.raw_dir / "channels"
    if channels_dir.exists():
        for ch_msg_path in sorted(channels_dir.glob("*/messages.jsonl")):
            with open(ch_msg_path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        msg = _safe_json_loads(line, ch_msg_path)
                        if msg is not None:
                            _add(msg, "channel")

    # 3) 키워드 검색 결과: data/raw/search/*.jsonl
    search_dir = cfg.raw_dir / "search"
    if search_dir.exists():
        for search_path in sorted(search_dir.glob("*.jsonl")):
            with open(search_path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        msg = _safe_json_loads(line, search_path)
                        if msg is not None:
                            _add(msg, "search")

    return all_messages, source_counts


@mcp.tool()
def get_collected_data(scope: str, format: str = "markdown") -> str:
    """수집된 데이터를 정제된 형태로 반환합니다.

    Args:
        scope: 조회 범위.
            - "all": 전체 수집 데이터
            - "channel:<이름>": 특정 채널 데이터 (예: "channel:general")
            - "week:<주>": 특정 주 데이터 (예: "week:2025-W22")
            - "recent:<N>": 최근 N일 데이터 (예: "recent:7")
            - "search:<query>": 특정 키워드 검색 결과 (예: "search:배포")
            - "summary": 수집 현황 요약
        format: 출력 형식. "markdown" (기본값) 또는 "json".
    """
    cfg = _get_cfg()

    # 3가지 소스에서 메시지 로드 (dedup 포함)
    all_messages, source_counts = _load_all_messages(cfg)

    if not all_messages and scope != "summary":
        return "수집된 데이터가 없습니다. crawl_user 또는 crawl_channel을 먼저 실행하세요."

    user_map = load_user_map_from_threads(cfg)
    channel_map = load_channel_map(cfg)
    cleaner = SlackTextCleaner(user_map, channel_map)
    tz = cfg.timezone

    # ── summary ──
    if scope == "summary":
        channels = _load_channels(cfg)
        thread_count = 0
        td = cfg.shared_threads_dir
        if td.exists():
            thread_count = len(list(td.glob("*.jsonl")))

        # 채널 수집 현황
        channels_dir = cfg.raw_dir / "channels"
        crawled_channels = 0
        if channels_dir.exists():
            crawled_channels = len(list(channels_dir.glob("*/messages.jsonl")))

        # 검색 수집 현황
        search_dir = cfg.raw_dir / "search"
        search_files: list[str] = []
        if search_dir.exists():
            search_files = [p.stem for p in sorted(search_dir.glob("*.jsonl"))]

        if not all_messages:
            summary = (
                f"수집 현황:\n"
                f"- 채널: {len(channels)}개\n"
                f"- 메시지: 0건\n"
                f"- 스레드: {thread_count}개\n"
                f"- 수집된 사용자: {', '.join(d.name for d in sorted(cfg.raw_dir.iterdir()) if d.is_dir() and d.name.startswith('U')) or ['없음']}\n"
                f"- 채널 전체 크롤: {crawled_channels}개"
            )
            if search_files:
                summary += f"\n- 검색 데이터: {', '.join(search_files)}"
            return summary

        all_messages.sort(key=lambda m: float(m.get("ts", "0")))
        ch_names = set(m.get("channel_name", "?") for m in all_messages)
        first = ts_to_str(all_messages[0]["ts"], tz)
        last = ts_to_str(all_messages[-1]["ts"], tz)

        summary = (
            f"수집 현황:\n"
            f"- 채널: {len(channels)}개 (활동: {len(ch_names)}개)\n"
            f"- 메시지: {len(all_messages)}건 "
            f"(사용자별: {source_counts['user']}, "
            f"채널별: {source_counts['channel']}, "
            f"검색: {source_counts['search']})\n"
            f"- 스레드: {thread_count}개\n"
            f"- 기간: {first} ~ {last}\n"
            f"- 수집된 사용자: {', '.join(d.name for d in sorted(cfg.raw_dir.iterdir()) if d.is_dir() and d.name.startswith('U')) or ['없음']}\n"
            f"- 채널 전체 크롤: {crawled_channels}개\n"
            f"- 활동 채널: {', '.join(sorted(ch_names))}"
        )
        if search_files:
            summary += f"\n- 검색 데이터: {', '.join(search_files)}"
        return summary

    # ── filter messages ──
    messages = all_messages

    if scope.startswith("channel:"):
        ch_name = scope.split(":", 1)[1]
        messages = [m for m in all_messages if m.get("channel_name") == ch_name]
        if not messages:
            return f"채널 '{ch_name}'에 수집된 메시지가 없습니다."

    elif scope.startswith("week:"):
        target_week = scope.split(":", 1)[1]
        messages = []
        for m in all_messages:
            dt = ts_to_dt(m["ts"], tz)
            wk = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
            if wk == target_week:
                messages.append(m)
        if not messages:
            return f"주차 '{target_week}'에 수집된 메시지가 없습니다."

    elif scope.startswith("recent:"):
        try:
            recent_days = int(scope.split(":", 1)[1])
        except ValueError:
            return "recent:<숫자> 형식으로 입력하세요. 예: recent:7"
        cutoff = (datetime.now(timezone.utc) - timedelta(days=recent_days)).timestamp()
        messages = [m for m in all_messages if float(m.get("ts", "0")) >= cutoff]
        if not messages:
            return f"최근 {recent_days}일 내 수집된 메시지가 없습니다."

    elif scope.startswith("search:"):
        query_keyword = scope.split(":", 1)[1]
        # 쿼리명과 매칭되는 검색 결과 파일에서만 로드
        search_dir = cfg.raw_dir / "search"
        if not search_dir.exists():
            return "검색 데이터가 없습니다. search_messages를 먼저 실행하세요."
        sanitized = re.sub(r'[^\w가-힣\s-]', '_', query_keyword).strip()
        sanitized = re.sub(r'\s+', '_', sanitized)
        target_path = search_dir / f"{sanitized}.jsonl"
        if target_path.exists():
            matched_paths = [target_path]
        else:
            # 정확한 파일이 없으면, 파일명에 키워드가 포함된 파일들에서 로드
            matched_paths = [
                p for p in search_dir.glob("*.jsonl")
                if query_keyword.lower() in p.stem.lower()
            ]
            if not matched_paths:
                available = [p.stem for p in search_dir.glob("*.jsonl")]
                return (
                    f"검색어 '{query_keyword}'에 해당하는 데이터가 없습니다.\n"
                    f"사용 가능한 검색 데이터: "
                    f"{', '.join(available) if available else '없음'}"
                )

        # 매칭 파일들에서 메시지 로드 (dedup)
        seen_search: set[str] = set()
        messages = []
        for mp in matched_paths:
            with open(mp, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        msg = _safe_json_loads(line, mp)
                        if msg is None:
                            continue
                        dk = f"{msg.get('ts', '')}_{msg.get('channel_id', '')}"
                        if dk not in seen_search:
                            seen_search.add(dk)
                            messages.append(msg)
        if not messages:
            return f"검색어 '{query_keyword}' 결과가 비어 있습니다."

    elif scope != "all":
        return (
            "scope 형식 오류. 사용 가능한 값:\n"
            "- all: 전체\n"
            "- channel:<이름>: 특정 채널\n"
            "- week:<주>: 특정 주 (예: 2025-W22)\n"
            "- recent:<N>: 최근 N일\n"
            "- search:<검색어>: 특정 키워드 검색 결과\n"
            "- summary: 수집 현황"
        )

    # ── format ──
    if format == "json":
        return json.dumps(messages, ensure_ascii=False, indent=2)

    # markdown: scope에 따라 그룹핑 방식 결정
    if scope.startswith("channel:"):
        return _format_channel_messages_md(messages, cleaner, tz, cfg)
    else:
        return _format_weekly_md(messages, cleaner, tz, cfg)


# ── 엔트리포인트 ─────────────────────────────────────────────────

def main():
    """stdio transport로 MCP 서버 실행."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
