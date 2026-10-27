"""Summary task manifests, delegated outputs, and deterministic merging."""

import json
from pathlib import Path
from typing import Any

import click
from pydantic import ValidationError

import config
from logging_config import getLogger
from schemas import SummaryManifest, SummaryOutput, SummaryTask

logger = getLogger(__name__)

SECTION_PROMPT = (
    "Summarize this textbook section in 1-3 sentences. Include key concepts "
    "and important details. Be thorough but concise."
)
CHAPTER_PROMPT = (
    "Summarize this textbook chapter in 3-5 sentences. Include key topics, "
    "concepts, and their relationships. Be comprehensive."
)
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}


def buildSectionContent(
    skills: Any,
    chapter_id: str,
    section_id: str,
) -> str:
    """Extract a section's raw text for subagent consumption.

    Args:
        skills: A ``Skills`` instance.
        chapter_id: Chapter number as a string.
        section_id: Section number as a string.

    Returns:
        The section text, or an empty string when the section has no chunks.
    """
    try:
        section_items = skills.getSectionRawContents(
            [(int(chapter_id), int(section_id))]
        )
    except IndexError:
        logger.warning(
            "No chunks found for section %s.%s", chapter_id, section_id
        )
        return ""

    return "\n".join(item.get("text", "") for item in section_items)


