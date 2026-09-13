from __future__ import annotations

import re
import subprocess
from typing import Any


_THREAD_SIGNALS = [
    re.compile(r"^>+ ", re.M),
    re.compile(r"^On .{5,80} wrote:", re.M | re.I),
    re.compile(r"-{3,}\s*(original|forwarded)\s+message\s*-{3,}", re.I),
    re.compile(r"^From:\s+\S+", re.M | re.I),
    re.compile(r"^Sent:\s+\w", re.M | re.I),
]


def detect_thread(text: str) -> dict[str, Any]:
    """Return thread depth estimate and whether a summary is warranted."""
    signals: dict[str, int] = {}
    for pat in _THREAD_SIGNALS:
        matches = pat.findall(text)
        if matches:
            signals[pat.pattern[:30]] = len(matches)

    from_count = len(re.findall(r"^From:\s+\S+", text, re.M | re.I))
    quote_lines = len(re.findall(r"^>+", text, re.M))

    is_thread = from_count >= 2 or quote_lines >= 3 or len(signals) >= 2
    depth = max(from_count, 1) if is_thread else 1

    return {
        "is_thread": is_thread,
        "depth": depth,
        "quote_lines": quote_lines,
        "from_count": from_count,
    }


def summarize_thread(text: str, model: str = "the configured local model") -> str:
    """Call Gemma via ollama to produce a concise thread summary."""
    prompt = (
        "You are an Docket operations assistant. The following is an email thread "
        "(possibly with typos or formatting issues). "
        "In 3-5 bullet points, summarise: "
        "(1) what the customer originally asked or complained about, "
        "(2) what has been tried or responded so far, "
        "(3) the current outstanding issue or request. "
        "Be factual and concise. Do not add greetings or sign-offs.\n\n"
        f"EMAIL THREAD:\n{text[:6000]}\n\nSUMMARY:"
    )
    try:
        result = subprocess.run(
            ["ollama", "run", model],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout.strip()
        if not output:
            return "Thread detected but summary could not be generated."
        return output
    except Exception as exc:
        return f"Summary unavailable: {exc}"
