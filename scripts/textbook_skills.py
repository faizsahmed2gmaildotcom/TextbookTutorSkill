"""Textbook retrieval skills backed by an enriched ToC and LanceDB table."""

import base64
from typing import Any

import config
from logging_config import getLogger

logger = getLogger(__name__)


def buildSummaryRerunCommand(pdf_name: str) -> str:
    """Build the background command that resumes summary generation.

    Args:
        pdf_name: PDF file name without its extension.

    Returns:
        A detached ``nohup`` command writing to the PDF's log file.
    """
    return (
        f'mkdir -p logs && nohup python '
        f'"{config.getSummaryGeneratorPath()}" "{pdf_name}" '
        f'>> "logs/{pdf_name}.log" 2>&1 &'
    )


def raiseForEmptySummaries(book_name: str, missing: list[str]) -> None:
    """Raise when requested summaries have not been generated yet.

    Args:
        book_name: PDF file name without its extension.
        missing: Human-readable chapter and section labels.

    Returns:
        None.

    Raises:
        RuntimeError: Always, naming the missing entries and rerun command.
    """
    raise RuntimeError(
        f"Summary generation is incomplete for '{book_name}': missing "
        f"{', '.join(missing)}. Rerun: {buildSummaryRerunCommand(book_name)}"
    )


