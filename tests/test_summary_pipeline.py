"""Tests for manifest generation, output validation, and summary merging."""

import json
from pathlib import Path

import click
import pytest

import config
import summary_pipeline


class FakeSkills:
    """Stand-in for ``Skills`` with deterministic section and chapter text."""

    def __init__(
        self,
        book_data: dict,
        section_texts: dict[str, str],
        chapter_texts: dict[str, str],
    ):
        """Store fake book data and text sources.

        Args:
            book_data: Enriched ToC dictionary.
            section_texts: ``"chapter.section"`` to text mapping.
            chapter_texts: Chapter ID to chapter-level text mapping.
        """
        self.book_data = book_data
        self.section_texts = section_texts
        self.chapter_texts = chapter_texts

    def getSectionRawContents(self, sections: list[tuple[int, int]]):
        """Return fake section content or raise IndexError.

        Args:
            sections: One section pair.

        Returns:
            One text item.
        """
        chapter_id, section_id = sections[0]
        key = f"{chapter_id}.{section_id}"

        if key not in self.section_texts:
            raise IndexError(f"Section {key} not found!")

        return [{"type": "text", "text": self.section_texts[key]}]

    def getChapterLevelContents(self, chapter_id: str) -> str:
        """Return fake chapter-level text.

        Args:
            chapter_id: Chapter number as a string.

        Returns:
            Chapter text, or an empty string.
        """
        return self.chapter_texts.get(chapter_id, "")


def makeBookData() -> dict:
    """Build a one-chapter enriched ToC for tests.

    Args:
        None.

    Returns:
        Enriched ToC dictionary.
    """
    return {
        "chapters": {
            "1": {
                "name": "Chapter One",
                "start": 11,
                "end": 17,
                "sections": {
                    "1": {"name": "Alpha", "start": 11, "end": 13},
                    "2": {"name": "Beta", "start": 13, "end": 17},
                },
            }
        }
    }


def prepareBook(data_root: Path) -> dict:
    """Write ToC inputs and generate a manifest for ``book``.

    Args:
        data_root: Temporary data root.

    Returns:
        The generated manifest dictionary.
    """
    book_data = makeBookData()
    parsed = {
        "chapters": {
            "1": {
                "name": "Chapter One",
                "start": 11,
                "sections": {
                    "1.1": {"name": "Alpha", "start": 11},
                    "1.2": {"name": "Beta", "start": 13},
                },
            }
        },
        "thinking": "fixture",
    }

    config.getParsedTocPath("book").parent.mkdir(parents=True)
    config.getParsedTocPath("book").write_text(
        json.dumps(parsed), encoding="utf-8"
    )
    config.getEnrichedTocPath("book").parent.mkdir(parents=True)
    config.getEnrichedTocPath("book").write_text(
        json.dumps(book_data), encoding="utf-8"
    )
    config.getMetadataPath("book").parent.mkdir(parents=True)
    config.getMetadataPath("book").write_text(
        json.dumps(
            {
                "pdf_path": str(data_root / "book.pdf"),
                "pdf_name": "book",
                "page_count": 20,
                "page_labels": [str(index) for index in range(1, 21)],
                "toc_pages": [4, 5],
                "toc_offset": 5,
                "pictures_dir": "pictures/book",
            }
        ),
        encoding="utf-8",
    )

    skills = FakeSkills(
        book_data,
        section_texts={"1.1": "Alpha raw text"},
        chapter_texts={"1": "Chapter-level overview"},
    )

    return summary_pipeline.buildSummaryManifest("book", skills)


def writeSummary(path: str, summary: str) -> None:
    """Write one delegated summary output.

    Args:
        path: Output path from the manifest.
        summary: Summary text.

    Returns:
        None.
    """
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"summary": summary}), encoding="utf-8"
    )


def testBuildManifestWritesTasksAndContent(data_root: Path) -> None:
    """Manifest tasks, prompts, dependencies, and content files are correct."""
    manifest = prepareBook(data_root)

    assert manifest["pdf_name"] == "book"
    assert len(manifest["section_tasks"]) == 2
    assert len(manifest["chapter_tasks"]) == 1

    first_section = manifest["section_tasks"][0]
    second_section = manifest["section_tasks"][1]
    chapter = manifest["chapter_tasks"][0]

    assert first_section["task_id"] == "section-1.1"
    assert first_section["system_prompt"] == summary_pipeline.SECTION_PROMPT
    assert first_section["output_schema"] == summary_pipeline.OUTPUT_SCHEMA
    assert Path(first_section["content_path"]).read_text(
        encoding="utf-8"
    ) == "Alpha raw text"
    assert Path(second_section["content_path"]).read_text(
        encoding="utf-8"
    ) == ""

    assert chapter["task_id"] == "chapter-1"
    assert chapter["system_prompt"] == summary_pipeline.CHAPTER_PROMPT
    assert chapter["depends_on"] == ["section-1.1", "section-1.2"]
    assert chapter["section_summary_paths"] == [
        first_section["output_path"],
        second_section["output_path"],
    ]
    assert Path(chapter["chapter_content_path"]).read_text(
        encoding="utf-8"
    ) == "Chapter-level overview"


