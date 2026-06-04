from __future__ import annotations

import json

from weather_bot.config import Settings
from weather_bot import runner_status as runner_status_module
from weather_bot.runner_status import runner_status_path, write_runner_status


def test_write_runner_status_lives_next_to_state_file(tmp_path):
    settings = Settings(state_path=str(tmp_path / "paper_state.json"))

    write_runner_status(settings, "evaluating", message="evaluating 3/40", markets_done=3, markets_total=40)

    path = runner_status_path(settings)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert path == tmp_path / "paper_runner_status.json"
    assert payload["phase"] == "evaluating"
    assert payload["message"] == "evaluating 3/40"
    assert payload["markets_done"] == 3
    assert payload["markets_total"] == 40
    assert payload["updated_at"]


def test_update_runner_status_fields_preserves_current_phase_and_message(tmp_path):
    settings = Settings(state_path=str(tmp_path / "paper_state.json"))
    write_runner_status(settings, "evaluating", message="evaluating 3/40", markets_done=3)

    assert hasattr(runner_status_module, "update_runner_status_fields")
    runner_status_module.update_runner_status_fields(
        settings,
        raw_snapshot_storage={"status": "suspended", "reason": "disk usage is dangerous"},
    )

    payload = json.loads(runner_status_path(settings).read_text(encoding="utf-8"))
    assert payload["phase"] == "evaluating"
    assert payload["message"] == "evaluating 3/40"
    assert payload["markets_done"] == 3
    assert payload["raw_snapshot_storage"]["status"] == "suspended"
    assert "disk usage" in payload["raw_snapshot_storage"]["reason"]
