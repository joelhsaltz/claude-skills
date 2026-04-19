---
name: convert-attachments-to-markdown
description: Use when the user asks to convert a directory of PDF/DOCX/XLSX attachments to markdown, re-convert an email archive, or batch-process binaries through Docling/Marker/MarkItDown. Covers orphan cleanup, Marker hang avoidance, smallest-first ordering, external watchdog monitoring, consent gating for kill actions, and downstream consumer correctness.
---

# Convert Attachments to Markdown

Batch-convert a directory of PDF/DOCX/XLSX/PPTX binaries to markdown sidecars that downstream search and matching pipelines can read. Built for email-archive style directories (`data/email-archive/{account}/attachments/`) but works for any binary tree.

**Last updated:** 2026-04-19 · **See Changelog at bottom** for evolution of guidance.

## Core principle

The conversion stack itself is fast. **The hangs, false-positive failures, and wasted hours all come from skipping pre-flight cleanup, leaving Marker enabled by default, and trusting the runner to monitor itself.** This skill exists because every one of those traps cost hours when discovered live.

**Critical downstream invariant (read this before using conversion output):** `vault/documents/*.md` contains entities for BOTH successful conversions AND failures (the pipeline writes an entity regardless of status so `is_stale()` can work idempotently). **Downstream consumers MUST gate on `status: success`** before reading the `path:` pointer. Do not assume every entity is usable.

```python
fm, body = read_entity(entity_path)
if fm.get("status") != "success":
    continue  # skip failures
process(fm["path"])
```

## When to use

- "Convert these emails' attachments to markdown"
- "Re-convert the archive"
- "Batch-process these PDFs"
- A new project ingests a directory of mixed-format binaries and needs them as text

## When NOT to use

- Single-file ad-hoc conversion (`expense convert FILE` or `pdftotext` is enough)
- The binaries are already partially converted with sidecars matching the current tool — `is_stale()` will skip them; just run the walker

## Runbook at a glance

Minimal commands for a planned batch re-conversion in a project that already has the stack. Adapt paths for your project.

```bash
# 0. Triage prior failures (are there any NEW unreviewed ones?)
uv run python scripts/conversion_triage.py status

# 1. Pre-cleanup: orphans (SQL), then obvious-irrelevant (manual filename+size match)
# 2. CONSENT GATE: authorize watchdog to kill+restart on hang (required for unattended runs)
# 3. Start runner in background, capture log + PID
RUN_ID=$(date +%s)
LOG=/tmp/convert_run_$RUN_ID.log
PID_FILE=/tmp/convert_run_$RUN_ID.pid
nohup uv run python /tmp/convert_smallest_first.py > "$LOG" 2>&1 &
echo $! > "$PID_FILE"

# 4. Watchdog tick (cron this every 10 min, off-minutes 7,17,27,...)
#    Uses byte-size progress signal; kills ONLY the recorded PID.
#    See "External watchdog" below for the full script.

# 5. Post-completion
uv run python scripts/normalize_conversion_dates.py   # align timestamp drift
uv run python scripts/conversion_triage.py status     # classify new failures
```

## Quick reference (priority order)

| Step | Why | Skip if… |
|---|---|---|
| 0. **Consent gate** for destructive watchdog actions | Operators need explicit user approval before `pkill` runs unattended. Log the approval. | You're attending the run; manual kill only |
| 1. Triage prior failures | `scripts/conversion_triage.py status` shows unreviewed failures from any prior run | First-ever run |
| 2. Pre-cleanup obvious-irrelevant binaries | Saves 30–50% of work; some PDFs hang Marker for hours | You've already done it |
| 3. **External watchdog with PID file** | Marker writes progress with `\r` not `\n`; stream-monitors silently miss hangs. Hang risk > ordering risk. | Single-file or trivial batch |
| 4. **Run with Marker disabled** | Marker hangs unpredictably on certain text-heavy PDFs (the 52-page rule); content-fallback off AND hard-disable on Docling-error | You have specific scanned/CID-encoded PDFs that need OCR |
| 5. Smallest-first ordering | Burns through fast PDFs first, isolates the slow tail | Single-file run |
| 6. Run `normalize_conversion_dates.py` after completion | Fixes timestamp drift between sidecar and vault entity (legacy bug) | The fix has fully landed everywhere |

## Consent gate (step 0)