def testBuildChapterContentUsesLegacyTemplate(
    data_root: Path,
) -> None:
    """The chapter input combines section summaries and chapter chunks."""
    manifest = prepareBook(data_root)
    enriched = {
        "chapters": {
            "1": {
                "sections": {
                    "1": {"name": "Alpha", "summary": "Alpha summary"},
                    "2": {"name": "Beta", "summary": "Beta summary"},
                }
            }
        }
    }

    content = summary_pipeline.buildChapterContent(
        enriched,
        summary_pipeline.SummaryTask.model_validate(
            manifest["chapter_tasks"][0]
        ),
    )

    assert content == (
        "Section Summaries:\n"
        "Section 1 (Alpha): Alpha summary\n"
        "---\n"
        "Section 2 (Beta): Beta summary\n\n"
        "Chapter Content:\n"
        "Chapter-level overview"
    )


def testApplySummariesMergesIntoEnrichedOnly(data_root: Path) -> None:
    """Summaries merge into normalized enriched sections; parsed stays raw."""
    manifest = prepareBook(data_root)
    original_parsed = config.getParsedTocPath("book").read_text(
        encoding="utf-8"
    )

    writeSummary(manifest["section_tasks"][0]["output_path"], "Alpha summary")
    writeSummary(manifest["chapter_tasks"][0]["output_path"], "Chapter summary")

    status = summary_pipeline.applySummaries("book")

    parsed = json.loads(
        config.getParsedTocPath("book").read_text(encoding="utf-8")
    )
    enriched = json.loads(
        config.getEnrichedTocPath("book").read_text(encoding="utf-8")
    )

    assert status == {
        "pdf_name": "book",
        "applied_sections": 2,
        "applied_chapters": 1,
    }
    assert config.getParsedTocPath("book").read_text(
        encoding="utf-8"
    ) == original_parsed
    assert "summary" not in parsed["chapters"]["1"]
    assert "summary" not in parsed["chapters"]["1"]["sections"]["1.1"]
    assert enriched["chapters"]["1"]["sections"]["1"]["summary"] == "Alpha summary"
    assert (
        enriched["chapters"]["1"]["sections"]["2"]["summary"]
        == "No content available for Section 1.2"
    )
    assert enriched["chapters"]["1"]["summary"] == "Chapter summary"


def testApplySummariesRejectsMissingOutputs(data_root: Path) -> None:
    """Missing non-empty section outputs are listed in one error."""
    prepareBook(data_root)

    with pytest.raises(click.ClickException) as error:
        summary_pipeline.applySummaries("book")

    assert "section-1.1" in str(error.value)
    assert "section-1.2" not in str(error.value)


def testApplySummariesRejectsEmptyOutputs(data_root: Path) -> None:
    """Empty summary payloads are treated as missing."""
    manifest = prepareBook(data_root)
    writeSummary(manifest["section_tasks"][0]["output_path"], "  ")

    with pytest.raises(click.ClickException) as error:
        summary_pipeline.applySummaries("book")

    assert "section-1.1" in str(error.value)


def testApplySummariesReusesExistingSummary(data_root: Path) -> None:
    """A cached enriched summary is reused when the output file is absent."""
    manifest = prepareBook(data_root)
    enriched = json.loads(
        config.getEnrichedTocPath("book").read_text(encoding="utf-8")
    )
    enriched["chapters"]["1"]["sections"]["1"]["summary"] = "Cached summary"
    config.getEnrichedTocPath("book").write_text(
        json.dumps(enriched), encoding="utf-8"
    )
    writeSummary(manifest["chapter_tasks"][0]["output_path"], "Chapter summary")

    summary_pipeline.applySummaries("book")

    updated = json.loads(
        config.getEnrichedTocPath("book").read_text(encoding="utf-8")
    )
    assert updated["chapters"]["1"]["sections"]["1"]["summary"] == "Cached summary"


def testApplySummariesRequiresEnrichedToc(data_root: Path) -> None:
    """Applying summaries fails when the enriched ToC is missing."""
    prepareBook(data_root)
    config.getEnrichedTocPath("book").unlink()

    with pytest.raises(FileNotFoundError) as error:
        summary_pipeline.applySummaries("book")

    assert "Enriched ToC" in str(error.value)


def testApplySummariesDoesNotReadParsedToc(data_root: Path) -> None:
    """The raw parsed ToC is not required once the enriched ToC exists."""
    manifest = prepareBook(data_root)
    config.getParsedTocPath("book").unlink()
    writeSummary(manifest["section_tasks"][0]["output_path"], "Alpha summary")
    writeSummary(manifest["chapter_tasks"][0]["output_path"], "Chapter summary")

    status = summary_pipeline.applySummaries("book")

    assert status["applied_sections"] == 2
    assert not config.getParsedTocPath("book").exists()
