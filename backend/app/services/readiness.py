from __future__ import annotations

import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

from .bert_analysis import runtime_status as bert_runtime_status
from .bert_analysis import verify_runtime as verify_bert_runtime


OLLAMA_MODEL = os.environ.get("TRIAGE_OLLAMA_MODEL", "the configured local model")
_last_verified_at: str | None = None


def _checked_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def ollama_status(probe: bool = False) -> dict[str, Any]:
    executable = shutil.which("ollama")
    result: dict[str, Any] = {
        "status": "unavailable",
        "model": OLLAMA_MODEL,
        "executable_available": bool(executable),
        "model_installed": False,
    }
    if not executable:
        result["error"] = "Ollama executable is not available."
        return result

    try:
        listed = subprocess.run(
            [executable, "list"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=10,
            text=True,
        )
    except Exception as exc:
        result["error"] = f"Unable to query Ollama models: {exc}"
        return result

    installed_models = {
        line.split()[0]
        for line in listed.stdout.splitlines()[1:]
        if line.strip()
    }
    result["model_installed"] = OLLAMA_MODEL in installed_models
    result["status"] = "available" if result["model_installed"] else "missing_model"
    if not result["model_installed"]:
        result["error"] = f"Required Ollama model {OLLAMA_MODEL} is not installed."
        return result

    if not probe:
        return result

    started = time.perf_counter()
    try:
        inference = subprocess.run(
            [executable, "run", OLLAMA_MODEL],
            input='Return only this JSON object: {"status":"ok"}',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=120,
            text=True,
        )
        if not inference.stdout.strip():
            raise RuntimeError("empty response")
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = f"Ollama inference failed: {exc}"
        return result

    result["status"] = "ready"
    result["latency_ms"] = round((time.perf_counter() - started) * 1000)
    return result


def system_status(probe: bool = False) -> dict[str, Any]:
    global _last_verified_at
    bert: dict[str, Any]
    try:
        bert = verify_bert_runtime() if probe else bert_runtime_status()
    except Exception as exc:
        bert = {
            **bert_runtime_status(),
            "status": "failed",
            "error": f"BERT inference failed: {exc}",
        }

    ollama = ollama_status(probe=probe)
    states = {bert.get("status"), ollama.get("status")}
    overall = "ready" if probe and states == {"ready"} else "available"
    if "unavailable" in states or "missing_model" in states or "failed" in states:
        overall = "degraded"
    checked_at = _checked_at()
    if probe and overall == "ready":
        _last_verified_at = checked_at
    elif not probe and _last_verified_at and overall != "degraded":
        overall = "ready"
        bert["status"] = "ready"
        bert["verified_at"] = _last_verified_at
        ollama["status"] = "ready"
        ollama["verified_at"] = _last_verified_at

    return {
        "ok": overall != "degraded",
        "status": overall,
        "checked_at": checked_at,
        "verified_at": _last_verified_at,
        "pipeline": "bert+ollama",
        "bert": bert,
        "ollama": ollama,
    }