def writeTextFile(path: Path, content: str) -> None:
    """Write UTF-8 text, creating parent directories as needed.

    Args:
        path: Destination file path.
        content: Text to write.

    Returns:
        None.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def buildSummaryManifest(pdf_name: str, skills: Any) -> dict:
    """Create content files and the summary task manifest for one book.

    Args:
        pdf_name: PDF file name without its extension.
        skills: A ``Skills`` instance whose ``book_data`` is the enriched ToC.

    Returns:
        The manifest as a JSON-ready dictionary.
    """
    summaries_dir = config.getSummariesDir(pdf_name)
    content_dir = summaries_dir / "content"
    sections_dir = summaries_dir / "sections"
    chapters_dir = summaries_dir / "chapters"

    for directory in (content_dir, sections_dir, chapters_dir):
        directory.mkdir(parents=True, exist_ok=True)

    book_data = skills.book_data
    section_tasks: list[SummaryTask] = []
    chapter_tasks: list[SummaryTask] = []

    for chapter_id, chapter in book_data["chapters"].items():
        section_task_ids: list[str] = []
        section_summary_paths: list[str] = []

        for section_id, section in chapter.get("sections", {}).items():
            task_id = f"section-{chapter_id}.{section_id}"
            content_path = content_dir / f"section-{chapter_id}.{section_id}.txt"
            output_path = sections_dir / f"{chapter_id}.{section_id}.json"
            section_content = buildSectionContent(
                skills, chapter_id, section_id
            )
            writeTextFile(content_path, section_content)

            section_tasks.append(
                SummaryTask(
                    task_id=task_id,
                    kind="section",
                    chapter_id=chapter_id,
                    section_id=section_id,
                    name=section["name"],
                    system_prompt=SECTION_PROMPT,
                    content_path=str(content_path.resolve()),
                    output_path=str(output_path.resolve()),
                    output_schema=OUTPUT_SCHEMA,
                )
            )
            section_task_ids.append(task_id)
            section_summary_paths.append(str(output_path.resolve()))

        chapter_content = skills.getChapterLevelContents(chapter_id)
        chapter_content_path = content_dir / f"chapter-{chapter_id}.txt"
        writeTextFile(chapter_content_path, chapter_content)

        chapter_tasks.append(
            SummaryTask(
                task_id=f"chapter-{chapter_id}",
                kind="chapter",
                chapter_id=chapter_id,
                name=chapter["name"],
                system_prompt=CHAPTER_PROMPT,
                section_summary_paths=section_summary_paths,
                chapter_content_path=str(chapter_content_path.resolve()),
                output_path=str((chapters_dir / f"{chapter_id}.json").resolve()),
                depends_on=section_task_ids,
                output_schema=OUTPUT_SCHEMA,
            )
        )

    manifest = SummaryManifest(
        pdf_name=pdf_name,
        section_tasks=section_tasks,
        chapter_tasks=chapter_tasks,
    )
    manifest_path = config.getManifestPath(pdf_name)
    writeTextFile(
        manifest_path,
        json.dumps(
            manifest.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    logger.info(
        "Wrote summary manifest with %s section and %s chapter tasks to %s",
        len(section_tasks),
        len(chapter_tasks),
        manifest_path,
    )

    return manifest.model_dump(mode="json", exclude_none=True)


def loadManifest(pdf_name: str) -> SummaryManifest:
    """Load and validate the summary manifest.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        The validated manifest.

    Raises:
        FileNotFoundError: If the manifest is missing.
    """
    manifest_path = config.getManifestPath(pdf_name)

    if not manifest_path.exists():
        raise FileNotFoundError(f"Summary manifest not found: {manifest_path}")

    return SummaryManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )


def readTextFile(path: str) -> str:
    """Read a UTF-8 text file, returning an empty string when missing.

    Args:
        path: File path as stored in the manifest.

    Returns:
        File contents or ``""``.
    """
    file_path = Path(path)

    if not file_path.exists():
        return ""

    return file_path.read_text(encoding="utf-8")


def loadSummaryOutput(output_path: str, task_id: str) -> str:
    """Load and validate one delegated summary output.

    Args:
        output_path: Path from the summary task.
        task_id: Task identifier used in error messages.

    Returns:
        The validated summary text.

    Raises:
        ValidationError: If the JSON is missing or malformed.
        ValueError: If the summary is empty.
    """
    output_file = Path(output_path)

    if not output_file.exists():
        raise FileNotFoundError(
            f"Summary output for {task_id} not found: {output_path}"
        )

    summary_output = SummaryOutput.model_validate_json(
        output_file.read_text(encoding="utf-8")
    )

    if not summary_output.summary.strip():
        raise ValueError(f"Summary output for {task_id} is empty")

    return summary_output.summary


def findMissingSummaryTasks(
    tasks: list[SummaryTask],
    contents: dict[str, str],
    parsed_toc: dict,
) -> list[str]:
    """Collect task IDs whose summary is absent and cannot be reused.

    Args:
        tasks: Section or chapter tasks to check.
        contents: Task ID to content mapping.
        parsed_toc: Parsed ToC that may already contain summaries.

    Returns:
        Missing or invalid task IDs.
    """
    missing_tasks: list[str] = []

    for task in tasks:
        content = contents.get(task.task_id, "")

        if not content.strip():
            continue

        if Path(task.output_path).exists():
            try:
                loadSummaryOutput(task.output_path, task.task_id)
            except (ValidationError, ValueError):
                missing_tasks.append(task.task_id)
            continue

        if not getExistingSummary(parsed_toc, task):
            missing_tasks.append(task.task_id)

    return missing_tasks


def getExistingSummary(parsed_toc: dict, task: SummaryTask) -> str:
    """Read a previously applied summary from the parsed ToC.

    Args:
        parsed_toc: Parsed ToC dictionary.
        task: Section or chapter task.

    Returns:
        The existing non-empty summary, or ``""``.
    """
    chapter = parsed_toc.get("chapters", {}).get(task.chapter_id, {})

    if task.kind == "section":
        section = chapter.get("sections", {}).get(task.section_id or "", {})
        return str(section.get("summary", "")).strip()

    return str(chapter.get("summary", "")).strip()


def resolveTaskSummary(
    task: SummaryTask,
    content: str,
    parsed_toc: dict,
    fallback: str,
) -> str:
    """Resolve one task's summary from its output, cache, or fallback.

    Args:
        task: Section or chapter task.
        content: Task content already read from disk.
        parsed_toc: Parsed ToC dictionary.
        fallback: Deterministic summary used when content is empty.

    Returns:
        The summary text to merge.
    """
    if not content.strip():
        return fallback

    if Path(task.output_path).exists():
        return loadSummaryOutput(task.output_path, task.task_id)

    existing_summary = getExistingSummary(parsed_toc, task)

    if existing_summary:
        logger.info("Reusing existing summary for %s", task.task_id)
        return existing_summary

    return loadSummaryOutput(task.output_path, task.task_id)


def mergeSectionSummaries(
    parsed_toc: dict,
    manifest: SummaryManifest,
) -> int:
    """Merge delegated section summaries into the parsed ToC in memory.

    Args:
        parsed_toc: Parsed ToC dictionary, mutated in place.
        manifest: Validated summary manifest.

    Returns:
        Number of section tasks merged.
    """
    applied_count = 0

    for task in manifest.section_tasks:
        content = readTextFile(task.content_path or "")
        fallback = f"No content available for Section {task.chapter_id}.{task.section_id}"
        summary = resolveTaskSummary(task, content, parsed_toc, fallback)
        chapter = parsed_toc.setdefault("chapters", {}).setdefault(
            task.chapter_id, {"sections": {}}
        )
        section = chapter.setdefault("sections", {}).setdefault(
            task.section_id or "", {}
        )
        section["summary"] = summary
        applied_count += 1

    return applied_count


def buildChapterContent(
    parsed_toc: dict,
    task: SummaryTask,
) -> str:
    """Assemble the legacy chapter input from section summaries and chunks.

    Args:
        parsed_toc: Parsed ToC with applied section summaries.
        task: Chapter task.

    Returns:
        The ``all_content`` block passed to the chapter summarizer.
    """
    chapter = parsed_toc.get("chapters", {}).get(task.chapter_id, {})
    section_summaries: list[str] = []

    print(chapter)
    for section_id, section in chapter.get("sections", {}).items():
        section_summaries.append(
            f'Section {section_id} ({section["name"]}): '
            f'{section.get("summary", "")}'
        )

    chapter_chunks = readTextFile(task.chapter_content_path or "")
    all_content = ""

    if section_summaries:
        all_content += (
            "Section Summaries:\n"
            + "\n---\n".join(section_summaries)
            + "\n\n"
        )

    if chapter_chunks:
        all_content += "Chapter Content:\n" + chapter_chunks

    return all_content


def mergeChapterSummaries(
    parsed_toc: dict,
    manifest: SummaryManifest,
) -> int:
    """Merge delegated chapter summaries into the parsed ToC in memory.

    Args:
        parsed_toc: Parsed ToC dictionary, mutated in place.
        manifest: Validated summary manifest.

    Returns:
        Number of chapter tasks merged.
    """
    applied_count = 0

    for task in manifest.chapter_tasks:
        all_content = buildChapterContent(parsed_toc, task)
        fallback = f"No content available for {task.name}"
        summary = resolveTaskSummary(task, all_content, parsed_toc, fallback)
        chapter = parsed_toc.setdefault("chapters", {}).setdefault(
            task.chapter_id, {"sections": {}}
        )
        chapter["summary"] = summary
        applied_count += 1

    return applied_count


def writeParsedToc(pdf_name: str, parsed_toc: dict) -> Path:
    """Write the parsed ToC with applied summaries in place.

    Args:
        pdf_name: PDF file name without its extension.
        parsed_toc: Parsed ToC dictionary.

    Returns:
        The written path.
    """
    parsed_path = config.getParsedTocPath(pdf_name)
    writeTextFile(
        parsed_path,
        json.dumps(parsed_toc, ensure_ascii=False, indent=2) + "\n",
    )

    return parsed_path


def writeEnrichedSummaries(pdf_name: str, parsed_toc: dict) -> Path:
    """Copy applied summaries into the enriched ToC.

    Args:
        pdf_name: PDF file name without its extension.
        parsed_toc: Parsed ToC with summaries.

    Returns:
        The enriched ToC path.

    Raises:
        FileNotFoundError: If ``toc_enriched/<pdf_name>.json`` is missing.
    """
    enriched_path = config.getEnrichedTocPath(pdf_name)

    if not enriched_path.exists():
        raise FileNotFoundError(f"Enriched ToC not found: {enriched_path}")

    enriched_toc = json.loads(enriched_path.read_text(encoding="utf-8"))

    for chapter_id, parsed_chapter in parsed_toc.get("chapters", {}).items():
        enriched_chapter = enriched_toc.setdefault("chapters", {}).setdefault(
            chapter_id, {"sections": {}}
        )

        if parsed_chapter.get("summary"):
            enriched_chapter["summary"] = parsed_chapter["summary"]

        for section_id, parsed_section in parsed_chapter.get(
            "sections", {}
        ).items():
            enriched_section = enriched_chapter.setdefault(
                "sections", {}
            ).setdefault(section_id, {})

            if parsed_section.get("summary"):
                enriched_section["summary"] = parsed_section["summary"]

    writeTextFile(
        enriched_path,
        json.dumps(enriched_toc, ensure_ascii=False, indent=2) + "\n",
    )

    return enriched_path


def applySummaries(pdf_name: str) -> dict:
    """Validate delegated outputs and merge them into both ToC files.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        A JSON-ready status dictionary with applied task counts.

    Raises:
        FileNotFoundError: If the manifest or parsed ToC is missing.
        click.ClickException: If any required summary output is missing.
    """
    parsed_path = config.getParsedTocPath(pdf_name)

    if not parsed_path.exists():
        raise FileNotFoundError(f"Parsed ToC not found: {parsed_path}")

    manifest = loadManifest(pdf_name)
    parsed_toc = json.loads(parsed_path.read_text(encoding="utf-8"))
    section_contents = {
        task.task_id: readTextFile(task.content_path or "")
        for task in manifest.section_tasks
    }
    missing_sections = findMissingSummaryTasks(
        manifest.section_tasks, section_contents, parsed_toc
    )

    if missing_sections:
        raise click.ClickException(
            "Missing section summary outputs: " + ", ".join(missing_sections)
        )

    applied_sections = mergeSectionSummaries(parsed_toc, manifest)
    chapter_contents = {
        task.task_id: buildChapterContent(parsed_toc, task)
        for task in manifest.chapter_tasks
    }
    missing_chapters = findMissingSummaryTasks(
        manifest.chapter_tasks, chapter_contents, parsed_toc
    )

    if missing_chapters:
        raise click.ClickException(
            "Missing chapter summary outputs: " + ", ".join(missing_chapters)
        )

    applied_chapters = mergeChapterSummaries(parsed_toc, manifest)

    writeParsedToc(pdf_name, parsed_toc)
    writeEnrichedSummaries(pdf_name, parsed_toc)

    return {
        "pdf_name": pdf_name,
        "applied_sections": applied_sections,
        "applied_chapters": applied_chapters,
    }
