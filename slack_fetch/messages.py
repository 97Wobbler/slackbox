"""메시지 수집 (search.messages + conversations.history)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from slack_fetch.config import CrawlerConfig
from slack_fetch.rate_limit import detect_tier, rate_wait, handle_rate_limit

logger = logging.getLogger(__name__)


# -- Checkpoint --

def _checkpoint_path(cfg: CrawlerConfig, user_id: str) -> Path:
    return cfg.user_raw_dir(user_id) / ".checkpoint.json"


def _load_checkpoint(cfg: CrawlerConfig, user_id: str) -> dict:
    cp = _checkpoint_path(cfg, user_id)
    if cp.exists():
        return json.loads(cp.read_text(encoding="utf-8"))
    return {}


def _save_checkpoint(cfg: CrawlerConfig, user_id: str, data: dict) -> None:
    _checkpoint_path(cfg, user_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _extract_thread_ts_from_permalink(permalink: str) -> str | None:
    m = re.search(r"thread_ts=(\d+\.\d+)", permalink)
    return m.group(1) if m else None


# -- search.messages --

def collect_via_search(client: WebClient, cfg: CrawlerConfig,
                       *, since: str | None = None, until: str | None = None,
                       user_id: str | None = None) -> int:
    """search.messages로 대상 사용자의 메시지를 수집. User Token 전용."""
    uid = user_id or cfg.target_user_id
    messages_path = cfg.user_messages_path(uid)
    messages_path.parent.mkdir(parents=True, exist_ok=True)

    query = f"from:<@{uid}>"
    if since:
        query += f" after:{since}"
    if until:
        query += f" before:{until}"

    seen_ts: set[str] = set()
    if messages_path.exists():
        with open(messages_path, encoding="utf-8") as ef:
            for line in ef:
                rec = json.loads(line)
                seen_ts.add(f"{rec['ts']}_{rec.get('channel_id', '')}")

    total = 0
    page = 1

    with open(messages_path, "a", encoding="utf-8") as f:
        while True:
            try:
                resp = client.search_messages(query=query, sort="timestamp",
                                              sort_dir="asc", count=100, page=page)
            except SlackApiError as e:
                if e.response.status_code == 429:
                    handle_rate_limit(e)
                    continue
                raise

            messages = resp.get("messages", {}).get("matches", [])
            if not messages:
                break

            for msg in messages:
                permalink = msg.get("permalink", "")
                thread_ts = msg.get("thread_ts") or _extract_thread_ts_from_permalink(permalink)
                ts = msg.get("ts", "")
                reply_count = msg.get("reply_count", 0)

                if thread_ts and thread_ts == ts and reply_count > 0:
                    msg_type = "thread_parent"
                elif thread_ts and thread_ts != ts:
                    msg_type = "thread_reply"
                elif thread_ts and thread_ts == ts:
                    msg_type = "thread_parent"
                else:
                    msg_type = "message"

                channel_id = msg.get("channel", {}).get("id", "")
                dedup_key = f"{ts}_{channel_id}"
                if dedup_key in seen_ts:
                    continue
                seen_ts.add(dedup_key)

                record = {
                    "ts": ts,
                    "channel_id": channel_id,
                    "channel_name": msg.get("channel", {}).get("name", ""),
                    "text": msg.get("text", ""),
                    "thread_ts": thread_ts,
                    "reply_count": reply_count,
                    "permalink": permalink,
                    "type": msg_type,
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                total += 1

            paging = resp.get("messages", {}).get("paging", {})
            if page >= paging.get("pages", 1):
                break
            page += 1
            rate_wait(1.0)

    _save_checkpoint(cfg, uid, {"phase": "search_done", "total_messages": total})
    logger.info("[%s] search.messages로 메시지 %d건 수집 완료", uid, total)
    return total


# -- conversations.history --

def collect_via_history(client: WebClient, cfg: CrawlerConfig, channels: list[dict],
                        *, since: str | None = None, until: str | None = None,
                        user_id: str | None = None) -> int:
    """conversations.history로 전체 채널을 순회하며 대상 사용자 메시지를 수집."""
    uid = user_id or cfg.target_user_id
    messages_path = cfg.user_messages_path(uid)
    messages_path.parent.mkdir(parents=True, exist_ok=True)

    ckpt = _load_checkpoint(cfg, uid)
    start_idx = ckpt.get("last_channel_idx", 0) if ckpt.get("phase") == "history" else 0
    total = ckpt.get("collected_messages", 0)
    delay = cfg.base_delay
    tier_detected = False

    with open(messages_path, "a", encoding="utf-8") as f:
        for i, ch in enumerate(channels):
            if i < start_idx:
                continue

            cursor = None
            logger.info("[%s] [%d/%d] #%s 수집 중...", uid, i + 1, len(channels), ch["name"])

            while True:
                try:
                    kwargs = {"channel": ch["id"], "limit": cfg.page_limit}
                    if cursor:
                        kwargs["cursor"] = cursor
                    if since:
                        kwargs["oldest"] = since
                    if until:
                        kwargs["latest"] = until

                    resp = client.conversations_history(**kwargs)

                    if not tier_detected and hasattr(resp, "headers"):
                        cfg.page_limit, delay = detect_tier(resp.headers)
                        tier_detected = True
                        logger.info("Rate limit tier 감지: limit=%d, delay=%.1fs",
                                    cfg.page_limit, delay)

                except SlackApiError as e:
                    if e.response.status_code == 429:
                        handle_rate_limit(e)
                        continue
                    if e.response["error"] == "not_in_channel":
                        logger.warning("#%s: not_in_channel, 건너뜀", ch["name"])
                        break
                    raise

                for msg in resp.get("messages", []):
                    if msg.get("user") != uid:
                        continue
                    if msg.get("subtype") in ("channel_join", "channel_leave", "bot_message"):
                        continue

                    record = {
                        "ts": msg.get("ts", ""),
                        "channel_id": ch["id"],
                        "channel_name": ch["name"],
                        "text": msg.get("text", ""),
                        "thread_ts": msg.get("thread_ts"),
                        "reply_count": msg.get("reply_count", 0),
                        "type": "thread_parent" if msg.get("reply_count", 0) > 0 else "message",
                        "files": [fi.get("name", "") for fi in msg.get("files", [])],
                    }
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    total += 1

                cursor = resp.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
                rate_wait(delay)

            _save_checkpoint(cfg, uid, {
                "phase": "history",
                "last_channel_idx": i + 1,
                "last_channel_id": ch["id"],
                "collected_messages": total,
            })
            rate_wait(delay)

    logger.info("[%s] conversations.history로 메시지 %d건 수집 완료", uid, total)
    return total
