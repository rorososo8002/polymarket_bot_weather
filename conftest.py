from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


def pytest_configure(config: Any) -> None:
    """Keep pytest temp files inside the workspace unless explicitly overridden."""
    if config.option.basetemp is None:
        repo_root = Path(__file__).resolve().parent
        cache_dir = repo_root / ".pytest_cache"
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir, ignore_errors=True)
        temp_parent = repo_root / ".pytest-tmp"
        if temp_parent.exists():
            shutil.rmtree(temp_parent, ignore_errors=True)
        temp_parent.mkdir(exist_ok=True)
        temp_base = temp_parent / "current"
        temp_base.mkdir(exist_ok=True)
        config.option.basetemp = temp_base


def pytest_unconfigure(config: Any) -> None:
    """Remove workspace-owned pytest scratch data after pytest exits."""
    basetemp = getattr(config.option, "basetemp", None)
    if basetemp is None:
        return
    repo_root = Path(__file__).resolve().parent
    temp_parent = repo_root / ".pytest-tmp"
    try:
        Path(basetemp).resolve().relative_to(temp_parent.resolve())
    except (OSError, ValueError):
        return
    shutil.rmtree(temp_parent, ignore_errors=True)
