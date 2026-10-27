# Summary Delegation

`db_populate.py` never summarizes anything. It writes content files and `summaries/<pdf_name>/manifest.json`, and the calling agent delegates each task to a subagent.

## Manifest Shape

```json
{
  "pdf_name": "book",
  "section_tasks": [
    {
      "task_id": "section-1.1",
      "kind": "section",
      "chapter_id": "1",
      "section_id": "1",
      "name": "Section Name",
      "system_prompt": "Summarize this textbook section in 1-3 sentences. Include key concepts and important details. Be thorough but concise.",
      "content_path": "/abs/path/summaries/book/content/section-1.1.txt",
      "output_path": "/abs/path/summaries/book/sections/1.1.json",
      "output_schema": {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"]
      }
    }
  ],
  "chapter_tasks": [
    {
      "task_id": "chapter-1",
      "kind": "chapter",
      "chapter_id": "1",
      "name": "Chapter Name",
      "system_prompt": "Summarize this textbook chapter in 3-5 sentences. Include key topics, concepts, and their relationships. Be comprehensive.",
      "section_summary_paths": ["/abs/path/summaries/book/sections/1.1.json"],
      "chapter_content_path": "/abs/path/summaries/book/content/chapter-1.txt",
      "output_path": "/abs/path/summaries/book/chapters/1.json",
      "depends_on": ["section-1.1"],
      "output_schema": {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"]
      }
    }
  ]
}
```

The task `system_prompt` is complete; do not rewrite it. Do not pass an output schema different from `output_schema`.

## Section Phase

1. Read the manifest JSON.
2. Delegate **every** `section_tasks` entry. Batch 5-10 tasks per `delegate_task` call so each subagent has enough context.
3. Each subagent instruction must say:
   - Read `content_path` with `read_file`.
   - Follow `system_prompt` exactly.
   - Write only `{"summary": "<1-3 sentence summary>"}` as UTF-8 JSON to `output_path` with `write_file`.
   - Return the `task_id` when done.
4. Wait for all section batches to finish.
5. Verify every section `output_path` exists and parses as `{"summary": <non-empty string>}`. Fix failures before continuing — chapter tasks depend on these files.

Tasks whose content file is empty may be skipped; `--apply-summaries` writes the deterministic fallback for them.

## Chapter Phase

1. Delegate every `chapter_tasks` entry.
2. Each subagent instruction must say:
   - Read `chapter_content_path`.
   - Read every file in `section_summary_paths` that exists. A missing path belongs to a section with no chunks; skip it — `--apply-summaries` writes that section's deterministic fallback.
   - Build the user content exactly as:

```text
Section Summaries:
Section <section_id> (<section name>): <summary>
---
Section <section_id> (<section name>): <summary>

Chapter Content:
<chapter_content_path contents>
```

   - Prepend `Chapter: <name>\n\n` to that block.
   - Follow `system_prompt` exactly.
   - Write only `{"summary": "<3-5 sentence summary>"}` to `output_path` with `write_file`.
3. Wait for all chapter batches, then verify every chapter output file.

## Apply Phase

Run:

```text
terminal(command="python \"$SKILL_DIR/scripts/db_populate.py\" <pdf_name> --apply-summaries")
```

It validates outputs, merges section summaries into `toc_parsed/<pdf_name>.json`, merges chapter summaries, and copies them to `toc_enriched/<pdf_name>.json`. It prints:

```json
{"pdf_name": "book", "applied_sections": 24, "applied_chapters": 8}
```

If any required output is missing, it fails with a `click.ClickException` naming every missing task. Re-delegate those tasks and rerun.

## Fallbacks and Re-runs

- A section with no chunks gets `No content available for Section <chapter>.<section>`.
- A chapter with no content gets `No content available for <name>`.
- `--apply-summaries` reuses an existing non-empty summary in `toc_parsed` when its output file is absent, so reruns do not require re-delegation.
- Content paths are derived from the enriched ToC at `db_populate.py` time. If the ToC changed, rerun the default command to regenerate the manifest.
