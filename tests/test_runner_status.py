from __future__ import annotations

import json
from pathlib import Path

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


def test_runner_status_writes_use_unique_temp_paths(tmp_path, monkeypatch):
    settings = Settings(state_path=str(tmp_path / "paper_state.json"))
    replaced_sources: list[str] = []
    real_replace = runner_status_module.os.replace

    def record_replace(src, dst):
        replaced_sources.append(Path(src).name)
        real_replace(src, dst)

    monkeypatch.setattr(runner_status_module.os, "replace", record_replace)

    write_runner_status(settings, "discovering", message="first")
    runner_status_module.update_runner_status_fields(settings, forecast_worker={"thread_alive": True})

    assert len(replaced_sources) == 2
    assert replaced_sources[0] != replaced_sources[1]
    assert all(name.startswith("paper_runner_status.json.") for name in replaced_sources)
    assert all(name.endswith(".tmp") for name in replaced_sources)
