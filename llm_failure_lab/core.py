"""F01: deterministic security boundary experiment. Python standard library only.

Never deploy baseline outside a local test environment. The authentication scheme is
an educational HMAC token format, NOT JWT / enterprise authentication.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, asdict
import hashlib
import hmac
import json
import os
import time
from typing import Any

# Fixed synthetic deployment records, never real customer or cloud data.
RECORDS = {
    ("tenant_alpha", "payments-api", "staging"): {
        "service": "payments-api", "environment": "staging",
        "status": "FAILED", "reason": "READINESS_PROBE_TIMEOUT",
        "revision": "a831c1d", "observed_at": "2026-11-19T09:00:00Z"
    },
    ("tenant_beta", "orders-api", "production"): {
        "service": "orders-api", "environment": "production",
        "status": "HEALTHY", "reason": "NONE",
        "revision": "b9c402e", "observed_at": "2026-11-19T10:00:00Z"
    },
}

TOOL_NAME = "get_deployment_status"


class AuthenticationError(Exception):
    pass


class AuthorizationError(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_id: str


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class LabIdentity:
    """Short-lived signed fixture identity. Key is supplied in local test code."""
    def __init__(self, signing_key: bytes):
        if len(signing_key) < 32:
            raise ValueError("Provide a 32+ byte local lab signing key")
        self._key = signing_key

    def issue(self, subject: str, tenant_id: str, ttl_seconds: int = 300) -> str:
        body = {"sub": subject, "tenant": tenant_id,
                "exp": int(time.time()) + ttl_seconds}
        payload = _b64url(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
        signature = _b64url(hmac.digest(self._key, payload.encode(), hashlib.sha256))
        return f"{payload}.{signature}"

    def verify(self, token: str | None) -> Principal:
        if not token:
            raise AuthenticationError("Missing token")
        try:
            payload, supplied_sig = token.split(".")
            expected_sig = _b64url(hmac.digest(self._key, payload.encode(), hashlib.sha256))
            if not hmac.compare_digest(supplied_sig, expected_sig):
                raise AuthenticationError("Invalid signature")
            data = json.loads(_unb64url(payload))
            if type(data.get("exp")) is not int or data["exp"] <= int(time.time()):
                raise AuthenticationError("Expired token")
            if not all(isinstance(data.get(k), str) and data[k] for k in ("sub", "tenant")):
                raise AuthenticationError("Invalid principal")
            return Principal(data["sub"], data["tenant"])
        except (ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError) as exc:
            raise AuthenticationError("Malformed token") from exc


class DeploymentStore:
    """Synthetic datastore with measurable reads and scoped access."""
    def __init__(self):
        self.reads = 0
        self._records = RECORDS

    def read_raw(self, tenant_id: str, service: str, environment: str) -> dict[str, str] | None:
        """DELIBERATELY UNSAFE primitive: baseline only, synthetic data."""
        self.reads += 1
        value = self._records.get((tenant_id, service, environment))
        return dict(value) if value else None

    def read_scoped(self, principal: Principal, requested_tenant: str,
                    service: str, environment: str) -> dict[str, str] | None:
        """Defense in depth: authorize again at storage boundary, BEFORE reading."""
        if requested_tenant != principal.tenant_id:
            raise AuthorizationError("Cross-tenant read denied")
        return self.read_raw(principal.tenant_id, service, environment)


def result(status: int, verification: str = "unverified", deployment: dict | None = None,
           error: str | None = None) -> dict[str, Any]:
    return {
        "http_status": status,
        "verification": verification,
        "deployment": deployment,
        "error": {"code": error} if error else None,
    }


class Executor:
    """Executes a *model-proposed* tool call; model output is untrusted."""
    def __init__(self, identity: LabIdentity, store: DeploymentStore):
        self.identity, self.store = identity, store
        self.audit: list[dict[str, str]] = []

    def execute(self, *, token: str | None, tool_call: dict[str, Any],
                mode: str = "hardened", untrusted_headers: dict | None = None) -> dict[str, Any]:
        if mode not in ("baseline", "hardened"):
            raise ValueError("Unknown mode")
        try:
            principal = self.identity.verify(token)
        except AuthenticationError:
            self.audit.append({"decision": "unauthenticated"})
            return result(401, error="UNAUTHENTICATED")

        # Ignore arbitrary tenant headers in BOTH modes. Identity is verified.
        # The baseline defect is the lack of authorization of model tool arguments.
        _ = untrusted_headers
        if not isinstance(tool_call, dict) or tool_call.get("name") != TOOL_NAME:
            self.audit.append({"decision": "invalid_tool"})
            return result(502, error="INVALID_TOOL_CALL")
        args = tool_call.get("arguments")
        if not isinstance(args, dict) or set(args) != {"tenant_id", "service", "environment"}:
            self.audit.append({"decision": "invalid_arguments"})
            return result(502, error="INVALID_TOOL_ARGUMENTS")
        if any(not isinstance(value, str) or not value or len(value) > 128 for value in args.values()):
            self.audit.append({"decision": "invalid_arguments"})
            return result(502, error="INVALID_TOOL_ARGUMENTS")

        wanted_tenant = args["tenant_id"]
        if mode == "hardened" and wanted_tenant != principal.tenant_id:
            self.audit.append({"decision": "denied", "tool": TOOL_NAME})
            return result(403, error="ACCESS_DENIED")

        try:
            if mode == "hardened":
                record = self.store.read_scoped(principal, wanted_tenant,
                                                args["service"], args["environment"])
            else:
                # Deliberately flawed baseline path. NEVER deploy this in a real API.
                record = self.store.read_raw(wanted_tenant, args["service"], args["environment"])
        except AuthorizationError:
            self.audit.append({"decision": "denied_at_store", "tool": TOOL_NAME})
            return result(403, error="ACCESS_DENIED")

        if record is None:
            return result(404, error="DEPLOYMENT_NOT_FOUND")
        if not record.get("status") or not record.get("reason"):
            return result(502, error="INCOMPLETE_TOOL_EVIDENCE")
        self.audit.append({"decision": "read", "tool": TOOL_NAME})
        return result(200, verification="verified", deployment=record)


def tool_call(tenant_id: str, service: str, environment: str) -> dict[str, Any]:
    return {"name": TOOL_NAME, "arguments": {
        "tenant_id": tenant_id, "service": service, "environment": environment}}


# NOT production key material. Used only to make examples reproducible.
# A deployed app must NOT use this sample key nor expose baseline functionality.
DEMO_KEY = b"llm-platform-failure-lab-v02-demo-only-signing-key"
