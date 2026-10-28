"""Tests that ToC-writing and indexing CLIs honor the summary lock."""

from pathlib import Path

import pytest
from click.testing import CliRunner

import config
import db_populate
import pdf_lock
import postprocess_toc


def writeActiveLock() -> Path:
    """Write a lock file that the patched detector treats as active.

    Args:
        None.

    Returns:
        The lock path.
    """
    lock_path = config.getLockPath("book")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("{}", encoding="utf-8")

    return lock_path


def testPostprocessBlocksWhileSummaryLockIsActive(
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enrichment must not rewrite the ToC during generation."""
    lock_path = writeActiveLock()
    monkeypatch.setattr(pdf_lock, "isLockActive", lambda _lock_data: True)

    result = CliRunner().invoke(
        postprocess_toc.postprocessCommand, ["book"]
    )

    assert isinstance(result.exception, RuntimeError)
    assert "already running" in str(result.exception)
    assert lock_path.exists()


def testPopulateBlocksWhileSummaryLockIsActive(
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Index rebuilding must not race with summary generation."""
    writeActiveLock()
    monkeypatch.setattr(pdf_lock, "isLockActive", lambda _lock_data: True)

    result = CliRunner().invoke(
        db_populate.populateCommand, ["book"]
    )

    assert isinstance(result.exception, RuntimeError)
    assert "already running" in str(result.exception)
