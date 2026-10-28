"""Pydantic models for ToC parsing, enrichment, and metadata."""

from pydantic import BaseModel


class TocSection(BaseModel):
    """One section exactly as produced by the ToC-parsing subagent."""

    name: str
    start: int


class TocChapter(BaseModel):
    """One chapter exactly as produced by the ToC-parsing subagent."""

    name: str
    start: int
    sections: dict[str, TocSection]


class TocStructure(BaseModel):
    """The deterministic output schema for ``toc_parsed/<pdf_name>.json``."""

    chapters: dict[str, TocChapter]
    thinking: str


class EnrichedSection(BaseModel):
    """A section after page-label mapping and end-page computation."""

    name: str
    start: int
    end: int
    summary: str = ""


class EnrichedChapter(BaseModel):
    """A chapter after page-label mapping, end-page computation, and summaries."""

    name: str
    start: int
    end: int
    summary: str = ""
    sections: dict[str, EnrichedSection]


class EnrichedToc(BaseModel):
    """The runtime document read by ``db_skills.py``."""

    chapters: dict[str, EnrichedChapter]


class PdfMetadata(BaseModel):
    """Bookkeeping written by ``preprocess.py`` for later deterministic stages."""

    pdf_path: str
    pdf_name: str
    page_count: int
    page_labels: list[str]
    toc_pages: list[int]
    toc_offset: int
    pictures_dir: str
