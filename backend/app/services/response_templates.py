"""
AI-powered draft response generator.

Uses Gemma (via Ollama) to generate a professional, context-aware draft
response for an Docket customer email. Falls back to template-based drafts
when Ollama is unavailable.
"""
from __future__ import annotations

import json
from typing import Any


# ── Template fallbacks (no LLM needed) ───────────────────────────────────────

_TEMPLATES: dict[str, dict[str, str]] = {
    "UNAUTHORISED_USE": {
        "subject": "Re: Fraud Report — Immediate Action Initiated",
        "body": (
            "Dear {customer_name},\n\n"
            "Thank you for bringing this to our immediate attention. We have registered "
            "your complaint regarding the unauthorized transaction and our Fraud & Risk team "
            "is actively investigating.\n\n"
            "As a precautionary measure, we recommend:\n"
            "1. Changing your self care portal password and MPIN immediately\n"
            "2. Blocking your associated debit/device via Docket the mobile app if not already done\n"
            "3. Filing a police complaint (FIR) for record purposes\n\n"
            "Your case reference number is: {case_id}\n"
            "Expected resolution: {resolution_deadline}\n\n"
            "We take fraud cases with the highest priority. Our team will contact you within "
            "4 hours with an update.\n\n"
            "Regards,\nDocket Trust & Safety Desk"
        ),
    },
    "PAYMENT_FAILURE": {
        "subject": "Re: Transaction Issue — Under Investigation",
        "body": (
            "Dear {customer_name},\n\n"
            "We acknowledge receipt of your complaint regarding the failed/pending transaction. "
            "Our technical team has initiated an investigation.\n\n"
            "Please note:\n"
            "- If the amount was debited but not credited, it will be reversed within 5 working days\n"
            "- payment disputes are typically resolved within 48 hours\n"
            "- payment disputes may take up to 7 working days\n\n"
            "Case ID: {case_id}\n\n"
            "We apologize for the inconvenience. You will receive a status update shortly.\n\n"
            "Regards,\nDocket Technical & Digital Service Support"
        ),
    },
    "PORTAL_ACCESS": {
        "subject": "Re: Account Access Issue",
        "body": (
            "Dear {customer_name},\n\n"
            "We have received your request regarding difficulty accessing your account. "
            "Our technical team is looking into this.\n\n"
            "In the meantime, you may:\n"
            "1. Reset your self care portal password at selfcare.example.com\n"
            "2. Call our 24×7 helpline: 1800 11 2211\n"
            "3. Visit your nearest Docket service_area with your Aadhaar/PAN card\n\n"
            "Case ID: {case_id}\n\n"
            "Regards,\nDocket Technical Support Team"
        ),
    },
    "CUSTOMER_GRIEVANCE": {
        "subject": "Re: Your Grievance — Acknowledged",
        "body": (
            "Dear {customer_name},\n\n"
            "We sincerely apologize for the inconvenience you have experienced. Your feedback "
            "is extremely valuable to us and we take all grievances seriously.\n\n"
            "Your complaint has been registered and assigned to our Customer Service team "
            "for immediate attention.\n\n"
            "Case ID: {case_id}\n"
            "Expected resolution within: {resolution_deadline}\n\n"
            "We assure you that this matter will be resolved at the earliest. A dedicated "
            "relationship officer will reach out to you within 24 hours.\n\n"
            "Regards,\nDocket Customer Resolution Desk"
        ),
    },
    "VERIFICATION_QUERY": {
        "subject": "Re: identity verification Update Request",
        "body": (
            "Dear {customer_name},\n\n"
            "Thank you for writing to us regarding identity verification documentation. "
            "To complete your identity verification update, please submit the following documents at your nearest Docket service_area:\n\n"
            "1. Recent passport-size photograph\n"
            "2. Aadhaar Card (original + self-attested copy)\n"
            "3. PAN Card (if applicable)\n"
            "4. Address proof (utility bill/passport/driving licence)\n\n"
            "You may also update your identity verification digitally through Docket the mobile app > Profile > identity verification Update.\n\n"
            "Case ID: {case_id}\n\n"
            "Regards,\nDocket Customer Service Team"
        ),
    },
    "REGULATORY": {
        "subject": "Re: Regulatory Query — Acknowledged",
        "body": (
            "Dear {customer_name},\n\n"
            "We acknowledge receipt of your correspondence regarding regulatory matters. "
            "This has been escalated to our Compliance Desk for urgent attention.\n\n"
            "Our compliance team will respond within 48 hours as required by the regulator guidelines.\n\n"
            "Case ID: {case_id}\n\n"
            "Regards,\nDocket Compliance Desk"
        ),
    },
    "LEGAL_NOTICE": {
        "subject": "Re: Legal Notice Received — Under Review",
        "body": (
            "Dear {customer_name},\n\n"
            "We acknowledge receipt of your legal notice dated and have forwarded it to our "
            "Legal & Compliance department for immediate review.\n\n"
            "Our legal team will respond formally within the statutory period.\n\n"
            "Case ID: {case_id}\n\n"
            "Regards,\nDocket Legal Affairs"
        ),
    },
    "CONNECTION_FAULT": {
        "subject": "Re: Technical Issue Reported",
        "body": (
            "Dear {customer_name},\n\n"
            "Thank you for reporting the technical issue with our self care services. "
            "Our IT team has been alerted and is working on a fix.\n\n"
            "Case ID: {case_id}\n\n"
            "You can check current service status at selfcare.example.com .\n\n"
            "Regards,\nDocket Digital Service Support Team"
        ),
    },
    "GENERAL_QUERY": {
        "subject": "Re: Your Query",
        "body": (
            "Dear {customer_name},\n\n"
            "Thank you for reaching out to Docket. We have received your query "
            "and our team will revert with the information requested within 2 working days.\n\n"
            "Case ID: {case_id}\n\n"
            "For immediate assistance, please call our 24×7 helpline: 1800 11 2211\n\n"
            "Regards,\nDocket Customer Care"
        ),
    },
}

