"""CLI: query textbook summaries, raw sections, and pictures."""

import json

import click

import config
import lancedb_store
from logging_config import setupLogging
from schemas import EnrichedToc
from textbook_skills import Skills


def parsePositiveIntList(
    value: str,
    param_hint: str,
) -> list[int]:
    """Parse a comma-separated list of positive integers.

    Args:
        value: Raw option value such as ``1,2,3``.
        param_hint: Option name used in error messages.

    Returns:
        Parsed integers in the requested order.

    Raises:
        click.BadParameter: If any entry is not a positive integer.
    """
    parsed: list[int] = []

    for part in value.split(","):
        part = part.strip()

        if not part:
            raise click.BadParameter(
                "expected a comma-separated list of positive integers",
                param_hint=param_hint,
            )

        try:
            number = int(part)
        except ValueError as exc:
            raise click.BadParameter(
                f"{part!r} is not an integer", param_hint=param_hint
            ) from exc

        if number < 1:
            raise click.BadParameter(
                f"{part!r} must be positive", param_hint=param_hint
            )

        parsed.append(number)

    return parsed


def parseSectionPairList(value: str) -> list[tuple[int, int]]:
    """Parse a comma-separated list of ``chapter.section`` pairs.

    Args:
        value: Raw option value such as ``1.1,3.2``.

    Returns:
        ``(chapter_number, section_number)`` pairs.

    Raises:
        click.BadParameter: If any entry is not a positive ``chapter.section``.
    """
    parsed: list[tuple[int, int]] = []

    for part in value.split(","):
        part = part.strip()
        id_parts = part.split(".")

        if len(id_parts) != 2:
            raise click.BadParameter(
                f"{part!r} is not a chapter.section pair",
                param_hint="--secraw",
            )

        try:
            chapter_number = int(id_parts[0])
            section_number = int(id_parts[1])
        except ValueError as exc:
            raise click.BadParameter(
                f"{part!r} is not a chapter.section pair",
                param_hint="--secraw",
            ) from exc

        if chapter_number < 1 or section_number < 1:
            raise click.BadParameter(
                f"{part!r} must contain positive numbers",
                param_hint="--secraw",
            )

        parsed.append((chapter_number, section_number))

    return parsed


def countSelectedOptions(
    chapsum: bool,
    secsum: str | None,
    secraw: str | None,
    picid: str | None,
) -> int:
    """Count how many mutually exclusive query options were supplied.

    Args:
        chapsum: Whether ``--chapsum`` was set.
        secsum: Raw ``--secsum`` value.
        secraw: Raw ``--secraw`` value.
        picid: Raw ``--picid`` value.

    Returns:
        Number of selected options.
    """
    return (
        int(bool(chapsum))
        + int(secsum is not None)
        + int(secraw is not None)
        + int(picid is not None)
    )


@click.command(
    name="db_skills",
    help="Query a textbook through exactly one retrieval option.",
)
@click.argument("pdf_name")
@click.option("--chapsum", is_flag=True, help="Print every chapter summary.")
@click.option("--secsum", help="Comma-separated chapter numbers, e.g. 1,2,3.")
@click.option("--secraw", help="Comma-separated chapter.section pairs, e.g. 1.1,3.2.")
@click.option("--picid", help="Comma-separated picture IDs, e.g. 1,2,3.")
def skillsCommand(
    pdf_name: str,
    chapsum: bool,
    secsum: str | None,
    secraw: str | None,
    picid: str | None,
) -> None:
    """Query PDF_NAME through exactly one retrieval option.

    Args:
        pdf_name: PDF file name without its extension.
        chapsum: Print chapter summaries.
        secsum: Chapter numbers for section summaries.
        secraw: Section pairs for raw section content.
        picid: Picture IDs to return as base64 payloads.

    Returns:
        None.

    Raises:
        click.UsageError: If zero or multiple options are supplied.
        FileNotFoundError: If the enriched ToC or LanceDB table is missing.
    """
    setupLogging()

    selected_count = countSelectedOptions(chapsum, secsum, secraw, picid)

    if selected_count != 1:
        raise click.UsageError(
            "Specify exactly one of --chapsum, --secsum, --secraw, or --picid."
        )

    enriched_path = config.getEnrichedTocPath(pdf_name)
    table_dir = config.getLanceTableDir(pdf_name)

    if not enriched_path.exists():
        raise FileNotFoundError(f"Enriched ToC not found: {enriched_path}")

    if not table_dir.exists():
        raise FileNotFoundError(f"LanceDB table not found: {table_dir}")

    chapter_numbers = (
        parsePositiveIntList(secsum, "--secsum") if secsum is not None else []
    )
    section_pairs = (
        parseSectionPairList(secraw) if secraw is not None else []
    )
    picture_ids = (
        parsePositiveIntList(picid, "--picid") if picid is not None else []
    )

    enriched_toc = json.loads(enriched_path.read_text(encoding="utf-8"))
    EnrichedToc.model_validate(enriched_toc)
    table = lancedb_store.openLanceDbTable(
        config.LANCEDB_DATA_DIR, config.getLanceTableName(pdf_name)
    )
    skills = Skills(pdf_name, enriched_toc, table)

    if chapsum:
        click.echo(skills.getChapterSummaries())
        return

    if secsum is not None:
        click.echo(skills.getSectionSummaries(chapter_numbers))
        return

    if secraw is not None:
        try:
            section_contents = skills.getSectionRawContents(section_pairs)
        except IndexError as exc:
            raise click.ClickException(str(exc)) from exc

        click.echo(
            json.dumps(section_contents, ensure_ascii=False, indent=2)
        )
        return

    pictures = skills.getPicturesByID(picture_ids)
    click.echo(json.dumps(pictures, ensure_ascii=False))


if __name__ == "__main__":
    skillsCommand()