For any run that will use an **external watchdog to kill and restart the process** (i.e., every overnight or unattended batch), get explicit user authorization before enabling the watchdog. Per global CLAUDE.md and typical operational policy, destructive actions (pkill, git reset --hard, etc.) require approval.

Record verbatim in the run log:

```
Approval: <user said "yes, kill on hang" | verbatim quote>
Authorized at: 2026-04-19T14:32:00Z
Authorized by: <user name from context>
Scope: TERM any process matching our PID file after 2 consecutive idle ticks (20 min)
       and restart with a fresh log. Does NOT authorize deleting data or code.
```

This is both UX (sets expectation that the watchdog WILL kill) and audit (someone reviewing logs months later can see authorization existed).

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

**Never prune mid-run.** The runner builds its candidate list once at startup; deletions mid-run produce phantom `FileNotFoundError` entries that inflate failure counts AND don't update the queue. Either prune before starting, or kill and restart after pruning.

## Disable Marker by default (step 4)

**Marker hangs.** It can spend hours on the "Recognizing Text: N/M" step and never recover. Observed on a 52-page text-based PDF that hung twice in a row at "46/52" (the second time after a fresh restart). MPS OOM forces per-page CPU fallback, then certain pages just don't return.

**Default behavior for batches:** disable Marker fallback entirely. Pass `min_chars=0` and `max_image_tag_ratio=1.0` so any Docling output (even empty) is accepted. Re-enable Marker selectively for files where Docling produces obviously sparse output AND you have evidence it's a scanned/image-only PDF.

### Important: `min_chars=0` is NOT total disable

Setting `min_chars=0` disables the **content-based** Marker fallback. It does NOT disable the **Docling-error** fallback path — when Docling raises (e.g. PDFium "Incorrect password" on encrypted PDFs), the pipeline still invokes Marker. Marker then fails fast on the same files (~30–180s of OCR preprocessing before re-hitting the same encryption check). Not catastrophic, but not the "Marker is off" state most people expect.

**Hard-disable option (recommended for batch runs):** patch `_convert_pdf` to honor a `CONVERT_NO_MARKER_ON_ERROR=1` env var (or a `--no-fallback-on-error` flag). When set, Docling exceptions return `status: conversion_failed` immediately without invoking Marker.

```python
# src/conversion/pipeline.py _convert_pdf excerpt
except Exception as e:
    if os.environ.get("CONVERT_NO_MARKER_ON_ERROR") or forced_tool == "docling":
        return "docling", get_docling_version(), "", "docling-error", "conversion_failed"
    # else fall through to marker fallback
```

**Preflight encrypted PDFs:** before handing to Docling, check encryption and short-circuit to `needs-password` without spinning up Marker for 3 minutes per file:

```python
import pikepdf
try:
    with pikepdf.open(path) as pdf:
        if pdf.is_encrypted:
            return Result(status="conversion_failed", fallback_reason="password-protected", ...)
except pikepdf.PasswordError:
    return Result(status="conversion_failed", fallback_reason="password-protected", ...)
```

### Narrow exceptions where Marker IS worth running

- **Genuine scanned PDFs** (image-only, no text layer). Docling produces empty output; only Marker's Surya OCR can extract.
- **CID-encoded PDFs** (Apple receipts, Balzac/French ToS appendices). These have a font without a ToUnicode CMap, so Docling produces `(cid:N)` glyph IDs. Marker's OCR fixes these.

For these, set `forced_tool: marker+surya` in the file's sidecar `.md` frontmatter. The next run will use Marker only for that file.

### CID auto-detection with guardrail

Instead of manually tagging every CID-encoded PDF, add a post-Docling check:

```python
import re
def has_cid_garbage(md: str, threshold: int = 20) -> bool:
    return len(re.findall(r"\(cid:\d+\)", md)) > threshold
```

Policy:
- If `has_cid_garbage(docling_output)` → mark `forced_tool: marker+surya` and retry.
- **Guardrail**: only auto-enable Marker retry for small PDFs (`pages ≤ 20` or `size ≤ 1.5 MB`). Larger docs hit the 52-page hang risk again; require manual opt-in via a sidecar `forced_tool:` override.

### Bad-actor quarantine

If a file hangs Marker **twice**, quarantine it permanently. Don't keep trying:
- Option A: write `forced_tool: docling-only` in its sidecar (a new fallback_reason value that means "never invoke Marker on this file").
- Option B: add an entry to `vault/notes/conversion-triage.yaml` with `disposition: blacklist_marker` keyed by `source_file_sha256` (covers forwarded copies).

