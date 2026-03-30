# Claude Code Skills

Personal [Claude Code](https://claude.ai/claude-code) skills that are available across all projects on any machine.

## Setup

Clone to `~/.claude/skills/` on any machine where you use Claude Code:

```bash
git clone git@github.com:joelhsaltz/claude-skills.git ~/.claude/skills
```

Skills are automatically discovered by Claude Code — no registration or configuration needed. Each skill appears as a `/skill-name` slash command.

## Skills

### backup-to-box

Automated PostgreSQL backup system with Box.com remote sync via rclone. Includes:

- Parameterized backup script with `flock` locking and `rclone copy` sync
- 4-level weekly restore validation (structural, quantitative, checksum, functional)
- SessionStart hook that warns on stale backups, failed syncs, or missed validations
- Gmail alerts on any failure
- Box OAuth troubleshooting guide

Invoke with `/backup-to-box` in any project.
