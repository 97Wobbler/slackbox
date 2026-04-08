"""채널 목록 수집."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from slack_fetch.config import CrawlerConfig

logger = logging.getLogger(__name__)


def collect_channels(client: WebClient, cfg: CrawlerConfig, *, include_archived: bool = False) -> list[dict]:
    """가입한 공개 채널 목록을 수집하고 channels.json에 저장."""
    channels: list[dict] = []
    cursor = None

    while True:
        try:
            resp = client.conversations_list(
                types="public_channel",
                limit=cfg.page_limit,
                exclude_archived=not include_archived,
                cursor=cursor or "",
            )
        except SlackApiError as e:
            logger.error("conversations_list 실패: %s", e.response["error"])
            raise

        for ch in resp["channels"]:
            if not ch.get("is_member"):
                continue
            channels.append({
                "id": ch["id"],
                "name": ch["name"],
                "purpose": (ch.get("purpose") or {}).get("value", ""),
                "topic": (ch.get("topic") or {}).get("value", ""),
                "num_members": ch.get("num_members", 0),
                "is_archived": ch.get("is_archived", False),
            })

        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    out = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "user_id": cfg.target_user_id,
        "total": len(channels),
        "channels": channels,
    }
    out_path = cfg.channels_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("채널 %d개 수집 -> %s", len(channels), out_path)
    return channels