The runner must check the blacklist before invoking Marker; if matched, skip Marker even on Docling-error and record `status: conversion_failed` with `fallback_reason: marker_blacklisted`.

## Smallest-first ordering (step 5)

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

## External watchdog (step 3)

**Marker's progress bars use `\r` (carriage return) within a single line, not `\n`.** This means:
- `tail -f` on the log shows nothing useful
- Any monitor that streams stdout line-by-line will silently miss a hang
- Even `wc -l` on the log doesn't increment during Marker's per-page progress

### Best progress signal: file size in bytes

```bash
CUR=$(stat -f "%z" "$LOG" 2>/dev/null || stat -c %s "$LOG" 2>/dev/null || wc -c < "$LOG")
```

The `stat -f "%z"` is macOS/BSD; `stat -c %s` is GNU; `wc -c` is the POSIX fallback. Pick the first that works; don't rely on `wc -l` (blind to `\r` AND introduces leading-whitespace bugs when piped into shell state files — see below).

### Whitespace-proof state I/O (hard-won lesson)

**Never `source` a shell state file.** Our initial watchdog used heredocs to write state like `last_counter=$CUR` where `$CUR` came from `wc -l` with leading whitespace. When `source` tried to evaluate `last_counter=       135`, the whitespace truncated the assignment to empty string, the numeric comparison `[ "$CUR" -gt "" ]` was always true (empty = 0), and the watchdog silently reported progress every tick forever. Hang detection was broken and I didn't notice for ~40 minutes.

**Safe pattern:**

```bash
STATE=/tmp/convert_monitor_state
PID_FILE=/tmp/convert_run.pid

# Write
printf '%s\n%s\n%s\n' "$CUR" "$idle_ticks" "$restart_count" > "$STATE"

# Read
read -r last_counter < "$STATE" 2>/dev/null || last_counter=0
# (for multi-line state, use `readarray` or multiple `read -r` off the same FD)

# Guard against empty / non-numeric
last_counter=${last_counter:-0}
case "$last_counter" in *[!0-9]*) last_counter=0 ;; esac
```

One integer per line, no shell syntax. `read -r` doesn't execute anything. Comparisons use `[ "$CUR" -gt "$last_counter" ]` with both sides guaranteed numeric.

### Use a PID file, not `pkill -f`

`pkill -f convert_smallest_first` will match ANY process whose command line contains that string — including editors viewing the script, grep commands by the watchdog itself, etc. Write the PID once at startup and kill only that PID:

```bash
# At runner start
echo $! > "$PID_FILE"

# In watchdog, when killing
if [ -f "$PID_FILE" ] && PID=$(cat "$PID_FILE") && kill -0 "$PID" 2>/dev/null; then
    kill -TERM "$PID"
    sleep 5
    kill -0 "$PID" 2>/dev/null && kill -KILL "$PID"  # escalate only if TERM ignored
fi
```

`kill -0 PID` checks liveness without signaling. `kill -TERM` first so Python's resource_tracker can clean up loky semaphores (ungraceful kill leaks them).

### Per-file hard timeout

Don't rely solely on the coarse 20-min watchdog. Wrap each Marker invocation in a per-file timeout so a single stuck page can't consume the whole watchdog budget:

```python
import subprocess
# Instead of calling marker library directly:
result = subprocess.run(
    ["timeout", "15m", "marker_single", str(path)],
    capture_output=True, timeout=900 + 30,  # belt+suspenders
)
if result.returncode == 124:  # GNU timeout's timeout exit code
    return Result(status="conversion_failed", fallback_reason="marker_timeout")
```

This keeps the watchdog as the backstop, not the primary guard.

### Preflight the watchdog before overnight runs

Don't trust a watchdog on its first run. Validate:

```bash
# Freeze the log (touch -c leaves mtime alone; use this to simulate idle)
initial=$(stat -f "%z" "$LOG" 2>/dev/null || stat -c %s "$LOG")
sleep 1
later=$(stat -f "%z" "$LOG" 2>/dev/null || stat -c %s "$LOG")
[ "$initial" -eq "$later" ] && echo "OK: byte-size stable while idle"

# Simulate a hang: fire 2 mock ticks with same $CUR and confirm watchdog
# would trigger (but comment out the actual pkill during the dry-run).
```

### Scheduler caveat: laptop sleep, cron gaps