_DEFAULT_TEMPLATE = {
    "subject": "Re: Your Email — Registered",
    "body": (
        "Dear {customer_name},\n\n"
        "Thank you for writing to us. We have registered your request and our team "
        "will respond within the stipulated timeframe.\n\n"
        "Case ID: {case_id}\n\n"
        "Regards,\nDocket Customer Care"
    ),
}


def _format_deadline(sla: dict[str, Any]) -> str:
    deadline = sla.get("resolution_due_at", "as soon as possible")
    if "T" in str(deadline):
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(str(deadline))
            return dt.strftime("%d %B %Y, %I:%M %p UTC")
        except Exception:
            pass
    return str(deadline)


def _build_rich_context_block(context: dict[str, Any]) -> str:
    """
    Build a context-specific paragraph injected into the response body.
    Pulls from customer record, extracted transaction details, and attachment entities.
    """
    lines: list[str] = []

    # Customer-specific details
    customer = context.get("matched_customer") or {}
    segment = customer.get("customer_segment", "RETAIL")
    rm = customer.get("account_manager")
    service_area = customer.get("service_area")
    payment_score = customer.get("payment_score")

    # Transaction details (from email + attachments)
    amount = context.get("amount_involved") or ""
    ref_ids = context.get("reference_ids") or []     # from attachments/extraction
    dates = context.get("incident_dates") or []
    acc_last4 = ""
    accounts = customer.get("connection_ids") or []
    if accounts:
        acc_last4 = str(accounts[0])[-4:]

    # Build specific lines
    if amount:
        try:
            amt_fmt = f"₹{float(str(amount).replace(',', '')):,.2f}"
        except ValueError:
            amt_fmt = str(amount)
        lines.append(f"Amount involved: {amt_fmt}")

    if ref_ids:
        lines.append(f"Reference number(s): {', '.join(ref_ids[:3])}")

    if dates:
        lines.append(f"Reported date: {dates[0]}")

    if acc_last4:
        lines.append(f"Account ending: ****{acc_last4}")

    # HNI / Priority customer gets RM mention
    if segment in ("HNI", "PRIORITY") and rm:
        lines.append(f"Your Relationship Manager {rm} has been notified and will contact you directly.")
    elif segment == "SENIOR_CITIZEN":
        lines.append("As a Senior Citizen account holder, your case has been prioritised for expedited resolution.")

    if service_area and not rm:
        lines.append(f"Home service_area: {service_area}")

    return "\n".join(lines)


def _fill_template(template: dict[str, str], context: dict[str, Any]) -> dict[str, str]:
    customer = context.get("customer_name") or "Valued Customer"
    case_id = context.get("case_id", "N/A")
    sla = context.get("sla_metadata") or {}
    deadline = _format_deadline(sla)

    fill = {
        "customer_name": customer,
        "case_id": case_id,
        "resolution_deadline": deadline,
    }
    return {
        "subject": template["subject"].format(**fill),
        "body": template["body"].format(**fill),
    }


# ── Ollama-backed generation ──────────────────────────────────────────────────

