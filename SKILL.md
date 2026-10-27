---
name: textbook-tutor
description: Tutor from textbook PDFs with cited section answers.
version: 0.1.0
author: Faiz, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Education, PDF, RAG, Textbook, Tutoring]
    related_skills: [pdf]
---

# Textbook Tutor Skill

Index a textbook PDF into Docling chunks, a deterministic chapter/section map, hierarchical summaries, and a LanceDB hybrid index, then answer questions with page citations. All parsing and summarization is delegated to subagents; the scripts never call an AI chat API and need no API keys.

## When to Use

- The user supplies a textbook PDF and asks about its chapters, sections, pages, figures, or concepts.
- The user wants a reusable index so later questions do not re-parse the book.
- The user asks for section-level explanations, comparisons, or citations back to page ranges.

Don't use for generic PDF editing, form filling, OCR cleanup, or non-textbook documents — use the `pdf` skill instead.

## Prerequisites

- Python 3.11+ in a virtual environment: `terminal(command="python -m venv $SKILL_DIR/.venv && source $SKILL_DIR/.venv/bin/activate")`
- Install dependencies once:
  `terminal(command="python -m pip install -r \"$SKILL_DIR/requirements.txt\"")`
- First run downloads the Docling layout models and `Qwen/Qwen3-Embedding-0.6B`. Allow network and disk time.
- No API keys, LiteLLM endpoint, LangChain, or OpenAI client are used.

## How to Run

Set `SKILL_DIR` to the directory containing this `SKILL.md`, then invoke every script with `terminal`:

```text
terminal(command="python \"$SKILL_DIR/scripts/preprocess.py\" \"/abs/path/book.pdf\"")
```

Run from the directory that should hold generated data. The data root defaults to the current working directory; set `TEXTBOOK_TUTOR_DATA_ROOT` to pin it elsewhere.

## Quick Reference

| Stage | Command | Output |
|---|---|---|
| 1. Preprocess | `python "$SKILL_DIR/scripts/preprocess.py" <pdf>` | `chunks/`, `toc_raw/`, `pictures/` |
| 2. Parse ToC | delegate subagent, write JSON | `toc_parsed/<pdf>.json` |
| 3. Postprocess | `python "$SKILL_DIR/scripts/postprocess_toc.py" <pdf name>` | `toc_enriched/<pdf>.json` |
| 4a. Index | `python "$SKILL_DIR/scripts/db_populate.py" <pdf name> [--force]` | `lancedb_data/`, summary manifest |
| 4b. Apply | `python "$SKILL_DIR/scripts/db_populate.py" <pdf name> --apply-summaries` | summaries in `toc_enriched` |
| 5. Query | `python "$SKILL_DIR/scripts/db_skills.py" <pdf name> --chapsum` | citations and text |

`<pdf name>` is the PDF file name without `.pdf`.

## Procedure

1. **Preprocess the PDF.** Run `preprocess.py` with the absolute path. If it reports no ToC pages, rerun with `--toc-pages 3-5` (1-indexed, accepts `3-10` or `3,4,5`). Use `--force` to rebuild chunks, pictures, raw ToC, and metadata. Completion: `chunks/<pdf>.jsonl`, `chunks/<pdf>.meta.json`, `toc_raw/<pdf>.txt`, and `pictures/<pdf>/` exist, and the command printed `Raw chunks and ToC contents successfully saved`.

2. **Parse the ToC with a subagent.** Read `references/toc-schema.md`. Delegate `toc_raw/<pdf>.txt` with the exact system prompt and `TOCStructure` schema from that file. Write the returned JSON to `toc_parsed/<pdf>.json` with the `write_file` tool, preserving numeric strings for chapter/section keys. Completion: the file validates against `TOCStructure` and contains every chapter and section from the raw ToC.

3. **Postprocess the ToC.** Run `postprocess_toc.py <pdf name>`. Completion: `toc_enriched/<pdf>.json` exists and every chapter/section has `start` and `end` page numbers.

