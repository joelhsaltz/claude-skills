#!/usr/bin/env python3
"""Standalone Gmail alerter — zero project-code dependencies.

Sends email alerts via the Gmail API using OAuth credentials stored at
~/.gmail-mcp/. Designed for use by backup scripts and cron jobs.

Graceful failure: if anything goes wrong (missing deps, expired token,
network error), this script exits 0 and prints the error to stderr.
It must NEVER take down a calling backup script.

Requires: google-auth google-auth-oauthlib google-api-python-client
Install:  pip install --user google-auth google-auth-oauthlib google-api-python-client

Usage: send_alert_standalone.py "Subject line" "Body text"
"""

import sys
from pathlib import Path

ALERT_TO = "joelhsaltz@gmail.com"
ALERT_FROM = "joelhsaltz@gmail.com"
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
OAUTH_KEYS_PATH = Path.home() / ".gmail-mcp" / "gcp-oauth.keys.json"
CREDENTIALS_PATH = Path.home() / ".gmail-mcp" / "credentials.json"


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <subject> <body>", file=sys.stderr)
        return

    subject = sys.argv[1]
    body = sys.argv[2]

    try:
        import json
        import base64
        import fcntl
        from datetime import datetime, timezone
        from email.mime.text import MIMEText

        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        # Load client secrets for token refresh
        keys_data = json.loads(OAUTH_KEYS_PATH.read_text())
        client_config = keys_data.get("installed", keys_data.get("web", {}))
        client_id = client_config["client_id"]
        client_secret = client_config["client_secret"]
        token_uri = client_config.get("token_uri", "https://oauth2.googleapis.com/token")

        # Load saved credentials — format: {access_token, refresh_token, expiry_date, token_type}
        # expiry_date is milliseconds since epoch (MCP server convention)
        creds_data = json.loads(CREDENTIALS_PATH.read_text())
        expiry_ms = creds_data.get("expiry_date")
        expiry_dt = (
            datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
            if expiry_ms else None
        )

        # Do NOT pass scopes — the existing credential was created with broader
        # scopes by the MCP server. Passing scopes here triggers a scope
        # validation check on refresh that fails with invalid_scope.
        creds = Credentials(
            token=creds_data.get("access_token"),
            refresh_token=creds_data.get("refresh_token"),
            token_uri=token_uri,
            client_id=client_id,
            client_secret=client_secret,
            expiry=expiry_dt,
        )

        # Refresh if expired, with file locking to avoid races with MCP server
        if creds.expired:
            creds.refresh(Request())
            updated = {
                "access_token": creds.token,
                "refresh_token": creds.refresh_token,
                "expiry_date": int(creds.expiry.timestamp() * 1000) if creds.expiry else None,
                "token_type": "Bearer",
            }
            with open(CREDENTIALS_PATH, "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                json.dump(updated, f, indent=2)
                fcntl.flock(f, fcntl.LOCK_UN)

        # Build and send
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)

        message = MIMEText(body)
        message["to"] = ALERT_TO
        message["from"] = ALERT_FROM
        message["subject"] = subject

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()

        print(f"Alert sent: {subject}", file=sys.stderr)

    except Exception as e:
        # Graceful failure — never take down the calling script
        print(f"send_alert_standalone.py: failed to send alert: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
