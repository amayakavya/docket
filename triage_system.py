#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import difflib
import email
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
import uuid
from datetime import datetime, timezone
from email import policy
from email.utils import getaddresses, parseaddr
from email.parser import BytesParser
from pathlib import Path
from typing import Any


PRIMARY_TYPES = {
    "A": "GENUINE_NEW_COMPLAINT",
    "B": "REPEAT_DUPLICATE_COMPLAINT",
    "C": "FOLLOW_UP_ON_EXISTING_CASE",
    "D": "NEW_ISSUE_FROM_EXISTING_COMPLAINT_CUSTOMER",
    "E": "REGULATORY_LEGAL_NOTICE",
    "F": "FRAUD_REPORT_BY_CUSTOMER",
    "G": "FRAUD_ATTEMPT_ON_BANK",
    "H": "GENUINE_QUERY",
    "I": "VENDOR_THIRD_PARTY_COMMUNICATION",
    "J": "INTERNAL_EMAIL_MISDIRECTED",
    "K": "ANONYMOUS_NO_CLEAR_IDENTITY",
    "L": "EMOTIONALLY_DISTRESSED_CUSTOMER",
    "M": "ABUSIVE_THREATENING_EMAIL",
    "N": "MEDIA_JOURNALIST_INQUIRY",
    "O": "BULK_AUTOMATED_EMAIL",
}

SECONDARY_TAGS = {
    "FINANCIAL-LOSS",
    "ACCOUNT-ACCESS",
    "DISCRIMINATION",
    "URGENCY-STATED",
    "ELDERLY-CUSTOMER",
    "DISABILITY-FLAG",
    "NRI-CUSTOMER",
    "MINOR-ACCOUNT",
    "DECEASED-ACCOUNT",
    "JOINT-ACCOUNT",
    "BUSINESS-ACCOUNT",
    "LEGAL-THREAT",
    "WELFARE-CONCERN",
    "THIRD-PARTY-CONTACT",
    "LOW-CONFIDENCE-PARSE",
    "INCOMPLETE-EMAIL",
    "STAFF-COMPLAINT",
    "POSITIVE-FEEDBACK",
    "AUTOMATED-SENDER",
    "NEEDS-HUMAN-REVIEW",
    "IMPERSONATION",
}

ROUTING_RULES = {
    "unauthorized transaction": ("Fraud", "Cybersecurity"),
    "card blocked": ("Retail Service", "Fraud"),
    "contract dispute": ("Contracts & Credit", "Legal"),
    "verification": ("Compliance", "Retail Service"),
    "account closure": ("Retail Service", "Compliance"),
    "interest rate dispute": ("Contracts & Credit", "Customer Relations"),
    "app issue": ("Tech Support", "Retail Service"),
    "card": ("Payments Ops", "Tech Support"),
    "autopay": ("Payments Ops", "Tech Support"),
    "wallet": ("Payments Ops", "Tech Support"),
    "add-on": ("Wealth / Deposits", "Retail Service"),
    "insurance": ("Wealth Management", "Compliance"),
    "investment": ("Wealth Management", "Compliance"),
    "discrimination": ("HR / Legal", "Compliance"),
    "regulatory notice": ("Legal", "Compliance + MD Office"),
    "media inquiry": ("Communications", "Legal"),
    "vendor payment dispute": ("Procurement", "Finance"),
    "service_area service complaint": ("Service area Operations", "Customer Relations"),
    "kiosk issue": ("Tech Support", "Retail Service"),
    "cybercrime": ("Cybersecurity", "Fraud + Legal"),
    "phishing": ("Cybersecurity", "Fraud + Legal"),
    "deceased": ("Legal", "Compliance + Retail"),
    "whistleblower": ("Compliance", "MD Office (confidential)"),
    "nri": ("NRI Services", "Compliance"),
}

SCORE_RULES = [
    ("fraud signal detected", 40),
    ("regulatory legal sender", 35),
    ("financial loss high", 30),
    ("financial loss low", 15),
    ("account access blocked", 20),
    ("repeat complaint", 25),
    ("sla breached", 30),
    ("distressed language", 20),
    ("stated urgency", 10),
    ("vulnerable customer", 15),
    ("vip business customer", 15),
    ("regulatory deadline", 25),
    ("first time complaint", 5),
    ("anonymous sender", 10),
    ("attachment present", 10),
    ("non business hours", 5),
]

REGULATORY_DOMAINS = {
    "regulator.example.gov",
    "ombudsman.example.gov",
    "gov.in",
    "nic.in",
    "courts.gov.in",
}

INTERNAL_DOMAINS = {
    "provider.internal",
    "provider.local",
    "mybank.com",
}

BLACKLIST_HINTS = {
    "mailinator",
    "guerrillamail",
    "tempmail",
    "10minutemail",
}

WEBMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "yahoo.com",
    "yahoo.co.in",
    "icloud.com",
    "me.com",
    "proton.me",
    "protonmail.com",
    "aol.com",
    "rediffmail.com",
    "zoho.com",
}

THREAT_PATTERNS = [
    r"\bkill myself\b",
    r"\bend my life\b",
    r"\bsuicide\b",
    r"\bself harm\b",
    r"\bhurt myself\b",
]

ABUSE_PATTERNS = [
    r"\bi will come to your service_area\b",
    r"\bthreat\b",
    r"\bviolence\b",
    r"\battack\b",
]

FRAUD_PATTERNS = [
    r"\bunauthori[sz]ed transaction\b",
    r"\bunauthori[sz]ed debit\b",
    r"\bwithout authorization\b",
    r"\bdebited from my\b",
    r"\bfraud\b",
    r"\bphishing\b",
    r"\bcard clon",
    r"\bidentity theft\b",
]

# Patterns that suggest the sender is IMPERSONATING Docket staff, regulators, law enforcement,
# or government officials — a serious social-engineering signalnal.
IMPERSONATION_PATTERNS = [
    r"\bi am (?:an? )?(?:regulator|ombudsman|tax officer|police officer|inspector)\b",
    r"\bfrom (?:the )?(?:regulator|ombudsman|tax department|police|licensing authority)\b",
    r"\bacting (?:on behalf of|as) (?:the provider|the regulator|head office)\b",
    r"\bbank (?:officer|official|manager|employee|representative|helpdesk|support team|customer care)\b",
    r"\b(?:regulatory|nodal) (?:officer|official|inspector|representative)\b",
    r"\bpolice (?:officer|constable|inspector|commissioner)\b.*\bbank\b",
    r"\bcourt order\b.*\brelease\b",
    r"\bfreeze\b.*\bimmediately\b.*\baccount\b",
    r"\bverif(?:y|ication) (?:your )?(?:otp|pin|password|cvv|connection id|aadhaar)\b",
    r"\bsend (?:your )?(?:otp|pin|cvv|password|account (?:number|details))\b",
    r"\bclick (?:here|the link|below) to (?:verify|update|confirm|secure|unfreeze)\b",
    r"\byour account (?:will be|has been) (?:blocked|suspended|frozen|deactivated)\b.*(?:click|call|share)\b",
    r"\bcall (?:our )?(?:helpline|support|toll.free)\b.*\bimmediately\b",
]

