"""Cloudflare Email Sending adapter; no implicit retry after an ambiguous send."""

import asyncio
import hashlib
import html
import json
from types import SimpleNamespace

from app.shared.notification_text import party_email

from erp_cf.db import plain
from erp_cf.errors import DomainError, ValidationError

PERMANENT = {
    "E_VALIDATION_ERROR",
    "E_FIELD_MISSING",
    "E_TOO_MANY_RECIPIENTS",
    "E_TOO_MANY_ATTACHMENTS",
    "E_SENDER_NOT_VERIFIED",
    "E_RECIPIENT_NOT_ALLOWED",
    "E_RECIPIENT_SUPPRESSED",
    "E_ACCOUNT_SUSPENDED",
    "E_SENDER_DOMAIN_NOT_AVAILABLE",
    "E_CONTENT_TOO_LARGE",
    "E_HEADER_NOT_ALLOWED",
    "E_HEADER_USE_API_FIELD",
    "E_HEADER_VALUE_INVALID",
    "E_HEADER_VALUE_TOO_LONG",
    "E_HEADER_NAME_INVALID",
    "E_HEADERS_TOO_LARGE",
    "E_HEADERS_TOO_MANY",
}
THROTTLED = {"E_RATE_LIMIT_EXCEEDED", "E_DAILY_LIMIT_EXCEEDED"}


def mode(env):
    value = str(getattr(env, "EMAIL_MODE", "disabled"))
    if value not in {"disabled", "test", "live"}:
        raise ValidationError("Invalid email delivery mode")
    return value


def sender(env):
    value = party_email(SimpleNamespace(emails=[str(getattr(env, "EMAIL_FROM", ""))]))
    if not value:
        raise DomainError("Email sender is not configured", code="email_unavailable", status=503)
    return value


def build_message(env, row):
    recipient = party_email(SimpleNamespace(emails=[row["recipient"]]))
    subject = row["subject"].replace("\r", " ").replace("\n", " ")
    if not recipient or len(subject.encode()) > 2000 or len(row["body"].encode()) > 131072:
        raise ValidationError("Invalid email recipient or content size")
    return {
        "to": recipient,
        "from": sender(env),
        "subject": subject,
        "text": row["body"],
        "html": '<div style="white-space:pre-wrap">' + html.escape(row["body"]) + "</div>",
        "headers": {"X-ERP-Mail-ID": row["id"], "Auto-Submitted": "auto-generated"},
    }


def payload_hash(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def error_code(exc):
    # JsException is a JsProxy: custom Error properties remain attributes.
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in PERMANENT | THROTTLED:
        return code
    return "send_outcome_unknown"


async def send(env, payload):
    if mode(env) != "live":
        raise DomainError("Live email is disabled", code="email_disabled", status=503)
    try:
        result = plain(await asyncio.wait_for(env.EMAIL.send(payload), timeout=60))
        identity = result["messageId"]
        if not isinstance(identity, str) or not identity.strip() or len(identity) > 256:
            raise ValueError("Invalid email acceptance response")
        return {"state": "accepted", "provider_id": identity, "code": None}
    except Exception as exc:
        code = error_code(exc)
        return {
            "state": "rejected" if code in PERMANENT | THROTTLED else "uncertain",
            "provider_id": None,
            "code": code,
        }
