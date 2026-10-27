# ToC Parsing Contract

`postprocess_toc.py` is deterministic. The only judgement call is turning raw ToC text into `toc_parsed/<pdf_name>.json`, and that is delegated to one subagent with a fixed schema.

## Subagent Prompt

System prompt (use verbatim):

```text
Extract the book chapter (a single number) and section (two numbers separated by a
separator, such as '.' or '-') page ranges. Each page number listed in the table of contents
is the starting page of that chapter/section.

CRITICAL: You must extract ALL chapters and sections from the table of contents.
Do not skip any chapters. Count the total number of chapters and ensure
you have extracted all of them before finishing.
```

User content: the full text of `toc_raw/<pdf_name>.txt`.

## Output Schema

The subagent must return exactly this shape:

```json
{
  "chapters": {
    "1": {
      "name": "Chapter Name",
      "start": 1,
      "sections": {
        "1.1": {"name": "Section Name", "start": 1},
        "1.2": {"name": "Section Name", "start": 9}
      }
    }
  },
  "thinking": "Brief reasoning trace."
}
```

Field rules:

- `chapters` keys are chapter numbers as strings (`"1"`, `"2"`, ...).
- `sections` keys are dotted strings. Two-part IDs (`"1.1"`) are normalized to the section number (`"1"`) later. Three-or-more-part IDs (`"1.1.2"`) are subsections and are dropped with a warning.
- `start` is the page number **printed in the ToC**, not a physical PDF page.
- `thinking` is required; keep it to one or two sentences describing how chapters and sections were identified.
- Every chapter and section visible in the raw ToC must appear. Do not invent entries or page numbers.

## Write and Validate

1. Use the `write_file` tool to save the subagent JSON to `toc_parsed/<pdf_name>.json`.
2. Validate before moving on:

```text
terminal(command="python -c \"import sys; sys.path.insert(0, '$SKILL_DIR/scripts'); from toc_pipeline import loadTocStructure; from pathlib import Path; loadTocStructure(Path('toc_parsed/<pdf_name>.json')); print('ok')\"")
```

3. Run `postprocess_toc.py <pdf_name>`.

## What Postprocessing Changes

- Maps each printed page number through `chunks/<pdf_name>.meta.json` `page_labels`.
- Applies the legacy 1:1 fallback: when every label equals its 1-based index, each page shifts by `toc_offset` (the last ToC page).
- Last chapter ends at `page_count - 1`; every other chapter ends one page before the next chapter starts.
- Section `end` equals the next section's `start`; the last section ends at the chapter end.
- Writes `toc_enriched/<pdf_name>.json` and leaves `toc_parsed/<pdf_name>.json` untouched.

Use `references/data-layout.md` for the metadata fields and page-index rules.
