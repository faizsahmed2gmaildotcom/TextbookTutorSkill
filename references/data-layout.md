# Data Layout and Page Rules

All generated data lives under one data root. The default is the current working directory; set `TEXTBOOK_TUTOR_DATA_ROOT` to pin it.

```text
<data_root>/
├── chunks/
│   ├── <pdf_name>.jsonl
│   └── <pdf_name>.meta.json
├── toc_raw/<pdf_name>.txt
├── toc_parsed/<pdf_name>.json
├── toc_enriched/<pdf_name>.json
├── pictures/<pdf_name>/pic_<id>.png
├── lancedb_data/<pdf_name>.lance/
└── summaries/<pdf_name>/
    ├── manifest.json
    ├── content/
    ├── sections/
    └── chapters/
```

`<pdf_name>` is `Path(pdf_path).stem`. Two books with the same stem collide; use separate data roots or rename one file.

## Metadata

`chunks/<pdf_name>.meta.json` is written by `preprocess.py` and read by `postprocess_toc.py`:

```json
{
  "pdf_path": "/absolute/path/book.pdf",
  "pdf_name": "book",
  "page_count": 283,
  "page_labels": ["i", "ii", "1", "2"],
  "toc_pages": [3, 4, 5],
  "toc_offset": 5,
  "pictures_dir": "pictures/book"
}
```

- `page_count` is the physical PDF page count.
- `page_labels` comes from pypdf, one entry per physical page. If labels cannot be read, it falls back to `"1"`, `"2"`, ...
- `toc_pages` is the contiguous ToC page group detected by Docling, or the `--toc-pages` override.
- `toc_offset` is the last ToC page, matching the legacy `LLMTOCParser` call.
- `pictures_dir` is relative to the data root.

## Page Mapping

`postprocess_toc.py` builds `page_to_idx` from `page_labels`:

1. `page_to_idx[label] = physical_page` (1-indexed).
2. If every label equals its own 1-based index (an unlabelled PDF), apply the legacy fallback: `page_to_idx[label] = index + 1 + toc_offset`.

A parsed ToC page found in the mapping becomes that physical page. A missing label falls back to the raw integer page with a warning instead of crashing.

## Legacy Quirks Preserved

These are intentional, not bugs to fix:

- The last chapter's `end` is `page_count - 1`, not `page_count`.
- Every other chapter's `end` is the next chapter's mapped `start - 1`.
- A section's `end` equals the next section's mapped `start`, so adjacent sections share a boundary page.
- The last section's `end` equals the chapter `end`.
- `toc_parsed` keeps `thinking` and raw dotted section IDs (`"1.1"`); it is never rewritten after the ToC subagent writes it. `toc_enriched` omits `thinking` and uses normalized section IDs (`"1"`), which is why summaries are applied only there.

Only crashes and data-shape problems were fixed: missing page labels no longer raise, empty provenance no longer raises, and chapter-level chunk rows are read correctly.

## LanceDB

Table name: the PDF stem with characters outside `[A-Za-z0-9_.-]` replaced by `_` (LanceDB rejects spaces and parentheses). On disk: `lancedb_data/<table_name>.lance/`. Columns:

| Column | Type | Notes |
|---|---|---|
| `vector` | list[float] | Qwen3-Embedding-0.6B, max sequence length 1024 |
| `text` | string | chunk text; FTS index |
| `items` | list[struct] | label, char_range, page_range |
| `header` | string | last Docling heading |
| `pic_id` | string | `""` for text chunks |
| `chapter_id` | string | `""` when outside any chapter |
| `chapter_name` | string | |
| `section_id` | string | `""` for chapter-level chunks |
| `section_name` | string | |
| `page_start` | int | inclusive |
| `page_end` | int | inclusive |

Empty-string IDs, rather than `null`, keep `WHERE` filters working for chapter-level and section-level queries.

## Summary Workspace

- `manifest.json` lists all section and chapter tasks with absolute content/output paths.
- `content/` holds raw text handed to subagents.
- `sections/<chapter>.<section>.json` and `chapters/<chapter>.json` hold `{"summary": "..."}`.
- `--apply-summaries` writes summaries only into `toc_enriched`; `toc_parsed` stays the raw subagent output.
