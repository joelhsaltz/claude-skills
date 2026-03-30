# backup-to-box

Automated PostgreSQL backup system that syncs to Box.com via rclone, with Gmail failure alerts, SessionStart health warnings, and weekly restore validation that proves backups are actually recoverable.

## What it does

1. **Backs up** — `pg_dump` to timestamped `.dump` files with `.counts` (row counts + checksums) and `.schema` (structure) sidecar files
2. **Syncs** — `rclone copy` (additive, never destructive) to Box.com with per-folder failure tracking
3. **Alerts** — Gmail notification on any failure (backup, sync, or validation)
4. **Warns at session start** — stale backups, failed syncs, and missed validations surface as warnings when you open Claude Code
5. **Validates weekly** — downloads from Box, restores to a throwaway Docker container, and verifies at 4 levels:
   - **Structural** — `pg_restore` succeeds, all tables/indexes/constraints exist
   - **Quantitative** — row counts match sidecar exactly
   - **Content** — aggregate checksums match sidecar exactly
   - **Functional** — project-specific canary queries (joins, index probes, constraint enforcement)

## Usage

Invoke `/backup-to-box` in any Claude Code session. Claude will ask for your project's variables and generate all the files.

## Templates

| File | Purpose |
|------|---------|
| `backup.sh.template` | Core backup script with `flock` locking, sidecars, `rclone copy`, Gmail alerts |
| `validate_restore.sh.template` | 7-phase weekly restore validation (everything in a temp dir) |
| `check_backup_age.sh.template` | Multi-project SessionStart hook |
| `send_alert_standalone.py` | Zero-dependency Gmail alerter using `~/.gmail-mcp/` OAuth |
| `validate_restore_config.sh.example` | Per-project validation config (table list, checksum tables) |
| `validate_queries.sql.example` | Per-project canary queries for Level 4 functional tests |

## Per-project setup

Each project gets these files generated from the templates:

```
{project}/backup/
├── backup.sh                    # Rendered from template
├── validate_restore_config.sh   # Table list, checksum tables, dump pattern
├── validate_queries.sql         # Optional: project-specific canary queries
```

## Cron schedule

| Time | What |
|------|------|
| 2x daily (staggered per project) | Backup + Box sync |
| Sunday afternoon (staggered) | Restore validation |
| Sunday morning | Box OAuth keepalive |

## Prerequisites

- Docker Desktop (for PostgreSQL containers)
- `rclone` with a `box:` remote configured (`brew install rclone`)
- `flock` (`brew install flock`)
- PostgreSQL client tools (`brew install libpq`)
- GNU coreutils for `timeout` (`brew install coreutils`)
- Gmail OAuth credentials at `~/.gmail-mcp/` (for alerts)
- Python 3 with `google-auth`, `google-auth-oauthlib`, `google-api-python-client`

## Key design decisions

- **`rclone copy` only** — never `sync`, which deletes remote files
- **`flock` locking** — prevents duplicate cron instances from racing on Box's single-use OAuth refresh tokens
- **Sidecars travel with dumps** — `.counts` and `.schema` uploaded to Box alongside each `.dump`, so validation compares against the sidecar (not live production)
- **Checksums use aggregate fingerprints** — `md5(sum(id) || count(*) || sum(abs(amount)))` is O(1) memory
- **Restore validation downloads from Box** — proves the remote copy is intact, not just the local one
- **Everything in temp** — validation uses `mktemp -d` + throwaway Docker container, cleaned up via `trap cleanup EXIT`
- **Standalone alerter** — no project-code dependencies, works from any context

## References

- `references/box-oauth-troubleshooting.md` — token expiry, race conditions, reconnect procedure
- `references/migration-guide.md` — steps to bring older projects up to current standard

## Origin

Built during a Claude Code session after a Box OAuth failure went undetected at session start. The investigation revealed duplicate cron instances racing on Box's single-use refresh token rotation — which led to this comprehensive backup infrastructure with flock locking, 4-level restore validation, and proactive session warnings.
