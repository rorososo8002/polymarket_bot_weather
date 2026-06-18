from pathlib import Path

from weather_bot.runtime_cleanup import prune_runtime_archive


def _write_bytes(path: Path, size: int, *, mtime: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    path.touch()
    import os

    os.utime(path, (mtime, mtime))


def test_prune_runtime_archive_deletes_oldest_diagnostic_archives_over_budget(tmp_path):
    archive = tmp_path / "archive"
    old = archive / "paper_event_portfolios.jsonl.1.zst"
    middle = archive / "forecast_request_log.jsonl.1.zst"
    newest = archive / "paper_raw_snapshots.29990101T000000Z.jsonl.gz"
    unknown = archive / "operator-note.txt"
    _write_bytes(old, 50, mtime=100)
    _write_bytes(middle, 40, mtime=200)
    _write_bytes(newest, 30, mtime=300)
    _write_bytes(unknown, 1000, mtime=50)

    summary = prune_runtime_archive(tmp_path, max_archive_bytes=70)

    assert summary.deleted_files == [str(old)]
    assert summary.before_bytes == 120
    assert summary.after_bytes == 70
    assert not old.exists()
    assert middle.exists()
    assert newest.exists()
    assert unknown.exists()


def test_prune_runtime_archive_never_deletes_active_ledgers(tmp_path):
    _write_bytes(tmp_path / "paper_state.json", 100, mtime=100)
    _write_bytes(tmp_path / "paper_trades.csv", 100, mtime=100)
    _write_bytes(tmp_path / "paper_decisions.csv", 100, mtime=100)
    _write_bytes(tmp_path / "archive" / "paper_event_portfolios.jsonl.1.zst", 90, mtime=100)

    summary = prune_runtime_archive(tmp_path, max_archive_bytes=10)

    assert summary.deleted_files == [str(tmp_path / "archive" / "paper_event_portfolios.jsonl.1.zst")]
    assert (tmp_path / "paper_state.json").exists()
    assert (tmp_path / "paper_trades.csv").exists()
    assert (tmp_path / "paper_decisions.csv").exists()


def test_prune_runtime_archive_includes_skip_diagnostics(tmp_path):
    archive_file = tmp_path / "archive" / "paper_skip_diagnostics.jsonl.1.zst"
    _write_bytes(archive_file, 120, mtime=100)

    summary = prune_runtime_archive(tmp_path, max_archive_bytes=50)

    assert summary.deleted_files == [str(archive_file)]
    assert not archive_file.exists()


def test_prune_runtime_archive_dry_run_reports_without_deleting(tmp_path):
    archive_file = tmp_path / "archive" / "station_nowcast_request_log.jsonl.1.zst"
    _write_bytes(archive_file, 120, mtime=100)

    summary = prune_runtime_archive(tmp_path, max_archive_bytes=50, dry_run=True)

    assert summary.deleted_files == [str(archive_file)]
    assert summary.after_bytes == 0
    assert archive_file.exists()
