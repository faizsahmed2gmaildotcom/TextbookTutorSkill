"""Tests for hierarchical summary generation and lock lifecycle."""

import json
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import summary_generator


class FakeCompletions:
    """Scripted chat-completions endpoint."""

    def __init__(self, responses: list[object]):
        """Store the responses returned by successive calls.

        Args:
            responses: Reply text, exceptions, or empty strings.
        """
        self.responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        """Record one request and return the next scripted response.

        Args:
            **kwargs: Chat-completions request arguments.

        Returns:
            A completion-shaped object.

        Raises:
            Exception: The next scripted exception, when configured.
        """
        self.calls.append(kwargs)
        response = self.responses.pop(0)

        if isinstance(response, Exception):
            raise response

        choices = []

        if response:
            choices = [
                SimpleNamespace(
                    message=SimpleNamespace(content=str(response))
                )
            ]

        return SimpleNamespace(choices=choices)


class FakeClient:
    """Client wrapper exposing ``chat.completions.create``."""

    def __init__(self, responses: list[object]):
        """Build a scripted client.

        Args:
            responses: Reply text, exceptions, or empty strings.
        """
        self.chat = SimpleNamespace(completions=FakeCompletions(responses))


class FakeSkills:
    """Deterministic stand-in for ``Skills`` content retrieval."""

    def __init__(
        self,
        section_texts: dict[str, str],
        chapter_texts: dict[str, str],
    ):
        """Store fake section and chapter text sources.

        Args:
            section_texts: ``"chapter.section"`` to text mapping.
            chapter_texts: Chapter ID to chapter-level text mapping.
        """
        self.section_texts = section_texts
        self.chapter_texts = chapter_texts

    def getSectionRawContents(self, sections: list[tuple[int, int]]):
        """Return one section's fake text or raise IndexError.

        Args:
            sections: One ``(chapter, section)`` pair.

        Returns:
            One text item.
        """
        chapter_id, section_id = sections[0]
        key = f"{chapter_id}.{section_id}"

        if key not in self.section_texts:
            raise IndexError(f"Section {key} not found!")

        return [{"type": "text", "text": self.section_texts[key]}]

    def getChapterLevelContents(self, chapter_id: str) -> str:
        """Return fake chapter-level text.

        Args:
            chapter_id: Chapter number as a string.

        Returns:
            Chapter text, or an empty string.
        """
        return self.chapter_texts.get(chapter_id, "")


def buildSettings() -> summary_generator.Settings:
    """Build test endpoint settings.

    Args:
        None.

    Returns:
        Deterministic settings.
    """
    return summary_generator.Settings(
        base_url="http://localhost:9/v1",
        api_key="test-key",
        model="test-model",
        timeout_seconds=5,
        max_retries=0,
        temperature=0.2,
    )


def buildEnrichedToc() -> dict:
    """Build a one-chapter enriched ToC with two sections.

    Args:
        None.

    Returns:
        Enriched ToC dictionary without summaries.
    """
    return {
        "chapters": {
            "1": {
                "name": "Chapter One",
                "start": 11,
                "end": 17,
                "summary": "",
                "sections": {
                    "1": {
                        "name": "Alpha",
                        "start": 11,
                        "end": 13,
                        "summary": "",
                    },
                    "2": {
                        "name": "Beta",
                        "start": 13,
                        "end": 17,
                        "summary": "",
                    },
                },
            }
        }
    }


def writeEnrichedToc(enriched_toc: dict) -> None:
    """Write an enriched ToC fixture for ``book``.

    Args:
        enriched_toc: Enriched ToC dictionary.

    Returns:
        None.
    """
    enriched_path = config.getEnrichedTocPath("book")
    enriched_path.parent.mkdir(parents=True, exist_ok=True)
    enriched_path.write_text(json.dumps(enriched_toc), encoding="utf-8")


def loadWrittenToc() -> dict:
    """Read the enriched ToC fixture for ``book``.

    Args:
        None.

    Returns:
        Parsed enriched ToC.
    """
    return json.loads(
        config.getEnrichedTocPath("book").read_text(encoding="utf-8")
    )


