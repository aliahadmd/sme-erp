"""Email job — sends transactional email via SMTP when configured.

Without SMTP_HOST the email body is logged instead of sent, so dev and CI
work without any mail infrastructure.
"""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.jobs.queue import register_job

logger = get_logger(__name__)


def _send_smtp(to: str, subject: str, body: str) -> None:
    settings = get_settings()
    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = settings.email_from
    message["To"] = to
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.sendmail(settings.email_from, [to], message.as_string())


@register_job
async def send_email(ctx: dict[str, Any], to: str, subject: str, body: str) -> dict[str, str]:
    settings = get_settings()
    if not settings.smtp_host:
        logger.info("email_logged_not_sent", to=to, subject=subject, length=len(body))
        return {"status": "logged"}
    _send_smtp(to, subject, body)
    logger.info("email_sent", to=to, subject=subject, length=len(body))
    return {"status": "sent"}
