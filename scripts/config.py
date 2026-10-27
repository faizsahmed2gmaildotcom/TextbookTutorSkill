"""Runtime paths and constants shared by every textbook-tutor script."""

import os
import re
from pathlib import Path


TABLE_NAME_INVALID_PATTERN = re.compile(r"[^A-Za-z0-9_.-]")

DATA_ROOT = Path(
    os.environ.get("TEXTBOOK_TUTOR_DATA_ROOT", str(Path.cwd()))
).expanduser().resolve()

CHUNKS_DIR = DATA_ROOT / "chunks"
TOC_RAW_DIR = DATA_ROOT / "toc_raw"
TOC_PARSED_DIR = DATA_ROOT / "toc_parsed"
TOC_ENRICHED_DIR = DATA_ROOT / "toc_enriched"
PICTURES_DIR = DATA_ROOT / "pictures"
LANCEDB_DATA_DIR = DATA_ROOT / "lancedb_data"
SUMMARIES_DIR = DATA_ROOT / "summaries"

EMBEDDING_MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"
EMBEDDING_MAX_SEQ_LENGTH = 1024

MAX_AGENT_CHAPTERS = 3
MAX_AGENT_SECTIONS = 3

CHUNKS_FILENAME_TEMPLATE = "{pdf_name}.jsonl"
METADATA_FILENAME_TEMPLATE = "{pdf_name}.meta.json"
TOC_RAW_FILENAME_TEMPLATE = "{pdf_name}.txt"
TOC_JSON_FILENAME_TEMPLATE = "{pdf_name}.json"
LANCE_TABLE_DIR_TEMPLATE = "{pdf_name}.lance"


def getChunksPath(pdf_name: str) -> Path:
    """Return the chunk JSONL path for one PDF.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        Path to ``chunks/<pdf_name>.jsonl``.
    """
    return CHUNKS_DIR / CHUNKS_FILENAME_TEMPLATE.format(pdf_name=pdf_name)


def getMetadataPath(pdf_name: str) -> Path:
    """Return the metadata JSON path for one PDF.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        Path to ``chunks/<pdf_name>.meta.json``.
    """
    return CHUNKS_DIR / METADATA_FILENAME_TEMPLATE.format(pdf_name=pdf_name)


def getRawTocPath(pdf_name: str) -> Path:
    """Return the raw ToC text path for one PDF.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        Path to ``toc_raw/<pdf_name>.txt``.
    """
    return TOC_RAW_DIR / TOC_RAW_FILENAME_TEMPLATE.format(pdf_name=pdf_name)


def getParsedTocPath(pdf_name: str) -> Path:
    """Return the parsed ToC JSON path for one PDF.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        Path to ``toc_parsed/<pdf_name>.json``.
    """
    return TOC_PARSED_DIR / TOC_JSON_FILENAME_TEMPLATE.format(pdf_name=pdf_name)


def getEnrichedTocPath(pdf_name: str) -> Path:
    """Return the enriched ToC JSON path for one PDF.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        Path to ``toc_enriched/<pdf_name>.json``.
    """
    return TOC_ENRICHED_DIR / TOC_JSON_FILENAME_TEMPLATE.format(pdf_name=pdf_name)


def getPicturesDir(pdf_name: str) -> Path:
    """Return the picture directory for one PDF.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        Path to ``pictures/<pdf_name>``.
    """
    return PICTURES_DIR / pdf_name


def getLanceTableName(pdf_name: str) -> str:
    """Return a LanceDB-safe table name derived from the PDF stem.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        A table name containing only alphanumerics, underscores, hyphens,
        and periods.
    """
    return TABLE_NAME_INVALID_PATTERN.sub("_", pdf_name)


def getLanceTableDir(pdf_name: str) -> Path:
    """Return the on-disk LanceDB table directory for one PDF.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        Path to ``lancedb_data/<table_name>.lance``.
    """
    return LANCEDB_DATA_DIR / LANCE_TABLE_DIR_TEMPLATE.format(
        pdf_name=getLanceTableName(pdf_name)
    )


def getSummariesDir(pdf_name: str) -> Path:
    """Return the summary workspace for one PDF.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        Path to ``summaries/<pdf_name>``.
    """
    return SUMMARIES_DIR / pdf_name


def getManifestPath(pdf_name: str) -> Path:
    """Return the summary manifest path for one PDF.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        Path to ``summaries/<pdf_name>/manifest.json``.
    """
    return getSummariesDir(pdf_name) / "manifest.json"
