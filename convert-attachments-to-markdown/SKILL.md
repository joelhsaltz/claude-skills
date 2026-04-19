---
name: convert-attachments-to-markdown
description: Use when the user asks to convert a directory of PDF/DOCX/XLSX attachments to markdown, re-convert an email archive, or batch-process binaries through Docling/Marker/MarkItDown. Covers orphan cleanup, Marker hang avoidance, smallest-first ordering, and external watchdog monitoring.
---

# Convert Attachments to Markdown

Batch-convert a directory of PDF/DOCX/XLSX/PPTX binaries to markdown sidecars that downstream search and matching pipelines can read. Built for email-archive style directories (`data/email-archive/{account}/attachments/`) but works for any binary tree.

## Core principle

The conversion stack itself is fast. **The hangs, false-positive failures, and wasted hours all come from skipping pre-flight cleanup, leaving Marker enabled by default, and trusting the runner to monitor itself.** This skill exists because every one of those traps cost hours when discovered live.

## When to use

- "Convert these emails' attachments to markdown"
- "Re-convert the archive"
- "Batch-process these PDFs"
- A new project ingests a directory of mixed-format binaries and needs them as text

## When NOT to use

- Single-file ad-hoc conversion (`expense convert FILE` or `pdftotext` is enough)
- The binaries are already partially converted with sidecars matching the current tool — `is_stale()` will skip them; just run the walker

## Quick reference (priority order)

| Step | Why | Skip if… |
|---|---|---|
| 1. Triage prior failures | `scripts/conversion_triage.py status` shows unreviewed failures from any prior run | First-ever run |
| 2. Pre-cleanup obvious-irrelevant binaries | Saves 30–50% of work; some PDFs hang Marker for hours | You've already done it |
| 3. **Run with Marker disabled** | Marker hangs unpredictably on certain text-heavy PDFs (the 52-page rule) | You have specific scanned/CID-encoded PDFs that need OCR |
| 4. Smallest-first ordering | Burns through fast PDFs first, isolates the slow tail | Single-file run |
| 5. Run in background with external watchdog | Marker writes progress with `\r` not `\n`, so log streaming silently misses a hang | Single-file or trivial batch |
| 6. Run `normalize_conversion_dates.py` after completion | Fixes timestamp drift between sidecar and vault entity (legacy bug) | The fix has fully landed everywhere |

## Pre-cleanup (step 2)

**Two cleanup categories that look alike but aren't.** Confusing them once will erase the matching pipeline's input.

| Category | Definition | Action |
|---|---|---|
| **Orphan** | File on disk; `gmail_message_id` not in the `emails` table | Safe to delete — no downstream code references it |
| **Unlinked to transaction** | Email IS in `emails` table; attachment is on disk; no `invoice_payments` row links it | **NEVER delete** — these are the input to the matching pipeline |

The wrong cleanup rule ("delete attachments without a transaction link") would erase ~all receipts. The right cleanup rule is "delete attachments without an email row."

**Other safe pre-cleanup categories:**
- Date-windowed orphans (e.g. attachments from emails before the current `extract-email --date-from` window)
- Hand-curated obvious-irrelevant: cookbooks, spa brochures, terms-of-service appendices, vendor outdoor-furniture promo PDFs. Match by filename substring AND a size threshold to avoid false positives.
- Forward duplicates (same content hash, different filename) — content-hash dedup will catch later, but pruning saves runtime now.

## Disable Marker by default (step 3)

**Marker hangs.** It can spend hours on the "Recognizing Text: N/M" step and never recover. Observed on a 52-page text-based PDF that hung twice in a row at "46/52" (the second time after a fresh restart). MPS OOM forces per-page CPU fallback, then certain pages just don't return.

**Default behavior for batches:** disable Marker fallback entirely. Pass `min_chars=0` and `max_image_tag_ratio=1.0` so any Docling output (even empty) is accepted. Re-enable Marker selectively for files where Docling produces obviously sparse output AND you have evidence it's a scanned/image-only PDF.

