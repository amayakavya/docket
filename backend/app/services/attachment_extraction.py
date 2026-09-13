"""
Attachment content extraction.

Re-parses the raw email bytes to extract actual attachment payloads (the triage
parser only stores filenames). Then extracts text and service entities from:

  PDF  — pdfplumber (accurate table/text extraction)
  JPG/PNG/TIFF — pytesseract OCR (graceful skip if Tesseract not installed)
  TXT/CSV — direct decode

Service entities extracted from attachment text:
  amounts          — ₹/Rs. figures
  reference_ids    — payment reference, transaction ref, UPI ref, transaction IDs (12+ digits)
  dates            — DD/MM/YYYY and DD-Mon-YYYY patterns
  connection_ids  — 11–18 digit connection ids
  error_codes      — provider error code patterns
"""
from __future__ import annotations

import io
import re
from email import policy
from email.parser import BytesParser
from typing import Any


# ── Entity extraction patterns ────────────────────────────────────────────────

_AMOUNT_RE = re.compile(
    r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d{1,2})?)", re.I
)
_UTR_RE = re.compile(
    r"\b(?:utr|utr\s*no\.?|transaction\s*(?:id|ref(?:erence)?))\s*[:#]?\s*([A-Z0-9]{12,22})\b",
    re.I,
)
_REF_RE = re.compile(
    r"\b(?:ref(?:erence)?\s*(?:no\.?|number|id|#)?|txn\s*(?:id|no)|"
    r"payment\s*ref|txn\s*ref|rrn|arn|approval\s*code)"
    r"\s*[:#]?\s*([A-Z0-9]{6,22})\b",
    re.I,
)
_DATE_RE = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s+"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{2,4})\b",
    re.I,
)
_ACCOUNT_RE = re.compile(r"\b(\d{11,18})\b")
_ERROR_CODE_RE = re.compile(
    # Require at least one digit so we don't match plain words like "Code"
    r"\b(?:error|err|error\s+code|err\s+code|failure\s+code)\s*[:#\s]?\s*([A-Z]{0,4}\d{2,8}[A-Z0-9]*)\b",
    re.I,
)


def _extract_entities(text: str) -> dict[str, list[str]]:
    """Extract service entities from a block of text."""

    def _dedup(items: list[str]) -> list[str]:
        seen: dict[str, None] = {}
        for x in items:
            seen[x] = None
        return list(seen.keys())

    amounts = _dedup([m.group(1).replace(",", "") for m in _AMOUNT_RE.finditer(text)])
    ref_ids = _dedup([m.group(1) for m in _UTR_RE.finditer(text)]
                    + [m.group(1) for m in _REF_RE.finditer(text)])
    dates = _dedup([m.group(1) for m in _DATE_RE.finditer(text)])
    accounts = _dedup([
        m.group(1) for m in _ACCOUNT_RE.finditer(text)
        if 11 <= len(m.group(1)) <= 18
    ])
    error_codes = _dedup([m.group(1) for m in _ERROR_CODE_RE.finditer(text)])

    return {
        "amounts": amounts[:10],
        "reference_ids": ref_ids[:10],
        "dates": dates[:10],
        "connection_ids": accounts[:10],
        "error_codes": error_codes[:5],
    }


# ── PDF extraction ────────────────────────────────────────────────────────────

def _extract_pdf_text(data: bytes) -> str:
    """Extract text from PDF bytes using pdfplumber (falls back to pypdf)."""
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            parts: list[str] = []
            for page in pdf.pages[:10]:  # cap at 10 pages
                t = page.extract_text()
                if t:
                    parts.append(t)
            return "\n".join(parts)
    except Exception:
        pass

    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(io.BytesIO(data))
        parts = []
        for page in reader.pages[:10]:
            t = page.extract_text()
            if t:
                parts.append(t)
        return "\n".join(parts)
    except Exception:
        return ""


# ── Image OCR ─────────────────────────────────────────────────────────────────

def _extract_image_text(data: bytes) -> str:
    """OCR an image attachment. Returns empty string if pytesseract not available."""
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
        img = Image.open(io.BytesIO(data))
        return pytesseract.image_to_string(img)
    except ImportError:
        return ""  # Tesseract not installed — skip silently
    except Exception:
        return ""


# ── Main extractor ────────────────────────────────────────────────────────────

def extract_attachments(raw_email_bytes: bytes) -> list[dict[str, Any]]:
    """
    Re-parse the raw email bytes and extract text + entities from each attachment.

    Returns a list of dicts, one per attachment:
        filename:     str
        content_type: str
        text:         str   — extracted text (may be empty)
        entities:     dict  — amounts, reference_ids, dates, connection_ids, error_codes
        extraction_method: str  — "pdf" | "ocr" | "plain" | "skipped"
        char_count:   int
    """
    results: list[dict[str, Any]] = []

    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw_email_bytes)
    except Exception:
        return results

    for part in msg.walk():
        if part.get_content_disposition() != "attachment":
            continue

        filename: str = part.get_filename() or "unnamed"
        content_type: str = part.get_content_type() or "application/octet-stream"

        payload = part.get_payload(decode=True)
        if not payload:
            continue

        text = ""
        method = "skipped"

        ct_lower = content_type.lower()
        fn_lower = filename.lower()

        if ct_lower == "application/pdf" or fn_lower.endswith(".pdf"):
            text = _extract_pdf_text(payload)
            method = "pdf"

        elif ct_lower.startswith("image/") or fn_lower.endswith((".png", ".jpg", ".jpeg", ".tiff", ".bmp")):
            text = _extract_image_text(payload)
            method = "ocr"

        elif ct_lower == "text/plain" or fn_lower.endswith((".txt", ".csv")):
            try:
                text = payload.decode("utf-8", errors="replace")
                method = "plain"
            except Exception:
                pass

        # Limit text to 4000 chars to avoid bloating the case record
        text = text[:4000].strip()

        results.append({
            "filename": filename,
            "content_type": content_type,
            "text": text,
            "entities": _extract_entities(text) if text else {
                "amounts": [], "reference_ids": [], "dates": [],
                "connection_ids": [], "error_codes": [],
            },
            "extraction_method": method,
            "char_count": len(text),
        })

    return results


def merge_attachment_entities(attachments: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Flatten all entity lists from multiple attachments into one deduplicated dict."""
    merged: dict[str, list[str]] = {
        "amounts": [], "reference_ids": [], "dates": [],
        "connection_ids": [], "error_codes": [],
    }
    seen: dict[str, set[str]] = {k: set() for k in merged}
    for att in attachments:
        for key, vals in att.get("entities", {}).items():
            if key in merged:
                for v in vals:
                    if v not in seen[key]:
                        merged[key].append(v)
                        seen[key].add(v)
    return merged
