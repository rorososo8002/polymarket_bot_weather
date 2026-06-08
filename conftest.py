from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def pytest_configure(config: Any) -> None:
    """Keep pytest temp files inside the workspace unless explicitly overridden."""
    if config.option.basetemp is None:
        temp_parent = Path(__file__).resolve().parent / ".pytest-tmp"
        temp_parent.mkdir(exist_ok=True)
        config.option.basetemp = temp_parent / f"pytest-{os.getpid()}"
