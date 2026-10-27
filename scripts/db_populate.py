"""CLI: build the LanceDB index, write summary tasks, or apply summaries."""

import json

import click

import config
import lancedb_store
import summary_pipeline
from logging_config import setupLogging
from schemas import EnrichedToc
from textbook_skills import Skills


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
    help="Build or reuse the LanceDB index and write summary tasks.",
)
@click.argument("pdf_name")
@click.option(
    "--force",
    is_flag=True,
    help="Drop and rebuild the LanceDB table for this PDF.",
)
@click.option(
    "--apply-summaries",
    "apply_summaries",
    is_flag=True,
    help="Merge delegated summary outputs into the ToC files.",
)
def populateCommand(
    pdf_name: str,
    force: bool,
    apply_summaries: bool,
) -> None:
    """Populate the vector index for PDF_NAME or apply its summary outputs.

    Args:
        pdf_name: PDF file name without its extension.
        force: Rebuild the LanceDB table.
        apply_summaries: Merge delegated summary outputs instead of indexing.

    Returns:
        None.
    """
    setupLogging()

    if apply_summaries:
        status = summary_pipeline.applySummaries(pdf_name)
        click.echo(json.dumps(status, ensure_ascii=False))
        return

    chunks_path = config.getChunksPath(pdf_name)
    enriched_path = config.getEnrichedTocPath(pdf_name)

    requireFile(chunks_path, "Chunk JSONL")
    requireFile(enriched_path, "Enriched ToC")

    enriched_toc = json.loads(enriched_path.read_text(encoding="utf-8"))
    EnrichedToc.model_validate(enriched_toc)

    table = lancedb_store.populateLanceDb(
        chunks_path,
        config.LANCEDB_DATA_DIR,
        config.getLanceTableName(pdf_name),
        enriched_toc,
        force=force,
    )
    skills = Skills(pdf_name, enriched_toc, table)
    manifest = summary_pipeline.buildSummaryManifest(pdf_name, skills)

    status = {
        "pdf_name": pdf_name,
        "table_name": config.getLanceTableName(pdf_name),
        "manifest_path": str(config.getManifestPath(pdf_name).resolve()),
        "section_task_count": len(manifest["section_tasks"]),
        "chapter_task_count": len(manifest["chapter_tasks"]),
    }
    click.echo(json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    populateCommand()