def testGenerateSummariesWritesSectionsThenChapter(data_root: Path) -> None:
    """Sections are summarized before the chapter that consumes them."""
    enriched_toc = buildEnrichedToc()
    writeEnrichedToc(enriched_toc)
    skills = FakeSkills(
        section_texts={"1.1": "Alpha raw", "1.2": "Beta raw"},
        chapter_texts={"1": "Chapter overview"},
    )
    client = FakeClient(
        ["Alpha summary", "Beta summary", "Chapter summary"]
    )
    settings = buildSettings()

    stats = summary_generator.generateSummaries(
        "book",
        enriched_toc,
        skills,
        client,
        settings,
        force=False,
    )

    written = loadWrittenToc()
    completions = client.chat.completions

    assert stats.generated_sections == 2
    assert stats.generated_chapters == 1
    assert stats.failed_tasks == 0
    assert written["chapters"]["1"]["sections"]["1"]["summary"] == "Alpha summary"
    assert written["chapters"]["1"]["sections"]["2"]["summary"] == "Beta summary"
    assert written["chapters"]["1"]["summary"] == "Chapter summary"
    assert completions.calls[0]["messages"][0]["content"] == (
        summary_generator.SECTION_PROMPT
    )
    assert completions.calls[0]["messages"][1]["content"] == "Alpha raw"
    assert completions.calls[2]["messages"][0]["content"] == (
        summary_generator.CHAPTER_PROMPT
    )
    assert completions.calls[2]["messages"][1]["content"] == (
        "Chapter: Chapter One\n\n"
        "Section Summaries:\n"
        "Section 1 (Alpha): Alpha summary\n"
        "---\n"
        "Section 2 (Beta): Beta summary\n\n"
        "Chapter Content:\n"
        "Chapter overview"
    )
    assert completions.calls[0]["temperature"] == 0.2
    assert completions.calls[0]["model"] == "test-model"


def testGenerateSummariesSkipsExisting(data_root: Path) -> None:
    """Non-empty summaries are skipped unless ``--force`` is set."""
    enriched_toc = buildEnrichedToc()
    enriched_toc["chapters"]["1"]["summary"] = "Existing chapter"
    enriched_toc["chapters"]["1"]["sections"]["1"]["summary"] = "Existing section"
    writeEnrichedToc(enriched_toc)
    skills = FakeSkills(
        section_texts={"1.1": "Alpha raw", "1.2": "Beta raw"},
        chapter_texts={"1": "Chapter overview"},
    )
    client = FakeClient(["Beta summary"])
    settings = buildSettings()

    stats = summary_generator.generateSummaries(
        "book",
        enriched_toc,
        skills,
        client,
        settings,
        force=False,
    )

    written = loadWrittenToc()

    assert stats.skipped_sections == 1
    assert stats.skipped_chapters == 1
    assert stats.generated_sections == 1
    assert stats.generated_chapters == 0
    assert len(client.chat.completions.calls) == 1
    assert written["chapters"]["1"]["sections"]["1"]["summary"] == "Existing section"
    assert written["chapters"]["1"]["sections"]["2"]["summary"] == "Beta summary"
    assert written["chapters"]["1"]["summary"] == "Existing chapter"


def testForceRegeneratesEverySummary(data_root: Path) -> None:
    """``--force`` replaces even non-empty summaries."""
    enriched_toc = buildEnrichedToc()
    enriched_toc["chapters"]["1"]["summary"] = "Old chapter"
    enriched_toc["chapters"]["1"]["sections"]["1"]["summary"] = "Old alpha"
    enriched_toc["chapters"]["1"]["sections"]["2"]["summary"] = "Old beta"
    writeEnrichedToc(enriched_toc)
    skills = FakeSkills(
        section_texts={"1.1": "Alpha raw", "1.2": "Beta raw"},
        chapter_texts={"1": "Chapter overview"},
    )
    client = FakeClient(
        ["New alpha", "New beta", "New chapter"]
    )
    settings = buildSettings()

    stats = summary_generator.generateSummaries(
        "book",
        enriched_toc,
        skills,
        client,
        settings,
        force=True,
    )

    written = loadWrittenToc()

    assert stats.generated_sections == 2
    assert stats.generated_chapters == 1
    assert stats.skipped_sections == 0
    assert stats.skipped_chapters == 0
    assert written["chapters"]["1"]["sections"]["1"]["summary"] == "New alpha"
    assert written["chapters"]["1"]["sections"]["2"]["summary"] == "New beta"
    assert written["chapters"]["1"]["summary"] == "New chapter"


