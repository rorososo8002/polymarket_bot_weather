from __future__ import annotations

import importlib.util
import os
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


def test_pytest_defaults_to_process_specific_workspace_temp_dir():
    module = _load_root_conftest()

    class Option:
        basetemp = None

    class Config:
        option = Option()

    config = Config()
    module.pytest_configure(config)

    assert Path(config.option.basetemp) == ROOT / ".pytest-tmp" / f"pytest-{os.getpid()}"


def test_running_pytest_uses_process_specific_workspace_temp_dir(tmp_path_factory):
    assert tmp_path_factory.getbasetemp() == ROOT / ".pytest-tmp" / f"pytest-{os.getpid()}"


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
