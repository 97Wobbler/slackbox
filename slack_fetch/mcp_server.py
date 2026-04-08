"""Slack Fetch MCP Server.

Claude Code에서 Slack 벌크 크롤링을 수행할 수 있는 MCP 서버.
기존 slack_fetch 함수들을 호출하는 얇은 래퍼.

실행: python -m slack_fetch
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp.server import FastMCP

from slack_fetch.config import CrawlerConfig
from slack_fetch.client import create_slack_client
from slack_fetch.channels import collect_channels
from slack_fetch.messages import collect_via_search, collect_via_history
from slack_fetch.threads import collect_threads
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
    total = 0

    for uid in cfg.target_user_ids:
        count = collect_via_history(
            client, cfg, [target_ch], since=since, user_id=uid
        )
        total += count

    period_desc = f"최근 {days}일" if days > 0 else "전체 기간"
    return (
        f"#{target_ch['name']} 채널 {period_desc} 대화 수집 완료.\n"
        f"수집된 메시지: {total}건 (대상 사용자 {len(cfg.target_user_ids)}명)\n"
        f"get_collected_data로 수집 데이터를 조회할 수 있습니다."
    )


@mcp.tool()
def crawl_user(user_id: str, days: int = 30) -> str:
    """특정 사용자의 최근 N일간 활동을 수집합니다.

    search.messages를 우선 사용하고, 실패 시 conversations.history로 fallback합니다.

    Args:
        user_id: Slack 사용자 ID (예: "U07AF1YDVD1")
        days: 수집할 기간 (일). 기본값 30일. 0이면 전체 기간 수집.
    """
    cfg = _get_cfg()
    client = _get_client()

    since = _since_str(days) if days > 0 else None

    try:
        total = collect_via_search(client, cfg, since=since, user_id=user_id)
        method = "search.messages"
    except Exception as e:
        logger.warning("search.messages 실패, conversations.history로 fallback: %s", e)
        channels = _load_channels(cfg)
        if not channels:
            channels = collect_channels(client, cfg)
        total = collect_via_history(client, cfg, channels, since=since, user_id=user_id)
        method = "conversations.history"

    period_desc = f"최근 {days}일" if days > 0 else "전체 기간"
    return (
        f"사용자 {user_id}의 {period_desc} 활동 수집 완료.\n"
        f"수집 방법: {method}\n"
        f"수집된 메시지: {total}건\n"
        f"스레드 수집이 필요하면 crawl_threads를 실행하세요."
    )


@mcp.tool()
def crawl_threads(channel: str, thread_ts_list: list[str]) -> str:
    """지정된 스레드들의 전체 대화를 수집합니다.

    Args:
        channel: 채널 이름 또는 채널 ID
        thread_ts_list: 수집할 스레드의 타임스탬프 목록 (예: ["1717000000.000000"])
    """
    cfg = _get_cfg()
    client = _get_client()

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

        except Exception as e:
            errors_list.append(f"{thread_ts}: {e}")

    result = f"#{channel_name} 스레드 {collected}/{len(thread_ts_list)}개 수집 완료."
    if errors_list:
        result += f"\n오류: {'; '.join(errors_list)}"
    return result


@mcp.tool()
def get_collected_data(scope: str, format: str = "markdown") -> str:
    """수집된 데이터를 정제된 형태로 반환합니다.

    Args:
        scope: 조회 범위.
            - "all": 전체 수집 데이터
            - "channel:<이름>": 특정 채널 데이터 (예: "channel:general")
            - "week:<주>": 특정 주 데이터 (예: "week:2025-W22")
            - "recent:<N>": 최근 N일 데이터 (예: "recent:7")
            - "summary": 수집 현황 요약
        format: 출력 형식. "markdown" (기본값) 또는 "json".
    """
    cfg = _get_cfg()

    # 모든 user의 메시지 로드
    all_messages: list[dict] = []
    for uid in cfg.target_user_ids:
        mp = cfg.user_messages_path(uid)
        if not mp.exists():
            continue
        with open(mp, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    all_messages.append(json.loads(line))

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

        if not all_messages:
            return (
                f"수집 현황:\n"
                f"- 채널: {len(channels)}개\n"
                f"- 메시지: 0건\n"
                f"- 스레드: {thread_count}개\n"
                f"- 대상 사용자: {', '.join(cfg.target_user_ids)}"
            )

        all_messages.sort(key=lambda m: float(m.get("ts", "0")))
        ch_names = set(m.get("channel_name", "?") for m in all_messages)
        first = ts_to_str(all_messages[0]["ts"], tz)
        last = ts_to_str(all_messages[-1]["ts"], tz)

        return (
            f"수집 현황:\n"
            f"- 채널: {len(channels)}개 (활동: {len(ch_names)}개)\n"
            f"- 메시지: {len(all_messages)}건\n"
            f"- 스레드: {thread_count}개\n"
            f"- 기간: {first} ~ {last}\n"
            f"- 대상 사용자: {', '.join(cfg.target_user_ids)}\n"
            f"- 활동 채널: {', '.join(sorted(ch_names))}"
        )

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

    elif scope != "all":
        return (
            "scope 형식 오류. 사용 가능한 값:\n"
            "- all: 전체\n"
            "- channel:<이름>: 특정 채널\n"
            "- week:<주>: 특정 주 (예: 2025-W22)\n"
            "- recent:<N>: 최근 N일\n"
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