def testMissingSectionContentUsesFallback(data_root: Path) -> None:
    """Sections without chunks get the deterministic fallback."""
    enriched_toc = buildEnrichedToc()
    writeEnrichedToc(enriched_toc)
    skills = FakeSkills(
        section_texts={"1.2": "Beta raw"},
        chapter_texts={"1": "Chapter overview"},
    )
    client = FakeClient(["Beta summary", "Chapter summary"])
    settings = buildSettings()

    stats = summary_generator.generateSummaries(
        "book",
        enriched_toc,
        skills,
        client,
        settings,
        force=False,
    )

    written = loadWrittenToc()

    assert stats.generated_sections == 2
    assert (
        written["chapters"]["1"]["sections"]["1"]["summary"]
        == "No content available for Section 1.1"
    )


def testMissingChapterContentUsesFallback(data_root: Path) -> None:
    """Chapters without sections or chapter-level text get a fallback."""
    enriched_toc = {
        "chapters": {
            "1": {
                "name": "Empty Chapter",
                "start": 11,
                "end": 17,
                "summary": "",
                "sections": {},
            }
        }
    }
    writeEnrichedToc(enriched_toc)
    skills = FakeSkills(section_texts={}, chapter_texts={})
    client = FakeClient([])
    settings = buildSettings()

    stats = summary_generator.generateSummaries(
        "book",
        enriched_toc,
        skills,
        client,
        settings,
        force=False,
    )

    written = loadWrittenToc()

    assert stats.generated_chapters == 1
    assert stats.failed_tasks == 0
    assert (
        written["chapters"]["1"]["summary"]
        == "No content available for Empty Chapter"
    )


def testApiFailureLeavesSummaryEmptyAndContinues(data_root: Path) -> None:
    """A failed call is logged, leaves the field empty, and does not stop."""
    enriched_toc = buildEnrichedToc()
    writeEnrichedToc(enriched_toc)
    skills = FakeSkills(
        section_texts={"1.1": "Alpha raw", "1.2": "Beta raw"},
        chapter_texts={"1": "Chapter overview"},
    )
    client = FakeClient(
        [RuntimeError("endpoint down"), "Beta summary", "Chapter summary"]
    )
    settings = buildSettings()

    stats = summary_generator.generateSummaries(
        "book",
        enriched_toc,
        skills,
        client,
        settings,
        force=False,
    )

    written = loadWrittenToc()

    assert stats.failed_tasks == 1
    assert stats.generated_sections == 1
    assert stats.generated_chapters == 1
    assert written["chapters"]["1"]["sections"]["1"]["summary"] == ""
    assert written["chapters"]["1"]["sections"]["2"]["summary"] == "Beta summary"


def testEmptyResponseLeavesSummaryEmpty(data_root: Path) -> None:
    """An empty model response counts as a failure, not a summary."""
    enriched_toc = buildEnrichedToc()
    writeEnrichedToc(enriched_toc)
    skills = FakeSkills(
        section_texts={"1.1": "Alpha raw", "1.2": "Beta raw"},
        chapter_texts={"1": "Chapter overview"},
    )
    client = FakeClient(["", "Beta summary", "Chapter summary"])
    settings = buildSettings()

    stats = summary_generator.generateSummaries(
        "book",
        enriched_toc,
        skills,
        client,
        settings,
        force=False,
    )

    assert stats.failed_tasks == 1
    assert loadWrittenToc()["chapters"]["1"]["sections"]["1"]["summary"] == ""


