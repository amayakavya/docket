"""
File-level metadata extraction for ingested email files.

Extracts three layers from raw bytes:
  file          — size, extension, SHA-256, receipt timestamp
  email_headers — RFC 2822 headers (From, To, Date, Message-ID, DKIM, hops …)
  structure     — MIME layout, attachment names/sizes, body charset
  risk_signals  — derived flags useful for fraud/phishing triage

Returns a flat dict that is merged into Case.email_metadata.
All code paths are safe — any parse failure returns an empty sub-dict.
No external dependencies (stdlib only).
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email import policy as _ep
from email.headerregistry import Address
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any


# ── helpers ───────────────────────────────────────────────────────────────────

def _safe_header(msg, name: str) -> str | None:
    """Return decoded header value or None."""
    try:
        v = msg.get(name)
        return str(v).strip() if v else None
    except Exception:
        return None


def _addr_list(msg, name: str) -> list[str]:
    """Return a list of bare email addresses from a header."""
    raw = _safe_header(msg, name)
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",")]
    addrs = []
    for p in parts:
        _, addr = parseaddr(p)
        if addr and "@" in addr:
            addrs.append(addr.lower())
    return addrs


def _extract_ip(received: str) -> str | None:
    """Pull the first IPv4 address from a Received: header."""
    m = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", received)
    return m.group(1) if m else None


# ── main extractor ────────────────────────────────────────────────────────────

def extract_file_metadata(raw_bytes: bytes, filename: str) -> dict[str, Any]:
    """
    Return a metadata dict for one uploaded email file.

    The result is designed to be merged into Case.email_metadata — all keys
    are namespaced under "file_meta" so they cannot collide with existing keys.
    """
    now_utc = datetime.now(timezone.utc).isoformat()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
    sha256 = hashlib.sha256(raw_bytes).hexdigest()

    base: dict[str, Any] = {
        "file": {
            "filename": filename,
            "extension": ext,
            "size_bytes": len(raw_bytes),
            "sha256": sha256,
            "received_at_utc": now_utc,
        },
        "email_headers": {},
        "structure": {},
        "risk_signals": {},
    }

    if ext != "eml":
        # Plain text — minimal structural info only
        try:
            text = raw_bytes.decode("utf-8", errors="replace")
            base["structure"] = {
                "format": "plain_text",
                "line_count": text.count("\n"),
                "char_count": len(text),
            }
        except Exception:
            pass
        return base

    # ── Parse as RFC 2822 email ───────────────────────────────────────────────
    try:
        msg = BytesParser(policy=_ep.compat32).parsebytes(raw_bytes)
    except Exception:
        return base

    # ── Headers ──────────────────────────────────────────────────────────────
    message_id = _safe_header(msg, "Message-ID")

    # Sender
    from_raw = _safe_header(msg, "From") or ""
    from_name, from_addr = parseaddr(from_raw)
    from_addr = from_addr.lower() if from_addr else None

    # Reply-To
    reply_to_raw = _safe_header(msg, "Reply-To")
    _, reply_to_addr = parseaddr(reply_to_raw or "")
    reply_to_addr = reply_to_addr.lower() if reply_to_addr else None

    # Return-Path
    return_path_raw = _safe_header(msg, "Return-Path") or ""
    _, return_path_addr = parseaddr(return_path_raw)
    return_path_addr = return_path_addr.lower() if return_path_addr else None

    # Date
    email_date_str: str | None = None
    email_date_utc: str | None = None
    try:
        date_hdr = _safe_header(msg, "Date")
        if date_hdr:
            email_date_str = date_hdr
            dt = parsedate_to_datetime(date_hdr)
            email_date_utc = dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass

    # Received chain
    received_headers: list[str] = msg.get_all("Received") or []
    hop_count = len(received_headers)
    originating_ip: str | None = None
    if received_headers:
        # Originating IP is typically in the last (oldest) Received header
        originating_ip = _extract_ip(received_headers[-1])

    # Auth / signing
    dkim_signed = bool(msg.get("DKIM-Signature"))
    auth_results = _safe_header(msg, "Authentication-Results")
    spf_result: str | None = None
    dkim_result: str | None = None
    if auth_results:
        m_spf = re.search(r"spf=(\w+)", auth_results, re.I)
        m_dkim = re.search(r"dkim=(\w+)", auth_results, re.I)
        if m_spf:
            spf_result = m_spf.group(1).lower()
        if m_dkim:
            dkim_result = m_dkim.group(1).lower()

    # Spam scores
    spam_status = _safe_header(msg, "X-Spam-Status")
    spam_score_raw = _safe_header(msg, "X-Spam-Score")
    spam_score: float | None = None
    try:
        spam_score = float(spam_score_raw) if spam_score_raw else None
    except (ValueError, TypeError):
        pass

    headers: dict[str, Any] = {
        "message_id": message_id,
        "date": email_date_str,
        "date_utc": email_date_utc,
        "from_address": from_addr,
        "from_name": from_name or None,
        "to_addresses": _addr_list(msg, "To"),
        "cc_addresses": _addr_list(msg, "Cc"),
        "reply_to": reply_to_addr,
        "return_path": return_path_addr,
        "in_reply_to": _safe_header(msg, "In-Reply-To"),
        "references": (_safe_header(msg, "References") or "").split() or None,
        "subject": _safe_header(msg, "Subject"),
        "x_mailer": _safe_header(msg, "X-Mailer") or _safe_header(msg, "User-Agent"),
        "x_originating_ip": _safe_header(msg, "X-Originating-IP") or originating_ip,
        "received_hop_count": hop_count,
        "dkim_signed": dkim_signed,
        "dkim_result": dkim_result,
        "spf_result": spf_result,
        "x_spam_status": spam_status,
        "x_spam_score": spam_score,
        "mime_version": _safe_header(msg, "MIME-Version"),
    }
    base["email_headers"] = headers

    # ── MIME structure ────────────────────────────────────────────────────────
    content_type = _safe_header(msg, "Content-Type") or ""
    is_multipart = msg.is_multipart()
    charset: str | None = msg.get_content_charset()

    parts = list(msg.walk())
    part_count = len(parts)
    has_html = any(p.get_content_type() == "text/html" for p in parts)
    has_plain = any(p.get_content_type() == "text/plain" for p in parts)

    attachments: list[dict[str, Any]] = []
    total_body_bytes = 0
    for part in parts:
        disposition = part.get_content_disposition()
        if disposition == "attachment":
            fname = part.get_filename() or "unnamed"
            payload = part.get_payload(decode=True) or b""
            attachments.append({
                "filename": fname,
                "content_type": part.get_content_type(),
                "size_bytes": len(payload),
            })
        elif part.get_content_type() in ("text/plain", "text/html"):
            payload = part.get_payload(decode=True) or b""
            total_body_bytes += len(payload)

    base["structure"] = {
        "format": "eml",
        "content_type": content_type.split(";")[0].strip(),
        "charset": charset,
        "is_multipart": is_multipart,
        "part_count": part_count,
        "has_html_body": has_html,
        "has_plain_text_body": has_plain,
        "attachment_count": len(attachments),
        "attachments": attachments,
        "body_bytes": total_body_bytes,
    }

    # ── Risk signals ──────────────────────────────────────────────────────────
    reply_to_mismatch = bool(
        reply_to_addr and from_addr and reply_to_addr != from_addr
    )
    return_path_mismatch = bool(
        return_path_addr and from_addr and return_path_addr != from_addr
    )

    future_dated = False
    very_old_date = False
    if email_date_utc:
        try:
            email_dt = datetime.fromisoformat(email_date_utc)
            now_dt = datetime.now(timezone.utc)
            delta_hours = (email_dt - now_dt).total_seconds() / 3600
            future_dated = delta_hours > 1          # dated more than 1h in the future
            very_old_date = delta_hours < -8760     # older than 1 year
        except Exception:
            pass

    base["risk_signals"] = {
        "reply_to_mismatch": reply_to_mismatch,
        "return_path_mismatch": return_path_mismatch,
        "no_message_id": not bool(message_id),
        "dkim_absent": not dkim_signed,
        "dkim_failed": dkim_result == "fail" if dkim_result else False,
        "spf_failed": spf_result in ("fail", "softfail") if spf_result else False,
        "high_hop_count": hop_count > 10,
        "future_dated": future_dated,
        "very_old_date": very_old_date,
        "high_spam_score": (spam_score or 0.0) > 5.0,
    }

    return base
