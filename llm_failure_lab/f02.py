"""F02: tool deadline and verification experiment with synthetic asynchronous data.

Python standard library only. The 'baseline' differs ONLY by lacking a tool timeout;
both modes authenticate and authorize. Designed for in-process CLI tests, not a server.
Cooperative async cancellation DOES NOT prove cancellation of external side effects.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from .core import (AuthenticationError, LabIdentity, Principal, RECORDS,
                   TOOL_NAME, result)


class ToolUnavailable(Exception):
    pass


class SyntheticAsyncTool:
    """Controlled synthetic dependency. Read count increments after delay completes."""
    def __init__(self, delay_seconds: float = 0.0, failure: bool = False,
                 incomplete: bool = False):
        if delay_seconds < 0:
            raise ValueError("Delay must be non-negative")
        self.delay_seconds = delay_seconds
        self.failure = failure
        self.incomplete = incomplete
        self.attempts = 0
        self.reads = 0
        self.cancelled = 0

    async def read(self, tenant_id: str, service: str, environment: str) -> dict | None:
        self.attempts += 1
        try:
            await asyncio.sleep(self.delay_seconds)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if self.failure:
            raise ToolUnavailable("synthetic dependency unavailable")
        self.reads += 1
        record = RECORDS.get((tenant_id, service, environment))
        if record is None:
            return None
        record = dict(record)
        if self.incomplete:
            record.pop("status", None)
        return record


class DeadlineExecutor:
    """Strictly validate and authorize proposed tool calls before accessing dependency."""
    def __init__(self, identity: LabIdentity, tool: SyntheticAsyncTool,
                 tool_timeout_seconds: float = .08):
        if tool_timeout_seconds <= 0:
            raise ValueError("Tool timeout must be positive")
        self.identity = identity
        self.tool = tool
        self.tool_timeout_seconds = tool_timeout_seconds
        self.audit: list[dict[str, Any]] = []

    async def execute(self, *, token: str | None, tool_call: dict,
                      mode: str = "hardened") -> dict:
        if mode not in ("baseline", "hardened"):
            raise ValueError("Unknown mode")
        try:
            principal: Principal = self.identity.verify(token)
        except AuthenticationError:
            self.audit.append({"decision": "unauthenticated"})
            return result(401, error="UNAUTHENTICATED")
        if not isinstance(tool_call, dict) or tool_call.get("name") != TOOL_NAME:
            self.audit.append({"decision": "invalid_tool"})
            return result(502, error="INVALID_TOOL_CALL")
        args = tool_call.get("arguments")
        if (not isinstance(args, dict)
                or set(args) != {"tenant_id", "service", "environment"}
                or any(not isinstance(v, str) or not v or len(v) > 128
                       for v in args.values())):
            self.audit.append({"decision": "invalid_arguments"})
            return result(502, error="INVALID_TOOL_ARGUMENTS")
        if args["tenant_id"] != principal.tenant_id:
            self.audit.append({"decision": "denied"})
            return result(403, error="ACCESS_DENIED")

        try:
            # F02 isolates deadline enforcement, not F01's intentional authorization defect.
            operation = self.tool.read(principal.tenant_id, args["service"], args["environment"])
            if mode == "hardened":
                evidence = await asyncio.wait_for(operation, timeout=self.tool_timeout_seconds)
            else:
                evidence = await operation
        except asyncio.TimeoutError:
            self.audit.append({"decision": "timeout", "timeout_ms": int(self.tool_timeout_seconds * 1000)})
            return result(504, error="TOOL_TIMEOUT")
        except ToolUnavailable:
            self.audit.append({"decision": "dependency_unavailable"})
            return result(503, error="TOOL_UNAVAILABLE")

        if evidence is None:
            self.audit.append({"decision": "not_found"})
            return result(404, error="DEPLOYMENT_NOT_FOUND")
        required = ("service", "environment", "status", "reason", "revision", "observed_at")
        if not isinstance(evidence, dict) or any(not evidence.get(k) for k in required):
            self.audit.append({"decision": "incomplete_evidence"})
            return result(502, error="INCOMPLETE_TOOL_EVIDENCE")
        self.audit.append({"decision": "read"})
        return result(200, verification="verified", deployment=evidence)


async def run_scenario(call: dict, identity: LabIdentity, *, mode: str, delay_seconds: float,
                       timeout_seconds: float, principal_subject: str = "alice",
                       principal_tenant: str = "tenant_alpha", failure: bool = False,
                       incomplete: bool = False) -> dict[str, Any]:
    backend = SyntheticAsyncTool(delay_seconds=delay_seconds, failure=failure,
                                 incomplete=incomplete)
    executor = DeadlineExecutor(identity, backend, tool_timeout_seconds=timeout_seconds)
    token = identity.issue(principal_subject, principal_tenant)
    started = time.monotonic()
    response = await executor.execute(token=token, tool_call=call, mode=mode)
    elapsed = (time.monotonic() - started) * 1000
    return {
        "scenario": "slow_tool" if delay_seconds > timeout_seconds else "healthy_tool",
        "principal": principal_subject,
        "mode": mode,
        "elapsed_ms": round(elapsed, 3),
        "http_status": response["http_status"],
        "verification": response["verification"],
        "returned_deployment": response["deployment"],
        "error_code": (response["error"] or {}).get("code"),
        "tool_attempts": backend.attempts,
        "tool_reads": backend.reads,
        "tool_cancellations": backend.cancelled,
        "audit": executor.audit,
    }