def testPersistenceHappensAfterEverySummary(
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each completed summary is flushed to disk before the next call."""
    enriched_toc = buildEnrichedToc()
    writeEnrichedToc(enriched_toc)
    skills = FakeSkills(
        section_texts={"1.1": "Alpha raw", "1.2": "Beta raw"},
        chapter_texts={"1": "Chapter overview"},
    )
    client = FakeClient(
        ["Alpha summary", "Beta summary", "Chapter summary"]
    )
    settings = buildSettings()
    snapshots: list[dict] = []
    original_writer = summary_generator.writeEnrichedToc

    def recordWrite(pdf_name: str, current_toc: dict) -> None:
        """Persist the ToC and snapshot it for assertions.

        Args:
            pdf_name: PDF file name without its extension.
            current_toc: Enriched ToC dictionary.

        Returns:
            None.
        """
        original_writer(pdf_name, current_toc)
        snapshots.append(loadWrittenToc())

    monkeypatch.setattr(summary_generator, "writeEnrichedToc", recordWrite)

    summary_generator.generateSummaries(
        "book",
        enriched_toc,
        skills,
        client,
        settings,
        force=False,
    )

    assert len(snapshots) == 3
    assert snapshots[0]["chapters"]["1"]["sections"]["1"]["summary"] == "Alpha summary"
    assert snapshots[0]["chapters"]["1"]["sections"]["2"]["summary"] == ""
    assert snapshots[1]["chapters"]["1"]["sections"]["2"]["summary"] == "Beta summary"
    assert snapshots[2]["chapters"]["1"]["summary"] == "Chapter summary"


def testWriteEnrichedTocLeavesNoTemporaryFiles(data_root: Path) -> None:
    """Atomic replacement leaves only the enriched JSON file."""
    writeEnrichedToc(buildEnrichedToc())

    summary_generator.writeEnrichedToc("book", buildEnrichedToc())

    enriched_dir = config.TOC_ENRICHED_DIR
    assert [path.name for path in enriched_dir.iterdir()] == ["book.json"]
    assert loadWrittenToc()["chapters"]["1"]["sections"]["1"]["summary"] == ""


def testBuildChapterContentUsesLegacyTemplate() -> None:
    """The chapter input matches the legacy section-summary template."""
    chapter = buildEnrichedToc()["chapters"]["1"]
    chapter["sections"]["1"]["summary"] = "Alpha summary"
    chapter["sections"]["2"]["summary"] = "Beta summary"

    content = summary_generator.buildChapterContent(chapter, "Chapter overview")

    assert content == (
        "Section Summaries:\n"
        "Section 1 (Alpha): Alpha summary\n"
        "---\n"
        "Section 2 (Beta): Beta summary\n\n"
        "Chapter Content:\n"
        "Chapter overview"
    )


def testRunGenerationReleasesLockOnSuccess(
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful detached run removes its lock and returns zero."""
    writeEnrichedToc(buildEnrichedToc())
    skills = FakeSkills(
        section_texts={"1.1": "Alpha raw", "1.2": "Beta raw"},
        chapter_texts={"1": "Chapter overview"},
    )
    client = FakeClient(
        ["Alpha summary", "Beta summary", "Chapter summary"]
    )

    monkeypatch.setattr(summary_generator, "loadSettings", buildSettings)
    monkeypatch.setattr(
        summary_generator,
        "openSkills",
        lambda _pdf_name, _toc: skills,
    )
    monkeypatch.setattr(
        summary_generator,
        "buildClient",
        lambda _settings: client,
    )

    exit_code = summary_generator.runGeneration(
        "book",
        force=False,
        install_handlers=False,
    )

    assert exit_code == 0
    assert not config.getLockPath("book").exists()
    assert loadWrittenToc()["chapters"]["1"]["summary"] == "Chapter summary"


def testRunGenerationReleasesLockOnException(
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected error still releases the lock."""
    writeEnrichedToc(buildEnrichedToc())
    skills = FakeSkills(section_texts={}, chapter_texts={})

    def failGeneration(*_args, **_kwargs):
        """Raise a generation error.

        Args:
            *_args: Ignored positional arguments.
            **_kwargs: Ignored keyword arguments.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(summary_generator, "loadSettings", buildSettings)
    monkeypatch.setattr(
        summary_generator,
        "openSkills",
        lambda _pdf_name, _toc: skills,
    )
    monkeypatch.setattr(
        summary_generator,
        "buildClient",
        lambda _settings: FakeClient([]),
    )
    monkeypatch.setattr(
        summary_generator,
        "generateSummaries",
        failGeneration,
    )

    with pytest.raises(RuntimeError):
        summary_generator.runGeneration(
            "book",
            force=False,
            install_handlers=False,
        )

    assert not config.getLockPath("book").exists()


def testRunGenerationReturnsOneWhenATaskFails(
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any failed task makes the detached process exit non-zero."""
    writeEnrichedToc(buildEnrichedToc())
    skills = FakeSkills(
        section_texts={"1.1": "Alpha raw", "1.2": "Beta raw"},
        chapter_texts={"1": "Chapter overview"},
    )
    client = FakeClient(
        [RuntimeError("endpoint down"), "Beta summary", "Chapter summary"]
    )

    monkeypatch.setattr(summary_generator, "loadSettings", buildSettings)
    monkeypatch.setattr(
        summary_generator,
        "openSkills",
        lambda _pdf_name, _toc: skills,
    )
    monkeypatch.setattr(
        summary_generator,
        "buildClient",
        lambda _settings: client,
    )

    exit_code = summary_generator.runGeneration(
        "book",
        force=False,
        install_handlers=False,
    )

    assert exit_code == 1
    assert not config.getLockPath("book").exists()


def testInstallSignalHandlersReleaseLock(
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIGINT and SIGTERM handlers release the lock before exiting."""
    pdf_lock_path = summary_generator.pdf_lock.acquireLock("book")
    captured_handlers: dict[int, object] = {}

    def captureSignal(signum: int, handler: object) -> None:
        """Record a registered signal handler.

        Args:
            signum: Signal number.
            handler: Registered handler.

        Returns:
            None.
        """
        captured_handlers[signum] = handler

    monkeypatch.setattr(summary_generator.signal, "signal", captureSignal)

    summary_generator.installSignalHandlers("book")

    assert set(captured_handlers) == {signal.SIGINT, signal.SIGTERM}

    with pytest.raises(SystemExit):
        captured_handlers[signal.SIGTERM](signal.SIGTERM, None)

    assert not pdf_lock_path.exists()


def testLoadSettingsReadsConfiguredFile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All six settings are read from the configured module."""
    settings_path = tmp_path / "settings.py"
    settings_path.write_text(
        "\n".join(
            [
                'OPENAI_BASE_URL = "http://localhost:8000/v1"',
                'OPENAI_API_KEY = "secret"',
                'OPENAI_MODEL = "local-model"',
                "OPENAI_TIMEOUT_SECONDS = 12.5",
                "OPENAI_MAX_RETRIES = 3",
                "OPENAI_TEMPERATURE = 0.4",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "getSettingsPath", lambda: settings_path)

    settings = summary_generator.loadSettings()

    assert settings.base_url == "http://localhost:8000/v1"
    assert settings.api_key == "secret"
    assert settings.model == "local-model"
    assert settings.timeout_seconds == 12.5
    assert settings.max_retries == 3
    assert settings.temperature == 0.4


def testLoadSettingsRejectsMissingFile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing settings file explains how to create it."""
    monkeypatch.setattr(
        config,
        "getSettingsPath",
        lambda: tmp_path / "missing.py",
    )

    with pytest.raises(RuntimeError) as error:
        summary_generator.loadSettings()

    assert "settings-example.py" in str(error.value)


def testLoadSettingsRejectsIncompleteFile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A settings file missing a field fails with the field name."""
    settings_path = tmp_path / "settings.py"
    settings_path.write_text(
        'OPENAI_BASE_URL = "http://localhost:8000/v1"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "getSettingsPath", lambda: settings_path)

    with pytest.raises(RuntimeError) as error:
        summary_generator.loadSettings()

    assert "OPENAI_API_KEY" in str(error.value)
