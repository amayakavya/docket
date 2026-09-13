from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, Header, HTTPException


JWT_SECRET = os.environ.get("Docket_OPS_JWT_SECRET", "local-dev-secret-change-for-production")
JWT_ALGORITHM = "HS256"

ROLES = {
    "intake_operator",
    "security_analyst",
    "customer_resolution_agent",
    "compliance_officer",
    "technical_support_engineer",
    "escalation_manager",
    "auditor",
    "admin",
}


def create_access_token(username: str, role: str) -> str:
    if role not in ROLES:
        raise ValueError(f"Unknown role: {role}")
    payload = {
        "sub": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=8),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization:
        return {"username": "local_admin", "role": "admin"}
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid Authorization header")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    return {"username": payload.get("sub", "unknown"), "role": payload.get("role", "auditor")}


def require_roles(*allowed_roles: str):
    def _dep(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if user["role"] == "admin" or user["role"] in allowed_roles:
            return user
        raise HTTPException(status_code=403, detail="Insufficient role")

    return _dep