class Skills:
    """Retrieval operations exposed to the textbook-tutor workflow."""

    def __init__(self, book_name: str, book_data: dict[str, Any], lancedb_table: Any):
        """Store the book identity, enriched ToC data, and LanceDB table.

        Args:
            book_name: PDF file name without its extension.
            book_data: Enriched ToC dictionary with summaries.
            lancedb_table: Open LanceDB table for this book.
        """
        self.book_name = book_name
        self.book_data = book_data
        self.lancedb_table = lancedb_table

    def getChapterSummaries(self) -> str:
        """Render every chapter summary with page ranges.

        Args:
            None.

        Returns:
            A newline-joined summary block for all chapters.

        Raises:
            RuntimeError: If any chapter summary is empty.
        """
        logger.info("Getting chapter summaries for %s", self.book_name)

        missing_summaries = [
            f"Chapter {chapter_id}"
            for chapter_id, chapter_meta in self.book_data["chapters"].items()
            if not str(chapter_meta.get("summary", "")).strip()
        ]

        if missing_summaries:
            raiseForEmptySummaries(self.book_name, missing_summaries)

        extracted_summaries: list[str] = []

        for chapter_id, chapter_meta in self.book_data["chapters"].items():
            # Legacy parity: the comma after the newline matches the POC output.
            extracted_summaries.append(
                f'--- "{chapter_meta["name"]}" : CHAPTER {chapter_id} : '
                f'PAGES {chapter_meta["start"]}-{chapter_meta["end"]} ---\n,'
                f'{chapter_meta["summary"]}\n\n'
            )

        return "\n".join(extracted_summaries).strip()

    def getSectionSummaries(self, chapters: list[int]) -> str:
        """Render section summaries for selected chapters.

        Args:
            chapters: Integer chapter numbers to include.

        Returns:
            A newline-joined summary block; unknown chapters are skipped.

        Raises:
            RuntimeError: If any requested chapter or section summary is empty.
        """
        logger.info(
            "Getting section summaries for chapters %s",
            ", ".join(str(chapter) for chapter in chapters),
        )

        missing_summaries: list[str] = []

        for chapter_id in (str(chapter) for chapter in chapters):
            if chapter_id not in self.book_data["chapters"]:
                logger.warning("Data for chapter %s not found", chapter_id)
                continue

            chapter_meta = self.book_data["chapters"][chapter_id]

            if not str(chapter_meta.get("summary", "")).strip():
                missing_summaries.append(f"Chapter {chapter_id}")

            for section_id, section_meta in chapter_meta["sections"].items():
                if not str(section_meta.get("summary", "")).strip():
                    missing_summaries.append(
                        f"Section {chapter_id}.{section_id}"
                    )

        if missing_summaries:
            raiseForEmptySummaries(self.book_name, missing_summaries)

        extracted_summaries: list[str] = []

        for chapter_id in (str(chapter) for chapter in chapters):
            if chapter_id not in self.book_data["chapters"]:
                logger.warning("Data for chapter %s not found", chapter_id)
                continue

            chapter_meta = self.book_data["chapters"][chapter_id]
            extracted_summaries.append(
                f'--- "{chapter_meta["name"]}" : CHAPTER {chapter_id} : '
                f'PAGES {chapter_meta["start"]}-{chapter_meta["end"]} ---\n'
            )

            for section_id, section_meta in chapter_meta["sections"].items():
                extracted_summaries.append(
                    f'-- "{section_meta["name"]}" : SECTION {section_id} : '
                    f'PAGES {section_meta["start"]}-{section_meta["end"]} --\n'
                    f'{section_meta["summary"]}\n'
                )

            extracted_summaries.append("")

        return "\n".join(extracted_summaries).strip()

    def getSectionRawContents(
        self, sections: list[tuple[int, int]]
    ) -> list[dict[str, str]]:
        """Render the full raw text and picture captions for selected sections.

        Args:
            sections: ``(chapter_number, section_number)`` pairs.

        Returns:
            One text message item per requested section.

        Raises:
            IndexError: If a requested section has no indexed chunks.
        """
        logger.info(
            "Getting section contents for %s",
            ", ".join(f"{chapter}.{section}" for chapter, section in sections),
        )

        parsed_sections: list[dict[str, str]] = []

        for chapter, section in sections:
            text_rows = (
                self.lancedb_table.search()
                .where(
                    f"chapter_id = '{chapter}' AND section_id = '{section}' "
                    "AND pic_id = ''"
                )
                .to_pandas()
                .sort_values("page_end")
                .reset_index(drop=True)
            )

            if text_rows.empty:
                raise IndexError(f"Section {chapter}.{section} not found!")

            section_start = -1
            section_end = -1
            section_name = ""
            page_text_contents: list[list[str]] = []
            last_header = ""
            last_page = -1

            for row_index, row in text_rows.iterrows():
                if not section_name:
                    section_name = row["section_name"]

                current_page = row["page_end"]

                if last_page != current_page:
                    last_page = current_page
                    page_text_contents.append([f"- PAGE {current_page} -"])

                if row_index == 0:
                    section_start = row["page_start"]
                elif row_index == len(text_rows) - 1:
                    section_end = row["page_end"]

                if row["header"].strip() and row["header"].strip() != last_header:
                    page_text_contents[-1].append("Header: " + row["header"])
                    last_header = row["header"].strip()

                page_text_contents[-1].append(row["text"])

            if not section_name:
                raise IndexError(f"Section {chapter}.{section} not found!")

            picture_rows = (
                self.lancedb_table.search()
                .where(
                    f"chapter_id = '{chapter}' AND section_id = '{section}' "
                    "AND pic_id != ''"
                )
                .to_pandas()
            )

            page_picture_contents: list[list[str]] = [
                [f"\n- PICTURES FOR PAGE {section_start + index} -"]
                for index in range(len(page_text_contents))
            ]

            for _row_index, row in picture_rows.iterrows():
                picture_page_index = row["page_end"] - section_start

                if not 0 <= picture_page_index < len(page_picture_contents):
                    logger.warning(
                        "Picture %s lies outside section %s.%s pages",
                        row["pic_id"],
                        chapter,
                        section,
                    )
                    continue

                page_picture_contents[picture_page_index].append(
                    f'Picture ID: {row["pic_id"]} | '
                    f'Caption: {row["text"] if row["text"] else "N/A"}'
                )

            for text_lines, picture_lines in zip(
                page_text_contents, page_picture_contents
            ):
                if len(picture_lines) > 1:
                    text_lines += picture_lines

            section_info = (
                f'-- "{section_name}" : CHAPTER {chapter} : '
                f"SECTION {section} : PAGES {section_start}-{section_end} --\n\n\n"
            )
            parsed_sections.append(
                {
                    "type": "text",
                    "text": section_info
                    + "\n\n\n".join(
                        "\n".join(page).strip() for page in page_text_contents
                    ),
                }
            )

        return parsed_sections

    def getChapterLevelContents(self, chapter_id: str) -> str:
        """Return raw chapter-level text that sits outside any section.

        Args:
            chapter_id: Chapter number as a string.

        Returns:
            Chapter-level chunk text joined by blank lines.
        """
        chapter_rows = (
            self.lancedb_table.search()
            .where(
                f"chapter_id = '{chapter_id}' AND section_id = '' "
                "AND pic_id = ''"
            )
            .to_pandas()
        )

        chunk_texts = [
            str(text) for text in chapter_rows.get("text", []) if str(text).strip()
        ]

        return "\n\n".join(chunk_texts)

    def getPicturesByID(self, pic_ids: list[int]) -> list[dict[str, str]]:
        """Read pictures by ID and return base64 image payloads.

        Args:
            pic_ids: Picture IDs to read, in the requested order.

        Returns:
            A list of ``{"type": "image_url", "image_url": <base64>}`` items.

        Raises:
            FileNotFoundError: If a requested picture file is missing.
        """
        logger.info(
            "Fetching pictures %s", ", ".join(str(pic_id) for pic_id in pic_ids)
        )

        pictures_dir = config.getPicturesDir(self.book_name)
        encoded_pictures: list[str] = []

        for pic_id in pic_ids:
            picture_path = pictures_dir / f"pic_{pic_id}.png"

            with picture_path.open("rb") as picture_file:
                encoded_pictures.append(
                    base64.b64encode(picture_file.read()).decode("utf-8")
                )

        return [
            {"type": "image_url", "image_url": encoded_picture}
            for encoded_picture in encoded_pictures
        ]
