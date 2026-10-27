"""Tests for page-label mapping, section normalization, and page ranges."""

from pathlib import Path

import pytest

import toc_pipeline
from pdf_chunks import separateTocFromIndex
from schemas import TocSection, TocStructure


def testSeparateTocFromIndexKeepsFirstContiguousRun() -> None:
    """Index pages after the ToC are dropped."""
    assert separateTocFromIndex([3, 4, 5, 20, 21]) == [3, 4, 5]


def testSeparateTocFromIndexEmpty() -> None:
    """An empty page list stays empty."""
    assert separateTocFromIndex([]) == []


def testBuildPageToIdxUsesRomanLabels() -> None:
    """Labels map directly when the PDF supplies real page labels."""
    mapping = toc_pipeline.buildPageToIdx(["i", "ii", "1", "2"], 10)

    assert mapping == {"i": 1, "ii": 2, "1": 3, "2": 4}


def testBuildPageToIdxAppliesLegacyFallback() -> None:
    """A 1:1 label list shifts every page by the ToC offset."""
    mapping = toc_pipeline.buildPageToIdx(["1", "2", "3"], 5)

    assert mapping == {"1": 6, "2": 7, "3": 8}


def testMapPageNumberMissingLabelFallsBackToInteger() -> None:
    """A missing label uses the raw page number instead of crashing."""
    assert toc_pipeline.mapPageNumber(27, {"1": 11}) == 27


def testNormalizeSectionIdsDropsAndRenames() -> None:
    """Deep IDs are dropped and dotted IDs collapse to their last part."""
    sections = {
        "1.1": TocSection(name="a", start=1),
        "1.2.3": TocSection(name="b", start=2),
        "2": TocSection(name="c", start=3),
    }

    normalized = toc_pipeline.normalizeSectionIds(sections)

    assert list(normalized.keys()) == ["1", "2"]
    assert normalized["1"].name == "a"


def testNormalizeSectionIdsKeepsFirstOnCollision() -> None:
    """Colliding normalized IDs keep the first section."""
    toc = TocStructure.model_validate(
        {
            "chapters": {
                "1": {
                    "name": "c",
                    "start": 1,
                    "sections": {
                        "1": {"name": "first", "start": 1},
                        "1.1": {"name": "second", "start": 2},
                    },
                }
            },
            "thinking": "t",
        }
    )

    normalized = toc_pipeline.normalizeSectionIds(toc.chapters["1"].sections)

    assert normalized["1"].name == "first"


def testEnrichTocStructureComputesLegacyRanges() -> None:
    """Chapter and section ends follow the legacy overlap rules."""
    labels = [f"f{index}" for index in range(10)] + [
        str(index) for index in range(1, 11)
    ]
    toc = TocStructure.model_validate(
        {
            "chapters": {
                "1": {
                    "name": "First",
                    "start": 1,
                    "sections": {
                        "1": {"name": "A", "start": 1},
                        "2": {"name": "B", "start": 3},
                    },
                },
                "2": {
                    "name": "Second",
                    "start": 8,
                    "sections": {"1": {"name": "C", "start": 8}},
                },
            },
            "thinking": "t",
        }
    )

    enriched = toc_pipeline.enrichTocStructure(toc, labels, 20, 10)

    first = enriched["chapters"]["1"]
    second = enriched["chapters"]["2"]
    assert first["start"] == 11
    assert first["end"] == 17
    assert first["sections"]["1"] == {"name": "A", "start": 11, "end": 13}
    assert first["sections"]["2"] == {"name": "B", "start": 13, "end": 17}
    assert second["start"] == 18
    assert second["end"] == 19
    assert second["sections"]["1"]["end"] == 19


def testEnrichTocStructureParityWithLegacyPoc(
    guide_parsed_toc: dict,
    guide_metadata: dict,
    guide_expected_enriched_toc: dict,
) -> None:
    """Every fixture chapter and section matches the legacy POC exactly."""
    toc = TocStructure.model_validate(guide_parsed_toc)

    enriched = toc_pipeline.enrichTocStructure(
        toc,
        guide_metadata["page_labels"],
        guide_metadata["page_count"],
        guide_metadata["toc_offset"],
    )

    expected_chapters = guide_expected_enriched_toc["chapters"]
    assert list(enriched["chapters"].keys()) == list(expected_chapters.keys())

    for chapter_id, expected_chapter in expected_chapters.items():
        actual_chapter = enriched["chapters"][chapter_id]

        assert actual_chapter["name"] == expected_chapter["name"]
        assert actual_chapter["start"] == expected_chapter["start"]
        assert actual_chapter["end"] == expected_chapter["end"]

        for section_id, expected_section in expected_chapter["sections"].items():
            actual_section = actual_chapter["sections"][section_id]

            assert actual_section["name"] == expected_section["name"]
            assert actual_section["start"] == expected_section["start"]
            assert actual_section["end"] == expected_section["end"]


def testWriteEnrichedTocOmitsThinking(
    data_root: Path,
    guide_parsed_toc: dict,
    guide_metadata: dict,
) -> None:
    """The enriched file has no thinking field and includes summary defaults."""
    toc = TocStructure.model_validate(guide_parsed_toc)
    enriched = toc_pipeline.enrichTocStructure(
        toc,
        guide_metadata["page_labels"],
        guide_metadata["page_count"],
        guide_metadata["toc_offset"],
    )
    output_path = data_root / "toc_enriched" / "guide-t-cp.json"

    toc_pipeline.writeEnrichedToc("guide-t-cp", enriched, output_path)

    written = output_path.read_text(encoding="utf-8")
    assert '"thinking"' not in written
    assert '"summary": ""' in written
