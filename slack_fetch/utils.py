"""공통 유틸리티 — 체크포인트, JSONL 처리 등."""

import json
from pathlib import Path


def checkpoint_load(path: Path) -> dict:
    """체크포인트 파일을 로드한다. 파일이 없으면 빈 dict를 반환."""
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def checkpoint_save(path: Path, data: dict) -> None:
    """체크포인트 데이터를 JSON으로 저장한다."""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
