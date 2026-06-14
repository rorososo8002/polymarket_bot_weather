from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_root_conftest():
    conftest_path = ROOT / "conftest.py"
    assert conftest_path.exists(), "root conftest.py must provide the pytest workspace-temp default"
    spec = importlib.util.spec_from_file_location("workflow_defaults_conftest", conftest_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pytest_defaults_to_stable_workspace_temp_dir():
    module = _load_root_conftest()

    class Option:
        basetemp = None

    class Config:
        option = Option()

    config = Config()
    module.pytest_configure(config)

    assert Path(config.option.basetemp) == ROOT / ".pytest-tmp" / "current"
    assert Path(config.option.basetemp).is_dir()


def test_pytest_configure_creates_workspace_temp_parent(tmp_path):
    module = _load_root_conftest()
    clean_root = tmp_path / "repo"
    clean_root.mkdir()

    original_file = module.__file__
    module.__file__ = str(clean_root / "conftest.py")
    try:
        class Option:
            basetemp = None

        class Config:
            option = Option()

        config = Config()
        module.pytest_configure(config)

        expected_parent = clean_root / ".pytest-tmp"
        assert expected_parent.is_dir()
        assert Path(config.option.basetemp) == expected_parent / "current"
        assert Path(config.option.basetemp).is_dir()
    finally:
        module.__file__ = original_file


def test_running_pytest_uses_stable_workspace_temp_dir(tmp_path_factory):
    assert tmp_path_factory.getbasetemp() == ROOT / ".pytest-tmp" / "current"


def test_pytest_preserves_explicit_basetemp_override():
    module = _load_root_conftest()
    explicit_path = ROOT / "custom-pytest-temp"

    class Option:
        basetemp = explicit_path

    class Config:
        option = Option()

    config = Config()
    module.pytest_configure(config)

    assert config.option.basetemp == explicit_path


def test_known_good_commands_are_linked_from_agents_and_codex_index():
    command_doc = ROOT / "docs" / "codex" / "known-good-commands.md"
    agents_text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    codex_index = (ROOT / "docs" / "codex" / "README.md").read_text(encoding="utf-8")

    assert command_doc.exists()
    assert "docs/codex/known-good-commands.md" in agents_text
    assert "known-good-commands.md" in codex_index


def test_known_good_commands_include_local_test_and_oracle_first_steps():
    text = (ROOT / "docs" / "codex" / "known-good-commands.md").read_text(encoding="utf-8")

    assert "& 'C:\\Users\\wpdla\\Python312\\python.exe' -m pytest -q" in text
    assert "ssh-key-2026-05-25.key" in text
    assert "$oracle = 'ubuntu@140.245.69.242'" in text
    assert "Test-Path -LiteralPath $key" in text
    assert "ssh -i $key $oracle date" in text
    assert "cd /opt/polymarket-weather-bot" in text
    assert "sudo -u polymarket .venv/bin/python -m pytest -q" in text


def test_fresh_chat_uses_active_task_card_not_process_diary():
    agents_text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    active_readme = (ROOT / "docs" / "active" / "README.md").read_text(encoding="utf-8")
    current_task = (ROOT / "docs" / "active" / "current-task.md").read_text(encoding="utf-8")

    assert "Mandatory fresh-chat read set" in agents_text
    assert "docs/active/current-task.md" in agents_text
    assert "docs/production-decisions.md" in agents_text
    assert "docs/production-progress.md` as an optional compact project board" in agents_text
    assert "Status: none" in current_task or "Status: active" in current_task
    assert "## New Chat Prompt" in current_task
    assert "Mandatory Fresh-Chat Read Set" in active_readme
    assert "docs/active/current-task.md" in active_readme


def test_handoff_docs_stay_compact_and_avoid_chronological_ledgers():
    line_limits = {
        "docs/active/current-task.md": 80,
        "docs/production-progress.md": 140,
        "docs/production-decisions.md": 220,
        "docs/production-implementation-plan.md": 350,
    }
    for relative_path, max_lines in line_limits.items():
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert len(text.splitlines()) <= max_lines, f"{relative_path} should stay under {max_lines} lines"

    decisions = (ROOT / "docs" / "production-decisions.md").read_text(encoding="utf-8")
    progress = (ROOT / "docs" / "production-progress.md").read_text(encoding="utf-8")

    assert "## Compact Ledger" not in decisions
    assert "Completed local" not in progress


def test_paper_validation_runbook_defines_live_readiness_gates():
    runbook_path = ROOT / "docs" / "paper-validation-runbook.md"
    decisions = (ROOT / "docs" / "production-decisions.md").read_text(encoding="utf-8")
    roadmap = (ROOT / "docs" / "strategy-validation-roadmap.md").read_text(encoding="utf-8")

    assert runbook_path.exists()
    runbook = runbook_path.read_text(encoding="utf-8")

    required_phrases = [
        "30 days",
        "paper-only",
        "decision rows",
        "open/close trades",
        "bid/ask-depth net PnL",
        "midpoint/reference gap",
        "no-liquidity",
        "core tests",
        "new experiment version",
        "live-trading safety project",
    ]
    for phrase in required_phrases:
        assert phrase in runbook

    assert "docs/paper-validation-runbook.md" in decisions
    assert "docs/paper-validation-runbook.md" in roadmap
