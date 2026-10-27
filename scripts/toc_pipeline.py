"""Deterministic ToC postprocessing: page-label mapping and end-page computation."""

import json
from pathlib import Path

from schemas import EnrichedToc, TocChapter, TocSection, TocStructure
from logging_config import getLogger

logger = getLogger(__name__)


def loadTocStructure(path: Path) -> TocStructure:
    """Load and validate a parsed ToC file.

    Args:
        path: Path to ``toc_parsed/<pdf_name>.json``.

    Returns:
        The validated ToC structure.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        pydantic.ValidationError: If the JSON does not match ``TocStructure``.
    """
    if not path.exists():
        raise FileNotFoundError(f"Parsed ToC not found: {path}")

    return TocStructure.model_validate_json(path.read_text(encoding="utf-8"))


def buildPageToIdx(page_labels: list[str], toc_offset: int) -> dict[str, int]:
    """Map every PDF page label to its 1-indexed physical page.

    Args:
        page_labels: PDF page labels ordered by physical page.
        toc_offset: Last ToC page number.

    Returns:
        Mapping of label string to 1-indexed page number.
    """
    page_to_idx = {
        label: index + 1 for index, label in enumerate(page_labels)
    }

    # Legacy parity: unlabelled/1:1 PDFs shift every page past the ToC.
    if all(label == str(index) for label, index in page_to_idx.items()):
        page_to_idx = {
            label: index + 1 + toc_offset
            for index, label in enumerate(page_labels)
        }

    return page_to_idx


def mapPageNumber(value: int, page_to_idx: dict[str, int]) -> int:
    """Map a parsed ToC page number through the page-label table.

    Args:
        value: Page number as printed in the ToC.
        page_to_idx: Label-to-physical-page mapping.

    Returns:
        The physical 1-indexed page number.

    Raises:
        ValueError: If the value is neither a known label nor an integer.
    """
    label = str(value)

    if label in page_to_idx:
        return page_to_idx[label]

    try:
        fallback_page = int(label)
    except ValueError as exc:
        raise ValueError(
            f"ToC page {value!r} has no page label and is not an integer"
        ) from exc

    logger.warning(
        "Page label %s is missing from the PDF; using %s as-is",
        label,
        fallback_page,
    )

    return fallback_page


def normalizeSectionIds(
    sections: dict[str, TocSection],
) -> dict[str, TocSection]:
    """Collapse dotted section IDs and drop subsections.

    Args:
        sections: Sections keyed by their parsed IDs.

    Returns:
        Sections keyed by normalized IDs, preserving first-seen order.
    """
    normalized: dict[str, TocSection] = {}

    for section_id, section in sections.items():
        id_parts = section_id.split(".")

        if len(id_parts) > 2:
            logger.warning(
                "Dropping subsection %s (%s)", section_id, section.name
            )
            continue

        normalized_id = id_parts[-1] if len(id_parts) == 2 else section_id

        if normalized_id in normalized:
            logger.warning(
                "Section ID collision for %s; keeping the first entry",
                normalized_id,
            )
            continue

        normalized[normalized_id] = section

    return normalized


def sortIds(ids: list[str]) -> list[str]:
    """Sort string IDs numerically when possible.

    Args:
        ids: Chapter or section ID strings.

    Returns:
        IDs ordered numerically, with non-numeric IDs last.
    """

    def sortKey(value: str) -> tuple[int, int | str]:
        """Order numeric IDs first and non-numeric IDs last.

        Args:
            value: Chapter or section ID.

        Returns:
            A sortable tuple.
        """
        try:
            return (0, int(value))
        except ValueError:
            return (1, value)

    return sorted(ids, key=sortKey)


def enrichTocStructure(
    toc_structure: TocStructure,
    page_labels: list[str],
    page_count: int,
    toc_offset: int,
) -> dict:
    """Compute physical page ranges for every chapter and section.

    Args:
        toc_structure: Validated parsed ToC.
        page_labels: PDF page labels ordered by physical page.
        page_count: Physical page count from the PDF.
        toc_offset: Last ToC page number.

    Returns:
        A JSON-ready ``{"chapters": ...}`` dictionary.
    """
    page_to_idx = buildPageToIdx(page_labels, toc_offset)
    chapter_ids = sortIds(list(toc_structure.chapters.keys()))
    enriched_chapters: dict[str, dict] = {}

    for index, chapter_id in enumerate(chapter_ids):
        chapter = toc_structure.chapters[chapter_id]
        chapter_start = mapPageNumber(chapter.start, page_to_idx)

        if index == len(chapter_ids) - 1:
            # Legacy parity: the last chapter ends at page_count - 1.
            chapter_end = page_count - 1
        else:
            next_chapter = toc_structure.chapters[chapter_ids[index + 1]]
            chapter_end = mapPageNumber(next_chapter.start, page_to_idx) - 1

        enriched_sections = enrichChapterSections(
            chapter, chapter_end, page_to_idx
        )
        enriched_chapters[chapter_id] = {
            "name": chapter.name,
            "start": chapter_start,
            "end": chapter_end,
            "sections": enriched_sections,
        }

    print(json.dumps(enriched_chapters))

    return {"chapters": enriched_chapters}


def enrichChapterSections(
    chapter: TocChapter,
    chapter_end: int,
    page_to_idx: dict[str, int],
) -> dict[str, dict]:
    """Map a chapter's sections and compute their overlapping end pages.

    Args:
        chapter: One parsed chapter.
        chapter_end: Physical end page of the chapter.
        page_to_idx: Label-to-physical-page mapping.

    Returns:
        Sections keyed by normalized ID with physical ranges.
    """
    normalized_sections = normalizeSectionIds(chapter.sections)
    section_ids = sortIds(list(normalized_sections.keys()))
    mapped_starts = {
        section_id: mapPageNumber(
            normalized_sections[section_id].start, page_to_idx
        )
        for section_id in section_ids
    }
    enriched_sections: dict[str, dict] = {}

    for index, section_id in enumerate(section_ids):
        section = normalized_sections[section_id]

        if index == len(section_ids) - 1:
            section_end = chapter_end
        else:
            # Legacy parity: a section ends where the next section starts.
            section_end = mapped_starts[section_ids[index + 1]]

        enriched_sections[section_id] = {
            "name": section.name,
            "start": mapped_starts[section_id],
            "end": section_end,
        }

    return enriched_sections


def writeEnrichedToc(pdf_name: str, enriched_toc: dict, output_path: Path) -> Path:
    """Validate and write an enriched ToC document.

    Args:
        pdf_name: PDF file name without its extension.
        enriched_toc: JSON-ready enriched ToC.
        output_path: Destination path.

    Returns:
        The written path.

    Raises:
        pydantic.ValidationError: If the enriched structure is malformed.
    """
    validated = EnrichedToc.model_validate(enriched_toc)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            validated.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    logger.info("Wrote enriched ToC for %s to %s", pdf_name, output_path)

    return output_path
