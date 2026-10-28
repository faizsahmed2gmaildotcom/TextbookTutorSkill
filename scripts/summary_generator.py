"""CLI: generate hierarchical textbook summaries for one PDF."""

import importlib.util
import json
import os
import signal
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import click

import config
import lancedb_store
import pdf_lock
from logging_config import getLogger, setupLogging
from schemas import EnrichedToc
from textbook_skills import Skills

logger = getLogger(__name__)

SECTION_PROMPT = (
    "Summarize this textbook section in 1-3 sentences. Include key concepts "
    "and important details. Be thorough but concise."
)
CHAPTER_PROMPT = (
    "Summarize this textbook chapter in 3-5 sentences. Include key topics, "
    "concepts, and their relationships. Be comprehensive."
)
SECTION_CONTENT_SEPARATOR = "\n"
SECTION_SUMMARY_SEPARATOR = "\n---\n"


@dataclass(frozen=True)
class Settings:
    """Resolved OpenAI-compatible endpoint settings."""

    base_url: str
    api_key: str
    model: str
    timeout_seconds: float
    max_retries: int
    temperature: float


@dataclass
class SummaryResult:
    """Outcome of one section or chapter summarization attempt."""

    summary: str = ""
    failed: bool = False


@dataclass
class GenerationStats:
    """Counters reported after a generation run."""

    generated_sections: int = 0
    skipped_sections: int = 0
    generated_chapters: int = 0
    skipped_chapters: int = 0
    failed_tasks: int = 0


def loadSettingsModule(settings_path: Path) -> ModuleType:
    """Load the user-managed ``settings.py`` file from disk.

    Args:
        settings_path: Path to an existing settings file.

    Returns:
        The imported settings module.

    Raises:
        RuntimeError: If the settings file cannot be loaded.
    """
    specification = importlib.util.spec_from_file_location(
        "textbook_tutor_settings",
        settings_path,
    )

    if specification is None or specification.loader is None:
        raise RuntimeError(f"Could not load summary settings: {settings_path}")

    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)

    return module


def requireSetting(module: ModuleType, name: str) -> Any:
    """Read one required setting and fail clearly when it is missing.

    Args:
        module: Imported settings module.
        name: Setting attribute name.

    Returns:
        The configured setting value.

    Raises:
        RuntimeError: If the setting is absent.
    """
    if not hasattr(module, name):
        raise RuntimeError(f"Summary settings are missing {name}")

    return getattr(module, name)


def loadSettings() -> Settings:
    """Load and validate the OpenAI-compatible endpoint settings.

    Args:
        None.

    Returns:
        Resolved settings.

    Raises:
        RuntimeError: If ``settings.py`` is missing or incomplete.
    """
    settings_path = config.getSettingsPath()

    if not settings_path.exists():
        raise RuntimeError(
            f"Summary settings not found: {settings_path}. Copy "
            f"{config.getSettingsExamplePath()} to {settings_path} and "
            "configure the endpoint, key, and model."
        )

    module = loadSettingsModule(settings_path)

    return Settings(
        base_url=str(requireSetting(module, "OPENAI_BASE_URL")),
        api_key=str(requireSetting(module, "OPENAI_API_KEY")),
        model=str(requireSetting(module, "OPENAI_MODEL")),
        timeout_seconds=float(requireSetting(module, "OPENAI_TIMEOUT_SECONDS")),
        max_retries=int(requireSetting(module, "OPENAI_MAX_RETRIES")),
        temperature=float(requireSetting(module, "OPENAI_TEMPERATURE")),
    )


def buildClient(settings: Settings) -> Any:
    """Create the OpenAI client for the configured endpoint.

    Args:
        settings: Resolved endpoint settings.

    Returns:
        An ``openai.OpenAI`` client.
    """
    from openai import OpenAI

    return OpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key,
        timeout=settings.timeout_seconds,
        max_retries=settings.max_retries,
    )


def requestSummary(
    client: Any,
    settings: Settings,
    system_prompt: str,
    user_content: str,
) -> str:
    """Request one summary from the configured chat-completions endpoint.

    Args:
        client: OpenAI-compatible client.
        settings: Resolved endpoint settings.
        system_prompt: Section or chapter summarization prompt.
        user_content: Text to summarize.

    Returns:
        The stripped model response, or an empty string when the model
        returned no content.
    """
    completion = client.chat.completions.create(
        model=settings.model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=settings.temperature,
    )

    if not completion.choices:
        return ""

    content = completion.choices[0].message.content or ""

    return content.strip()