`cron` doesn't fire during macOS/Linux laptop sleep. For overnight batch runs, prefer:
- **macOS**: `launchd` with `RunAtLoad: true` + wake schedule
- **Linux**: `systemd` timers with `Persistent=yes`

If you must use cron on a laptop, set the laptop to not sleep (or plug it in with sleep-on-AC disabled) for the duration.

### Cron-tick structure (financial-tracker session reference)

Every 10 min at off-minutes (7,17,27,37,47,57). Each tick:

1. Read state file (whitespace-proof, see above).
2. Read current log byte size.
3. Read `PID_FILE`, check `kill -0` to confirm PID alive.
4. If alive AND size changed: progress. Update state, reset idle counter.
5. If alive AND size unchanged: increment idle. If idle ≥ 2 (20 min): kill PID + sleep 5 + restart runner with NEW log + NEW PID file.
6. If dead AND pending > 0: restart runner.
7. If dead AND pending == 0: run `normalize_conversion_dates.py`, report stats, delete the cron.

### Diagnosing "alive but stuck"

`kill -0 $PID` returning success (PID exists) is necessary but **not sufficient**. The hang we encountered showed:
- `kill -0` → process alive
- Parent `ps -p PID -o stat,pcpu` → STAT=`SN`, %CPU=0.0 (uv launcher idle)
- Actual python child (`pgrep -P $PID`) → STAT=`UN` (uninterruptible sleep), %CPU=4.2 (scheduled but stuck in kernel wait)
- Log mtime → unchanged for hours

**Diagnostic checklist when the watchdog flags idle:**
1. `ps -p $PID -o pid,etime,pcpu,stat,wchan` — STAT=`UN` is fatal. STAT=`R` or `S` with non-zero CPU means it might still recover.
2. Log mtime older than 20 min = wedged regardless of process state.
3. Always check the python CHILD process (`pgrep -P $PARENT_PID`) — parent often shows 0% CPU even when child does real work.

If any one signal is bad: `kill -TERM "$PID"`, wait 5s, `kill -KILL` only if TERM ignored.

### Marker model warmup

On a cold start (after restart), Marker spends **1–3 minutes** loading Surya layout/text/table-recognition models before processing the first file. The watchdog's progress signal stays at 0 during this. Don't false-alarm: initialize `last_counter=0` and only count idle ticks where `last_counter > 0` AND `CUR == last_counter`. This gives a free "first real tick" grace period.

### FileNotFoundError noise from mid-run cleanup

The smallest-first runner builds its candidate list ONCE at startup (via `rglob`). If you delete files mid-run, the runner will hit `FileNotFoundError` on each deleted entry — logged as failures (`✗`) but they aren't real.

Two corollaries:
- **Restart after pruning** — the new walk picks up only files that still exist.
- **Subtract FileNotFoundError from failure stats** — `grep -c FileNotFoundError $log` gives the count of phantom failures; real failures = `grep -cE '^\[.*✗' $log` minus that.

## Failure triage (ongoing)

Failures fall into three buckets (plus one new). Track them in `vault/notes/conversion-triage.yaml` (registry keyed by `source_file_sha256`, dedups forwarded copies). Use `scripts/conversion_triage.py status` to surface only NEW unreviewed failures.

