"""CLI: map a parsed ToC onto physical page ranges."""

import json

import click

import config
import toc_pipeline
from logging_config import setupLogging
from schemas import PdfMetadata


def postprocessToc(pdf_name: str) -> dict:
    """Deterministically enrich a parsed ToC and write ``toc_enriched``.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        The enriched ToC dictionary that was written.

    Raises:
        FileNotFoundError: If the raw ToC, parsed ToC, or metadata is missing.
        pydantic.ValidationError: If the parsed ToC is malformed.
    """
    raw_toc_path = config.getRawTocPath(pdf_name)
    parsed_toc_path = config.getParsedTocPath(pdf_name)
    metadata_path = config.getMetadataPath(pdf_name)

    if not raw_toc_path.exists():
        raise FileNotFoundError(f"Raw ToC not found: {raw_toc_path}")

    if not parsed_toc_path.exists():
        raise FileNotFoundError(f"Parsed ToC not found: {parsed_toc_path}")

    if not metadata_path.exists():
        raise FileNotFoundError(f"PDF metadata not found: {metadata_path}")

    toc_structure = toc_pipeline.loadTocStructure(parsed_toc_path)
    metadata = PdfMetadata.model_validate_json(
        metadata_path.read_text(encoding="utf-8")
    )
    enriched_toc = toc_pipeline.enrichTocStructure(
        toc_structure,
        metadata.page_labels,
        metadata.page_count,
        metadata.toc_offset,
    )
    toc_pipeline.writeEnrichedToc(
        pdf_name,
        enriched_toc,
        config.getEnrichedTocPath(pdf_name),
    )

    return enriched_toc


@click.command(
    name="postprocess_toc",
    help="Map a parsed ToC onto physical page ranges.",
)
@click.argument("pdf_name")
def postprocessCommand(pdf_name: str) -> None:
    """Enrich the parsed ToC for PDF_NAME with physical page ranges.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        None.
    """
    setupLogging()
    postprocessToc(pdf_name)
    click.echo(f"Enriched ToC saved for {pdf_name}")

    enriched_path = config.getEnrichedTocPath(pdf_name)
    chapter_count = len(
        json.loads(enriched_path.read_text(encoding="utf-8")).get("chapters", {})
    )
    click.echo(f"Chapters: {chapter_count}")


if __name__ == "__main__":
    postprocessCommand()
