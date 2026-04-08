"""스레드 대화 수집 (conversations.replies)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from slack_fetch.config import CrawlerConfig

logger = logging.getLogger(__name__)


def _thread_checkpoint_path(cfg: CrawlerConfig, user_id: str) -> Path:
    return cfg.user_raw_dir(user_id) / ".thread_checkpoint.json"


def _load_thread_checkpoint(cfg: CrawlerConfig, user_id: str) -> set[str]:
    cp = _thread_checkpoint_path(cfg, user_id)
    if cp.exists():
        data = json.loads(cp.read_text(encoding="utf-8"))
        return set(data.get("done", []))
    return set()


def _save_thread_checkpoint(cfg: CrawlerConfig, user_id: str, done: set[str]) -> None:
    _thread_checkpoint_path(cfg, user_id).write_text(
        json.dumps({"done": list(done)}, ensure_ascii=False),
        encoding="utf-8",
    )


def _resolve_user(client: WebClient, user_id: str, cache: dict[str, str]) -> str:
    if user_id in cache:
        return cache[user_id]
    try:
        resp = client.users_info(user=user_id)
        name = resp["user"]["profile"].get("real_name") or resp["user"].get("name", user_id)
        cache[user_id] = name
        return name
    except SlackApiError:
        cache[user_id] = user_id
        return user_id


def _load_public_channel_ids(cfg: CrawlerConfig) -> set[str]:
    channels_path = cfg.channels_path()
    if not channels_path.exists():
        return set()
    data = json.loads(channels_path.read_text(encoding="utf-8"))
    return {ch["id"] for ch in data.get("channels", [])}


def collect_threads(client: WebClient, cfg: CrawlerConfig, *, user_id: str | None = None) -> int:
    """messages.jsonl에서 스레드가 있는 메시지를 찾아 대화 전문을 수집."""
    uid = user_id or cfg.target_user_id
    messages_path = cfg.user_messages_path(uid)
    threads_dir = cfg.shared_threads_dir
    threads_dir.mkdir(parents=True, exist_ok=True)

    if not messages_path.exists():
        logger.error("[%s] messages.jsonl이 없습니다. collect를 먼저 실행하세요.", uid)
        return 0

    public_channels = _load_public_channel_ids(cfg)

    targets: dict[str, dict] = {}
    skipped = 0
    with open(messages_path, encoding="utf-8") as f:
        for line in f:
            msg = json.loads(line)
            thread_ts = msg.get("thread_ts")
            if not thread_ts:
                continue
            ch_id = msg["channel_id"]
            if ch_id.startswith("D") or (public_channels and ch_id not in public_channels):
                skipped += 1
                continue
            key = f"{ch_id}_{thread_ts}"
            if key not in targets:
                targets[key] = {
                    "channel_id": ch_id,
                    "channel_name": msg["channel_name"],
                    "thread_ts": thread_ts,
                }

    if skipped:
        logger.info("[%s] DM/private 채널 스레드 %d건 건너뜀 (scope 제한)", uid, skipped)

    target_ids = cfg.all_user_ids_set
    done = _load_thread_checkpoint(cfg, uid)
    user_cache: dict[str, str] = {}
    collected = 0

    logger.info("[%s] 스레드 %d개 대상 중 %d개 완료, %d개 남음",
                uid, len(targets), len(done), len(targets) - len(done))

    for key, info in targets.items():
        if key in done:
            continue

        out_path = threads_dir / f"{key}.jsonl"

        # 공유 캐시: 다른 사용자 수집 시 이미 저장된 파일이면 API 호출 skip
        if out_path.exists():
            logger.debug("[%s] 스레드 %s 이미 존재 — skip", uid, key)
            done.add(key)
            collected += 1
            if collected % 10 == 0:
                _save_thread_checkpoint(cfg, uid, done)
            continue

        replies: list[dict] = []
        cursor = None

        while True:
            try:
                kwargs = {
                    "channel": info["channel_id"],
                    "ts": info["thread_ts"],
                    "limit": cfg.page_limit,
                }
                if cursor:
                    kwargs["cursor"] = cursor
                resp = client.conversations_replies(**kwargs)
            except SlackApiError as e:
                if e.response.status_code == 429:
                    retry_after = int(e.response.headers.get("Retry-After", 30))
                    logger.warning("Rate limited. %d초 대기...", retry_after)
                    time.sleep(retry_after)
                    continue
                logger.warning("스레드 %s 수집 실패: %s", key, e.response["error"])
                break

            for msg in resp.get("messages", []):
                msg_user_id = msg.get("user", "unknown")
                replies.append({
                    "ts": msg.get("ts", ""),
                    "user": msg_user_id,
                    "user_name": _resolve_user(client, msg_user_id, user_cache),
                    "text": msg.get("text", ""),
                    "is_target_user": msg_user_id in target_ids,
                    "files": [fi.get("name", "") for fi in msg.get("files", [])],
                })

            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
            time.sleep(cfg.base_delay)

        if replies:
            with open(out_path, "w", encoding="utf-8") as f:
                for r in replies:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            collected += 1

        done.add(key)
        if collected % 10 == 0:
            _save_thread_checkpoint(cfg, uid, done)
        time.sleep(cfg.base_delay)

    _save_thread_checkpoint(cfg, uid, done)
    logger.info("[%s] 스레드 %d개 수집 완료", uid, collected)
    return collected
