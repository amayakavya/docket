"""
Clarification request generator.

When identity verification is incomplete (partial match, mild mismatch) but there's
no hard fraud signal, this module generates a targeted email draft asking the sender
for the specific missing or conflicting pieces of information — not a boilerplate
"please provide all your details" response.

Clarification is generated when:
  - validation_status is "partial" or "none" or "no_identifiers"
  - impersonation_risk.level is NONE, LOW, or MEDIUM
  - NOT when risk is HIGH or CRITICAL (those go to fraud escalation, not clarification)
  - NOT when validation_status is "full" with risk NONE/LOW (already verified)
"""
from __future__ import annotations

from typing import Any


# Mapping of missing field keys → human-readable labels and what to ask for
_FIELD_LABELS: dict[str, tuple[str, str]] = {
    "connection_id": (
        "connection id",
        "your connection id, printed on your bill and shown in the self care portal",
    ),
    "customer_ref": (
        "Customer ID / customer reference",
        "your customer reference, shown under Profile in the mobile app",
    ),
    "mobile_number": (
        "registered mobile number",
        "your 10-digit mobile number registered with Docket",
    ),
    "card_number": (
        "device serial",
        "the last 4 digits of your Docket debit or device",
    ),
    "upi_id": (
        "UPI ID",
        "your UPI ID linked to your Docket account (for example yourname@okbank)",
    ),
    "email": (
        "registered email address",
        "the email address registered with Docket for your account",
    ),
    "name": (
        "the name the account is held in",
        "your full name exactly as it appears in your Docket records",
    ),
}


def _missing_strong_fields(cv_result: dict[str, Any]) -> list[str]:
    """Return strong identifier types that were NOT matched."""
    matched = set(cv_result.get("matched_fields", []))
    strong = ["customer_ref", "connection_id"]
    return [f for f in strong if f not in matched]


def _conflicting_fields(cv_result: dict[str, Any], imp_risk: dict[str, Any]) -> list[str]:
    """Return field names that had a conflict signal from impersonation detection."""
    conflicts: list[str] = []
    signals = imp_risk.get("signals", [])
    sig_text = " ".join(signals).lower()
    if "email" in sig_text and "domain" in sig_text:
        conflicts.append("email")
    if "mobile" in sig_text and "not match" in sig_text:
        conflicts.append("mobile_number")
    if "name" in sig_text and ("not match" in sig_text or "low overlap" in sig_text):
        conflicts.append("name")
    return conflicts