**Important: `min_chars=0` does NOT disable the Docling-error fallback path.** When Docling raises (e.g. PDFium "Incorrect password" on encrypted PDFs), the pipeline still invokes Marker as a backup. Marker fails fast on the same files (~30–180s of OCR preprocessing before re-hitting the same encryption check) — annoying but not catastrophic. To truly skip Marker on docling-error, you'd need to patch `_convert_pdf` in `pipeline.py`; for batch runs the fast-fail behavior is acceptable.

**Two narrow exceptions where Marker IS worth running:**
- **Genuine scanned PDFs** (image-only, no text layer). Docling produces empty output; only Marker's Surya OCR can extract.
- **CID-encoded PDFs** (Apple receipts, Balzac/French ToS appendices). These have a font without a ToUnicode CMap, so Docling produces `(cid:N)` glyph IDs. Marker's OCR fixes these.

For these exceptions, set `forced_tool: marker+surya` in the file's sidecar `.md` frontmatter. The next run will use Marker only for that file.

## Smallest-first ordering (step 4)

The default `--archive-walk` order is filesystem-determined (effectively random). Sort by file size ascending so:
- Quick text PDFs (1–50 KB receipts) finish in seconds
- Medium docs (100–500 KB) burn through next
- Large multi-page docs (1+ MB) are the tail you can monitor and triage

A reference runner script that does this lives at `/tmp/convert_smallest_first.py` in the financial-tracker session. Pattern:

```python
candidates = [(p.stat().st_size, p) for p in archive_root.rglob("*") if matches_extension(p) and is_stale(p)]
candidates.sort()
for size, path in candidates:
    convert_to_markdown(path, vault_root=vault_root, min_chars=0, max_image_tag_ratio=1.0)
```

## External watchdog (step 5)

**Marker's progress bars use `\r` (carriage return) within a single line, not `\n`.** This means:
- `tail -f` on the log shows nothing useful
- Any monitor that streams stdout line-by-line will silently miss a hang
- Even `wc -l` on the log doesn't increment during Marker's per-page progress

**Working watchdog pattern** (from financial-tracker session):
- Cron-scheduled (10 min interval, off-minutes like 7,17,27,37,47,57)
- Each tick: progress signal, `pgrep` for child process, compare to last-tick state in `/tmp/convert_monitor_state.txt`
- **Best progress signal: file size in bytes** (`stat -f "%z" $log` on macOS / `stat -c %s` on Linux) — catches Marker's `\r` writes which `wc -l` is blind to
- After **2 consecutive idle ticks (20 min)** with the process alive: `pkill -TERM -f convert_smallest_first`, sleep 5, restart with a fresh log path (so you can grep the original after the fact)
- On clean process exit + zero pending: run normalize, report stats, delete the cron

Don't use the loop skill's dynamic mode for this — fixed 10-min cron is more reliable across context-window resets.

### Diagnosing "alive but stuck"

`pgrep` returning a PID is necessary but **not sufficient**. The hang we encountered showed:
- `pgrep -f convert_smallest_first` → returned the PID (looked alive)
- `ps -p PID` → STAT=`SN`, %CPU=0.0, RSS=32 (parent uv launcher idle)
- Actual python child (PID+2) → STAT=`UN` (uninterruptible sleep), %CPU=4.2 (still scheduled but stuck in kernel wait)
- Log mtime → unchanged for hours

**Diagnostic checklist when the watchdog flags idle:**
1. `ps -p $PID -o pid,etime,pcpu,stat,wchan` — STAT=`UN` is fatal, kill immediately. STAT=`R` or `S` with non-zero CPU means it might still recover.
2. `stat -f "%Sm" $log` (macOS) or `stat -c %y $log` (Linux) — log mtime older than 20 min = wedged regardless of process state.
3. Look for the python CHILD process via `pgrep -P $PARENT_PID` — the parent (`uv run python ...`) often shows 0% CPU even when the child is doing real work. Always check the child.

If any one of those signals is bad: kill via `pkill -TERM -f convert_smallest_first` (gets parent + child), wait 5s, restart. Don't `kill -9` first — TERM lets Python's resource_tracker clean up its loky semaphores.

### Marker model warmup

