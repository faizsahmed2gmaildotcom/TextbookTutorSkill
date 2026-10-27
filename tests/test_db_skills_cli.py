"""Tests for the db_skills CLI contract and retrieval output shapes."""

import base64
import json
from pathlib import Path

import lancedb
import pytest
from click.testing import CliRunner

import config
import db_skills


def writeEnrichedToc() -> None:
    """Write a one-chapter enriched ToC for ``book``.

    Args:
        None.

    Returns:
        None.
    """
    enriched = {
        "chapters": {
            "1": {
                "name": "Chapter One",
                "start": 11,
                "end": 17,
                "summary": "Chapter summary",
                "sections": {
                    "1": {
                        "name": "Alpha",
                        "start": 11,
                        "end": 13,
                        "summary": "Alpha summary",
                    }
                },
            }
        }
    }
    enriched_path = config.getEnrichedTocPath("book")
    enriched_path.parent.mkdir(parents=True, exist_ok=True)
    enriched_path.write_text(json.dumps(enriched), encoding="utf-8")


def makeRow(
    text: str,
    page: int,
    pic_id: str = "",
    section_id: str = "1",
) -> dict:
    """Build one LanceDB row for the fixture book.

    Args:
        text: Chunk text.
        page: Physical page number.
        pic_id: Picture ID, empty for text.
        section_id: Section ID.

    Returns:
        A JSON-ready record.
    """
    return {
        "vector": [0.1, 0.2, 0.3, 0.4],
        "text": text,
        "items": [
            {
                "label": "text",
                "char_range": [0, max(len(text) - 1, 0)],
                "page_range": [page, page],
            }
        ],
        "header": f"Header {section_id}",
        "pic_id": pic_id,
        "chapter_id": "1",
        "chapter_name": "Chapter One",
        "section_id": section_id,
        "section_name": "Alpha",
        "page_start": page,
        "page_end": page,
    }


def createTable() -> None:
    """Create the fixture LanceDB table.

    Args:
        None.

    Returns:
        None.
    """
    config.LANCEDB_DATA_DIR.mkdir(parents=True, exist_ok=True)
    database = lancedb.connect(str(config.LANCEDB_DATA_DIR))
    database.create_table(
        "book",
        data=[
            makeRow("Alpha first page", 11),
            makeRow("Alpha second page", 12),
            makeRow("A diagram caption", 11, pic_id="4"),
        ],
    )


def testCliRequiresExactlyOneOption(data_root: Path) -> None:
    """Zero selected options is a UsageError."""
    result = CliRunner().invoke(db_skills.skillsCommand, ["book"])

    assert result.exit_code == 2
    assert "exactly one" in result.output


def testCliRejectsMultipleOptions(data_root: Path) -> None:
    """More than one selected option is a UsageError."""
    result = CliRunner().invoke(
        db_skills.skillsCommand, ["book", "--chapsum", "--secsum", "1"]
    )

    assert result.exit_code == 2
    assert "exactly one" in result.output


def testCliMissingEnrichedToc(data_root: Path) -> None:
    """Missing ToC data surfaces FileNotFoundError."""
    result = CliRunner().invoke(
        db_skills.skillsCommand, ["book", "--chapsum"]
    )

    assert isinstance(result.exception, FileNotFoundError)


def testCliMissingLanceTable(data_root: Path) -> None:
    """Missing table directories surface FileNotFoundError."""
    writeEnrichedToc()

    result = CliRunner().invoke(
        db_skills.skillsCommand, ["book", "--chapsum"]
    )

    assert isinstance(result.exception, FileNotFoundError)
    assert "LanceDB" in str(result.exception)


@pytest.mark.parametrize(
    ("arguments", "option_name"),
    [
        (["--secsum", "1,x"], "--secsum"),
        (["--secsum", "0"], "--secsum"),
        (["--secraw", "1"], "--secraw"),
        (["--secraw", "1.2.3"], "--secraw"),
        (["--picid", "-1"], "--picid"),
    ],
)
def testCliRejectsBadFormats(
    data_root: Path,
    arguments: list[str],
    option_name: str,
) -> None:
    """Invalid option values raise click.BadParameter with the option name."""
    writeEnrichedToc()
    config.getLanceTableDir("book").mkdir(parents=True)

    result = CliRunner().invoke(
        db_skills.skillsCommand, ["book", *arguments]
    )

    assert result.exit_code == 2
    assert option_name in result.output


def testCliChapterSummaries(data_root: Path) -> None:
    """--chapsum prints the legacy summary block."""
    writeEnrichedToc()
    createTable()

    result = CliRunner().invoke(
        db_skills.skillsCommand, ["book", "--chapsum"]
    )

    assert result.exit_code == 0
    assert 'CHAPTER 1' in result.output
    assert "PAGES 11-17" in result.output
    assert "Chapter summary" in result.output


def testCliSectionSummaries(data_root: Path) -> None:
    """--secsum prints section summaries for the requested chapter."""
    writeEnrichedToc()
    createTable()

    result = CliRunner().invoke(
        db_skills.skillsCommand, ["book", "--secsum", "1,99"]
    )

    assert result.exit_code == 0
    assert 'SECTION 1' in result.output
    assert "Alpha summary" in result.output


def testCliRawSectionContents(data_root: Path) -> None:
    """--secraw emits JSON text with headers, pages, and picture captions."""
    writeEnrichedToc()
    createTable()

    result = CliRunner().invoke(
        db_skills.skillsCommand, ["book", "--secraw", "1.1"]
    )

    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert payload[0]["type"] == "text"
    assert "PAGES 11-12" in payload[0]["text"]
    assert "Header: Header 1" in payload[0]["text"]
    assert "Picture ID: 4" in payload[0]["text"]


def testCliMissingSection(data_root: Path) -> None:
    """Unknown sections produce a ClickException instead of a traceback."""
    writeEnrichedToc()
    createTable()

    result = CliRunner().invoke(
        db_skills.skillsCommand, ["book", "--secraw", "9.9"]
    )

    assert result.exit_code == 1
    assert "Section 9.9 not found" in result.output


def testCliPicturePayload(data_root: Path) -> None:
    """--picid preserves the legacy base64 image_url shape."""
    writeEnrichedToc()
    createTable()
    picture_dir = config.getPicturesDir("book")
    picture_dir.mkdir(parents=True, exist_ok=True)
    picture_dir.joinpath("pic_4.png").write_bytes(b"fake-png")

    result = CliRunner().invoke(
        db_skills.skillsCommand, ["book", "--picid", "4"]
    )

    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert payload == [
        {
            "type": "image_url",
            "image_url": base64.b64encode(b"fake-png").decode("utf-8"),
        }
    ]
