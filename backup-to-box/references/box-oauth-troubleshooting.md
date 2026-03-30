# Box.com OAuth Troubleshooting

## Most Common Cause: Duplicate Cron Races

Box OAuth uses **single-use refresh token rotation** — each refresh token can only be used once. If two backup instances fire simultaneously (duplicate cron triggers), they race to refresh the same token. The loser gets `invalid_grant` and Box invalidates the entire token chain.

**Symptoms:** `invalid_grant: maybe token expired?` in backup log, even though the token was recently created.

**Fix:** The backup.sh template includes `flock` locking to prevent this. If you see this error:
1. Check `backup.log` for paired "Backup started" entries at the same timestamp
2. Confirm `flock` is in your backup.sh (it should be)
3. Re-authenticate: `rclone config reconnect box:` (opens browser)

## Token Expiry (60-Day Inactivity)

Box refresh tokens expire after **60 days of application inactivity**. If no rclone operation touches Box for 60 days, the token dies silently.

**Prevention:** Weekly keepalive cron:
```
0 9 * * 0 /opt/homebrew/bin/rclone lsd box: > /dev/null 2>&1
```

**Recovery:** `rclone config reconnect box:` — interactive, requires browser. Cannot be automated from cron.

## Distinguishing Token Issues from Network Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `invalid_grant` | Token revoked (race or expiry) | `rclone config reconnect box:` |
| `connection refused` / timeout | Network issue | Check internet, try again |
| `403 Forbidden` | App permissions changed | Check Box developer console |
| `rclone: command not found` | PATH issue in cron | Add `/opt/homebrew/bin` to script PATH |

## Checking Token Status

```bash
# Test if current token works
rclone lsd box: 2>&1

# See token expiry time
rclone config show box 2>/dev/null | grep expiry

# Force a token refresh
rclone about box: 2>&1
```

## References

- [Box: Refresh Token Rotation](https://developer.box.com/guides/authentication/tokens/refresh)
- [rclone Box remote docs](https://rclone.org/box/)
