"""Tests for chunk parsing, ToC enrichment, and LanceDB persistence."""

import json
from pathlib import Path

import pytest

import config
import lancedb_store


class FakeVector:
    """Minimal vector wrapper exposing ``tolist``."""

    def __init__(self, values: list[float]):
        """Store the vector values.

        Args:
            values: Vector components.
        """
        self.values = values

    def tolist(self) -> list[float]:
        """Return the vector as a plain list.

        Returns:
            Vector components.
        """
        return self.values


class FakeEncoder:
    """Deterministic embedding stand-in for tests."""

    def encode(self, texts: list[str], show_progress_bar: bool = False):
        """Encode texts into fixed-length vectors.

        Args:
            texts: Input strings.
            show_progress_bar: Ignored.

        Returns:
            One fake vector per input.
        """
        return [
            FakeVector([float(index), 0.1, 0.2, 0.3])
            for index, _text in enumerate(texts)
        ]


def makeRawChunk(text: str, page: int) -> dict:
    """Build one raw Docling-shaped text chunk.

    Args:
        text: Chunk text.
        page: Physical page number.

    Returns:
        A JSON-ready chunk dictionary.
    """
    return {
        "text": text,
        "meta": {
            "headings": ["Chapter 1"],
            "doc_items": [
                {
                    "label": "text",
                    "prov": [{"charspan": [0, max(len(text) - 1, 0)], "page_no": page}],
                }
            ],
        },
    }


def testParseChunkComputesRangesAndOffsets() -> None:
    """Character ranges accumulate across items while pages merge."""
    raw_chunk = {
        "text": "abcdefghijk",
        "meta": {
            "headings": ["Chapter 1"],
            "doc_items": [
                {
                    "label": "text",
                    "prov": [
                        {"charspan": [0, 5], "page_no": 2},
                        {"charspan": [6, 10], "page_no": 3},
                    ],
                },
                {
                    "label": "text",
                    "prov": [{"charspan": [0, 4], "page_no": 3}],
                },
            ],
        },
    }

    parsed = lancedb_store.parseChunk(raw_chunk)

    assert parsed["header"] == "Chapter 1"
    assert parsed["pic_id"] == ""
    assert parsed["items"][0]["char_range"] == (0, 10)
    assert parsed["items"][0]["page_range"] == (2, 3)
    assert parsed["items"][1]["char_range"] == (11, 15)


def testParseChunkToleratesMissingProvenance() -> None:
    """Items without provenance do not crash parsing."""
    raw_chunk = {
        "text": "caption",
        "meta": {
            "headings": [],
            "doc_items": [{"label": "picture", "prov": []}],
        },
        "pic_id": "7",
    }

    parsed = lancedb_store.parseChunk(raw_chunk)

    assert parsed["header"] == ""
    assert parsed["pic_id"] == "7"
    assert parsed["items"][0]["page_range"] == (10**9, -1)


def testGetChunkPageRangeDefaultsWhenEmpty() -> None:
    """Chunks without usable pages fall back to page 1."""
    assert lancedb_store.getChunkPageRange({"items": []}) == (1, 1)


def testEnrichChunkWithTocBoundaries() -> None:
    """Chapter and section IDs follow the legacy overlap rules."""
    toc = {
        "chapters": {
            "1": {
                "name": "Chapter One",
                "start": 11,
                "end": 17,
                "sections": {
                    "1": {"name": "A", "start": 11, "end": 13},
                    "2": {"name": "B", "start": 13, "end": 17},
                },
            }
        }
    }

    inside = lancedb_store.enrichChunkWithToc(
        {"items": [{"page_range": (13, 13)}]}, toc
    )
    outside = lancedb_store.enrichChunkWithToc(
        {"items": [{"page_range": (10, 10)}]}, toc
    )
    overlapping = lancedb_store.enrichChunkWithToc(
        {"items": [{"page_range": (16, 18)}]}, toc
    )

    assert inside["chapter_id"] == "1"
    assert inside["section_id"] == "1"
    assert inside["section_name"] == "A"
    assert outside == {
        "chapter_id": "",
        "chapter_name": "",
        "section_id": "",
        "section_name": "",
        "page_start": 10,
        "page_end": 10,
    }
    assert overlapping["chapter_id"] == "1"
    assert overlapping["section_id"] == "2"


def testLanceTableNameIsSanitized() -> None:
    """LanceDB-invalid characters are replaced in table names."""
    assert config.getLanceTableName("my book (1)") == "my_book__1_"
    assert config.getLanceTableName("book.v2") == "book.v2"


def testPopulateLanceDbCreatesTableAndFts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Records are embedded, stored, and full-text searchable."""
    jsonl_path = tmp_path / "chunks.jsonl"
    chunks = [
        makeRawChunk("alpha beta gamma", 11),
        makeRawChunk("delta epsilon", 12),
    ]
    jsonl_path.write_text(
        "\n".join(json.dumps(chunk) for chunk in chunks) + "\n",
        encoding="utf-8",
    )
    db_uri = tmp_path / "lancedb_data"
    toc = {
        "chapters": {
            "1": {
                "name": "Chapter One",
                "start": 11,
                "end": 17,
                "sections": {
                    "1": {"name": "A", "start": 11, "end": 13},
                },
            }
        }
    }
    monkeypatch.setattr(lancedb_store, "getEncoder", FakeEncoder)

    table = lancedb_store.populateLanceDb(
        jsonl_path,
        db_uri,
        "book",
        toc,
        force=False,
    )
    records = lancedb_store.buildRecords(chunks, toc, FakeEncoder())

    assert not table.reused
    assert table.table.count_rows() == 2
    assert (db_uri / "book.lance").exists()
    assert records[0]["chapter_id"] == "1"
    assert records[0]["section_id"] == "1"
    assert records[1]["section_id"] == "1"

    hits = table.table.search("alpha", query_type="fts").limit(1).to_pandas()
    assert len(hits) == 1


def testPopulateLanceDbReusesAndRebuilds(tmp_path: Path, monkeypatch) -> None:
    """Existing tables are reused unless --force is supplied."""
    jsonl_path = tmp_path / "chunks.jsonl"
    jsonl_path.write_text(
        json.dumps(makeRawChunk("alpha", 11)) + "\n", encoding="utf-8"
    )
    db_uri = tmp_path / "lancedb_data"
    toc = {"chapters": {}}
    monkeypatch.setattr(lancedb_store, "getEncoder", FakeEncoder)

    first_table = lancedb_store.populateLanceDb(
        jsonl_path, db_uri, "book", toc, force=False
    )
    reused_table = lancedb_store.populateLanceDb(
        jsonl_path, db_uri, "book", toc, force=False
    )
    rebuilt_table = lancedb_store.populateLanceDb(
        jsonl_path, db_uri, "book", toc, force=True
    )

    assert first_table.table.count_rows() == 1
    assert reused_table.table.count_rows() == 1
    assert rebuilt_table.table.count_rows() == 1
    assert not first_table.reused
    assert reused_table.reused
    assert not rebuilt_table.reused
