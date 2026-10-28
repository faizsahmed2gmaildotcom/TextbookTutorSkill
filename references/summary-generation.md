# Summary Generation

`summary_generator.py` is a standalone, detached process that writes hierarchical summaries into `toc_enriched/<pdf_name>.json`. It never reads or rewrites `toc_parsed/<pdf_name>.json`, and no subagents are involved.

## Settings

Copy `scripts/settings-example.py` to `scripts/settings.py` and edit it manually. `settings.py` is gitignored.

| Setting | Meaning |
|---|---|
| `OPENAI_BASE_URL` | Chat-completions base URL, including `/v1` when the provider requires it |
| `OPENAI_API_KEY` | API key or local endpoint token |
| `OPENAI_MODEL` | Model name accepted by that endpoint |
| `OPENAI_TIMEOUT_SECONDS` | Per-request timeout |
| `OPENAI_MAX_RETRIES` | SDK retry count |
| `OPENAI_TEMPERATURE` | Sampling temperature, default `0.2` |

A missing or incomplete `settings.py` raises `RuntimeError` before the lock is acquired. The script reports which setting is missing.

## Command

Run from the data root:

```text
mkdir -p logs && nohup python "$SKILL_DIR/scripts/summary_generator.py" "<pdf name>" >> "logs/<pdf name>.log" 2>&1 &
```

The agent must not wait for this process, poll it, or keep the response open. Tell the user it is running and end the response.

Add `--force` to regenerate every summary for that PDF. Without it, any non-empty summary is skipped.

## Order of Work

1. Acquire `toc_enriched/.<pdf name>.lock`.
2. Load and validate `toc_enriched/<pdf name>.json`.
3. Open `lancedb_data/<table_name>.lance`.
4. Summarize **all sections** in numeric chapter order, using the raw indexed section text.
5. Summarize **all chapters**, using the section summaries plus chapter-level chunks.
6. Release the lock.

The legacy prompts and the legacy chapter message (`Chapter: <name>\n\n...`) are preserved verbatim.

## Persistence

Every completed section or chapter summary atomically replaces `toc_enriched/<pdf name>.json` through a same-directory temporary file and `os.replace`. A crash or `SIGKILL` keeps every summary completed before it, and the next run resumes by skipping non-empty summaries.

## Lock Protocol

The lock file contains JSON:

```json
{
  "pid": 12345,
  "pdf_name": "book",
  "started_at": "2026-10-28T12:34:56Z"
}
```

- `summary_generator.py` creates it through an atomic hard-link that fails if the lock already exists.
- `postprocess_toc.py`, `db_populate.py`, and all four `db_skills.py` options check it before reading or writing the enriched ToC.
- An active lock raises `RuntimeError` naming the PID, start time, and log path.
- A stale lock is removed automatically with a warning. A lock is stale when any of these is true:
  - the PID does not exist,
  - the process is a zombie,
  - the process was created after `started_at` (PID reuse),
  - the command line does not contain `summary_generator.py` and the PDF name,
  - the file is empty, malformed, or missing required fields.
- Normal exit, exceptions, `SIGINT`, and `SIGTERM` release the lock. `SIGKILL` cannot be intercepted, so the next guarded command removes that lock as stale.

Do not delete an active lock manually. If a lock is reported as active, generation is still running.

## Logs

Shell redirection appends both normal logs and tracebacks to `logs/<pdf name>.log`. Each run writes a header with the timestamp, PDF, PID, and force flag, and a footer with generated/skipped/failure counts. Append mode preserves history across reruns.

Inspect progress with:

```text
tail -n 20 "logs/<pdf name>.log"
```

## Failure Behavior

- Missing section chunks produce `No content available for Section <chapter>.<section>`.
- A chapter with no section summaries and no chapter-level text produces `No content available for <name>`.
- Fallbacks are non-empty, so the default skip rule preserves them. Use `--force` to replace them after chunks are added.
- An API exception or an empty model response leaves that summary empty, logs the traceback, continues to the next task, and makes the process exit `1`.
- Empty is therefore the signal for "not generated yet". `--chapsum` and `--secsum` raise `RuntimeError` with the rerun command when they encounter an empty summary.
- `--secraw` and `--picid` do not depend on summaries and still work.

Rerun the same background command to retry failures. Completed summaries are skipped.

## Files

- Input: `toc_enriched/<pdf name>.json`, `lancedb_data/<table_name>.lance/`, `scripts/settings.py`
- Output: `toc_enriched/<pdf name>.json`
- Lock: `toc_enriched/.<pdf name>.lock`
- Log: `logs/<pdf name>.log`
- Never touched: `toc_parsed/<pdf name>.json`
