"""Tests for the per-PDF summary lock and stale-lock recovery."""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil
import pytest

import config
import pdf_lock


def buildStartedAt(offset_seconds: float = 0) -> str:
    """Build an ISO-8601 UTC timestamp.

    Args:
        offset_seconds: Seconds relative to now.

    Returns:
        A ``Z``-suffixed timestamp.
    """
    moment = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)

    return moment.isoformat().replace("+00:00", "Z")


def writeLock(pdf_name: str, payload: dict) -> Path:
    """Write a raw lock payload for one PDF.

    Args:
        pdf_name: PDF file name without its extension.
        payload: JSON-ready lock payload.

    Returns:
        The lock path.
    """
    lock_path = config.getLockPath(pdf_name)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(payload), encoding="utf-8")

    return lock_path


def spawnGeneratorLikeProcess(pdf_name: str) -> subprocess.Popen:
    """Start a sleeping process whose command line looks like the generator.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        The running subprocess.
    """
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            "summary_generator.py",
            pdf_name,
        ]
    )

    for _attempt in range(200):
        if "summary_generator.py" in " ".join(
            psutil.Process(process.pid).cmdline()
        ):
            return process

        time.sleep(0.01)

    return process


def testAcquireAndReleaseLock(data_root: Path) -> None:
    """Acquisition writes the payload and release is idempotent."""
    lock_path = pdf_lock.acquireLock("book")
    payload = json.loads(lock_path.read_text(encoding="utf-8"))

    assert lock_path == config.getLockPath("book")
    assert payload["pid"] == os.getpid()
    assert payload["pdf_name"] == "book"
    assert payload["started_at"].endswith("Z")

    pdf_lock.releaseLock("book")
    pdf_lock.releaseLock("book")

    assert not lock_path.exists()


def testActiveLockBlocksAcquisition(data_root: Path) -> None:
    """A live generator process keeps its lock and blocks a second run."""
    process = spawnGeneratorLikeProcess("book")

    try:
        lock_path = writeLock(
            "book",
            {
                "pid": process.pid,
                "pdf_name": "book",
                "started_at": buildStartedAt(),
            },
        )

        with pytest.raises(RuntimeError) as error:
            pdf_lock.acquireLock("book")

        assert "already running" in str(error.value)
        assert str(process.pid) in str(error.value)
        assert lock_path.exists()

        with pytest.raises(RuntimeError):
            pdf_lock.assertNoActiveLock("book")
    finally:
        process.terminate()
        process.wait(timeout=10)


def testDeadPidLockIsRemovedOnAcquire(data_root: Path) -> None:
    """A lock written by a dead PID is replaced by the caller."""
    writeLock(
        "book",
        {
            "pid": 2**31 - 1,
            "pdf_name": "book",
            "started_at": buildStartedAt(),
        },
    )

    lock_path = pdf_lock.acquireLock("book")
    payload = json.loads(lock_path.read_text(encoding="utf-8"))

    assert payload["pid"] == os.getpid()


def testWrongCommandLineIsTreatedAsStale(data_root: Path) -> None:
    """A reused PID that does not run the generator does not hold the lock."""
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"]
    )

    try:
        lock_path = writeLock(
            "book",
            {
                "pid": process.pid,
                "pdf_name": "book",
                "started_at": buildStartedAt(),
            },
        )

        pdf_lock.assertNoActiveLock("book")

        assert not lock_path.exists()
    finally:
        process.terminate()
        process.wait(timeout=10)


def testProcessStartedAfterLockIsTreatedAsStale(data_root: Path) -> None:
    """A process created after the recorded start cannot own the lock."""
    process = spawnGeneratorLikeProcess("book")

    try:
        lock_path = writeLock(
            "book",
            {
                "pid": process.pid,
                "pdf_name": "book",
                "started_at": buildStartedAt(-3600),
            },
        )

        pdf_lock.assertNoActiveLock("book")

        assert not lock_path.exists()
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.mark.parametrize("contents", ["", "not json", "[]", '"text"'])
def testMalformedLocksAreTreatedAsStale(
    data_root: Path,
    contents: str,
) -> None:
    """Empty or malformed lock files are removed by guarded readers."""
    lock_path = config.getLockPath("book")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(contents, encoding="utf-8")

    pdf_lock.assertNoActiveLock("book")

    assert not lock_path.exists()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"pid": "123", "pdf_name": "book", "started_at": buildStartedAt()},
        {"pid": os.getpid(), "pdf_name": "book", "started_at": "bad"},
        {"pid": os.getpid(), "pdf_name": "", "started_at": buildStartedAt()},
    ],
)
def testIsLockActiveRejectsInvalidPayloads(
    data_root: Path,
    payload: dict | None,
) -> None:
    """Invalid payloads never count as an active lock."""
    assert not pdf_lock.isLockActive(payload)


def testMissingLockDoesNotBlock(data_root: Path) -> None:
    """No lock file means guarded commands can proceed."""
    pdf_lock.assertNoActiveLock("book")

    assert not config.getLockPath("book").exists()
