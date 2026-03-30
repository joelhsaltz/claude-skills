---
name: backup-to-box
description: Use when setting up PostgreSQL backup infrastructure for a project, migrating an older backup script to the current standard, or troubleshooting Box.com sync failures and rclone token issues
---

# Backup to Box

Automated PostgreSQL backup system with Box.com remote sync, Gmail failure alerts, SessionStart health warnings, and weekly restore validation that proves backups are actually recoverable.

## Quick Setup (New Project)

Gather these variables, then render templates:

| Variable | Example | Purpose |
|----------|---------|---------|
| `PROJECT_NAME` | `financial-tracker` | Alerts, Box paths, lock files |
| `CONTAINER_NAME` | `financial_tracker_db` | Docker container to dump |
| `DB_USER` / `DB_NAME` | from `.env` | Database credentials |
| `BOX_FOLDER` | `financial-tracker` | Box.com subfolder |
| `HEALTH_CHECK_TABLE` | `transactions` | Primary table (refuse backup if empty) |
| `COUNT_TABLES` | `transactions vendors emails` | Tables for .counts sidecar |
| `CHECKSUM_TABLES` | `transactions vendors` | Subset with aggregate checksums |
| `MIN_DUMP_BYTES` | `10240` | Minimum dump size (recommend 5% of expected) |
| `EXTRA_SYNC_DIRS` | array or `()` | Additional rclone copy targets |

**Steps:**
1. Copy `templates/backup.sh.template` → `{project}/backup/backup.sh`, fill CONFIGURE block
2. Copy `templates/validate_restore_config.sh.example` → `{project}/backup/validate_restore_config.sh`
3. Optionally create `{project}/backup/validate_queries.sql` (see example for patterns)
4. Add project to `check_backup_age.sh` BACKUP_SOURCES array
5. Install crons (see below)

## Templates

| File | Purpose |
|------|---------|
| `backup.sh.template` | Core backup: flock, pg_dump, .counts/.schema sidecars, rclone copy, alerts |
| `validate_restore.sh.template` | 4-level weekly validation: structural, quantitative, checksum, functional |
| `check_backup_age.sh.template` | SessionStart hook: dump age, sync status, restore validation status |
| `send_alert_standalone.py` | Zero-dependency Gmail alerter (uses `~/.gmail-mcp/` OAuth) |

## Cron Schedule

```
# Backup 2x daily
0 8,20 * * *  {project}/backup/backup.sh >> {project}/backups/backup.log 2>&1

# Weekly restore validation (stagger 30min between projects)
0 14 * * 0    validate_restore.sh {project}/backup/validate_restore_config.sh >> {project}/backups/validation.log 2>&1

# Box OAuth keepalive (defense-in-depth against 60-day token expiry)
0 9 * * 0     /opt/homebrew/bin/rclone lsd box: > /dev/null 2>&1
```

## Key Design Decisions

- **`rclone copy` only** — never `sync` (additive, prevents remote deletions)
- **`flock` locking** — prevents duplicate cron races that kill Box OAuth tokens
- **Sidecars travel with dumps** — `.counts` and `.schema` uploaded to Box alongside each `.dump`
- **Restore validation downloads from Box** — proves the remote copy is intact, not just the local one
- **Checksums use aggregate fingerprints** — O(1) memory, detects corruption without string_agg explosion
- **Standalone alerter** — no project-code dependencies, works from any context

## Troubleshooting

See `references/box-oauth-troubleshooting.md` for token issues (most common: duplicate cron race, not 60-day expiry).

## Migrating Older Projects

See `references/migration-guide.md` for step-by-step checklist.