def _generate_with_llm(
    classification: str,
    context: dict[str, Any],
    model: str = "the configured local model",
) -> dict[str, str] | None:
    """Generate a response using the local Ollama LLM. Returns None on failure."""
    try:
        import urllib.request

        customer = context.get("customer_name") or "the customer"
        case_id = context.get("case_id", "N/A")
        summary = context.get("summary") or context.get("issue_summary") or ""
        dept = context.get("department") or ""
        priority = context.get("priority") or "MEDIUM"
        sla = context.get("sla_metadata") or {}
        deadline = _format_deadline(sla)

        # Rich specifics from customer record + attachments
        matched = context.get("matched_customer") or {}
        segment = matched.get("customer_segment", "RETAIL")
        service_area = matched.get("service_area", "")
        rm = matched.get("account_manager", "")
        amount = context.get("amount_involved") or ""
        ref_ids = context.get("reference_ids") or []
        incident_dates = context.get("incident_dates") or []
        attachment_summary = context.get("attachment_summary") or ""
        regulatory = context.get("regulatory_breach") or {}
        reg_type = regulatory.get("breach_type", "NONE")
        reg_sla = regulatory.get("compliance_sla_hours", 0)

        specifics = []
        if amount:
            specifics.append(f"Amount involved: {amount}")
        if ref_ids:
            specifics.append(f"Transaction ref: {', '.join(ref_ids[:2])}")
        if incident_dates:
            specifics.append(f"Incident date: {incident_dates[0]}")
        if segment in ("HNI", "PRIORITY") and rm:
            specifics.append(f"Customer's RM: {rm} (notify directly)")
        if reg_type not in ("NONE", "REGULATORY_MENTION"):
            specifics.append(f"Regulatory escalation: {reg_type} — SLA {reg_sla} hours")
        if attachment_summary:
            specifics.append(f"From attachments: {attachment_summary[:200]}")

        specifics_text = "\n".join(f"  - {s}" for s in specifics) if specifics else "  N/A"

        prompt = (
            f"You are a professional customer service executive at Docket (Docket). "
            f"Write a concise, empathetic, and professional response email to a customer.\n\n"
            f"Context:\n"
            f"- Classification: {classification}\n"
            f"- Priority: {priority}\n"
            f"- Customer segment: {segment}\n"
            f"- Department handling: {dept}\n"
            f"- Issue summary: {summary}\n"
            f"- Resolution deadline: {deadline}\n"
            f"- Case ID: {case_id}\n"
            f"- Customer name: {customer}\n"
            f"- Home service_area: {service_area or 'N/A'}\n"
            f"Specific details (MUST reference these accurately):\n{specifics_text}\n\n"
            f"Instructions:\n"
            f"- Reference the specific amounts, reference numbers, and dates above\n"
            f"- If regulatory escalation is present, include the statutory timeline\n"
            f"- For HNI/PRIORITY customers, mention RM will follow up\n"
            f"- Body: formal, max 220 words, include Case ID, end with 'Regards, Docket Customer Care'\n"
            f"Output ONLY valid JSON with keys 'subject' and 'body'."
        )

        payload = json.dumps({
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 400},
        }).encode()

        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        text = data.get("response", "")
        # Extract JSON from the response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end])
            if "subject" in parsed and "body" in parsed:
                return {"subject": str(parsed["subject"]), "body": str(parsed["body"])}
    except Exception:
        pass
    return None


# ── Public entry point ────────────────────────────────────────────────────────

def generate_draft_response(
    classification: str,
    context: dict[str, Any],
    model: str = "the configured local model",
    use_llm: bool = True,
) -> dict[str, Any]:
    """
    Generate a draft email response for the given classification and context.

    Tries LLM generation first; falls back to enriched template on failure.

    Args:
        classification:  e.g. "UNAUTHORISED_USE", "PAYMENT_FAILURE"
        context:         dict — accepted keys:
            customer_name       str
            case_id             str
            summary             str
            amount_involved     str | float
            priority            str
            department          str
            sla_metadata        dict
            matched_customer    dict   — full customer record from validation
            reference_ids       list[str]  — from attachment extraction
            incident_dates      list[str]  — from attachment extraction
            attachment_summary  str        — human-readable summary of attachment findings
            regulatory_breach   dict       — from regulatory.detect_regulatory_breach()
        model:    Ollama model identifier
        use_llm:  set False to use templates directly (faster, for batch/ingestion)

    Returns:
        {subject, body, source: "llm"|"template", classification, context_injected: dict}
    """
    source = "template"
    draft = None

    if use_llm:
        draft = _generate_with_llm(classification, context, model)
        if draft:
            source = "llm"

    if not draft:
        # Determine best template, then inject rich context block after first paragraph
        template = _TEMPLATES.get(classification, _DEFAULT_TEMPLATE)
        base = _fill_template(template, context)
        rich_block = _build_rich_context_block(context)
        if rich_block:
            # Insert the context block after the first paragraph break
            body = base["body"]
            split_pos = body.find("\n\n")
            if split_pos != -1:
                body = body[:split_pos + 2] + rich_block + "\n\n" + body[split_pos + 2:]
            else:
                body = body + "\n\n" + rich_block
            draft = {"subject": base["subject"], "body": body}
        else:
            draft = base

    # Summarise what context was actually injected (for the operator panel)
    context_injected = {
        "amount": context.get("amount_involved"),
        "reference_ids": context.get("reference_ids") or [],
        "customer_segment": (context.get("matched_customer") or {}).get("customer_segment"),
        "has_regulatory": bool(
            (context.get("regulatory_breach") or {}).get("breach_type", "NONE") not in ("NONE", "REGULATORY_MENTION")
        ),
    }

    return {
        "subject": draft["subject"],
        "body": draft["body"],
        "source": source,
        "classification": classification,
        "context_injected": context_injected,
    }
