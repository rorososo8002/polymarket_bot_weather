"""Prune rotated runtime diagnostics without touching paper ledgers."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
DIAGNOSTIC_ARCHIVE_PREFIXES = (
    "paper_raw_snapshots",
    "forecast_request_log",
    "station_nowcast_request_log",
    "paper_event_portfolios",
)


@dataclass(frozen=True)
class RuntimeCleanupSummary:
    archive_dir: str
    max_archive_bytes: int
    before_bytes: int
    after_bytes: int
    deleted_files: list[str]
    dry_run: bool


def _is_diagnostic_archive(path: Path) -> bool:
    return path.is_file() and any(
        path.name.startswith(prefix) for prefix in DIAGNOSTIC_ARCHIVE_PREFIXES
    )


def _archive_files(archive_dir: Path) -> Iterable[Path]:
    if not archive_dir.exists():
        return []
    return [path for path in archive_dir.iterdir() if _is_diagnostic_archive(path)]


def prune_runtime_archive(
    data_dir: str | Path,
    *,
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
    dry_run: bool = False,
) -> RuntimeCleanupSummary:
    """Delete oldest known diagnostic archives until the archive is under budget."""

    archive_dir = Path(data_dir) / "archive"
    files = list(_archive_files(archive_dir))
    sizes = {path: path.stat().st_size for path in files}
    before_bytes = sum(sizes.values())
    remaining_bytes = before_bytes
    deleted_files: list[str] = []

    if remaining_bytes > max_archive_bytes:
        ordered = sorted(files, key=lambda path: (path.stat().st_mtime, path.name))
        for path in ordered:
            if remaining_bytes <= max_archive_bytes:
                break
            size = sizes[path]
            deleted_files.append(str(path))
            remaining_bytes -= size
            if not dry_run:
                path.unlink()

    return RuntimeCleanupSummary(
        archive_dir=str(archive_dir),
        max_archive_bytes=max_archive_bytes,
        before_bytes=before_bytes,
        after_bytes=remaining_bytes,
        deleted_files=deleted_files,
        dry_run=dry_run,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prune rotated Polymarket weather bot diagnostic archives."
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Runtime data directory that contains the archive directory.",
    )
    parser.add_argument(
        "--max-archive-bytes",
        type=int,
        default=DEFAULT_MAX_ARCHIVE_BYTES,
        help="Maximum total bytes for known diagnostic archives.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report files that would be deleted without deleting them.",
    )
    args = parser.parse_args(argv)

    summary = prune_runtime_archive(
        args.data_dir,
        max_archive_bytes=args.max_archive_bytes,
        dry_run=args.dry_run,
    )
    print(json.dumps(asdict(summary), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
