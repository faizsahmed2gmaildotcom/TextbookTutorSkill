"""Shared pytest fixtures for the textbook-tutor scripts."""

import json
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import config  # noqa: E402


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every generated-data path at an isolated temporary directory.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch helper.

    Returns:
        The temporary data root.
    """
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(config, "CHUNKS_DIR", tmp_path / "chunks")
    monkeypatch.setattr(config, "TOC_RAW_DIR", tmp_path / "toc_raw")
    monkeypatch.setattr(config, "TOC_PARSED_DIR", tmp_path / "toc_parsed")
    monkeypatch.setattr(config, "TOC_ENRICHED_DIR", tmp_path / "toc_enriched")
    monkeypatch.setattr(config, "PICTURES_DIR", tmp_path / "pictures")
    monkeypatch.setattr(config, "LANCEDB_DATA_DIR", tmp_path / "lancedb_data")
    monkeypatch.setattr(config, "LOGS_DIR", tmp_path / "logs")

    return tmp_path


@pytest.fixture
def guide_parsed_toc() -> dict:
    """Return the parsed ToC fixture reconstructed from the legacy POC.

    Args:
        None.

    Returns:
        Parsed ToC dictionary.
    """
    return json.loads(
        (FIXTURES_DIR / "guide_toc_parsed.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def guide_expected_enriched_toc() -> dict:
    """Return the legacy enriched page ranges for the fixture book.

    Args:
        None.

    Returns:
        Expected enriched ToC without summaries.
    """
    return json.loads(
        (FIXTURES_DIR / "guide_toc_enriched_expected.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.fixture
def guide_metadata() -> dict:
    """Return metadata matching the fixture book.

    Args:
        None.

    Returns:
        Metadata dictionary.
    """
    return json.loads(
        (FIXTURES_DIR / "guide_toc_meta.json").read_text(encoding="utf-8")
    )
