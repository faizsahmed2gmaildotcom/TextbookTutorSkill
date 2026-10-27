"""Chunk parsing, enrichment, embeddings, and LanceDB persistence."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import config
from logging_config import getLogger

logger = getLogger(__name__)

_ENCODER: Any = None


@dataclass
class Item:
    """A single Docling item inside a chunk, with physical ranges."""

    label: str
    char_range: tuple[int, int] = (10**9, -1)
    page_range: tuple[int, int] = (10**9, -1)


def getEncoder() -> Any:
    """Return the process-wide embedding model, loading it on first use.

    Returns:
        A ``SentenceTransformer`` configured for the textbook encoder.
    """
    global _ENCODER

    if _ENCODER is None:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model: %s", config.EMBEDDING_MODEL_NAME)
        _ENCODER = SentenceTransformer(
            config.EMBEDDING_MODEL_NAME, trust_remote_code=True
        )
        _ENCODER.max_seq_length = config.EMBEDDING_MAX_SEQ_LENGTH

    return _ENCODER


def loadChunks(jsonl_path: Path) -> list[dict]:
    """Read every non-empty JSONL line into a list of chunk dictionaries.

    Args:
        jsonl_path: Path to ``chunks/<pdf_name>.jsonl``.

    Returns:
        Parsed chunk dictionaries.

    Raises:
        FileNotFoundError: If ``jsonl_path`` does not exist.
    """
    if not jsonl_path.exists():
        raise FileNotFoundError(f"Chunk JSONL not found: {jsonl_path}")

    chunks: list[dict] = []

    with jsonl_path.open("r", encoding="utf-8") as chunks_file:
        for line in chunks_file:
            if line.strip():
                chunks.append(json.loads(line))

    return chunks


def parseChunk(raw_chunk: dict[str, Any]) -> dict[str, Any]:
    """Convert one Docling chunk into its LanceDB shape.

    Args:
        raw_chunk: One JSONL chunk produced by ``pdf_chunks.exportChunks``.

    Returns:
        A dictionary with text, item ranges, header, and picture ID.
    """
    headings = raw_chunk["meta"].get("headings") or [""]
    parsed: dict[str, Any] = {
        "text": raw_chunk["text"],
        "items": [],
        "header": headings[-1] or "",
        "pic_id": raw_chunk.get("pic_id", ""),
    }

    char_offset = 0

    for raw_item in raw_chunk["meta"]["doc_items"]:
        parsed_item = Item(label=raw_item["label"])
        provenances = raw_item.get("prov") or []

        for provenance in provenances:
            parsed_item.char_range = (
                min(
                    parsed_item.char_range[0],
                    char_offset + provenance["charspan"][0],
                ),
                max(
                    parsed_item.char_range[1],
                    char_offset + provenance["charspan"][1],
                ),
            )
            parsed_item.page_range = (
                min(parsed_item.page_range[0], provenance["page_no"]),
                max(parsed_item.page_range[1], provenance["page_no"]),
            )

        if provenances:
            char_offset += provenances[-1]["charspan"][1] + 1

        parsed["items"].append(asdict(parsed_item))

    return parsed


def getChunkPageRange(parsed_chunk: dict) -> tuple[int, int]:
    """Return the physical page span covered by a parsed chunk.

    Args:
        parsed_chunk: Output of ``parseChunk``.

    Returns:
        Inclusive ``(page_start, page_end)``, defaulting to ``(1, 1)``.
    """
    min_page = 10**9
    max_page = -1

    for item in parsed_chunk.get("items", []):
        page_start, page_end = item.get("page_range", (10**9, -1))
        min_page = min(min_page, page_start)
        max_page = max(max_page, page_end)

    if min_page <= max_page:
        return min_page, max_page

    return 1, 1


def enrichChunkWithToc(parsed_chunk: dict, toc_structure: dict) -> dict[str, str | int]:
    """Attach chapter, section, and page metadata to a parsed chunk.

    Args:
        parsed_chunk: Output of ``parseChunk``.
        toc_structure: Enriched ToC dictionary with physical page ranges.

    Returns:
        Enrichment fields; missing IDs are empty strings so LanceDB filters work.
    """
    page_start, page_end = getChunkPageRange(parsed_chunk)
    enrichment: dict[str, str | int] = {
        "chapter_id": "",
        "chapter_name": "",
        "section_id": "",
        "section_name": "",
        "page_start": page_start,
        "page_end": page_end,
    }
    chapters = toc_structure.get("chapters", {})

    for chapter_id, chapter in chapters.items():
        chapter_start = chapter.get("start", 0)
        chapter_end = chapter.get("end", 0)
        overlaps_chapter = (
            chapter_start <= page_start <= chapter_end
            or chapter_start <= page_end <= chapter_end
        )

        if not overlaps_chapter:
            continue

        enrichment["chapter_id"] = chapter_id
        enrichment["chapter_name"] = chapter.get("name", "")

        for section_id, section in chapter.get("sections", {}).items():
            section_start = section.get("start", 0)
            section_end = section.get("end", 0)
            overlaps_section = (
                section_start <= page_start <= section_end
                or section_start <= page_end <= section_end
            )

            if overlaps_section:
                enrichment["section_id"] = section_id
                enrichment["section_name"] = section.get("name", "")
                break

        break

    return enrichment


def tableExists(database: Any, table_name: str) -> bool:
    """Check whether a LanceDB table exists.

    Args:
        database: A connected LanceDB database handle.
        table_name: Table name to look up.

    Returns:
        True when the table exists.
    """
    return table_name in database.list_tables().tables


def buildRecords(
    chunks: list[dict],
    toc_structure: dict,
    encoder: Any,
) -> list[dict]:
    """Parse, enrich, and embed every chunk into a LanceDB record.

    Args:
        chunks: Raw chunk dictionaries.
        toc_structure: Enriched ToC dictionary.
        encoder: Embedding model exposing ``encode``.

    Returns:
        Records ready for ``create_table``.
    """
    parsed_chunks = [parseChunk(chunk) for chunk in chunks]
    texts = [chunk.get("text", "") for chunk in chunks]

    logger.info("Generating embeddings for %s chunks", len(texts))
    embeddings = encoder.encode(texts, show_progress_bar=True)
    records: list[dict] = []

    for index, parsed_chunk in enumerate(parsed_chunks):
        record = {
            "vector": embeddings[index].tolist(),
            **parsed_chunk,
            **enrichChunkWithToc(parsed_chunk, toc_structure),
        }
        records.append(record)

    return records


def populateLanceDb(
    jsonl_path: Path,
    db_uri: Path,
    table_name: str,
    toc_structure: dict,
    force: bool = False,
) -> Any:
    """Create or reuse a LanceDB table populated with textbook chunks.

    Args:
        jsonl_path: Path to ``chunks/<pdf_name>.jsonl``.
        db_uri: LanceDB data directory.
        table_name: Table name, normally the PDF name.
        toc_structure: Enriched ToC dictionary.
        force: Drop and rebuild an existing table.

    Returns:
        The open LanceDB table.

    Raises:
        FileNotFoundError: If ``jsonl_path`` does not exist.
        ValueError: If the JSONL contains no chunks.
    """
    import lancedb

    db_uri.mkdir(parents=True, exist_ok=True)
    database = lancedb.connect(str(db_uri))

    if tableExists(database, table_name):
        if not force:
            logger.info("Reusing existing LanceDB table %s", table_name)
            return database.open_table(table_name)

        logger.info("Dropping existing LanceDB table %s", table_name)
        database.drop_table(table_name)

    chunks = loadChunks(jsonl_path)

    if not chunks:
        raise ValueError(f"No chunks found in {jsonl_path}")

    records = buildRecords(chunks, toc_structure, getEncoder())

    logger.info(
        "Writing %s records to LanceDB table %s", len(records), table_name
    )
    table = database.create_table(table_name, data=records)

    logger.info("Creating full-text search index on the 'text' column")
    table.create_fts_index(field_names="text")

    return table


def openLanceDbTable(db_uri: Path, table_name: str) -> Any:
    """Open an existing LanceDB table.

    Args:
        db_uri: LanceDB data directory.
        table_name: Table name, normally the PDF name.

    Returns:
        The open LanceDB table.

    Raises:
        FileNotFoundError: If the database directory or table is missing.
    """
    import lancedb

    if not db_uri.exists():
        raise FileNotFoundError(f"LanceDB directory not found: {db_uri}")

    database = lancedb.connect(str(db_uri))

    if not tableExists(database, table_name):
        raise FileNotFoundError(f"LanceDB table not found: {table_name}")

    return database.open_table(table_name)
