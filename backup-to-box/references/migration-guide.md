# Migration Guide: Bringing Older Projects to Current Standard

Use this when a project has an older backup.sh that's missing features from the current template. Compare each item against the project's existing script.

## Checklist

1. **Move hardcoded credentials to `.env`**
   - No `DB_PASS="password"` in the script
   - Source `.env` with `set -a; source .env; set +a`

2. **Add `flock` instance locking**
   - Prevents duplicate cron races that kill Box OAuth tokens
   - Add near top: `exec 9>"/tmp/${PROJECT_NAME}-backup.lock"; flock -n 9 || exit 0`

3. **Add `trap on_failure ERR` with email alert**
   - Script crashes should send Gmail notification
   - Use standalone alerter: `$HOME/.claude/skills/backup-to-box/templates/send_alert_standalone.py`

4. **Add Docker daemon startup check**
   - `docker info` check + `open -a Docker` if down
   - Needed because cron doesn't have a running Docker context

5. **Replace raw rclone calls with `rclone_copy()` function**
   - Old pattern: `rclone copy ... 2>&1 | while read` — swallows exit code
   - New pattern: mktemp logfile, capture `$?` directly

6. **Switch any `rclone sync` to `rclone copy`**
   - `sync` is destructive (deletes remote files not in local)
   - `copy` is additive only — safe for backups

7. **Add `.last_sync_status` tracking**
   - Write OK/FAILED/SKIPPED after Box sync
   - Read by SessionStart hook

8. **Add `.counts` + `.schema` sidecar generation**
   - `.counts`: row counts + aggregate checksums, named per-dump
   - `.schema`: `pg_dump --schema-only | gzip`, named per-dump
   - Both uploaded to Box alongside dump

9. **Create `backup/validate_restore_config.sh`**
   - Copy from `validate_restore_config.sh.example`
   - Set project's table list, checksum tables, dump pattern

10. **Create `backup/validate_queries.sql`** (optional)
    - Project-specific canary queries for Level 4 functional tests
    - See `validate_queries.sql.example` for patterns

11. **Install `validate_restore.sh` Sunday cron**
    - Stagger 30min between projects to avoid resource contention
    - `0 14 * * 0` for first project, `30 14 * * 0` for second, etc.

12. **Update `check_backup_age.sh`**
    - Add project to BACKUP_SOURCES array in the SessionStart hook
    - Or switch to the template version if using the old single-project hook

13. **Add Box keepalive cron**
    - `0 9 * * 0 rclone lsd box: > /dev/null 2>&1`
    - Defense-in-depth against 60-day token expiry
