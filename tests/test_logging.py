"""The log files are shared by the Gunicorn workers: rotating them must not lose history."""
import logging
import multiprocessing
import sys
import time

import pytest

from application import SharedRotatingFileHandler

LINE = "x" * 90  # with "w0-0000 " and the newline, 99 bytes per line
MAX_BYTES = 10_000
BACKUPS = 8  # 9 files of 10 KB: exactly room for the 3 x 300 lines (89 KB) written below


def _write_lines(path, worker, count, handler_class=SharedRotatingFileHandler, start=None):
    """What one worker does: its own handler on the shared file."""
    handler = handler_class(str(path), maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    if start is not None:
        start.wait()  # all workers write at the same time, as under real traffic
    for n in range(count):
        if start is not None:
            time.sleep(0.0005)  # and their lines interleave
        handler.emit(logging.LogRecord("audit", logging.INFO, __file__, 0, f"{worker}-{n:04d} {LINE}", None, None))
    handler.close()


def _logged_lines(tmp_path):
    lines = []
    for f in tmp_path.glob("audit.log*"):
        if not f.name.endswith(".lock"):
            lines += f.read_text(encoding="utf-8").splitlines()
    return lines


def test_rotation_keeps_every_line(tmp_path):
    _write_lines(tmp_path / "audit.log", "w0", 400)
    lines = _logged_lines(tmp_path)
    assert sorted(lines) == sorted(f"w0-{n:04d} {LINE}" for n in range(400))
    assert all(f.stat().st_size <= MAX_BYTES for f in tmp_path.glob("audit.log*"))


@pytest.mark.skipif(sys.platform == "win32", reason="Windows can't rename a file another process has open; "
                                                   "production runs on Linux")
def test_workers_sharing_a_log_lose_nothing_when_it_rotates(tmp_path):
    # Without the lock each worker rotated on its own and kept writing to a file another one had
    # renamed: files were rotated half full (seen: 6 KB of 10), so the same lines needed more
    # files and the oldest ones, with their history, were deleted early. With room for exactly
    # what is written, a plain RotatingFileHandler loses lines here; this one keeps them all.
    fork = multiprocessing.get_context("fork")
    start = fork.Barrier(3)
    workers = [fork.Process(target=_write_lines,
                            args=(tmp_path / "audit.log", f"w{i}", 300, SharedRotatingFileHandler, start))
               for i in range(3)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(60)
        assert w.exitcode == 0
    lines = _logged_lines(tmp_path)
    assert sorted(lines) == sorted(f"w{i}-{n:04d} {LINE}" for i in range(3) for n in range(300))
    assert all(f.stat().st_size <= MAX_BYTES for f in tmp_path.glob("audit.log*"))
