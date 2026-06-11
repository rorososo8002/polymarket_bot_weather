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
        temp_parent.mkdir(exist_ok=True)
        for stale_temp in temp_parent.glob("pytest-*"):
            if stale_temp.is_dir() and stale_temp.parent == temp_parent:
                shutil.rmtree(stale_temp, ignore_errors=True)
        temp_base = temp_parent / "current"
        if temp_base.exists():
            shutil.rmtree(temp_base, ignore_errors=True)
        temp_base.mkdir(exist_ok=True)
        config.option.basetemp = temp_base
