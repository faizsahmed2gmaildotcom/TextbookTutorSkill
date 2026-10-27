"""PDF conversion, chunk export, ToC detection, and PDF metadata extraction."""

import json
import shutil
from pathlib import Path
from typing import Any

from docling_core.transforms.chunker.hierarchical_chunker import (
    ChunkingSerializerProvider,
)

import config
from logging_config import getLogger

logger = getLogger(__name__)


class PictureTraversingSerializerProvider(ChunkingSerializerProvider):
    """Provide a Markdown serializer that also walks text nested in pictures."""

    def get_serializer(self, doc: Any) -> Any:
        """Build a chunk serializer for the supplied document.

        Args:
            doc: A ``DoclingDocument`` instance.

        Returns:
            A ``ChunkingDocSerializer`` configured for picture traversal.
        """
        from docling_core.transforms.chunker.hierarchical_chunker import (
            ChunkingDocSerializer,
        )
        from docling_core.transforms.serializer.markdown import MarkdownParams

        params = MarkdownParams(traverse_pictures=True, compact_tables=True)

        return ChunkingDocSerializer(doc=doc, params=params)


def convertPdf(pdf_path: Path, page_range: tuple[int, int] | None = None) -> Any:
    """Convert a PDF into a postprocessed DoclingDocument.

    Args:
        pdf_path: Absolute path to the PDF.
        page_range: Optional 1-indexed inclusive page range.

    Returns:
        The converted and postprocessed ``DoclingDocument``.

    Raises:
        FileNotFoundError: If ``pdf_path`` does not exist.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from hierarchical.postprocessor import ResultPostprocessor

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pipeline_options = PdfPipelineOptions(
        generate_page_images=True,
        images_scale=2.0,
        do_formula_enrichment=False,
    )
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )

    logger.info(
        "Converting PDF: %s%s",
        pdf_path,
        f" (pages {page_range[0]}-{page_range[1]})" if page_range else " (full document)",
    )

    if page_range:
        result = converter.convert(str(pdf_path), page_range=page_range)
    else:
        result = converter.convert(str(pdf_path))

    ResultPostprocessor(result).process()

    return result.document


def exportChunks(document: Any, pdf_name: str) -> Path:
    """Write text and picture chunks plus rendered picture files.

    Args:
        document: A converted ``DoclingDocument``.
        pdf_name: PDF file name without its extension.

    Returns:
        Path to the written ``chunks/<pdf_name>.jsonl`` file.

    Raises:
        OSError: If the output files cannot be written.
    """
    from docling.chunking import HybridChunker

    chunks_path = config.getChunksPath(pdf_name)
    pictures_dir = config.getPicturesDir(pdf_name)

    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    if pictures_dir.exists():
        shutil.rmtree(pictures_dir)
    pictures_dir.mkdir(parents=True, exist_ok=True)

    chunker = HybridChunker(
        merge_peers=True,
        serializer_provider=PictureTraversingSerializerProvider(),
    )
    ref_to_text = {element.self_ref: element.text for element in document.texts}

    logger.info("Exporting chunks to %s", chunks_path)

    with chunks_path.open("w", encoding="utf-8") as chunks_file:
        for chunk in chunker.chunk(document):
            chunks_file.write(
                json.dumps(chunk.export_json_dict(), ensure_ascii=False) + "\n"
            )

        for picture in document.pictures:
            picture_id = picture.self_ref.split("/")[-1]
            picture_image = picture.get_image(document)

            if picture_image:
                picture_image.save(pictures_dir / f"pic_{picture_id}.png")

            picture_chunk = {
                "text": picture.caption_text(document),
                "meta": {
                    "doc_items": [picture.model_dump(mode="json")],
                    "headings": [ref_to_text.get(picture.parent.cref, "")],
                },
                "pic_id": picture_id,
            }
            chunks_file.write(
                json.dumps(picture_chunk, ensure_ascii=False) + "\n"
            )

    logger.info("Chunking and JSONL export complete for %s", pdf_name)

    return chunks_path


def getTocPages(document: Any) -> list[int]:
    """Detect ToC pages and keep only the first contiguous group.

    Args:
        document: A converted ``DoclingDocument``.

    Returns:
        Sorted 1-indexed ToC page numbers, or an empty list when none exist.
    """
    from docling.datamodel.base_models import DocItemLabel

    toc_pages: set[int] = set()

    for item, _level in document.iterate_items():
        if item.label != DocItemLabel.DOCUMENT_INDEX:
            continue

        for provenance in getattr(item, "prov", []) or []:
            toc_pages.add(provenance.page_no)

    if not toc_pages:
        logger.warning("No document_index items found; cannot auto-detect ToC pages")
        return []

    contiguous_pages = separateTocFromIndex(sorted(toc_pages))
    logger.info("Auto-detected ToC pages: %s", contiguous_pages)

    return contiguous_pages


def separateTocFromIndex(sorted_toc_pages: list[int]) -> list[int]:
    """Keep only the first run of adjacent ToC pages so an index is excluded.

    Args:
        sorted_toc_pages: Ascending page numbers that contain index items.

    Returns:
        The retained contiguous page numbers.
    """
    retained_pages = list(sorted_toc_pages)

    if not retained_pages:
        return retained_pages

    index = 0
    previous_page = retained_pages[0] - 1
    tolerance = 1

    while index < len(retained_pages):
        if (retained_pages[index] - previous_page) > tolerance:
            retained_pages.pop(index)
            continue

        previous_page = retained_pages[index]
        index += 1

    return retained_pages


def getTocContent(pdf_path: Path, toc_pages: list[int]) -> str:
    """Extract raw text from the detected ToC pages with pymupdf.

    Args:
        pdf_path: Absolute path to the PDF.
        toc_pages: Sorted 1-indexed ToC page numbers.

    Returns:
        Concatenated page text separated by newlines.

    Raises:
        FileNotFoundError: If ``pdf_path`` does not exist.
        ValueError: If ``toc_pages`` is empty.
    """
    import pymupdf

    if not toc_pages:
        raise ValueError("Cannot extract ToC content without at least one page")

    logger.info("Extracting ToC content from pages %s", toc_pages)
    document = pymupdf.open(str(pdf_path))

    try:
        page_texts = [
            document[page_number - 1].get_text() for page_number in toc_pages
        ]
    finally:
        document.close()

    content = "\n".join(page_texts)
    logger.info(
        "Extracted %s characters from %s ToC pages",
        len(content),
        len(toc_pages),
    )

    return content


def readPdfMetadata(pdf_path: Path) -> tuple[int, list[str]]:
    """Read the physical page count and PDF page labels.

    Args:
        pdf_path: Absolute path to the PDF.

    Returns:
        A tuple of ``(page_count, page_labels)``.

    Raises:
        FileNotFoundError: If ``pdf_path`` does not exist.
    """
    from pypdf import PdfReader

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    with PdfReader(str(pdf_path)) as reader:
        page_count = len(reader.pages)

        try:
            page_labels = [str(label) for label in reader.page_labels]
        except Exception:
            logger.warning(
                "Could not read page labels for %s; falling back to 1-based labels",
                pdf_path,
            )
            page_labels = [str(index) for index in range(1, page_count + 1)]

    if len(page_labels) != page_count:
        logger.warning(
            "Page label count %s does not match page count %s for %s; "
            "falling back to 1-based labels",
            len(page_labels),
            page_count,
            pdf_path,
        )
        page_labels = [str(index) for index in range(1, page_count + 1)]

    return page_count, page_labels


def writeRawToc(pdf_name: str, content: str) -> Path:
    """Write raw ToC text to ``toc_raw/<pdf_name>.txt``.

    Args:
        pdf_name: PDF file name without its extension.
        content: Raw concatenated ToC text.

    Returns:
        Path to the written file.
    """
    raw_toc_path = config.getRawTocPath(pdf_name)
    raw_toc_path.parent.mkdir(parents=True, exist_ok=True)
    raw_toc_path.write_text(content, encoding="utf-8")

    return raw_toc_path


def writeMetadata(
    pdf_path: Path,
    pdf_name: str,
    page_count: int,
    page_labels: list[str],
    toc_pages: list[int],
) -> Path:
    """Write the bookkeeping metadata needed by later stages.

    Args:
        pdf_path: Absolute path to the PDF.
        pdf_name: PDF file name without its extension.
        page_count: Physical page count from the PDF.
        page_labels: PDF page labels indexed by physical page.
        toc_pages: Sorted 1-indexed ToC pages.

    Returns:
        Path to ``chunks/<pdf_name>.meta.json``.
    """
    metadata_path = config.getMetadataPath(pdf_name)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)

    metadata = {
        "pdf_path": str(pdf_path),
        "pdf_name": pdf_name,
        "page_count": page_count,
        "page_labels": page_labels,
        "toc_pages": toc_pages,
        "toc_offset": toc_pages[-1] if toc_pages else 0,
        "pictures_dir": f"pictures/{pdf_name}",
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return metadata_path