def generate_clarification_request(
    cv_result: dict[str, Any],
    impersonation_risk: dict[str, Any],
    *,
    sender_name: str | None = None,
    customer_name: str | None = None,  # name from matched customer record (if any)
) -> dict[str, Any]:
    """
    Generate a targeted clarification email draft, or return {needed: False}.

    The generated draft asks ONLY for the specific fields that are missing or
    conflicting — never a generic "please provide all your details" response.

    Returns:
        needed:           bool
        subject:          str
        body:             str
        missing_fields:   list[str]   — field keys the sender should provide
        conflict_fields:  list[str]   — field keys with mismatch signals
        reason:           str         — one-line reason for the request
    """
    risk_level = impersonation_risk.get("level", "UNKNOWN")
    status = cv_result.get("validation_status", "no_identifiers")

    # Never send clarification for fraud-level signals — escalate instead
    if risk_level in ("HIGH", "CRITICAL"):
        return {"needed": False, "reason": f"Risk level {risk_level} — escalate, do not clarify"}

    # No clarification needed if strongly verified
    if status == "full" and risk_level in ("NONE", "LOW"):
        return {"needed": False, "reason": "Identity fully verified"}

    # Determine what's missing and what's conflicting
    missing = _missing_strong_fields(cv_result)
    conflicts = _conflicting_fields(cv_result, impersonation_risk)

    # Also surface unmatched identifier types that were found in the email
    # (identifiers provided but didn't resolve to any customer)
    unmatched_types = cv_result.get("unmatched_fields", [])
    for uf in unmatched_types:
        # normalise to canonical key
        key = uf.rstrip("s")  # "connection_ids" → "connection_id"
        if key not in missing and key not in conflicts:
            conflicts.append(key)

    if not missing and not conflicts:
        # Partial match but nothing specifically wrong — ask for one strong ID
        missing = ["connection_id"]

    greeting_name = sender_name or "Valued Customer"
    salutation_name = customer_name or None  # use record name in body if mismatch detected

    # ── Build subject ──────────────────────────────────────────────────────────
    if status == "no_identifiers":
        subject = "Re: Your Query — Please Provide Your Account Details"
        reason = "No identifiers found in email"
    elif conflicts and "email" in conflicts:
        subject = "Re: Your Query — Please Write From Your Registered Email Address"
        reason = "Sender email doesn't match registered email on record"
    elif conflicts and "name" in conflicts:
        subject = "Re: Your Query — Please Confirm the Name on the Account"
        reason = "Name provided doesn't match our records"
    elif missing:
        labels = " / ".join(_FIELD_LABELS[f][0] for f in missing if f in _FIELD_LABELS)
        subject = f"Re: Your Query — Please Share Your {labels.title()}"
        reason = f"Missing strong identifier(s): {', '.join(missing)}"
    else:
        subject = "Re: Your Query — Additional Verification Required"
        reason = "Partial identity match — additional field required"

    # ── Build body ─────────────────────────────────────────────────────────────
    lines: list[str] = [f"Dear {greeting_name},", ""]
    lines.append(
        "Thank you for writing to Docket. We have received your query "
        "and are keen to assist you."
    )
    lines.append("")

    # Explain what the issue is without being alarming
    if status == "no_identifiers":
        lines.append(
            "To locate your account and assist you accurately, we need a few details "
            "that were not included in your email:"
        )
    elif conflicts and "email" in conflicts:
        lines.append(
            "For the security of your account, we process queries only from the email "
            "address registered with us. Our records indicate a different email address "
            "on file for this account."
        )
        lines.append("")
        lines.append("Please take one of the following steps:")
        lines.append(
            "  1. Resend your query from your registered email address, OR"
        )
        lines.append(
            "  2. Call our 24×7 toll-free helpline 1800 11 2211 to update your registered email, "
            "after which you can resubmit your query."
        )
        lines.append("")
        lines.append(
            "Alternatively, if you believe your registered email is correct, "
            "please provide the following to confirm your identity:"
        )
    elif conflicts and "name" in conflicts:
        if salutation_name:
            lines.append(
                f"We found an account matching the details you provided, but the name in "
                f"our records does not match the name in your email."
            )
        else:
            lines.append(
                "We found an account matching some of the details you provided, but we "
                "could not fully confirm your identity from this email."
            )
        lines.append(
            "\nPlease confirm the following so we can proceed:"
        )
    else:
        lines.append(
            "We found a partial match in our records but need one more piece of information "
            "to fully verify your identity and process your request securely:"
        )

    # List specific items needed
    items_to_request: list[str] = []

    for field in missing:
        if field in _FIELD_LABELS:
            items_to_request.append(f"Your {_FIELD_LABELS[field][1]}")

    for field in conflicts:
        if field == "email":
            pass  # handled above in prose
        elif field == "name":
            items_to_request.append(
                "Your full name exactly as it appears on your account"
            )
        elif field == "mobile_number":
            items_to_request.append(
                "Your 10-digit mobile number currently registered with Docket "
                "(this may differ from your contact number)"
            )
        elif field in _FIELD_LABELS:
            items_to_request.append(f"Your {_FIELD_LABELS[field][1]}")

    if items_to_request and not (conflicts and "email" in conflicts and not missing):
        lines.append("")
        for i, item in enumerate(items_to_request[:3], 1):  # cap at 3 items
            lines.append(f"  {i}. {item}")

    lines += [
        "",
        "Once we receive these details, we will prioritise your query and respond promptly.",
        "",
        "For urgent matters, please contact us directly:",
        "  • Toll-free helpline: 1800 11 2211 (24×7)",
        "  • Docket mobile app: app.example.com",
        "",
        "Regards,",
        "Docket Customer Care Team",
        "Docket",
    ]

    return {
        "needed": True,
        "subject": subject,
        "body": "\n".join(lines),
        "missing_fields": missing,
        "conflict_fields": conflicts,
        "reason": reason,
    }
