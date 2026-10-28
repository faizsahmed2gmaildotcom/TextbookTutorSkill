# Query Workflow

This is the legacy `orchestrator.buildTextbookRagAgent` loop expressed as agent instructions. The scripts expose the same four skills; the calling agent decides what to fetch.

## Retrieval Discipline

Use this system prompt as your own operating rule:

```text
You are a textbook research assistant. Your task is to gather section-level textbook information
to answer the user's question.

Follow this multi-step procedure:
1. Call `getChapterSummaries` to inspect all chapter summaries.
2. Select the most relevant chapter numbers (at most 3 chapters)
   and request their section summaries using `getSectionSummaries`.
3. Select the most relevant section numbers (at most 3 sections)
   and request their raw text content using `getSectionRawContents`.
   Optional: If and only if the user's query cannot be answered without analyzing a page's pictures,
   request that page's pictures using `getPicturesByID`.
4. Answer the user's query thoroughly using ONLY the retrieved raw section contents.
```

Never skip to `--secraw` without first reading chapter and section summaries. Never exceed 3 chapters or 3 sections in one turn; make another turn if needed.

## Commands

```text
# 1. All chapter summaries
terminal(command="python \"$SKILL_DIR/scripts/db_skills.py\" <pdf_name> --chapsum")

# 2. Section summaries for selected chapters
terminal(command="python \"$SKILL_DIR/scripts/db_skills.py\" <pdf_name> --secsum 1,2,3")

# 3. Raw text for selected sections
terminal(command="python \"$SKILL_DIR/scripts/db_skills.py\" <pdf_name> --secraw 2.1,2.3")

# 4. Pictures by ID (base64 payloads)
terminal(command="python \"$SKILL_DIR/scripts/db_skills.py\" <pdf_name> --picid 4,9")
```

Exactly one option per invocation. Values are comma-separated with no spaces required; spaces around entries are accepted.

## Output Shapes

`--chapsum` and `--secsum` print plain text blocks:

```text
--- "Chapter Name" : CHAPTER 1 : PAGES 11-17 ---
<summary>

-- "Section Name" : SECTION 1 : PAGES 11-13 --
<summary>
```

`--secraw` prints a JSON list. Each entry contains a section header plus page blocks:

```json
[
  {
    "type": "text",
    "text": "-- \"Section Name\" : CHAPTER 2 : SECTION 1 : PAGES 18-24 --\n\n\n- PAGE 18 -\nHeader: ...\n<text>\n\n- PICTURES FOR PAGE 18 -\nPicture ID: 4 | Caption: ..."
  }
]
```

`--picid` prints `[{"type": "image_url", "image_url": "<base64>"}, ...]`, preserving the legacy shape and request order.

## Answering

- Ground every claim in the raw section text. If the retrieved text does not answer the question, say so and name the closest sections.
- Cite chapter and page range, for example `Sorting and Searching (pp. 45-57)` or `Section 2.1, "Language Features" (pp. 18-24)`.
- Treat `PAGES x-y` as physical 1-indexed PDF pages.
- Do not quote entire sections; summarize and cite.
- If two sections disagree, state both with citations.

## Pictures

`--picid` base64 payloads are large and only work when the agent can accept image content. Prefer:

1. Find the `Picture ID` and page in `--secraw` output.
2. Run `vision_analyze` on `pictures/<pdf_name>/pic_<id>.png`.
3. Cite the picture page and section.

Use pictures only when the question is about a figure, diagram, chart, or visual layout. Text questions should stay text-only.

## Error Handling

- `RuntimeError: Summary generation is already running` means the background generator owns `toc_enriched/.<pdf name>.lock`. Do not retry in a loop and do not delete the lock. Tell the user generation is still running and show `tail -n 20 "logs/<pdf name>.log"`.
- `RuntimeError: Summary generation is incomplete` means `--chapsum` or `--secsum` found an empty summary. Relaunch the background generator command from `references/summary-generation.md`; completed summaries are skipped.
- `FileNotFoundError` for `toc_enriched/<pdf_name>.json` or `lancedb_data/<pdf_name>.lance` means the pipeline is incomplete; return to the preprocessing/indexing steps.
- `IndexError` / `ClickException: Section c.s not found` means the requested section has no indexed chunks. Retry with a neighbouring section.
- A `click.UsageError` means zero or multiple options were passed; pass exactly one.
