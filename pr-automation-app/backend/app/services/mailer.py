from __future__ import annotations

import asyncio
import smtplib
import uuid
from email.message import EmailMessage as StdEmailMessage

import httpx

from app.core.config import get_settings


class MailDispatchError(Exception):
    """Raised when live mail dispatch fails or provider credentials are missing."""


async def dispatch_pitch_email(
    *,
    to_email: str,
    subject: str,
    body: str,
    rfc_message_id: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, str]:
    """Dispatch a pitch email via the configured MAIL_PROVIDER.

    Returns (provider_message_id, delivery_status).
    """
    settings = get_settings()
    provider = settings.MAIL_PROVIDER

    if provider == "dry_run":
        return f"dryrun-{uuid.uuid4().hex[:12]}", "sandboxed"

    if provider == "resend":
        if not settings.RESEND_API_KEY or not settings.RESEND_FROM_EMAIL:
            raise MailDispatchError(
                "MAIL_PROVIDER='resend' requires RESEND_API_KEY and RESEND_FROM_EMAIL."
            )
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0), transport=transport) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": settings.RESEND_FROM_EMAIL,
                    "to": [to_email],
                    "subject": subject,
                    "text": body,
                    "headers": {"Message-ID": rfc_message_id},
                },
            )
            if resp.status_code >= 400:
                raise MailDispatchError(f"Resend dispatch failed ({resp.status_code}): {resp.text[:200]}")
            msg_id = str(resp.json().get("id") or f"resend-{uuid.uuid4().hex[:12]}")
            return msg_id, "sent"

    if provider == "smtp":
        if not settings.SMTP_HOST or not (settings.SMTP_FROM_EMAIL or settings.SMTP_USER):
            raise MailDispatchError(
                "MAIL_PROVIDER='smtp' requires SMTP_HOST and SMTP_FROM_EMAIL/SMTP_USER."
            )
        from_addr = settings.SMTP_FROM_EMAIL or settings.SMTP_USER

        def _send_smtp() -> None:
            mime_msg = StdEmailMessage()
            mime_msg["From"] = from_addr
            mime_msg["To"] = to_email
            mime_msg["Subject"] = subject
            mime_msg["Message-ID"] = rfc_message_id
            mime_msg.set_content(body)
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
                server.starttls()
                if settings.SMTP_USER and settings.SMTP_PASSWORD:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(mime_msg)

        await asyncio.to_thread(_send_smtp)
        return f"smtp-{uuid.uuid4().hex[:12]}", "sent"

    raise MailDispatchError(
        f"MAIL_PROVIDER='{provider}' is not configured with OAuth credentials in this environment."
    )

