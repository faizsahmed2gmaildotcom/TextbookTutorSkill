"""Pydantic models for ToC parsing, enrichment, metadata, and summaries."""

from pydantic import BaseModel, Field


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


class SummaryOutput(BaseModel):
    """The deterministic output schema for one delegated summary task."""

    summary: str


class SummaryTask(BaseModel):
    """One unit of work delegated to a subagent.

    Section tasks populate ``content_path``. Chapter tasks populate
    ``section_summary_paths``, ``chapter_content_path``, and ``depends_on``.
    """

    task_id: str
    kind: str
    chapter_id: str
    section_id: str | None = None
    name: str
    system_prompt: str
    section_summary_paths: list[str] | None = None
    chapter_content_path: str | None = None
    content_path: str | None = None
    output_path: str
    depends_on: list[str] | None = None
    output_schema: dict = Field(default_factory=dict)


class SummaryManifest(BaseModel):
    """The manifest consumed by the calling agent and ``--apply-summaries``."""

    pdf_name: str
    section_tasks: list[SummaryTask]
    chapter_tasks: list[SummaryTask]
