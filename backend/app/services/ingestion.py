from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from triage_system import EmailInput, normalize_text, parse_email_bytes

from ..database import VAR_DIR


RAW_DIR = VAR_DIR / "raw_emails"
RAW_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class IngestedEmail:
    email_input: EmailInput
    raw_hash: str
    raw_path: Path
    normalized_text: str
    source_type: str


def source_type_from_filename(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".eml"):
        return "eml"
    if lower.endswith(".txt"):
        return "txt"
    return "text"


def ingest_email_bytes(raw_bytes: bytes, filename: str) -> IngestedEmail:
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()
    source_type = source_type_from_filename(filename)
    safe_suffix = "eml" if source_type == "eml" else "txt"
    raw_path = RAW_DIR / f"{raw_hash}.{safe_suffix}"
    if not raw_path.exists():
        raw_path.write_bytes(raw_bytes)
    email_input = parse_email_bytes(raw_bytes, filename)
    return IngestedEmail(
        email_input=email_input,
        raw_hash=raw_hash,
        raw_path=raw_path,
        normalized_text=normalize_text(email_input),
        source_type=source_type,
    )
