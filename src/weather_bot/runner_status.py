from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def runner_status_path(settings: Settings) -> Path:
    return Path(settings.state_path).with_name("paper_runner_status.json")


def read_runner_status(settings: Settings) -> dict[str, Any]:
    path = runner_status_path(settings)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_runner_status(settings: Settings, phase: str, **fields: Any) -> None:
    payload = {
        "updated_at": utc_now_iso(),
        "phase": phase,
    }
    payload.update({key: value for key, value in fields.items() if value is not None})
    _write_runner_status_payload(settings, payload)


def update_runner_status_fields(settings: Settings, **fields: Any) -> None:
    payload = read_runner_status(settings)
    if "phase" not in payload:
        payload["phase"] = "unknown"
    payload.update({key: value for key, value in fields.items() if value is not None})
    payload["updated_at"] = utc_now_iso()
    _write_runner_status_payload(settings, payload)


def _write_runner_status_payload(settings: Settings, payload: dict[str, Any]) -> None:
    path = runner_status_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