4. **Index and summarize.** Run `db_populate.py <pdf name>`. It prints JSON with `manifest_path`, `section_task_count`, and `chapter_task_count`; keep that manifest. Then read `references/summary-delegation.md` and:
   - Delegate **all section tasks first**, in batches. Each subagent reads its `content_path`, follows `system_prompt`, and writes exactly `{"summary": "..."}` to `output_path`.
   - Verify every section `output_path` for sections with content exists and is valid before continuing.
   - Delegate chapter tasks next; each subagent reads `chapter_content_path` plus every **existing** path in `section_summary_paths`, then writes its `output_path`.
   - Run `db_populate.py <pdf name> --apply-summaries`.

   Completion: the apply command prints `applied_sections` and `applied_chapters` equal to the manifest task counts, and `toc_enriched` has a non-empty summary for every chapter and section with content. `toc_parsed` stays exactly as the ToC subagent wrote it.

5. **Answer the question.** Read `references/query-workflow.md` and follow the three-step retrieval loop:
   - `db_skills.py <pdf name> --chapsum` — pick at most 3 relevant chapters.
   - `db_skills.py <pdf name> --secsum 1,2` — pick at most 3 relevant sections.
   - `db_skills.py <pdf name> --secraw 1.1,2.3` — read the raw text.

   Answer only from retrieved text, cite `<chapter name> (pp. <start>-<end>)`, and use `vision_analyze` on `pictures/<pdf>/pic_<id>.png` only when a figure is essential.

## Pitfalls

- **No ToC detected**: Docling may find no `document_index` items. `--toc-pages` is the escape hatch; do not guess chapters from headings.
- **Page-label offsets**: PDFs with roman front matter map printed labels to physical pages. Keep `chunks/<pdf>.meta.json`; postprocessing needs its `page_labels`, `page_count`, and `toc_offset`.
- **Legacy page ranges**: the last chapter ends at `page_count - 1`, and a section's `end` equals the next section's `start`. These are intentional parity choices.
- **Table reuse**: `db_populate.py` reuses an existing LanceDB table unless `--force` is given. A stale table keeps old metadata; rebuild after changing chunks or the ToC.
- **Raw parsed ToC**: `toc_parsed` is the unmodified ToC subagent output and is never rewritten. Summaries go only into `toc_enriched`, which has normalized section IDs and computed `end` pages.
- **Summary order**: chapter tasks depend on section summaries. Applying before every section output exists fails with a list of missing tasks.
- **No-content tasks**: sections without chunks get `No content available for Section <c>.<s>`; empty chapters get `No content available for <name>`. Their output files are optional.
- **Picture payloads**: `--picid` returns base64 in the legacy shape. Prefer `vision_analyze` on the picture file for normal question answering.
- **1-indexed pages**: every CLI page number is physical and 1-indexed, matching Docling provenance.
- **Duplicate PDF names**: data is keyed by the PDF stem. Two books with the same stem collide; use separate data roots.
- **No AI APIs**: never add an OpenAI/LiteLLM call. The calling agent and its subagents are the only models.

## Verification

- `chunks/<pdf>.meta.json` exists and its `page_labels` length equals `page_count`.
- `toc_parsed/<pdf>.json` validates against `TOCStructure` and is unchanged from the subagent output; `toc_enriched/<pdf>.json` validates against `EnrichedToc`.
- Every enriched chapter/section has `summary` (fallback text is acceptable only for empty content).
- `lancedb_data/<table_name>.lance/` exists, and `db_populate.py` reported a table plus FTS index without error. `<table_name>` is the PDF stem with characters outside `[A-Za-z0-9_.-]` replaced by `_`.
- Spot-check `--chapsum`, `--secsum 1`, `--secraw 1.1`, and `--picid 1`.
- Compare a few page ranges with the legacy `chapters_sections.json`; they must match exactly.
