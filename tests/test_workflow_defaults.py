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


def test_pytest_unconfigure_removes_workspace_temp_tree(tmp_path):
    module = _load_root_conftest()
    clean_root = tmp_path / "repo"
    temp_base = clean_root / ".pytest-tmp" / "current"
    temp_base.mkdir(parents=True)
    (temp_base / "leftover.txt").write_text("temporary", encoding="utf-8")

    original_file = module.__file__
    module.__file__ = str(clean_root / "conftest.py")
    try:
        class Option:
            basetemp = temp_base

        class Config:
            option = Option()

        module.pytest_unconfigure(Config())
    finally:
        module.__file__ = original_file

    assert not (clean_root / ".pytest-tmp").exists()


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


def test_fresh_chat_uses_one_strategy_source():
    agents_text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    current_task = (ROOT / "docs" / "active" / "current-task.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "docs/active/current-task.md" in agents_text
    assert "STRATEGY.md" in agents_text
    assert "STRATEGY.md" in current_task
    assert "STRATEGY.md" in readme
    assert "Status: none" in current_task or "Status: active" in current_task
    assert "## New Chat Prompt" in current_task


def test_active_docs_stay_compact():
    line_limits = {
        "docs/active/current-task.md": 80,
        "STRATEGY.md": 180,
    }
    for relative_path, max_lines in line_limits.items():
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert len(text.splitlines()) <= max_lines, f"{relative_path} should stay under {max_lines} lines"

def test_obsolete_duplicate_handoff_and_report_files_are_removed():
    obsolete_paths = [
        "docs/active/README.md",
        "docs/active/new-chat-task-prompts.md",
        "docs/codex/strategy-research.md",
        "docs/live-trading-safety-plan.md",
        "docs/paper-validation-runbook.md",
        "docs/production-decisions.md",
        "docs/production-progress.md",
        "docs/production-implementation-plan.md",
        "docs/dashboard-build-spec.md",
        "docs/station-registry-audit.md",
        "docs/strategy-validation-roadmap.md",
        "docs/solutions",
        "docs/VPS_LIVE_PAPER.md",
        "readme2.md",
        "scripts/daily_report.py",
        "tests/test_daily_report.py",
    ]

    for relative_path in obsolete_paths:
        assert not (ROOT / relative_path).exists(), f"{relative_path} should be removed"


def test_strategy_defines_exact_no_contract():
    strategy = (ROOT / "STRATEGY.md").read_text(encoding="utf-8")
    required_phrases = [
        "name: 관측 경계 확정 NO 종이매매 봇",
        "last_updated:",
        "최고 30℃가 들어오면 29℃ NO를 즉시 확인",
        "최저 21℃가 들어오면 22℃ NO를 즉시 확인",
        "최종 최고가 30℃라면 29℃와 31℃ 모두 NO",
        "0.92달러 이하",
        "최대 2개",
        "고정된 도시·날짜 5% 제한으로 후보",
        "기온이 그대로라는 이유로 후보 목록에서 지우지 않는다",
    ]
    for phrase in required_phrases:
        assert phrase in strategy

    forbidden_phrases = [
        "0.85달러 이하",
        "안전거리 2℃",
        "NO 확률을 최대 96%",
        "후보 판정까지 3초 이내",
        "총원금은 최대 5%",
    ]
    for phrase in forbidden_phrases:
        assert phrase not in strategy
