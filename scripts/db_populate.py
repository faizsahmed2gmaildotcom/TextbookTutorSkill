"""CLI: build or reuse the LanceDB index for one textbook PDF."""

import json

import click

import config
import lancedb_store
import pdf_lock
from logging_config import setupLogging
from schemas import EnrichedToc


def requireFile(path, description: str) -> None:
    """Raise when a required input file is missing.

    Args:
        path: Path to check.
        description: Human-readable artifact name.

    Returns:
        None.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"{description} not found: {path}")


@click.command(
    name="db_populate",
    help="Build or reuse the LanceDB index for one PDF.",
)
@click.argument("pdf_name")
@click.option(
    "--force",
    is_flag=True,
    help="Drop and rebuild the LanceDB table for this PDF.",
)
def populateCommand(pdf_name: str, force: bool) -> None:
    """Populate the vector index for PDF_NAME.

    Args:
        pdf_name: PDF file name without its extension.
        force: Rebuild the LanceDB table.

    Returns:
        None.
    """
    setupLogging()
    pdf_lock.assertNoActiveLock(pdf_name)

    chunks_path = config.getChunksPath(pdf_name)
    enriched_path = config.getEnrichedTocPath(pdf_name)

    requireFile(chunks_path, "Chunk JSONL")
    requireFile(enriched_path, "Enriched ToC")

    enriched_toc = json.loads(enriched_path.read_text(encoding="utf-8"))
    EnrichedToc.model_validate(enriched_toc)

    result = lancedb_store.populateLanceDb(
        chunks_path,
        config.LANCEDB_DATA_DIR,
        config.getLanceTableName(pdf_name),
        enriched_toc,
        force=force,
    )

    status = {
        "pdf_name": pdf_name,
        "table_name": config.getLanceTableName(pdf_name),
        "row_count": result.table.count_rows(),
        "reused": result.reused,
    }
    click.echo(json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    populateCommand()
