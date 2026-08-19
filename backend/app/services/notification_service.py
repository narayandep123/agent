"""User notifications for major actions.

Sends a real email when SMTP is configured via environment variables; otherwise
falls back to console logging. Every notification is also kept in an in-memory
``OUTBOX`` so it can be surfaced in-app (and asserted in tests) even without an
email server.

Configure real delivery with:
    SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD,
    SMTP_FROM (default "CampusFlow AI <no-reply@campusflow.edu>"),
    SMTP_TLS ("false" to disable STARTTLS)
"""
import os
import smtplib
import threading
from datetime import datetime, timezone
from email.message import EmailMessage
from uuid import uuid4

OUTBOX: list[dict] = []  # newest first

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM", "CampusFlow AI <no-reply@campusflow.edu>")
SMTP_TLS = os.getenv("SMTP_TLS", "true").lower() != "false"


def _send_smtp(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
        if SMTP_TLS:
            server.starttls()
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)


def notify(to: str, subject: str, body: str) -> dict:
    """Record and deliver a notification. Never raises: delivery is best-effort."""
    if not to or "@" not in to:
        return {}
    entry = {
        "id": f"NTF-{uuid4().hex[:12].upper()}",
        "to": to,
        "subject": subject,
        "body": body,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "channel": "email" if SMTP_HOST else "console",
        "delivered": not SMTP_HOST,  # console mode is "delivered" immediately
        "read_at": None,
    }
    OUTBOX.insert(0, entry)
    if SMTP_HOST:
        def worker():
            try:
                _send_smtp(to, subject, body)
                entry["delivered"] = True
            except Exception as error:  # noqa: BLE001 - delivery must never break a request
                entry["error"] = str(error)
                print(f"[notify] SMTP delivery failed for {to}: {error}")
        threading.Thread(target=worker, daemon=True).start()
    else:
        print(f"\n[notify:console] To: {to}\nSubject: {subject}\n{body}\n")
    return entry


def inbox_for(email: str) -> list[dict]:
    return [{**n, "read_at": n.get("read_at")} for n in OUTBOX if n["to"] == email]


def mark_all_read(email: str) -> list[dict]:
    """Mark the user's current inbox as seen without deleting its history."""
    now = datetime.now(timezone.utc).isoformat()
    for notification in OUTBOX:
        if notification["to"] == email and not notification.get("read_at"):
            notification["read_at"] = now
    return inbox_for(email)