def loadEnrichedToc(pdf_name: str) -> dict[str, Any]:
    """Load and validate the enriched ToC for one PDF.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        A normalized enriched ToC dictionary.

    Raises:
        FileNotFoundError: If the enriched ToC is missing.
        pydantic.ValidationError: If the file is malformed.
    """
    enriched_path = config.getEnrichedTocPath(pdf_name)

    if not enriched_path.exists():
        raise FileNotFoundError(f"Enriched ToC not found: {enriched_path}")

    enriched_toc = json.loads(enriched_path.read_text(encoding="utf-8"))
    validated = EnrichedToc.model_validate(enriched_toc)

    return validated.model_dump(mode="json")


def writeEnrichedToc(pdf_name: str, enriched_toc: dict[str, Any]) -> None:
    """Atomically replace the enriched ToC with the current summaries.

    Args:
        pdf_name: PDF file name without its extension.
        enriched_toc: Enriched ToC dictionary with updated summaries.

    Returns:
        None.

    Raises:
        pydantic.ValidationError: If ``enriched_toc`` is malformed.
    """
    validated = EnrichedToc.model_validate(enriched_toc)
    serialized = (
        json.dumps(
            validated.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    destination = config.getEnrichedTocPath(pdf_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary_path = Path(temporary_file.name)

    try:
        with temporary_file:
            temporary_file.write(serialized)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, destination)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def openSkills(pdf_name: str, enriched_toc: dict[str, Any]) -> Skills:
    """Open the LanceDB table and build the retrieval skills object.

    Args:
        pdf_name: PDF file name without its extension.
        enriched_toc: Enriched ToC dictionary.

    Returns:
        A ``Skills`` instance for this PDF.
    """
    table = lancedb_store.openLanceDbTable(
        config.LANCEDB_DATA_DIR,
        config.getLanceTableName(pdf_name),
    )

    return Skills(pdf_name, enriched_toc, table)


def sortChapterIds(chapter_ids: Any) -> list[str]:
    """Sort chapter IDs numerically while tolerating non-numeric IDs.

    Args:
        chapter_ids: Iterable of chapter ID strings.

    Returns:
        Chapter IDs in numeric order first, then lexicographic order.
    """
    return sorted(
        chapter_ids,
        key=lambda chapter_id: (
            (0, int(chapter_id))
            if str(chapter_id).isdigit()
            else (1, str(chapter_id))
        ),
    )


def readSectionContent(
    skills: Any,
    chapter_id: str,
    section_id: str,
) -> str:
    """Read the raw indexed text for one section.

    Args:
        skills: A ``Skills`` instance.
        chapter_id: Chapter number as a string.
        section_id: Section number as a string.

    Returns:
        Joined section text, or an empty string when the section has no chunks.
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

    return SECTION_CONTENT_SEPARATOR.join(
        item.get("text", "") for item in section_items
    )


def summarizeSection(
    client: Any,
    settings: Settings,
    skills: Any,
    chapter_id: str,
    section_id: str,
) -> SummaryResult:
    """Generate the summary for one section.

    Args:
        client: OpenAI-compatible client.
        settings: Resolved endpoint settings.
        skills: A ``Skills`` instance.
        chapter_id: Chapter number as a string.
        section_id: Section number as a string.

    Returns:
        The section summary, the deterministic fallback, or an empty failure.
    """
    section_content = readSectionContent(skills, chapter_id, section_id)

    if not section_content.strip():
        return SummaryResult(
            summary=f"No content available for Section {chapter_id}.{section_id}"
        )

    try:
        summary = requestSummary(
            client,
            settings,
            SECTION_PROMPT,
            section_content,
        )
    except Exception:
        logger.exception(
            "Failed to generate summary for Section %s.%s",
            chapter_id,
            section_id,
        )
        return SummaryResult(failed=True)

    if not summary:
        logger.error(
            "Empty summary response for Section %s.%s",
            chapter_id,
            section_id,
        )
        return SummaryResult(failed=True)

    return SummaryResult(summary=summary)


def buildChapterContent(chapter: dict[str, Any], chapter_chunks: str) -> str:
    """Assemble the legacy chapter input from section summaries and chunks.

    Args:
        chapter: Enriched chapter dictionary with section summaries.
        chapter_chunks: Chapter-level text outside any section.

    Returns:
        The combined ``Section Summaries`` and ``Chapter Content`` block.
    """
    section_summaries = [
        f'Section {section_id} ({section["name"]}): '
        f'{section.get("summary", "")}'
        for section_id, section in chapter.get("sections", {}).items()
    ]
    all_content = ""

    if section_summaries:
        all_content += (
            "Section Summaries:\n"
            + SECTION_SUMMARY_SEPARATOR.join(section_summaries)
            + "\n\n"
        )

    if chapter_chunks:
        all_content += "Chapter Content:\n" + chapter_chunks

    return all_content


def summarizeChapter(
    client: Any,
    settings: Settings,
    skills: Any,
    chapter_id: str,
    chapter: dict[str, Any],
) -> SummaryResult:
    """Generate the summary for one chapter.

    Args:
        client: OpenAI-compatible client.
        settings: Resolved endpoint settings.
        skills: A ``Skills`` instance.
        chapter_id: Chapter number as a string.
        chapter: Enriched chapter dictionary.

    Returns:
        The chapter summary, the deterministic fallback, or an empty failure.
    """
    chapter_name = chapter["name"]

    try:
        chapter_chunks = skills.getChapterLevelContents(chapter_id)
    except Exception:
        logger.exception(
            "Failed to retrieve chapter-level content for Chapter %s",
            chapter_id,
        )
        chapter_chunks = ""

    all_content = buildChapterContent(chapter, chapter_chunks)

    if not all_content.strip():
        return SummaryResult(
            summary=f"No content available for {chapter_name}"
        )

    user_content = f"Chapter: {chapter_name}\n\n{all_content}"

    try:
        summary = requestSummary(
            client,
            settings,
            CHAPTER_PROMPT,
            user_content,
        )
    except Exception:
        logger.exception(
            "Failed to generate summary for Chapter %s", chapter_id
        )
        return SummaryResult(failed=True)

    if not summary:
        logger.error("Empty summary response for Chapter %s", chapter_id)
        return SummaryResult(failed=True)

    return SummaryResult(summary=summary)


def hasSummary(metadata: dict[str, Any]) -> bool:
    """Check whether an enriched ToC entry already has a summary.

    Args:
        metadata: Chapter or section dictionary.

    Returns:
        True when ``summary`` contains non-whitespace text.
    """
    return bool(str(metadata.get("summary", "")).strip())


def generateSectionSummaries(
    pdf_name: str,
    enriched_toc: dict[str, Any],
    skills: Any,
    client: Any,
    settings: Settings,
    force: bool,
) -> GenerationStats:
    """Generate and persist every missing section summary.

    Args:
        pdf_name: PDF file name without its extension.
        enriched_toc: Enriched ToC dictionary, mutated in place.
        skills: A ``Skills`` instance.
        client: OpenAI-compatible client.
        settings: Resolved endpoint settings.
        force: Regenerate summaries that already exist.

    Returns:
        Section counters for this run.
    """
    stats = GenerationStats()

    for chapter_id in sortChapterIds(enriched_toc["chapters"]):
        chapter = enriched_toc["chapters"][chapter_id]

        for section_id, section in chapter.get("sections", {}).items():
            if not force and hasSummary(section):
                logger.info(
                    "Skipping existing summary for Section %s.%s",
                    chapter_id,
                    section_id,
                )
                stats.skipped_sections += 1
                continue

            logger.info(
                "Generating summary for Section %s.%s: %s",
                chapter_id,
                section_id,
                section["name"],
            )
            result = summarizeSection(
                client,
                settings,
                skills,
                chapter_id,
                section_id,
            )

            if result.failed:
                stats.failed_tasks += 1
                continue

            section["summary"] = result.summary
            writeEnrichedToc(pdf_name, enriched_toc)
            stats.generated_sections += 1

    return stats


def generateChapterSummaries(
    pdf_name: str,
    enriched_toc: dict[str, Any],
    skills: Any,
    client: Any,
    settings: Settings,
    force: bool,
    stats: GenerationStats,
) -> GenerationStats:
    """Generate and persist every missing chapter summary.

    Args:
        pdf_name: PDF file name without its extension.
        enriched_toc: Enriched ToC dictionary, mutated in place.
        skills: A ``Skills`` instance.
        client: OpenAI-compatible client.
        settings: Resolved endpoint settings.
        force: Regenerate summaries that already exist.
        stats: Counters updated in place.

    Returns:
        The updated counters.
    """
    for chapter_id in sortChapterIds(enriched_toc["chapters"]):
        chapter = enriched_toc["chapters"][chapter_id]

        if not force and hasSummary(chapter):
            logger.info(
                "Skipping existing summary for Chapter %s", chapter_id
            )
            stats.skipped_chapters += 1
            continue

        logger.info(
            "Generating summary for Chapter %s: %s",
            chapter_id,
            chapter["name"],
        )
        result = summarizeChapter(
            client,
            settings,
            skills,
            chapter_id,
            chapter,
        )

        if result.failed:
            stats.failed_tasks += 1
            continue

        chapter["summary"] = result.summary
        writeEnrichedToc(pdf_name, enriched_toc)
        stats.generated_chapters += 1

    return stats


def generateSummaries(
    pdf_name: str,
    enriched_toc: dict[str, Any],
    skills: Any,
    client: Any,
    settings: Settings,
    force: bool,
) -> GenerationStats:
    """Generate section summaries first, then chapter summaries.

    Args:
        pdf_name: PDF file name without its extension.
        enriched_toc: Enriched ToC dictionary, mutated in place.
        skills: A ``Skills`` instance.
        client: OpenAI-compatible client.
        settings: Resolved endpoint settings.
        force: Regenerate summaries that already exist.

    Returns:
        Counters for the completed run.
    """
    stats = generateSectionSummaries(
        pdf_name,
        enriched_toc,
        skills,
        client,
        settings,
        force,
    )

    return generateChapterSummaries(
        pdf_name,
        enriched_toc,
        skills,
        client,
        settings,
        force,
        stats,
    )


def installSignalHandlers(pdf_name: str) -> None:
    """Release the lock and exit when SIGINT or SIGTERM arrives.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        None.
    """

    def handleSignal(signum: int, _frame: Any) -> None:
        """Release the summary lock and terminate the process.

        Args:
            signum: Received signal number.
            _frame: Unused stack frame.

        Returns:
            None.

        Raises:
            SystemExit: Always, with the conventional signal exit code.
        """
        logger.warning(
            "Received signal %s; releasing summary lock", signum
        )
        pdf_lock.releaseLock(pdf_name)
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGINT, handleSignal)
    signal.signal(signal.SIGTERM, handleSignal)


def logRunHeader(pdf_name: str, force: bool) -> None:
    """Log a header that separates runs inside the shared log file.

    Args:
        pdf_name: PDF file name without its extension.
        force: Whether existing summaries are regenerated.

    Returns:
        None.
    """
    logger.info(
        "Starting summary generation for %s (pid=%s, force=%s) at %s",
        pdf_name,
        os.getpid(),
        force,
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


def logRunFooter(pdf_name: str, stats: GenerationStats) -> None:
    """Log the completion counts for one generation run.

    Args:
        pdf_name: PDF file name without its extension.
        stats: Counters for the finished run.

    Returns:
        None.
    """
    log_method = logger.error if stats.failed_tasks else logger.info
    log_method(
        "Finished summary generation for %s: sections generated=%s "
        "skipped=%s, chapters generated=%s skipped=%s, failures=%s",
        pdf_name,
        stats.generated_sections,
        stats.skipped_sections,
        stats.generated_chapters,
        stats.skipped_chapters,
        stats.failed_tasks,
    )


def runGeneration(
    pdf_name: str,
    force: bool,
    install_handlers: bool = True,
) -> int:
    """Run one full summary generation under the per-PDF lock.

    Args:
        pdf_name: PDF file name without its extension.
        force: Regenerate summaries that already exist.
        install_handlers: Install SIGINT/SIGTERM cleanup handlers.

    Returns:
        Zero when every task succeeded, otherwise one.
    """
    settings = loadSettings()
    pdf_lock.acquireLock(pdf_name)

    try:
        if install_handlers:
            installSignalHandlers(pdf_name)

        logRunHeader(pdf_name, force)
        enriched_toc = loadEnrichedToc(pdf_name)
        skills = openSkills(pdf_name, enriched_toc)
        client = buildClient(settings)
        stats = generateSummaries(
            pdf_name,
            enriched_toc,
            skills,
            client,
            settings,
            force,
        )
        logRunFooter(pdf_name, stats)
    finally:
        pdf_lock.releaseLock(pdf_name)

    return 0 if stats.failed_tasks == 0 else 1


@click.command(
    name="summary_generator",
    help="Generate hierarchical summaries for one PDF in the background.",
)
@click.argument("pdf_name")
@click.option(
    "--force",
    is_flag=True,
    help="Regenerate summaries that already exist.",
)
def summaryCommand(pdf_name: str, force: bool) -> None:
    """Generate summaries for PDF_NAME from the configured endpoint.

    Args:
        pdf_name: PDF file name without its extension.
        force: Regenerate existing summaries.

    Returns:
        None.

    Raises:
        SystemExit: With code one when any summary task failed.
    """
    setupLogging()
    exit_code = runGeneration(pdf_name, force)

    if exit_code != 0:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    summaryCommand()