On a cold start (after restart), Marker spends **1–3 minutes** loading Surya layout/text/table-recognition models before processing the first file. The watchdog's progress signal stays at 0 during this. Don't false-alarm: either (a) initialize `last_counter=0` and only count idle ticks where `last_counter > 0` AND `CUR == last_counter`, or (b) bump the first-tick threshold to skip the warmup window.

### FileNotFoundError noise from mid-run cleanup

The smallest-first runner builds its candidate list ONCE at startup (via `rglob`). If you delete files mid-run (e.g. pruning during conversion), the runner will hit `FileNotFoundError` on each deleted entry — these get logged as failures (`✗`) but they aren't real failures. They inflate the failure count.

Two corollaries:
- **Restart after pruning** — the new walk picks up only files that still exist. Avoids the phantom failures.
- **Subtract FileNotFoundError from failure stats** — `grep -c FileNotFoundError $log` gives the count of phantom failures; real failures = `grep -cE '^\[.*✗' $log` minus that.

## Failure triage (ongoing)

Failures fall into three buckets. Track them in `vault/notes/conversion-triage.yaml` (registry keyed by `source_file_sha256`, dedups forwarded copies). Use `scripts/conversion_triage.py status` to surface only NEW unreviewed failures.

| Disposition | When to use |
|---|---|
| `irrelevant` | Not financial, content captured elsewhere, vendor terms-of-service appendix. No follow-up. |
| `needs-password` | Encrypted PDF (Webb CPA tax docs, Fisher rental wires, Sutton renters policy). Won't convert without the password. Track for future password collection but don't retry on every run. |
| `needs-followup` | Real gap — financial content is missing and needs sourcing from the sender or a parallel document. |

Common patterns observed:
- **Webb CPA tax organizers + tax returns** are always password-protected
- **Bank wire instructions** (Fisher Nantucket etc.) are password-protected
- **Insurance policies** (Sutton renters) are password-protected
- **Vendor ToS appendices** (Apple, Balzac) malform via PDFium ("Data format error")
- **Pre-2007 `.doc`** can't be parsed by markitdown — separate gap, mark `needs-followup`

## Project-local code reference

When working in financial-tracker (or any project that already has the conversion stack):

| Asset | Path | Purpose |
|---|---|---|
| CLI | `expense convert FILE` / `expense convert --archive-walk` | Wraps the pipeline |
| Routing | `src/conversion/pipeline.py:convert_to_markdown` | Selects backend, writes sidecar + vault entity |
| Backends | `src/conversion/{docling,marker,markitdown}_backend.py` | Thin wrappers around each lib |
| Walker | `src/conversion/reconvert.py:reconvert_archive` | Idempotent via `is_stale()` |
| Triage | `scripts/conversion_triage.py` | Failure disposition CLI |
| Triage data | `vault/notes/conversion-triage.yaml` | Hash-keyed registry |
| Normalize | `scripts/normalize_conversion_dates.py` | Post-run timestamp alignment |

When working in a project WITHOUT this stack, install `docling`, `marker-pdf`, and `markitdown[docx,pptx,xlsx]`, then write thin equivalents. Don't carry over the whole `src/conversion/` tree — just adopt the patterns.

**Dependency conflict to know about:** `marker-pdf 1.10.x` requires `transformers <5`. `mlx-vlm 0.4.4+` requires `transformers ≥5`. They can't coexist. Drop mlx-vlm; revisit after 2026-06-17.

## Backend wrapper defensive patterns

If you write your own backend wrappers (or extend the existing ones), two specific bugs we hit are worth designing against from the start:

### Empty output ≠ success

`MarkItDown` (and any backend) can return `""` without raising. The naive wrapper records `status: success`, `chars_extracted: 0`, and the next idempotency check (`is_stale`) treats the file as freshly converted — permanently hiding the failure. Always:

```python
body = backend.convert(path)
if not body.strip():
    return Result(status="conversion_failed", fallback_reason="empty output", ...)
return Result(status="success", body=body, ...)
```

The PDF branch has `needs_marker_fallback` for this; Office/MarkItDown branches need an equivalent guard.

### Mirrored writes need shared derived state

