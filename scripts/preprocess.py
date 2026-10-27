"""CLI: convert a textbook PDF into chunks, raw ToC text, and bookkeeping."""

from pathlib import Path

import click

import config
import pdf_chunks
from logging_config import setupLogging


def parseTocPages(
    _ctx: click.Context,
    _param: click.Parameter,
    value: str | None,
) -> list[int] | None:
    """Parse ``--toc-pages`` into sorted 1-indexed page numbers.

    Args:
        _ctx: Click context (unused).
        _param: Click parameter (unused).
        value: A value such as ``3-10``, ``3,4,5``, or ``3-5,7``.

    Returns:
        Sorted page numbers, or ``None`` when the option is absent.

    Raises:
        click.BadParameter: If any page specification is invalid.
    """
    if value is None:
        return None

    pages: set[int] = set()

    for part in value.split(","):
        part = part.strip()

        if not part:
            raise click.BadParameter(
                f"{value!r} contains an empty page specification"
            )

        if "-" in part:
            bounds = part.split("-", 1)
            try:
                start_page = int(bounds[0])
                end_page = int(bounds[1])
            except ValueError as exc:
                raise click.BadParameter(
                    f"{part!r} is not a valid page range"
                ) from exc

            if start_page < 1 or end_page < start_page:
                raise click.BadParameter(
                    f"{part!r} must be a positive ascending range"
                )

            pages.update(range(start_page, end_page + 1))
        else:
            try:
                page_number = int(part)
            except ValueError as exc:
                raise click.BadParameter(
                    f"{part!r} is not a valid page number"
                ) from exc

            if page_number < 1:
                raise click.BadParameter(
                    f"{part!r} must be a positive page number"
                )

            pages.add(page_number)

    return sorted(pages)


def resolvePdfPath(raw_path: str) -> Path:
    """Resolve and validate a PDF path.

    Args:
        raw_path: Path supplied on the command line.

    Returns:
        The absolute resolved path.

    Raises:
        FileNotFoundError: If the path is missing or is not a ``.pdf`` file.
    """
    pdf_path = Path(raw_path).expanduser().resolve()

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    if pdf_path.suffix.lower() != ".pdf":
        raise FileNotFoundError(f"Not a PDF file: {pdf_path}")

    return pdf_path


def preprocessPdf(
    raw_pdf_path: str,
    force: bool = False,
    toc_pages: list[int] | None = None,
) -> None:
    """Build chunks, raw ToC text, and metadata for one PDF.

    Args:
        raw_pdf_path: PDF path supplied on the command line.
        force: Rebuild chunks, pictures, raw ToC, and metadata.
        toc_pages: Explicit 1-indexed ToC pages, bypassing detection.

    Returns:
        None. Completion is signalled by the CLI message.

    Raises:
        FileNotFoundError: If the PDF path is invalid.
        click.ClickException: If no ToC pages can be detected.
    """
    pdf_path = resolvePdfPath(raw_pdf_path)
    pdf_name = pdf_path.stem

    chunks_path = config.getChunksPath(pdf_name)
    metadata_path = config.getMetadataPath(pdf_name)
    raw_toc_path = config.getRawTocPath(pdf_name)
    outputs_exist = (
        chunks_path.exists()
        and metadata_path.exists()
        and raw_toc_path.exists()
        and config.getPicturesDir(pdf_name).exists()
    )

    if not force and toc_pages is None and outputs_exist:
        return

    reuse_chunks = (
        not force
        and toc_pages is not None
        and chunks_path.exists()
        and config.getPicturesDir(pdf_name).exists()
    )
    document = None

    if not reuse_chunks:
        document = pdf_chunks.convertPdf(pdf_path)
        pdf_chunks.exportChunks(document, pdf_name)

    if toc_pages is None:
        if document is None:
            document = pdf_chunks.convertPdf(pdf_path)

        toc_pages = pdf_chunks.getTocPages(document)

    if not toc_pages:
        raise click.ClickException(
            "No table of contents pages were detected. "
            "Rerun with --toc-pages, for example --toc-pages 3-5."
        )

    page_count, page_labels = pdf_chunks.readPdfMetadata(pdf_path)

    if max(toc_pages) > page_count:
        raise click.ClickException(
            f"--toc-pages requests page {max(toc_pages)}, but the PDF has "
            f"only {page_count} pages."
        )

    toc_content = pdf_chunks.getTocContent(pdf_path, toc_pages)

    pdf_chunks.writeRawToc(pdf_name, toc_content)
    pdf_chunks.writeMetadata(
        pdf_path,
        pdf_name,
        page_count,
        page_labels,
        toc_pages,
    )


@click.command(
    name="preprocess",
    help="Convert a textbook PDF into chunks, raw ToC text, and metadata.",
)
@click.argument("pdf_path")
@click.option(
    "--force",
    is_flag=True,
    help="Rebuild chunks, pictures, raw ToC, and metadata.",
)
@click.option(
    "--toc-pages",
    "toc_pages",
    callback=parseTocPages,
    help="1-indexed ToC pages, for example 3-10 or 3,4,5.",
)
def preprocessCommand(
    pdf_path: str,
    force: bool,
    toc_pages: list[int] | None,
) -> None:
    """Convert PDF_PATH into chunks plus raw ToC text.

    Args:
        pdf_path: Absolute or relative path to the textbook PDF.
        force: Rebuild every generated artifact.
        toc_pages: Explicit ToC pages.

    Returns:
        None.
    """
    setupLogging()
    preprocessPdf(pdf_path, force=force, toc_pages=toc_pages)
    click.echo("Raw chunks and ToC contents successfully saved")


if __name__ == "__main__":
    preprocessCommand()