CASE_FORM_TEMPLATE = {
    "customer_name": None,
    "customer_email": None,
    "customer_phone": None,
    "customer_id": None,
    "connection_id_masked": None,
    "account_type": None,
    "customer_segment": None,
    "is_nri": False,
    "is_senior_citizen": False,
    "is_minor_related": False,
    "is_joint_account": False,
    "product_type": None,
    "service_line": None,
    "issue_category": None,
    "issue_subcategory": None,
    "complaint_nature": None,
    "incident_date": None,
    "reported_date": None,
    "channel": None,
    "transaction_type": None,
    "transaction_reference": None,
    "merchant_or_counterparty": None,
    "area_code_or_location": None,
    "city_or_region": None,
    "currency": None,
    "amount_involved": None,
    "amount_disputed": None,
    "financial_impact": None,
    "fraud_indicator": False,
    "legal_threat_indicator": False,
    "welfare_risk_indicator": False,
    "sensitive_data_present": False,
    "attachments_present": False,
    "links_present": False,
    "regulatory_deadline": None,
    "requested_action": None,
    "service_summary": None,
    "operational_notes": None,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_email_address(value: str | None) -> str:
    if not value:
        return ""
    _name, addr = parseaddr(value)
    return addr.strip().lower()


def extract_display_name(value: str | None) -> str | None:
    if not value:
        return None
    name, addr = parseaddr(value)
    cleaned = name.strip().strip('"').strip("'")
    if cleaned:
        return cleaned
    if addr:
        local = addr.split("@", 1)[0].replace(".", " ").replace("_", " ").replace("-", " ")
        local = re.sub(r"\s+", " ", local).strip()
        return local.title() if local else None
    return None


def mask_email(addr: str) -> str:
    if "@" not in addr:
        return addr[:2] + "***"
    name, domain = addr.split("@", 1)
    masked_name = (name[:2] + "***") if name else "***"
    domain_parts = domain.split(".")
    if domain_parts:
        domain_parts[0] = domain_parts[0][:2] + "***"
    return masked_name + "@" + ".".join(domain_parts)


def mask_sensitive_data(text: str) -> str:
    text = re.sub(r"\b\d{12}\b", lambda m: "XXXXXXXX" + m.group(0)[-4:], text)
    text = re.sub(r"\b[A-Z]{5}\d{4}[A-Z]\b", lambda m: "XXXXXX" + m.group(0)[-4:], text)
    text = re.sub(r"\b\d{9,18}\b", lambda m: ("X" * max(0, len(m.group(0)) - 4)) + m.group(0)[-4:], text)
    return text


def clean_llm_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", str(value))
    # Gemma sometimes line-wraps inside strings — collapse all whitespace runs to single space
    text = re.sub(r"[\r\n]+", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    if text.lower() in {"null", "none", "unknown", "n/a", "na", "string or null", "number or null",
                        "not applicable", "not available", "not specified", "not provided"}:
        return None
    return text or None


def parse_money_value(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "")
    match = re.search(r"(-?\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def find_phone(text: str) -> str | None:
    match = re.search(r"(?:\+91[-\s]?)?[6-9]\d{9}\b", text)
    return match.group(0) if match else None


def find_transaction_reference(text: str) -> str | None:
    # Only extract genuine transaction/reference identifiers — must be preceded by a label keyword
    patterns = [
        r"\b(?:utr|rrn|txn(?:\s*id)?|transaction\s*(?:id|ref(?:erence)?|no\.?)|ref(?:erence)?\s*(?:no\.?|id|#)?)\s*[:#-]?\s*([A-Z0-9]{6,24})\b",
        r"\b((?:IN|BET|RBX|UTR|NFS)\d{9,20})\b",           # structured payment tokens
        r"\b(\d{12,22})\b",                                  # 12-22 digit numeric refs (UTR format)
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            candidate = match.group(1).upper()
            # Reject plain English words accidentally captured
            if re.fullmatch(r"[A-Z]{3,}", candidate):
                continue
            return candidate
    return None


def guess_channel(text: str) -> str | None:
    lower = text.lower()
    mapping = {
        "self care portal": "Internet Service",
        "net service": "Internet Service",
        "mobile app": "Mobile App",
        "upi": "UPI",
        "kiosk": "service kiosk",
        "service_area": "Service area",
        "device": "Device",
        "device": "Device",
        "wallet": "wallet",
        "card": "card",
        "autopay": "autopay",
        "pos": "POS",
    }
    for key, value in mapping.items():
        if key in lower:
            return value
    return None


def guess_product_type(text: str) -> str | None:
    lower = text.lower()
    mapping = {
        "salary account": "Savings Account",
        "savings account": "Savings Account",
        "current account": "Current Account",
        "device": "Device",
        "device": "Device",
        "contract": "Contract",
        "fd": "Fixed Deposit",
        "add-on": "Fixed Deposit",
        "insurance": "Insurance",
        "mutual fund": "Investment",
        "upi": "Payments",
    }
    for key, value in mapping.items():
        if key in lower:
            return value
    return None


def detect_explicit_date(text: str) -> str | None:
    patterns = [
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{2,4}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(0)
    return None


def has_connection_id_evidence(text: str) -> bool:
    return bool(re.search(r"\b(?:account|a/c)\s*(?:number|no)?\s*[:#-]?\s*\d{6,18}\b", text, re.I))


def ground_case_form(case_form: dict[str, Any], parsed_text: str, email_input: EmailInput) -> dict[str, Any]:
    grounded = dict(case_form)
    explicit_date = detect_explicit_date(parsed_text)
    if not re.search(r"\b(?:on|dated|date of|txn date|transaction on|incident on)\b", parsed_text, re.I):
        grounded["incident_date"] = None
    if not explicit_date:
        grounded["regulatory_deadline"] = None
    if not has_connection_id_evidence(parsed_text):
        grounded["connection_id_masked"] = None
    if not find_phone(parsed_text):
        grounded["customer_phone"] = None
    sender_email = email_input.sender if email_input.sender and "@" in email_input.sender else None
    recipient_set = {addr.lower() for addr in email_input.recipients}
    if grounded.get("customer_email"):
        field_email = grounded["customer_email"].strip().lower()
        if sender_email and field_email != sender_email.lower():
            grounded["customer_email"] = sender_email
        elif field_email in recipient_set:
            grounded["customer_email"] = sender_email
    grounded["attachments_present"] = bool(email_input.attachments)
    grounded["links_present"] = bool(email_input.links)
    grounded["reported_date"] = datetime.now().date().isoformat()
    if not grounded.get("customer_name"):
        grounded["customer_name"] = extract_display_name(email_input.headers.get("from")) or extract_display_name(email_input.sender)
    return grounded


@dataclasses.dataclass
class EmailInput:
    source_path: str
    raw_bytes: bytes
    subject: str
    sender: str
    recipients: list[str]
    body: str
    headers: dict[str, str]
    attachments: list[str]
    links: list[str]


def parse_email_bytes(raw_bytes: bytes, source_path: str = "memory.txt") -> EmailInput:
    subject = ""
    sender = ""
    recipients: list[str] = []
    body = raw_bytes.decode("utf-8", errors="replace")
    headers: dict[str, str] = {}
    attachments: list[str] = []
    links = re.findall(r"https?://\S+", body)

    if source_path.lower().endswith((".eml", ".msg")) or b"From:" in raw_bytes[:500]:
        msg = BytesParser(policy=policy.default).parsebytes(raw_bytes)
        subject = str(msg.get("subject", "")).strip()
        sender = extract_email_address(str(msg.get("from", "")).strip())
        recipient_headers = [str(msg.get(key, "")).strip() for key in ("to", "cc", "bcc")]
        recipients = [addr.lower() for _name, addr in getaddresses(recipient_headers) if addr]
        headers = {k.lower(): str(v) for k, v in msg.items()}
        parts: list[str] = []
        for part in msg.walk():
            content_disposition = part.get_content_disposition()
            if content_disposition == "attachment":
                attachments.append(part.get_filename() or "unnamed_attachment")
                continue
            if part.get_content_type() == "text/plain":
                try:
                    parts.append(part.get_content())
                except Exception:
                    payload = part.get_payload(decode=True) or b""
                    parts.append(payload.decode("utf-8", errors="replace"))
        if parts:
            body = "\n".join(parts)
        links = re.findall(r"https?://\S+", body)
    else:
        lines = body.splitlines()
        header_body_index = 0
        for idx, line in enumerate(lines[:40]):
            if not line.strip():
                header_body_index = idx + 1
                break
            header_match = re.match(r"^\s*([A-Za-z-]+)\s*:\s*(.+)$", line)
            if header_match:
                headers[header_match.group(1).lower()] = header_match.group(2).strip()
        subject = headers.get("subject", lines[0][:160] if lines else "")
        sender = extract_email_address(headers.get("from", "").strip())
        recipient_text = " ".join(filter(None, [headers.get("to", ""), headers.get("cc", ""), headers.get("bcc", "")]))
        recipients = re.findall(r"[\w.+-]+@[\w.-]+\.\w+", recipient_text)
        if header_body_index:
            body = "\n".join(lines[header_body_index:]).strip() or body
        if not sender:
            sender = extract_sender_from_text(body)

    return EmailInput(
        source_path=source_path,
        raw_bytes=raw_bytes,
        subject=subject,
        sender=sender,
        recipients=recipients,
        body=body,
        headers=headers,
        attachments=attachments,
        links=links,
    )


def parse_email_file(path: Path) -> EmailInput:
    return parse_email_bytes(path.read_bytes(), str(path))


def extract_sender_from_text(text: str) -> str:
    match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", text)
    return match.group(0) if match else "unknown@unknown"


class AuditLog:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def add(self, action: str, detail: str, level: str = "INFO") -> None:
        self.entries.append(
            {
                "ts": utc_now_iso(),
                "level": level,
                "action": action,
                "detail": detail,
            }
        )


class LocalStore:
    def __init__(self, db_path: Path, session_started_at: str | None = None) -> None:
        self.db_path = db_path
        self.session_started_at = session_started_at
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS triage_runs (
                ticket_id TEXT PRIMARY KEY,
                received_at TEXT NOT NULL,
                sender_email TEXT NOT NULL,
                raw_email_hash TEXT NOT NULL,
                primary_type TEXT NOT NULL,
                secondary_tags TEXT NOT NULL,
                department_primary TEXT NOT NULL,
                department_secondary TEXT NOT NULL,
                priority_score INTEGER NOT NULL,
                priority_tier TEXT NOT NULL,
                override_applied INTEGER NOT NULL,
                status TEXT NOT NULL,
                parsed_text TEXT NOT NULL,
                result_json TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def find_duplicate(self, raw_hash: str, sender_email: str, subject: str) -> str | None:
        if self.session_started_at:
            row = self.conn.execute(
                """
                SELECT ticket_id
                FROM triage_runs
                WHERE received_at >= ?
                  AND (
                    raw_email_hash = ?
                    OR (sender_email = ? AND parsed_text LIKE ?)
                  )
                ORDER BY received_at DESC
                LIMIT 1
                """,
                (self.session_started_at, raw_hash, sender_email, f"%{subject[:60]}%"),
            ).fetchone()
        else:
            row = self.conn.execute(
                """
                SELECT ticket_id
                FROM triage_runs
                WHERE raw_email_hash = ?
                   OR (sender_email = ? AND parsed_text LIKE ?)
                ORDER BY received_at DESC
                LIMIT 1
                """,
                (raw_hash, sender_email, f"%{subject[:60]}%"),
            ).fetchone()
        return row[0] if row else None

    def save(self, result: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO triage_runs (
                ticket_id, received_at, sender_email, raw_email_hash, primary_type,
                secondary_tags, department_primary, department_secondary,
                priority_score, priority_tier, override_applied, status,
                parsed_text, result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result["ticket_id"],
                result["received_at"],
                result["sender_email_masked"],
                result["raw_email_hash"],
                result["primary_type"],
                json.dumps(result["secondary_tags"]),
                result["department_primary"],
                result["department_secondary"],
                result["priority_score"],
                result["priority_tier"],
                1 if result["override_applied"] else 0,
                result["status"],
                result["parsed_text"],
                json.dumps(result, ensure_ascii=True),
            ),
        )
        self.conn.commit()

    def list_queue(self, limit: int = 25) -> list[dict[str, Any]]:
        if self.session_started_at:
            rows = self.conn.execute(
                """
                SELECT result_json
                FROM triage_runs
                WHERE received_at >= ?
                ORDER BY
                    CASE priority_tier
                        WHEN 'P1' THEN 1
                        WHEN 'P2' THEN 2
                        WHEN 'P3' THEN 3
                        ELSE 4
                    END ASC,
                    priority_score DESC,
                    received_at DESC
                LIMIT ?
                """,
                (self.session_started_at, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT result_json
                FROM triage_runs
                ORDER BY
                    CASE priority_tier
                        WHEN 'P1' THEN 1
                        WHEN 'P2' THEN 2
                        WHEN 'P3' THEN 3
                        ELSE 4
                    END ASC,
                    priority_score DESC,
                    received_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_case(self, ticket_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT result_json
            FROM triage_runs
            WHERE ticket_id = ?
            LIMIT 1
            """,
            (ticket_id,),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def clear_all_cases(self) -> None:
        self.conn.execute("DELETE FROM triage_runs")
        self.conn.commit()

    def list_all_cases(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT result_json
            FROM triage_runs
            ORDER BY received_at DESC
            """
        ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def update_case(self, ticket_id: str, updater: Any) -> dict[str, Any] | None:
        case = self.get_case(ticket_id)
        if not case:
            return None
        updated = updater(dict(case))
        if not updated:
            return None
        self.save(updated)
        return updated


def normalize_text(email_input: EmailInput) -> str:
    recipients = ", ".join(email_input.recipients)
    combined = f"Subject: {email_input.subject}\nFrom: {email_input.sender}\nTo: {recipients}\n\n{email_input.body}"
    cleaned = combined.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return mask_sensitive_data(cleaned)


def sender_domain(sender: str) -> str:
    match = re.search(r"@([\w.-]+\.\w+)", sender)
    return match.group(1).lower() if match else "unknown"


def classify_sender_origin(sender: str) -> str:
    domain = sender_domain(sender)
    if sender == "unknown@unknown" or domain == "unknown":
        return "unknown"
    if domain in REGULATORY_DOMAINS or domain.endswith(".gov.in"):
        return "regulatory"
    if domain in INTERNAL_DOMAINS or domain.endswith(".internal"):
        return "internal_or_bank"
    if domain in WEBMAIL_DOMAINS:
        return "external_webmail"
    if any(hint in domain for hint in BLACKLIST_HINTS):
        return "disposable_or_suspicious"
    return "external_domain"


def run_trust_checks(email_input: EmailInput, parsed_text: str, audit: AuditLog) -> dict[str, Any]:
    sender = email_input.sender or "unknown@unknown"
    domain = sender_domain(sender)
    sender_origin = classify_sender_origin(sender)
    verified = sender_origin in {"regulatory", "internal_or_bank"}
    suspicious_domain = any(hint in domain for hint in BLACKLIST_HINTS)
    has_attachments = bool(email_input.attachments)
    has_links = bool(email_input.links)
    unknown_sender = sender == "unknown@unknown" or sender.startswith("unknown")
    business_hours = 4 <= datetime.now().hour <= 20

    audit.add("sender_check", f"sender={mask_email(sender)} domain={domain} origin={sender_origin}")
    if sender_origin == "external_webmail":
        audit.add("sender_profile", "customer appears to be writing from a standard consumer email service")
    elif sender_origin == "external_domain":
        audit.add("sender_profile", "customer or third party appears to be writing from an external organization domain")
    elif verified:
        audit.add("sender_profile", "sender appears to be from a regulatory, internal, or provider-controlled domain")
    elif sender_origin == "unknown":
        audit.add("sender_profile", "sender address could not be established from the email", level="WARN")
    if suspicious_domain:
        audit.add("domain_risk", "sender domain matched suspicious/disposable heuristic", level="WARN")
    if has_attachments:
        audit.add("attachment_check", f"attachments detected: {email_input.attachments}")
    if has_links:
        audit.add("link_check", f"links detected: {email_input.links[:5]}")
    if unknown_sender:
        audit.add("identity", "sender identity unclear", level="WARN")

    return {
        "sender_verified": verified,
        "suspicious_domain": suspicious_domain,
        "has_attachments": has_attachments,
        "has_links": has_links,
        "unknown_sender": unknown_sender,
        "business_hours": business_hours,
        "domain": domain,
        "sender_origin": sender_origin,
    }


def format_amount_for_audit(amount: Any, currency: str | None) -> str:
    parsed = parse_money_value(amount)
    if parsed is None:
        return "amount not identified"
    currency = currency or "INR"
    return f"{currency} {parsed:,.0f}"


def build_meaningful_audit(
    email_input: EmailInput,
    result: dict[str, Any],
    match_details: dict[str, Any],
    trust: dict[str, Any],
    score_factors_detected: list[str],
    model_used: str,
    model_fallback_used: bool,
    base_audit: AuditLog,
) -> list[dict[str, Any]]:
    audit = AuditLog()
    form = result.get("case_form", {})
    sender_origin = trust.get("sender_origin", "unknown")
    sender_label = email_input.sender or "unknown@unknown"
    recipient_label = ", ".join(email_input.recipients[:5]) if email_input.recipients else "not identified"
    amount_text = format_amount_for_audit(form.get("amount_involved"), form.get("currency"))
    if sender_origin == "external_webmail":
        sender_note = "Sender is using a standard consumer email service, which is normal for customer complaints."
    elif sender_origin == "external_domain":
        sender_note = "Sender is using an external organization email domain."
    elif sender_origin == "internal_or_bank":
        sender_note = "Sender address appears to belong to the provider or an internal domain."
    elif sender_origin == "regulatory":
        sender_note = "Sender address appears to belong to a regulatory or government domain."
    else:
        sender_note = "Sender address could not be confirmed from the message."
    audit.add("intake", f"Email ingested from {sender_label} to {recipient_label}. {sender_note}")

    if model_fallback_used:
        audit.add("ai_evaluation", "Primary AI evaluation did not return usable JSON, so fallback rules were applied.", level="WARN")
    else:
        audit.add("ai_evaluation", f"Gemma completed full-email evaluation using model {model_used}.")

    audit.add(
        "classification",
        f"Classified as {result.get('primary_type')} ({result.get('primary_type_label')}). "
        f"Department mapped to {result.get('department_primary')} with secondary route {result.get('department_secondary')}.",
    )

    audit.add(
        "customer_context",
        f"Customer identified as {form.get('customer_name') or 'not identified'}; "
        f"customer email recorded as {form.get('customer_email') or 'not identified'}; "
        f"channel assessed as {form.get('channel') or 'not identified'}.",
    )

    audit.add(
        "exposure_assessment",
        f"Financial exposure assessed as {amount_text}; fraud indicator={bool(form.get('fraud_indicator'))}; "
        f"regulatory deadline={form.get('regulatory_deadline') or 'not identified'}.",
    )

    factor_text = ", ".join(score_factors_detected) if score_factors_detected else "no major score factors detected"
    audit.add(
        "priority_reasoning",
        f"Priority assigned as {result.get('priority_tier')} with score {result.get('priority_score')}. "
        f"Factors considered: {factor_text}.",
    )

    match_state = result.get("database_matching_state")
    if match_state == "EXACT_MATCH":
        audit.add(
            "history_match",
            f"Matched to existing active case {match_details.get('matched_ticket_id')} by explicit token reference. "
            "Queue entry should be updated instead of creating a new thread.",
        )
    elif match_state == "PARTIAL_MATCH":
        audit.add(
            "history_match",
            f"Likely linked to prior case {match_details.get('matched_ticket_id') or 'unknown'} based on duplicate or similarity signals. "
            "Case should be reviewed for merge and supervisor check.",
        )
    else:
        audit.add("history_match", "No prior active case match was established. New token generated for this complaint.")

    audit.add(
        "downstream_action",
        f"Operational action selected: {result.get('downstream_action')}. "
        f"Acknowledgment policy: {'auto response allowed' if result.get('acknowledgment', {}).get('send_auto_ack') else 'human acknowledgment only'}.",
    )

    if result.get("response_draft"):
        audit.add("response_prepared", "A response draft was prepared based on the current case state.")

    for entry in base_audit.entries:
        if entry["action"] in {"domain_risk", "override", "identity", "llm_failure"}:
            audit.add(entry["action"], entry["detail"], level=entry["level"])

    return audit.entries


def check_overrides(parsed_text: str, trust: dict[str, Any], audit: AuditLog) -> tuple[bool, list[str]]:
    lower = parsed_text.lower()
    reasons: list[str] = []
    fraud_signal = any(re.search(pattern, lower) for pattern in FRAUD_PATTERNS)
    formal_regulatory_language = any(
        term in lower
        for term in [
            "regulatory notice",
            "court notice",
            "show cause notice",
            "statutory direction",
            "inspection officer",
        ]
    )
    # Customers frequently cite the regulator protection guidelines while reporting fraud.
    # A reference in the message body must not impersonate a regulatory sender.
    if trust.get("sender_origin") == "regulatory" or (formal_regulatory_language and not fraud_signal):
        reasons.append("regulatory_or_legal_sender")
    if fraud_signal and "in progress" in lower:
        reasons.append("active_fraud_in_progress")
    if any(re.search(p, lower) for p in THREAT_PATTERNS):
        reasons.append("self_harm_or_crisis_language")
    if trust["suspicious_domain"] and fraud_signal:
        reasons.append("known_threat_actor_signal")
    if "deceased" in lower or "death certificate" in lower:
        reasons.append("deceased_account_sensitivity")

    # Impersonation detection — sender pretends to be Docket staff, the regulator, law enforcement, etc.
    # Flag even if origin looks legitimate (social engineering can come from hijacked domains).
    impersonation_hits = [p for p in IMPERSONATION_PATTERNS if re.search(p, lower)]
    if impersonation_hits:
        # Only flag as impersonation when the sender is NOT an internal/regulatory domain.
        # A genuine message from the regulator may contain some of these phrases legitimately.
        if trust.get("sender_origin") not in {"internal_or_bank", "regulatory"}:
            reasons.append("impersonation_attempt_detected")
            audit.add(
                "impersonation",
                f"impersonation signal in email body: {len(impersonation_hits)} pattern(s) matched "
                f"(sender origin: {trust.get('sender_origin', 'unknown')})",
                level="WARN",
            )

    if reasons:
        audit.add("override", f"auto-P1 override triggered: {', '.join(reasons)}", level="WARN")
    return bool(reasons), reasons


def build_prompt(parsed_text: str, trust: dict[str, Any], duplicate_of: str | None) -> str:
    detected_lang = detect_language(parsed_text)
    return textwrap.dedent(
        f"""
        You are an expert email triage classifier for Docket (Docket). Return ONLY valid JSON — no markdown, no explanation, no preamble.
        CRITICAL: Do NOT insert line breaks or newlines inside any string value. Every string must be a single line.

        ## Classification types
        {json.dumps(PRIMARY_TYPES, indent=2)}

        ## Allowed secondary tags (use AT MOST 3)
        {json.dumps(sorted(SECONDARY_TAGS))}

        ## Prior duplicate ticket
        {duplicate_of or "none — treat as new"}

        ## Detected language hint
        Script/language detected in email: {detected_lang}

        ## Output schema — fill with real values, never leave placeholder text
        {{
          "primary_type": "A",
          "primary_type_confidence": 0.85,
          "abstain": false,
          "abstain_reason": null,
          "secondary_tags": ["FINANCIAL-LOSS"],
          "issue_summary": "One formal sentence summarising the customer's issue.",
          "customer_language": "{detected_lang}",
          "requires_human_review": false,
          "suggested_routing_reason": "One sentence explaining why you chose this department.",
          "case_form": {{
            "customer_name": null,
            "customer_email": null,
            "customer_phone": null,
            "customer_id": null,
            "connection_id_masked": null,
            "account_type": null,
            "customer_segment": "Retail",
            "is_nri": false,
            "is_senior_citizen": false,
            "is_minor_related": false,
            "is_joint_account": false,
            "product_type": null,
            "service_line": null,
            "issue_category": null,
            "issue_subcategory": null,
            "complaint_nature": null,
            "incident_date": null,
            "reported_date": null,
            "channel": null,
            "transaction_type": null,
            "transaction_reference": null,
            "merchant_or_counterparty": null,
            "area_code_or_location": null,
            "city_or_region": null,
            "currency": "INR",
            "amount_involved": 0,
            "amount_disputed": 0,
            "financial_impact": null,
            "fraud_indicator": false,
            "legal_threat_indicator": false,
            "welfare_risk_indicator": false,
            "sensitive_data_present": false,
            "attachments_present": false,
            "links_present": false,
            "regulatory_deadline": null,
            "requested_action": null,
            "service_summary": null
          }}
        }}

        ## Classification rules
        - primary_type_confidence: 0.0–1.0. Set abstain=true when confidence < 0.60 or the email is too vague to classify.
        - Type F = customer reporting fraud ON THEIR OWN account (unauthorised debit, card clone, OTP misuse).
        - Type G = an attack aimed at the provider itself (phishing lure sent to the desk, impersonation of staff).
        - Type L = customer in emotional distress or welfare crisis — route to senior empathy queue.
        - Type E = emails FROM regulatory/government bodies OR containing formal legal/court notices.
        - Type B = identical or near-identical repeat of a prior complaint (use duplicate_of field).
        - secondary_tags: only apply tags from the allowed list. Senior citizens, NRIs, and deceased accounts always get their tag.
        - transaction_reference: set ONLY if a genuine UTR/RRN/transaction-ID appears. Never set it for generic words.
        - service_summary: one formal sentence based strictly on what the email states. No assumptions.
        - customer_email: the sender's external email. Never an internal mailbox address.
        - If login/blocked/OTP/access problems: add ACCOUNT-ACCESS tag.
        - If money was lost or disputed: add FINANCIAL-LOSS tag.
        - If email is in {detected_lang} (not English): set customer_language accordingly; still classify normally.

        ## Trust context
        {json.dumps(trust)}

        ## Email to classify
        {parsed_text}
        """
    ).strip()


def call_ollama(model: str, prompt: str, audit: AuditLog) -> dict[str, Any]:
    cmd = ["ollama", "run", model]
    try:
        proc = subprocess.run(
            cmd,
            input=prompt.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=180,
        )
        output = proc.stdout.decode("utf-8", errors="replace").strip()
        audit.add("llm", f"ollama model {model} returned response")
        return extract_json_object(output)
    except Exception as exc:
        audit.add("llm_failure", f"ollama unavailable or invalid output: {exc}", level="ERROR")
        raise


def _try_parse(blob: str) -> dict[str, Any] | None:
    """Attempt to parse a JSON blob, tolerating literal newlines inside strings."""
    try:
        obj = json.loads(blob, strict=False)
    except json.JSONDecodeError:
        try:
            obj = json.loads(re.sub(r"(?<=[^\\])\n", " ", blob), strict=False)
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None


def extract_json_object(text: str) -> dict[str, Any]:
    """
    Extract the triage JSON from model output.

    Gemma4 is a *thinking* model: it instalments a long reasoning section (often
    containing partial JSON / the schema being drafted) followed by the final
    answer. The final answer's outer object contains "primary_type"; its nested
    "case_form" object does NOT. We therefore scan all TOP-LEVEL balanced
    objects and return the LAST one that contains "primary_type" — i.e. the
    final answer, never an inner case_form or a draft from the thinking trace.
    """
    # Strip ANSI escape codes + control chars (keep tab/LF/CR)
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

    # Collect every top-level balanced {...} object (skip past each once closed,
    # so nested objects like case_form are not scanned as separate candidates).
    candidates: list[dict[str, Any]] = []
    fallback_any: dict[str, Any] | None = None
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        end = -1
        for j in range(i, n):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        if end == -1:
            break  # unbalanced tail
        obj = _try_parse(text[i:end])
        if obj is not None:
            if "primary_type" in obj:
                candidates.append(obj)
            elif fallback_any is None:
                fallback_any = obj
        i = end  # continue AFTER this top-level object

    if candidates:
        return candidates[-1]      # the final answer
    if fallback_any is not None:
        return fallback_any        # better than nothing
    raise ValueError("No valid JSON object found in model output")


def fallback_classifier(parsed_text: str, duplicate_of: str | None, audit: AuditLog) -> dict[str, Any]:
    lower = parsed_text.lower()
    primary = "A"
    tags: list[str] = []
    summary = "Customer complaint requires manual follow-up."

    if duplicate_of:
        primary = "B"
        summary = "Likely duplicate or repeated complaint linked to previous ticket."
    elif any(re.search(p, lower) for p in THREAT_PATTERNS):
        primary = "L"
        tags.append("WELFARE-CONCERN")
        summary = "Customer language suggests crisis or welfare concern."
    elif any(re.search(p, lower) for p in ABUSE_PATTERNS):
        primary = "M"
        summary = "Email contains threatening or abusive language."
    elif any(re.search(p, lower) for p in FRAUD_PATTERNS):
        first_party_markers = [
            "my account", "my card", "my salary account", "my device",
            "my device", "from my", "i noticed", "i did not approve",
            "i did not trigger", "mera account", "mere account", "meri salary",
        ]
        primary = "F" if any(marker in lower for marker in first_party_markers) else "G"
        tags.append("FINANCIAL-LOSS")
        summary = "Fraud-related issue detected from keywords."
    elif any(x in lower for x in ["regulator", "ombudsman", "court", "legal notice", "advocate", "show cause"]):
        primary = "E"
        summary = "Regulatory or legal notice detected."
    elif any(x in lower for x in ["vendor", "invoice", "purchase order", "supplier"]):
        primary = "I"
        summary = "Vendor or third-party communication detected."
    elif any(x in lower for x in ["journalist", "media", "press", "reporter"]):
        primary = "N"
        summary = "Media inquiry detected."
    elif any(x in lower for x in ["balance", "interest rate", "service_area timing", "how do i", "what is the"]):
        primary = "H"
        summary = "General information query detected."
    elif "thank you" in lower or "appreciate" in lower or "great service" in lower:
        primary = "H"
        tags.append("POSITIVE-FEEDBACK")
        summary = "Positive feedback or low-risk message detected."

    if any(x in lower for x in ["urgent", "immediately", "asap", "jaldi", "abhi"]):
        tags.append("URGENCY-STATED")
    if any(x in lower for x in ["login", "locked", "access", "blocked", "otp", "password reset"]):
        tags.append("ACCOUNT-ACCESS")
    if "nri" in lower or "non resident" in lower:
        tags.append("NRI-CUSTOMER")
    if "minor" in lower:
        tags.append("MINOR-ACCOUNT")
    if "deceased" in lower or "death" in lower:
        tags.append("DECEASED-ACCOUNT")
    if "disability" in lower or "visually impaired" in lower or "differently abled" in lower:
        tags.append("DISABILITY-FLAG")
    if "joint account" in lower:
        tags.append("JOINT-ACCOUNT")
    if "business account" in lower or "company account" in lower or "current account" in lower:
        tags.append("BUSINESS-ACCOUNT")
    if "senior citizen" in lower or "pensioner" in lower or "retired" in lower:
        tags.append("ELDERLY-CUSTOMER")

    tags.append("NEEDS-HUMAN-REVIEW")
    seen: list[str] = []
    for t in tags:
        if t not in seen:
            seen.append(t)
    tags = seen[:3]

    audit.add("fallback_classifier", f"fallback classification used: {primary}", level="WARN")
    case_form = build_case_form_from_text(parsed_text, primary, tags)
    return {
        "primary_type": primary,
        "primary_type_confidence": 0.50,
        "abstain": True,
        "abstain_reason": "AI model unavailable; rule-based fallback applied.",
        "secondary_tags": tags,
        "issue_summary": summary,
        "customer_language": detect_language(parsed_text),
        "requires_human_review": True,
        "suggested_routing_reason": "Rule-based fallback — human review required.",
        "case_form": case_form,
    }


def sanitize_llm_output(result: dict[str, Any], audit: AuditLog) -> dict[str, Any]:
    primary = str(result.get("primary_type", "K")).strip().upper()
    if primary not in PRIMARY_TYPES:
        # Gemma4 sometimes returns the full label (e.g. "FRAUD_REPORT_BY_CUSTOMER")
        # instead of the letter code — map it back to the letter.
        label_to_letter = {label.upper(): letter for letter, label in PRIMARY_TYPES.items()}
        if primary in label_to_letter:
            primary = label_to_letter[primary]
        else:
            audit.add("llm_validation", f"invalid primary type {primary}, forcing K", level="WARN")
            primary = "K"

    # Extract real model confidence (0.0–1.0)
    raw_conf = result.get("primary_type_confidence")
    try:
        confidence = float(raw_conf) if raw_conf is not None else 0.70
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.70

    abstain = bool(result.get("abstain", False)) or confidence < 0.60
    abstain_reason = clean_llm_text(result.get("abstain_reason")) if abstain else None

    tags = []
    for tag in result.get("secondary_tags", []):
        tag = str(tag).strip().upper()
        if tag in SECONDARY_TAGS and tag not in tags:
            tags.append(tag)
    case_form = standardize_case_form(result.get("case_form", {}))
    return {
        "primary_type": primary,
        "primary_type_confidence": round(confidence, 4),
        "abstain": abstain,
        "abstain_reason": abstain_reason,
        "secondary_tags": tags[:3],
        "issue_summary": clean_llm_text(result.get("issue_summary")) or "No summary provided.",
        "customer_language": clean_llm_text(result.get("customer_language")) or "English",
        "requires_human_review": bool(result.get("requires_human_review", False)) or abstain,
        "suggested_routing_reason": clean_llm_text(result.get("suggested_routing_reason")) or "",
        "case_form": case_form,
    }


def standardize_case_form(case_form: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(CASE_FORM_TEMPLATE)
    if not isinstance(case_form, dict):
        case_form = {}
    for key in merged:
        if key not in case_form:
            continue
        if isinstance(merged[key], bool):
            merged[key] = bool(case_form[key])
        elif key in {"amount_involved", "amount_disputed"}:
            merged[key] = parse_money_value(case_form[key])
        else:
            merged[key] = clean_llm_text(case_form[key]) if case_form[key] is not None else None
    return merged


def build_case_form_from_text(parsed_text: str, primary: str, tags: list[str]) -> dict[str, Any]:
    lower = parsed_text.lower()
    amount = extract_amount(parsed_text)
    currency = "INR" if re.search(r"(?:rs\.?|inr|₹)", parsed_text, re.I) else None
    name_match = re.search(r"(?:regards|thanks|sincerely)[,\s]+([A-Za-z .'-]{3,60})$", parsed_text, re.I)
    customer_email = extract_sender_from_text(parsed_text)
    fraud_indicator = primary in {"F", "G"}
    welfare_risk = primary == "L" or "WELFARE-CONCERN" in tags
    legal_threat = primary == "E" or "LEGAL-THREAT" in tags or "legal" in lower
    product_type = guess_product_type(parsed_text)
    channel = guess_channel(parsed_text)
    issue_category = "Fraud / Dispute" if fraud_indicator else "Service Request" if primary == "H" else "Complaint Management"
    complaint_nature = {
        "F": "Unauthorized transaction reported by customer",
        "G": "Suspected phishing or impersonation",
        "E": "Regulatory or legal correspondence",
        "L": "Customer distress or welfare concern",
        "M": "Threatening or abusive communication",
    }.get(primary, "Customer complaint or service issue")
    reported_date = datetime.now().date().isoformat()
    deadline_match = re.search(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", parsed_text)

    return standardize_case_form(
        {
            "customer_name": name_match.group(1).strip() if name_match else None,
            "customer_email": customer_email if customer_email != "unknown@unknown" else None,
            "customer_phone": find_phone(parsed_text),
            "customer_id": None,
            "connection_id_masked": None,
            "account_type": product_type if product_type in {"Savings Account", "Current Account"} else None,
            "customer_segment": "NRI" if "NRI-CUSTOMER" in tags else "Business" if "BUSINESS-ACCOUNT" in tags else "Retail",
            "is_nri": "NRI-CUSTOMER" in tags or "nri" in lower,
            "is_senior_citizen": "ELDERLY-CUSTOMER" in tags or "senior citizen" in lower,
            "is_minor_related": "MINOR-ACCOUNT" in tags or "minor" in lower,
            "is_joint_account": "JOINT-ACCOUNT" in tags or "joint account" in lower,
            "product_type": product_type,
            "service_line": "Customer Service" if primary in {"A", "B", "C", "D", "H"} else "Risk / Fraud" if fraud_indicator else "Compliance / Legal",
            "issue_category": issue_category,
            "issue_subcategory": "Account Access" if "ACCOUNT-ACCESS" in tags else "Financial Loss" if "FINANCIAL-LOSS" in tags else None,
            "complaint_nature": complaint_nature,
            "incident_date": None,
            "reported_date": reported_date,
            "channel": channel,
            "transaction_type": channel if channel in {"UPI", "CARD", "AUTOPAY", "CARD", "service kiosk", "POS"} else None,
            "transaction_reference": find_transaction_reference(parsed_text),
            "merchant_or_counterparty": None,
            "area_code_or_location": None,
            "city_or_region": None,
            "currency": currency,
            "amount_involved": amount,
            "amount_disputed": amount,
            "financial_impact": "Customer reports monetary loss" if amount else None,
            "fraud_indicator": fraud_indicator,
            "legal_threat_indicator": legal_threat,
            "welfare_risk_indicator": welfare_risk,
            "sensitive_data_present": bool(re.search(r"\b(?:aadhaar|pan|connection id|otp)\b", lower)),
            "attachments_present": False,
            "links_present": "http" in lower,
            "regulatory_deadline": deadline_match.group(1) if deadline_match else None,
            "requested_action": "Immediate investigation and resolution required" if "urgent" in lower or fraud_indicator else None,
            "service_summary": complaint_nature,
            "operational_notes": None,
        }
    )


def infer_routing(primary: str, parsed_text: str, tags: list[str]) -> tuple[str, str]:
    lower = parsed_text.lower()
    if primary == "E":
        return ("Legal", "Compliance + MD Office")
    if primary == "F":
        return ("Fraud", "Cybersecurity")
    if primary == "G":
        return ("Cybersecurity", "Fraud + Legal")
    if primary == "I":
        return ("Procurement", "Finance")
    if primary == "J":
        return ("Internal Operations", "IT Helpdesk")
    if primary == "K":
        return ("Compliance", "MD Office (confidential)")
    if primary == "L":
        return ("Senior Agent Queue", "Service area Manager")
    if primary == "M":
        return ("Legal", "Security")
    if primary == "N":
        return ("Communications", "Legal")
    if "DECEASED-ACCOUNT" in tags:
        return ("Legal", "Compliance + Retail")
    for phrase, route in ROUTING_RULES.items():
        if phrase in lower:
            return route
    return ("Customer Relations", "Retail Service")


def extract_amount(parsed_text: str) -> int | None:
    patterns = [
        r"(?:rs\.?|inr|₹)\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*(lakh|lakhs|lac|lacs|crore|crores)?",
        r"([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*(lakh|lakhs|lac|lacs|crore|crores|rupees|rs\.?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, parsed_text, re.I)
        if not match:
            continue
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        scale = (match.group(2) or "").lower()
        if scale in {"lakh", "lakhs", "lac", "lacs"}:
            value *= 100000
        elif scale in {"crore", "crores"}:
            value *= 10000000
        return int(value)
    plain_match = re.search(r"\b([0-9]{4,})\b", parsed_text)
    if plain_match:
        try:
            return int(plain_match.group(1))
        except ValueError:
            return None
    return None


def extract_referenced_tokens(parsed_text: str) -> list[str]:
    patterns = [
        r"\bBET-\d{8}-[A-Z0-9]{6,12}\b",
        r"\bDocket-[A-Z]{2,6}-\d{8}-\d{4}-[A-Z0-9]{4}\b",
        r"\bRBX-\d{3,12}\b",
        r"\b(?:ticket|token|reference|ref)\s*[:#-]?\s*([A-Z0-9-]{6,24})\b",
    ]
    matches: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, parsed_text, re.I):
            token = match if isinstance(match, str) else match[0]
            token = token.upper().strip()
            if token and token not in matches:
                matches.append(token)
    return matches


def summarize_score_factors(
    primary: str,
    tags: list[str],
    trust: dict[str, Any],
    override_reasons: list[str],
    duplicate_of: str | None,
    parsed_text: str,
) -> list[str]:
    factors: list[str] = []
    amount = extract_amount(parsed_text)
    if primary in {"F", "G"}:
        factors.append("fraud_signal_detected")
    if detect_regulatory_deadline(parsed_text, primary):
        factors.append("regulatory_or_deadline_pressure")
    if amount is not None and amount >= 1000000:
        factors.append("extreme_financial_exposure")
    elif amount is not None and amount >= 100000:
        factors.append("high_financial_exposure")
    elif amount is not None and amount > 0:
        factors.append("financial_exposure_detected")
    sentiment_level = detect_sentiment_severity(parsed_text)
    if sentiment_level != "none":
        factors.append(f"sentiment_severity_{sentiment_level}")
    if duplicate_of:
        factors.append("repeat_or_duplicate_thread")
    if override_reasons:
        factors.extend(override_reasons)
    return factors


def detect_regulatory_deadline(parsed_text: str, primary: str) -> bool:
    lower = parsed_text.lower()
    deadline_terms = ["deadline", "by ", "before ", "within ", "formal response by", "reply by", "respond by"]
    return primary == "E" or any(term in lower for term in deadline_terms) and bool(detect_explicit_date(parsed_text))


def detect_language(text: str) -> str:
    """Heuristic language detection for common Indian service languages."""
    # Check for Devanagari (Hindi/Marathi), Telugu, Tamil, Kannada, Bengali, Gujarati script blocks
    script_ranges = [
        ("\u0900", "\u097F", "Hindi"),      # Devanagari
        ("\u0C00", "\u0C7F", "Telugu"),
        ("\u0B80", "\u0BFF", "Tamil"),
        ("\u0C80", "\u0CFF", "Kannada"),
        ("\u0980", "\u09FF", "Bengali"),
        ("\u0A80", "\u0AFF", "Gujarati"),
        ("\u0A00", "\u0A7F", "Punjabi"),
        ("\u0D00", "\u0D7F", "Malayalam"),
    ]
    for start, end, lang in script_ranges:
        count = sum(1 for ch in text if start <= ch <= end)
        if count > 10:
            return lang
    # Hinglish detection: English text with Hindi keywords
    hinglish_markers = [
        "mera account", "mere account", "meri salary", "band kar diya", "paisa",
        "rupaye", "bahut", "abhi", "kyun", "nahi", "nahin", "theek", "achha",
    ]
    lower = text.lower()
    if sum(1 for m in hinglish_markers if m in lower) >= 2:
        return "Hinglish"
    return "English"


def detect_sentiment_severity(parsed_text: str) -> str:
    lower = parsed_text.lower()
    critical_terms = [
        "suicide", "self harm", "kill myself", "end my life", "ruined", "destroyed my life",
        "life savings", "entire savings wiped", "catastrophic", "emergency",
        "dying", "no reason to live", "harm myself",
    ]
    high_terms = [
        "urgent", "immediately", "asap", "panic", "severe distress", "filed fir",
        "police complaint", "frozen account", "large loss", "crying", "desperate",
        "cheated", "looted", "scammed", "robbed", "helpless", "harassment",
    ]
    medium_terms = [
        "concerned", "worried", "please help", "follow up", "issue unresolved",
        "disappointed", "frustrated", "not resolved", "waiting since",
    ]
    if any(term in lower for term in critical_terms):
        return "critical"
    if any(term in lower for term in high_terms):
        return "high"
    if any(term in lower for term in medium_terms):
        return "medium"
    return "none"


def determine_database_matching_state(
    store: LocalStore,
    parsed_text: str,
    subject: str,
    sender: str,
    duplicate_of: str | None,
) -> tuple[str, dict[str, Any]]:
    queue = store.list_queue(limit=250)
    referenced_tokens = extract_referenced_tokens(parsed_text)
    sender_masked = mask_email(sender)
    lowered_text = parsed_text.lower()

    for case in queue:
        ticket_id = (case.get("ticket_id") or "").upper()
        if ticket_id and ticket_id in referenced_tokens:
            return "EXACT_MATCH", {
                "matched_ticket_id": ticket_id,
                "match_reason": "explicit_active_ticket_reference",
                "assigned_investigator": case.get("department_primary"),
            }

    if duplicate_of:
        return "PARTIAL_MATCH", {
            "matched_ticket_id": duplicate_of,
            "match_reason": "duplicate_detection_and_recent_history",
            "assigned_investigator": None,
        }

    best_match: dict[str, Any] | None = None
    best_ratio = 0.0
    for case in queue:
        comparison_text = f"{case.get('parsed_text', '')} {case.get('llm_summary', '')}".lower()
        ratio = difflib.SequenceMatcher(None, lowered_text[:1200], comparison_text[:1200]).ratio()
        same_sender = case.get("sender_email_masked") == sender_masked
        if same_sender:
            ratio += 0.08
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = case

    if best_match and best_ratio >= 0.72:
        return "PARTIAL_MATCH", {
            "matched_ticket_id": best_match.get("ticket_id"),
            "match_reason": f"textual_similarity_{best_ratio:.2f}",
            "assigned_investigator": best_match.get("department_primary"),
        }

    return "NO_MATCH", {
        "matched_ticket_id": None,
        "match_reason": "fresh_thread_no_prior_history",
        "assigned_investigator": None,
    }


def determine_downstream_action(
    primary: str,
    priority_tier: str,
    database_matching_state: str,
    tags: list[str],
    parsed_text: str,
    ticket_id: str,
) -> tuple[str, str]:
    lower = parsed_text.lower()
    if primary in {"O"} or "POSITIVE-FEEDBACK" in tags:
        return "NO_ACTION", ""
    if database_matching_state == "EXACT_MATCH":
        return "NO_ACTION", ""
    if primary in {"F", "G", "E", "L", "M"} or priority_tier == "P1":
        draft = (
            f"Reference {ticket_id}: We have received your email and initiated urgent review. "
            "Where applicable, the account and related activity are being reviewed for immediate protective action. "
            "If this concerns cyber fraud, please call 1930 and preserve all transaction details, SMS alerts, and screenshots."
        )
        return "INTERIM_RESPONSE", draft
    if primary == "H":
        draft = (
            f"Reference {ticket_id}: Thank you for contacting us. "
            "Your query has been recorded and is being processed through the appropriate service desk. "
            "We will share a structured response shortly."
        )
        return "AUTO_RESPONSE", draft
    if any(term in lower for term in ["password reset", "how do i", "service_area timing", "balance inquiry"]):
        draft = (
            f"Reference {ticket_id}: Your request has been logged. "
            "Please follow the standard service guidance already applicable to your request; if the issue persists, reply with supporting details."
        )
        return "AUTO_RESPONSE", draft
    if priority_tier in {"P2"}:
        draft = (
            f"Reference {ticket_id}: We have logged your complaint and escalated it for prompt review. "
            "Our team will contact you if additional verification is required."
        )
        return "INTERIM_RESPONSE", draft
    return "NO_ACTION", ""


def merge_with_existing_case(existing_case: dict[str, Any], new_case: dict[str, Any]) -> dict[str, Any]:
    merged = dict(new_case)
    merged["ticket_id"] = existing_case.get("ticket_id", new_case["ticket_id"])
    merged["opened_at"] = existing_case.get("opened_at", existing_case.get("received_at", new_case["received_at"]))
    merged["received_at"] = new_case["received_at"]
    merged["last_updated_at"] = new_case["received_at"]
    merged["thread_message_count"] = int(existing_case.get("thread_message_count", 1)) + 1
    merged["customer_history_flag"] = "repeat"
    merged["duplicate_of"] = existing_case.get("ticket_id")
    merged["status"] = existing_case.get("status", new_case["status"])

    prior_messages = list(existing_case.get("thread_messages", []))
    prior_messages.append(
        {
            "received_at": new_case["received_at"],
            "subject": new_case.get("parsing_metadata", {}).get("subject"),
            "sender": new_case.get("parsing_metadata", {}).get("sender"),
            "summary": new_case.get("llm_summary"),
            "database_matching_state": new_case.get("database_matching_state"),
        }
    )
    merged["thread_messages"] = prior_messages[-25:]

    merged["parsed_text"] = (
        f"{existing_case.get('parsed_text', '').strip()}\n\n--- FOLLOW UP ---\n\n{new_case.get('parsed_text', '').strip()}"
    ).strip()
    merged["priority_score"] = max(int(existing_case.get("priority_score", 0)), int(new_case.get("priority_score", 0)))
    merged["priority_tier"] = existing_case.get("priority_tier")
    if {"P1": 4, "P2": 3, "P3": 2, "P4": 1}.get(new_case.get("priority_tier"), 0) > {"P1": 4, "P2": 3, "P3": 2, "P4": 1}.get(existing_case.get("priority_tier"), 0):
        merged["priority_tier"] = new_case.get("priority_tier")
    merged["secondary_tags"] = sorted(set(existing_case.get("secondary_tags", [])) | set(new_case.get("secondary_tags", [])))
    merged["audit_log"] = list(existing_case.get("audit_log", []))[-30:] + list(new_case.get("audit_log", []))
    merged["case_form"]["operational_notes"] = (
        (existing_case.get("case_form", {}).get("operational_notes") or "") + " Follow-up received and appended to existing case."
    ).strip()
    return merged


def compute_priority(
    primary: str,
    tags: list[str],
    trust: dict[str, Any],
    override_applied: bool,
    override_reasons: list[str],
    duplicate_of: str | None,
    parsed_text: str,
    audit: AuditLog,
) -> tuple[int, str]:
    if override_applied:
        audit.add("priority", f"override produced P1 for reasons: {override_reasons}")
        return 100, "P1"

    amount = extract_amount(parsed_text)
    score = 0

    regulatory_score = 35 if detect_regulatory_deadline(parsed_text, primary) else (15 if primary == "E" else 0)

    exposure_score = 0
    if amount is not None and amount >= 10000000:
        exposure_score = 45
    elif amount is not None and amount >= 5000000:
        exposure_score = 40
    elif amount is not None and amount >= 1000000:
        exposure_score = 35
    elif amount is not None and amount >= 500000:
        exposure_score = 28
    elif amount is not None and amount >= 100000:
        exposure_score = 20
    elif amount is not None and amount >= 10000:
        exposure_score = 12
    elif amount is not None and amount > 0:
        exposure_score = 6

    fraud_score = 0
    if primary == "F":
        fraud_score = 35
    elif primary == "G":
        fraud_score = 30
    elif "FINANCIAL-LOSS" in tags and primary not in {"F", "G"}:
        fraud_score = 15
    elif primary == "E":
        fraud_score = 10

    # Vulnerable customer uplift
    vulnerable_uplift = 0
    if "ELDERLY-CUSTOMER" in tags or "DISABILITY-FLAG" in tags:
        vulnerable_uplift = 10
    elif "MINOR-ACCOUNT" in tags or "DECEASED-ACCOUNT" in tags:
        vulnerable_uplift = 8
    elif "NRI-CUSTOMER" in tags:
        vulnerable_uplift = 5

    sentiment_score = {
        "critical": 25,
        "high": 15,
        "medium": 8,
        "none": 0,
    }[detect_sentiment_severity(parsed_text)]

    score = regulatory_score + exposure_score + fraud_score + sentiment_score + vulnerable_uplift

    if duplicate_of or primary == "B":
        score += 8   # repeat / unresolved → push up

    if primary in {"H", "I", "N", "O"}:
        score = min(score, 24)   # info/vendor/media never P1 unless override

    # Hard minimums for severe cases
    if amount is not None and amount >= 5000000 and primary in {"F", "G"}:
        score = max(score, 95)
    elif amount is not None and amount >= 1000000 and primary in {"F", "G"}:
        score = max(score, 88)
    elif primary == "L":
        score = max(score, 70)   # welfare crisis always at least P2

    score = max(0, min(score, 100))
    tier = "P4"
    if score >= 75:
        tier = "P1"
    elif score >= 50:
        tier = "P2"
    elif score >= 25:
        tier = "P3"
    audit.add("priority", f"scored priority={score} tier={tier}")
    return score, tier


def acknowledgment_policy(primary: str, tags: list[str], priority_tier: str, override_reasons: list[str]) -> dict[str, Any]:
    if primary in {"E"} or "WELFARE-CONCERN" in tags or primary == "L" or "regulatory_or_legal_sender" in override_reasons:
        return {
            "send_auto_ack": False,
            "message": "No automated response. Human handling required.",
            "target_time_minutes": None,
        }
    base = f"We have received your email. Expected response time: {sla_from_tier(priority_tier)}."
    if primary in {"F", "G"}:
        base = "We have flagged this for immediate review."
    elif priority_tier == "P1":
        base = "We are treating this as urgent."
    return {
        "send_auto_ack": True,
        "message": base,
        "target_time_minutes": 15,
    }


def sla_from_tier(priority_tier: str) -> str:
    return {
        "P1": "within 1 hour",
        "P2": "same day",
        "P3": "within 3 days",
        "P4": "within 7 days",
    }[priority_tier]


def make_ticket_id() -> str:
    return "BET-" + datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:8].upper()


def triage_email(email_input: EmailInput, store: LocalStore, model: str) -> dict[str, Any]:
    audit = AuditLog()
    parsed_text = normalize_text(email_input)
    raw_hash = hashlib.sha256(email_input.raw_bytes).hexdigest()
    audit.add("ingest", f"loaded email from {email_input.source_path}")
    duplicate_of = store.find_duplicate(raw_hash, mask_email(email_input.sender), email_input.subject)
    if duplicate_of:
        audit.add("dedupe", f"possible duplicate of {duplicate_of}", level="WARN")

    trust = run_trust_checks(email_input, parsed_text, audit)
    override_applied, override_reasons = check_overrides(parsed_text, trust, audit)

    model_fallback_used = False
    try:
        llm_result = call_ollama(model, build_prompt(parsed_text, trust, duplicate_of), audit)
        classified = sanitize_llm_output(llm_result, audit)
    except Exception:
        model_fallback_used = True
        classified = fallback_classifier(parsed_text, duplicate_of, audit)

    # Always ensure customer_language is set, even after fallback
    if not classified.get("customer_language") or classified["customer_language"] == "English":
        detected_lang = detect_language(parsed_text)
        classified["customer_language"] = detected_lang

    if duplicate_of and classified["primary_type"] == "A":
        classified["primary_type"] = "B"
        audit.add("dedupe_override", "primary type changed to B due to duplicate evidence")

    if trust["unknown_sender"] and classified["primary_type"] == "H":
        classified["primary_type"] = "K"
        audit.add("identity_override", "unclear sender changed general query to anonymous type", level="WARN")

    if any(re.search(p, parsed_text.lower()) for p in THREAT_PATTERNS):
        if "WELFARE-CONCERN" not in classified["secondary_tags"]:
            classified["secondary_tags"].append("WELFARE-CONCERN")
        classified["primary_type"] = "L"

    lower = parsed_text.lower()
    first_party_markers = [
        "my account",
        "my card",
        "my salary account",
        "my savings account",
        "my current account",
        "my device",
        "my device",
        "from my",
        "i noticed",
        "i did not approve",
        "i did not trigger",
    ]
    if any(re.search(p, lower) for p in FRAUD_PATTERNS) and any(marker in lower for marker in first_party_markers):
        classified["primary_type"] = "F"
        if "FINANCIAL-LOSS" not in classified["secondary_tags"]:
            classified["secondary_tags"].append("FINANCIAL-LOSS")

    if "regulatory_or_legal_sender" in override_reasons:
        classified["primary_type"] = "E"

    # Impersonation → type G (attack on the provider) + IMPERSONATION tag
    if "impersonation_attempt_detected" in override_reasons:
        classified["primary_type"] = "G"
        if "IMPERSONATION" not in classified["secondary_tags"]:
            classified["secondary_tags"].insert(0, "IMPERSONATION")
        if "NEEDS-HUMAN-REVIEW" not in classified["secondary_tags"]:
            classified["secondary_tags"].append("NEEDS-HUMAN-REVIEW")

    if "deceased" in lower and "DECEASED-ACCOUNT" not in classified["secondary_tags"]:
        classified["secondary_tags"].append("DECEASED-ACCOUNT")
    if any(x in lower for x in ["login issue", "cannot log in", "unable to log in", "locked out", "account blocked", "cannot access self care portal", "unable to access self care portal"]):
        if "ACCOUNT-ACCESS" not in classified["secondary_tags"]:
            classified["secondary_tags"].append("ACCOUNT-ACCESS")
    if any(x in lower for x in ["urgent", "immediately", "asap"]):
        if "URGENCY-STATED" not in classified["secondary_tags"]:
            classified["secondary_tags"].append("URGENCY-STATED")
    if extract_amount(parsed_text) and "FINANCIAL-LOSS" not in classified["secondary_tags"] and classified["primary_type"] in {"F", "G"}:
        classified["secondary_tags"].append("FINANCIAL-LOSS")

    case_form = standardize_case_form(classified.get("case_form"))
    fallback_case_form = build_case_form_from_text(parsed_text, classified["primary_type"], classified["secondary_tags"])
    for key, value in fallback_case_form.items():
        if case_form.get(key) in {None, ""} and value not in {None, ""}:
            case_form[key] = value
    case_form["customer_email"] = case_form["customer_email"] or (email_input.sender if email_input.sender != "unknown@unknown" else None)
    case_form["service_summary"] = case_form["service_summary"] or classified["issue_summary"]
    # Only populate operational_notes when the model provided a meaningful value
    routing_reason = classified.get("suggested_routing_reason", "")
    if routing_reason and routing_reason not in {"Rule-based fallback — human review required.", ""}:
        case_form["operational_notes"] = case_form["operational_notes"] or routing_reason
    case_form["fraud_indicator"] = classified["primary_type"] in {"F", "G"}
    case_form["legal_threat_indicator"] = case_form["legal_threat_indicator"] or classified["primary_type"] == "E" or "LEGAL-THREAT" in classified["secondary_tags"]
    case_form["welfare_risk_indicator"] = case_form["welfare_risk_indicator"] or classified["primary_type"] == "L" or "WELFARE-CONCERN" in classified["secondary_tags"]
    case_form = ground_case_form(case_form, parsed_text, email_input)

    dept_primary, dept_secondary = infer_routing(
        classified["primary_type"],
        parsed_text,
        classified["secondary_tags"],
    )
    score, tier = compute_priority(
        classified["primary_type"],
        classified["secondary_tags"],
        trust,
        override_applied,
        override_reasons,
        duplicate_of,
        parsed_text,
        audit,
    )
    database_matching_state, match_details = determine_database_matching_state(
        store,
        parsed_text,
        email_input.subject,
        email_input.sender,
        duplicate_of,
    )
    existing_case = None
    matched_ticket_id = match_details.get("matched_ticket_id")
    if matched_ticket_id:
        existing_case = store.get_case(matched_ticket_id)

    ack = acknowledgment_policy(classified["primary_type"], classified["secondary_tags"], tier, override_reasons)
    final_ticket_id = existing_case.get("ticket_id") if existing_case else make_ticket_id()
    downstream_action, response_draft = determine_downstream_action(
        classified["primary_type"],
        tier,
        database_matching_state,
        classified["secondary_tags"],
        parsed_text,
        final_ticket_id,
    )
    score_factors_detected = summarize_score_factors(
        classified["primary_type"],
        classified["secondary_tags"],
        trust,
        override_reasons,
        duplicate_of,
        parsed_text,
    )
    result = {
        "ticket_id": final_ticket_id,
        "received_at": utc_now_iso(),
        "opened_at": existing_case.get("opened_at", existing_case.get("received_at")) if existing_case else utc_now_iso(),
        "last_updated_at": utc_now_iso(),
        "sender_email_masked": mask_email(email_input.sender),
        "sender_verified": trust["sender_verified"],
        "customer_id": None,
        "customer_history_flag": "repeat" if duplicate_of else "first_contact",
        "raw_email_hash": raw_hash,
        "parsed_text": parsed_text,
        "primary_type": classified["primary_type"],
        "primary_type_label": PRIMARY_TYPES[classified["primary_type"]],
        "primary_type_confidence": classified.get("primary_type_confidence", 0.70),
        "abstain": classified.get("abstain", False),
        "abstain_reason": classified.get("abstain_reason"),
        "secondary_tags": classified["secondary_tags"],
        "department_primary": dept_primary,
        "department_secondary": dept_secondary,
        "priority_score": score,
        "priority_tier": tier,
        "override_applied": override_applied,
        "override_reasons": override_reasons,
        "duplicate_of": duplicate_of,
        "status": "OPEN",
        "llm_summary": classified["issue_summary"],
        "customer_language": classified["customer_language"],
        "requires_human_review": classified["requires_human_review"] or override_applied,
        "case_form": case_form,
        "parsing_metadata": {
            "source_path": email_input.source_path,
            "subject": email_input.subject,
            "sender": email_input.sender,
            "recipients": email_input.recipients,
            "attachments_count": len(email_input.attachments),
            "links_count": len(email_input.links),
            "sender_verified": trust["sender_verified"],
            "referenced_tokens": extract_referenced_tokens(parsed_text),
        },
        "classification": {
            "issue_type": PRIMARY_TYPES[classified["primary_type"]],
            "mapped_department": dept_primary,
        },
        "priority_ranking": {
            "score_factors_detected": score_factors_detected,
            "assigned_tier": tier,
        },
        "database_matching_state": database_matching_state,
        "database_match_details": match_details,
        "downstream_action": downstream_action,
        "response_draft": response_draft,
        "workflow": {
            "origin": "central_console",
            "current_stage": "CENTRAL_TRIAGE",
            "routed_department": dept_primary,
            "handoff_status": "READY_FOR_DEPARTMENT",
            "department_status": "PENDING_RECEIPT",
            "central_received_at": utc_now_iso(),
            "department_received_at": None,
            "department_last_action": "QUEUED_FROM_CENTRAL",
            "department_last_action_at": None,
            "resolved_at": None,
        },
        "acknowledgment": ack,
        "attachments": email_input.attachments,
        "links": email_input.links,
        "audit_log": [],
        "thread_message_count": 1,
        "thread_messages": [
            {
                "received_at": utc_now_iso(),
                "subject": email_input.subject,
                "sender": email_input.sender,
                "summary": classified["issue_summary"],
                "database_matching_state": database_matching_state,
            }
        ],
    }
    result["audit_log"] = build_meaningful_audit(
        email_input=email_input,
        result=result,
        match_details=match_details,
        trust=trust,
        score_factors_detected=score_factors_detected,
        model_used=model,
        model_fallback_used=model_fallback_used,
        base_audit=audit,
    )
    if existing_case and database_matching_state in {"EXACT_MATCH", "PARTIAL_MATCH"}:
        result = merge_with_existing_case(existing_case, result)
    store.save(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline email triage prototype powered by local Ollama.")
    parser.add_argument("email_file", help="Path to .eml or plain-text email file")
    parser.add_argument("--model", default=os.environ.get("TRIAGE_OLLAMA_MODEL", "the configured local model"))
    parser.add_argument("--db", default="triage_runs.db", help="SQLite database path")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args()

    email_path = Path(args.email_file)
    if not email_path.exists():
        print(f"Email file not found: {email_path}", file=sys.stderr)
        return 1

    store = LocalStore(Path(args.db))
    email_input = parse_email_file(email_path)
    result = triage_email(email_input, store, args.model)
    if args.pretty:
        print(json.dumps(result, indent=2, ensure_ascii=True))
    else:
        print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