Conversion writes the same metadata to two files: the sidecar `.md` next to the binary AND the vault entity at `vault/documents/{uid}.md`. If each writer independently calls `datetime.now()`, the timestamps drift by however long the first write takes — and any future consistency check that compares them flags a phantom diff. **Compute timestamps (and any derivative state) in the caller, then pass into both writers.** Pattern:

```python
# Wrong: each writer derives its own now()
_write_sidecar(result, body)        # calls datetime.now() internally
_write_entity(result, vault_root)   # calls datetime.now() internally — different value

# Right: derive once, pass in
conversion_date = datetime.now(timezone.utc)
_write_sidecar(result, body, conversion_date)
_write_entity(result, vault_root, conversion_date)
```

If you find existing dual-writes that were buggy, ship a `normalize_conversion_dates.py` script that aligns the entity timestamp to the sidecar (sidecar is written first → more authoritative). Read the sidecar's `conversion_date`, overwrite the entity's. Idempotent. Run once after the bug is fixed in the writer.

## Common mistakes

| Mistake | Fix |
|---|---|
| Running with Marker enabled by default | Disable content-based fallback (`min_chars=0`); selectively re-enable per-file via `forced_tool: marker+surya` |
| Streaming the log to detect hangs | Use the cron+byte-size watchdog instead — progress bars hide via `\r`, and `wc -l` is also blind to them |
| `pgrep` returning a PID = process is healthy | Insufficient. Check the python CHILD's STAT (`UN` = uninterruptible sleep = fatal) AND log mtime (>20 min stale = wedged regardless of process state) |
| Pre-cleanup using "no transaction link" rule | That deletes the matching pipeline's input. Use "no email row" rule instead. |
| Re-running and re-failing on password-protected PDFs every time | Mark them `needs-password` in `conversion-triage.yaml` |
| Pruning files mid-run | Runner caches candidate list at startup → phantom `FileNotFoundError` failures. Restart after pruning. Subtract `grep -c FileNotFoundError $log` from failure totals. |
| Counting all `^\[.*✗` lines as real failures | Many are FileNotFoundError noise from earlier prunes. Real failures = total ✗ minus FileNotFoundError. |
| False-alarming the watchdog during Marker model warmup | First 1–3 min after restart show no progress (Surya models loading). Wait 1 grace tick before counting idle. |
| Treating empty backend output as success | MarkItDown can return `""` without raising. Wrap with `if not body.strip(): status="conversion_failed"`. |
| Each writer calling `datetime.now()` independently | Sidecar and entity timestamps drift by however long the first write took. Compute in caller, pass to both. |
| Sequential single-process for >100 files in foreground | Background + watchdog |
| Assuming conversion writes to the DB | It doesn't — sidecars (gitignored) + vault entities (committed) only |
| Inventing CLI flags like `--dry-run` | Read the actual CLI source first; the real "what's pending" check is `is_stale()` over an `rglob` |
| `kill -9` to recover from hang | Use `kill -TERM` first so Python's resource_tracker cleans up its loky semaphores. Only escalate to KILL if TERM doesn't take in 5s. |

## Red flags — STOP and reconsider

- "I'll just run `--archive-walk` with default flags and watch the output" → no, you need pre-cleanup + Marker-disabled + watchdog
- "I'll delete attachments that don't link to a transaction" → no, that erases the matching pipeline's input
- "Marker is hung, I'll just wait — it might recover" → no, kill it; it doesn't recover from this state
- "I'll restart the runner manually if it hangs" → no, set up the watchdog so off-hours runs heal themselves
- "Conversion run is at 95%, I'll let it finish" → check the log mtime; if it hasn't updated in 20+ min, it's wedged at file 95.5/100, not finishing the last 5
- "The process is alive (pgrep returned a PID), so it must be working" → check the python CHILD's STAT (`UN` = stuck) and log mtime
- "200 failures in the log means 200 real failures" → subtract FileNotFoundError noise from prior prunes
- "I'll prune some files while the runner is going to save time on the tail" → the runner won't pick up the deletions; either restart it or wait until the run completes
- "MarkItDown returned empty string but didn't raise — must be an empty document" → no, treat empty as failure; the next `is_stale()` check would otherwise mask the real problem forever
