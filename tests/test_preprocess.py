"""Tests for preprocess path validation, ToC-page parsing, and orchestration."""

from pathlib import Path

import click
import pytest
from click.testing import CliRunner

import config
import preprocess
from schemas import PdfMetadata


def testResolvePdfPathMissing(tmp_path: Path) -> None:
    """A missing PDF path raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        preprocess.resolvePdfPath(str(tmp_path / "missing.pdf"))


def testResolvePdfPathRejectsNonPdf(tmp_path: Path) -> None:
    """A non-PDF extension raises FileNotFoundError."""
    text_path = tmp_path / "notes.txt"
    text_path.write_text("not a pdf", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        preprocess.resolvePdfPath(str(text_path))


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("3-5", [3, 4, 5]),
        ("3,4,5", [3, 4, 5]),
        ("3-5,7", [3, 4, 5, 7]),
        ("5, 5, 4", [4, 5]),
    ],
)
def testParseTocPagesValid(raw_value: str, expected: list[int]) -> None:
    """Ranges and comma lists expand into sorted unique pages."""
    assert preprocess.parseTocPages(None, None, raw_value) == expected


@pytest.mark.parametrize(
    "raw_value",
    ["", "0", "-3", "5-3", "abc", "3,,4", "3-"],
)
def testParseTocPagesInvalid(raw_value: str) -> None:
    """Malformed page specifications raise click.BadParameter."""
    with pytest.raises(click.BadParameter):
        preprocess.parseTocPages(None, None, raw_value)


def testPreprocessSkipsWhenOutputsExist(
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing outputs short-circuit preprocessing without conversion."""
    pdf_path = data_root / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    config.getChunksPath("book").parent.mkdir(parents=True)
    config.getChunksPath("book").write_text("{}\n", encoding="utf-8")
    config.getMetadataPath("book").write_text("{}", encoding="utf-8")
    config.getRawTocPath("book").parent.mkdir(parents=True)
    config.getRawTocPath("book").write_text("toc", encoding="utf-8")
    config.getPicturesDir("book").mkdir(parents=True, exist_ok=True)

    def failConversion(*_args, **_kwargs):
        """Fail the test if PDF conversion is attempted.

        Args:
            *_args: Ignored positional arguments.
            **_kwargs: Ignored keyword arguments.

        Returns:
            None.

        Raises:
            AssertionError: Always.
        """
        raise AssertionError("conversion should not run")

    monkeypatch.setattr(preprocess.pdf_chunks, "convertPdf", failConversion)

    preprocess.preprocessPdf(str(pdf_path))

    assert config.getRawTocPath("book").read_text(encoding="utf-8") == "toc"


def testPreprocessReusesChunksWithTocOverride(
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--toc-pages reuses chunks while re-extracting the ToC and metadata."""
    pdf_path = data_root / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    config.getChunksPath("book").parent.mkdir(parents=True)
    config.getChunksPath("book").write_text("{}\n", encoding="utf-8")
    config.getPicturesDir("book").mkdir(parents=True, exist_ok=True)

    recorded: dict = {}

    def failConversion(*_args, **_kwargs):
        """Fail the test if PDF conversion is attempted.

        Args:
            *_args: Ignored positional arguments.
            **_kwargs: Ignored keyword arguments.

        Returns:
            None.

        Raises:
            AssertionError: Always.
        """
        raise AssertionError("conversion should not run")

    monkeypatch.setattr(preprocess.pdf_chunks, "convertPdf", failConversion)
    monkeypatch.setattr(
        preprocess.pdf_chunks,
        "readPdfMetadata",
        lambda _path: (20, [str(index) for index in range(1, 21)]),
    )
    monkeypatch.setattr(
        preprocess.pdf_chunks,
        "getTocContent",
        lambda _path, pages: f"pages:{pages}",
    )
    monkeypatch.setattr(
        preprocess.pdf_chunks,
        "writeRawToc",
        lambda name, content: recorded.setdefault("toc", (name, content)),
    )
    monkeypatch.setattr(
        preprocess.pdf_chunks,
        "writeMetadata",
        lambda *args: recorded.setdefault("meta", args),
    )

    preprocess.preprocessPdf(str(pdf_path), toc_pages=[3, 4])

    assert recorded["toc"] == ("book", "pages:[3, 4]")
    assert recorded["meta"][2] == 20
    assert recorded["meta"][4] == [3, 4]


def testPreprocessFailsWithoutDetectedToc(
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing ToC pages produce an actionable ClickException."""
    pdf_path = data_root / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(
        preprocess.pdf_chunks, "convertPdf", lambda _path: object()
    )
    monkeypatch.setattr(
        preprocess.pdf_chunks, "exportChunks", lambda *_args: None
    )
    monkeypatch.setattr(preprocess.pdf_chunks, "getTocPages", lambda _doc: [])

    with pytest.raises(click.ClickException) as error:
        preprocess.preprocessPdf(str(pdf_path))

    assert "--toc-pages" in str(error.value)


def testPreprocessCommandPrintsContractMessage(
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI prints the exact completion message."""
    pdf_path = data_root / "book.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(preprocess, "preprocessPdf", lambda *_args, **_kwargs: None)

    result = CliRunner().invoke(preprocess.preprocessCommand, [str(pdf_path)])

    assert result.exit_code == 0
    assert result.output.strip() == "Raw chunks and ToC contents successfully saved"


def testMetadataModelRoundTrip(guide_metadata: dict) -> None:
    """The metadata fixture matches the PdfMetadata contract."""
    metadata = PdfMetadata.model_validate(guide_metadata)

    assert len(metadata.page_labels) == metadata.page_count
    assert metadata.toc_offset == metadata.toc_pages[-1]
