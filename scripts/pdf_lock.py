"""Per-PDF mutex locks that guard summary generation and enriched ToC readers."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

import config
from logging_config import getLogger

logger = getLogger(__name__)

PROCESS_CREATE_TIME_TOLERANCE_SECONDS = 1.0
LOCK_ACQUIRE_ATTEMPTS = 2


def buildLockPayload(pdf_name: str) -> dict[str, Any]:
    """Build the JSON payload written into a fresh lock file.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        A dictionary with the writer PID, PDF name, and UTC start time.
    """
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    return {
        "pid": os.getpid(),
        "pdf_name": pdf_name,
        "started_at": started_at,
    }


def writeExclusiveLock(lock_path: Path, payload: dict[str, Any]) -> None:
    """Create a lock file atomically with complete contents.

    Args:
        lock_path: Destination lock path.
        payload: JSON-ready lock payload.

    Returns:
        None.

    Raises:
        FileExistsError: If another process already created the lock.
    """
    temporary_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=lock_path.parent,
        prefix=f".{lock_path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary_path = Path(temporary_file.name)

    try:
        with temporary_file:
            json.dump(payload, temporary_file)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.link(temporary_path, lock_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def readLockData(lock_path: Path) -> dict[str, Any] | None:
    """Read a lock file, returning ``None`` when it is missing or malformed.

    Args:
        lock_path: Lock path to read.

    Returns:
        Parsed lock payload, or ``None``.
    """
    try:
        raw_contents = lock_path.read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        lock_data = json.loads(raw_contents)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    if not isinstance(lock_data, dict):
        return None

    return lock_data


def parseStartedAt(started_at: Any) -> datetime | None:
    """Parse a lock's ISO-8601 start timestamp.

    Args:
        started_at: Raw value from the lock payload.

    Returns:
        An aware ``datetime``, or ``None`` when the value is invalid.
    """
    if not isinstance(started_at, str):
        return None

    try:
        parsed_timestamp = datetime.fromisoformat(
            started_at.replace("Z", "+00:00")
        )
    except ValueError:
        return None

    if parsed_timestamp.tzinfo is None:
        return None

    return parsed_timestamp


def isLockActive(lock_data: dict[str, Any] | None) -> bool:
    """Check whether a lock belongs to a live summary generator process.

    Args:
        lock_data: Parsed lock payload, or ``None``.

    Returns:
        True only when the PID is alive, its creation time is not later than
        the recorded start, and its command line names this generator and PDF.
    """
    if not isinstance(lock_data, dict):
        return False

    pid = lock_data.get("pid")
    pdf_name = lock_data.get("pdf_name")
    started_at = parseStartedAt(lock_data.get("started_at"))

    if type(pid) is not int or pid <= 0:
        return False

    if not isinstance(pdf_name, str) or not pdf_name:
        return False

    if started_at is None:
        return False

    try:
        process = psutil.Process(pid)

        if process.status() == psutil.STATUS_ZOMBIE:
            return False

        create_time = process.create_time()
        command_line = " ".join(process.cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False

    if create_time > started_at.timestamp() + PROCESS_CREATE_TIME_TOLERANCE_SECONDS:
        return False

    return "summary_generator.py" in command_line and pdf_name in command_line


def buildActiveLockMessage(
    pdf_name: str,
    lock_data: dict[str, Any] | None,
) -> str:
    """Build the error text returned when a live lock blocks an operation.

    Args:
        pdf_name: PDF file name without its extension.
        lock_data: Parsed lock payload.

    Returns:
        A message naming the owning PID, start time, and log file.
    """
    payload = lock_data or {}
    pid = payload.get("pid", "unknown")
    started_at = payload.get("started_at", "unknown")

    return (
        f"Summary generation is already running for '{pdf_name}' "
        f"(pid {pid}, started {started_at}). Wait for it to finish, then "
        f"inspect {config.getLogPath(pdf_name)}."
    )


def raiseForActiveLock(
    pdf_name: str,
    lock_data: dict[str, Any] | None,
) -> None:
    """Raise a ``RuntimeError`` describing an active summary lock.

    Args:
        pdf_name: PDF file name without its extension.
        lock_data: Parsed lock payload.

    Returns:
        None.

    Raises:
        RuntimeError: Always.
    """
    raise RuntimeError(buildActiveLockMessage(pdf_name, lock_data))


def acquireLock(pdf_name: str) -> Path:
    """Create the per-PDF summary lock or fail when one is active.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        The acquired lock path.

    Raises:
        RuntimeError: If a live summary generator owns the lock.
    """
    lock_path = config.getLockPath(pdf_name)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = buildLockPayload(pdf_name)

    for _attempt in range(LOCK_ACQUIRE_ATTEMPTS):
        try:
            writeExclusiveLock(lock_path, payload)
        except FileExistsError:
            lock_data = readLockData(lock_path)

            if isLockActive(lock_data):
                raiseForActiveLock(pdf_name, lock_data)

            logger.warning("Removing stale summary lock %s", lock_path)
            lock_path.unlink(missing_ok=True)
            continue

        logger.info(
            "Acquired summary lock %s (pid %s)",
            lock_path,
            payload["pid"],
        )
        return lock_path

    raise RuntimeError(
        f"Could not acquire the summary lock for '{pdf_name}': {lock_path}"
    )


def assertNoActiveLock(pdf_name: str) -> None:
    """Raise when a live summary generator owns this PDF's lock.

    Stale or malformed locks are removed first.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        None.

    Raises:
        RuntimeError: If a live summary generator owns the lock.
    """
    lock_path = config.getLockPath(pdf_name)

    if not lock_path.exists():
        return

    lock_data = readLockData(lock_path)

    if isLockActive(lock_data):
        raiseForActiveLock(pdf_name, lock_data)

    logger.warning("Removing stale summary lock %s", lock_path)
    releaseLock(pdf_name)


def releaseLock(pdf_name: str) -> None:
    """Remove this PDF's summary lock if it exists.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        None.
    """
    config.getLockPath(pdf_name).unlink(missing_ok=True)