| Disposition | When to use |
|---|---|
| `irrelevant` | Not financial, content captured elsewhere, vendor terms-of-service appendix. No follow-up. |
| `needs-password` | Encrypted PDF (Webb CPA tax docs, Fisher rental wires, Sutton renters policy). Won't convert without the password. Track for future password collection but don't retry on every run. |
| `needs-followup` | Real gap — financial content is missing and needs sourcing from the sender or a parallel document. |
| `blacklist_marker` | File hangs Marker twice; never invoke Marker on this SHA again. Docling-only (accept whatever, even empty). |

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
| Running with Marker enabled by default | Disable content-based fallback (`min_chars=0`) AND hard-disable on Docling-error (`CONVERT_NO_MARKER_ON_ERROR=1`). Selectively re-enable per-file via `forced_tool: marker+surya`. |
| Streaming the log to detect hangs | Use the cron+byte-size watchdog instead — progress bars hide via `\r`, and `wc -l` is also blind to them |
| Sourcing a heredoc'd state file | `source` evaluates shell syntax; leading whitespace in values silently breaks assignments. Use one-integer-per-line + `read -r` instead. |
| Using `pkill -f PATTERN` to kill the runner | Pattern matches unrelated processes (editors, grep itself). Write a PID file at startup; kill only that PID. |
| `pgrep` returning a PID = process is healthy | Insufficient. Check the python CHILD's STAT (`UN` = uninterruptible sleep = fatal) AND log mtime (>20 min stale = wedged regardless of process state) |
| Pre-cleanup using "no transaction link" rule | That deletes the matching pipeline's input. Use "no email row" rule instead. |
| Re-running and re-failing on password-protected PDFs every time | Mark them `needs-password` in `conversion-triage.yaml` AND add a pikepdf preflight to short-circuit before Marker |
| Hanging the same file again after a Marker timeout | Add to `conversion-triage.yaml` as `blacklist_marker` (per-SHA denylist) so future runs skip Marker entirely for that content |
| Pruning files mid-run | Runner caches candidate list at startup → phantom `FileNotFoundError` failures. Restart after pruning. Subtract `grep -c FileNotFoundError $log` from failure totals. |
| Counting all `^\[.*✗` lines as real failures | Many are FileNotFoundError noise from earlier prunes. Real failures = total ✗ minus FileNotFoundError. |
| False-alarming the watchdog during Marker model warmup | First 1–3 min after restart show no progress (Surya models loading). Initialize `last_counter=0` and require `last_counter > 0` before counting idle. |
| Relying solely on the 20-min watchdog | Add per-file `timeout 15m` wrapper so one stuck page can't eat the whole watchdog budget |
| Treating empty backend output as success | MarkItDown can return `""` without raising. Wrap with `if not body.strip(): status="conversion_failed"`. |
| Each writer calling `datetime.now()` independently | Sidecar and entity timestamps drift by however long the first write took. Compute in caller, pass to both. |
| Sequential single-process for >100 files in foreground | Background + watchdog |
| Assuming conversion writes to the DB | It doesn't — sidecars (gitignored) + vault entities (committed) only |
| Assuming `vault/documents/*.md` only contains successes | Also contains `conversion_failed` entities (by design, for `is_stale()` idempotency). Downstream code MUST gate on `status: success`. |
| Inventing CLI flags like `--dry-run` | Read the actual CLI source first; the real "what's pending" check is `is_stale()` over an `rglob` |
| `kill -9` to recover from hang | Use `kill -TERM` first so Python's resource_tracker cleans up its loky semaphores. Only escalate to KILL if TERM doesn't take in 5s. |
| Skipping the consent gate | Unattended `pkill` without approval violates policy. Record user authorization verbatim before starting the watchdog. |
| Running cron on a laptop | macOS/Linux cron doesn't fire during sleep; use launchd / systemd with `Persistent=yes`, or disable sleep for the run. |

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
- "`pkill -f convert` is close enough to a PID file" → no, pattern matches grep itself and anything else with that substring
- "I'll source the state file like a config" → no, use `read -r` on one-integer-per-line; never evaluate shell syntax from transient state
- "I don't need the user's approval to auto-restart a hung process" → wrong, destructive actions need explicit consent; record it
- "This vault/documents file has a path and a UID, therefore it's a successful conversion" → no, check `status: success` first; failed conversions also get entities
- "Marker hung once; try it again on the next run" → no, quarantine after 2 hangs via `blacklist_marker` — it's the same file, same hang

## Changelog

- **2026-04-19 (major revision)** — post-OpenAI-audit. Added: consent gate
  (step 0), runbook-at-a-glance, whitespace-proof state I/O, PID-file kill
  pattern, per-file timeout, watchdog preflight, laptop-sleep caveat,
  bad-actor Marker quarantine (`blacklist_marker` disposition), hard-disable
  Marker on Docling-error + encrypted PDF preflight, CID auto-detection with
  page/size guardrail, downstream `status: success` gating callout. Rank-
  ordered External Watchdog ahead of Smallest-first (hangs cost more than
  ordering). Elevated "don't prune mid-run" to a Red Flag.
- **2026-04-19** — failure-mode catalog pass. Added alive-but-stuck
  diagnostic checklist, FileNotFoundError phantom failures, Marker warmup
  grace window, empty-output guard pattern, mirrored-write derived state
  pattern. Replaced `wc -l` progress signal recommendation with byte size.
- **2026-04-19** — initial commit after the financial-tracker 2026-04-18/19
  archive re-conversion (1,121 entities, 3 passes, 2 Marker hangs).
