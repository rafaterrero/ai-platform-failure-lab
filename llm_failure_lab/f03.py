"""F03: bounded retries for a synthetic read-only dependency.

A deterministic, in-process educational experiment (no network service). The baseline
has a deliberately overgenerous fixed number of attempts, ignores Retry-After and has
no cumulative deadline. It is still capped to keep the demonstration safe. Hardened
uses at most two attempts, a cumulative *tool-operation* deadline, and only retries
transient failures (429/503). Cancellation does not imply external side-effect rollback.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from typing import Any, Iterable

from .core import AuthenticationError, LabIdentity, Principal, RECORDS, TOOL_NAME, result


class DependencyFailure(Exception):
    def __init__(self, status: int, retry_after_seconds: float | None = None):
        self.status = status
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"simulated dependency HTTP {status}")


@dataclass(frozen=True)
class ResponseStep:
    status: int = 200
    delay_seconds: float = 0.0
    retry_after_seconds: float | None = None


class ScriptedReadOnlyTool:
    """A synthetic dependency. Each attempt consumes one scripted outcome."""
    def __init__(self, steps: Iterable[ResponseStep]):
        self.steps = tuple(steps)
        if not self.steps:
            raise ValueError("Supply at least one scripted response")
        if any(step.delay_seconds < 0 or
               (step.retry_after_seconds is not None and step.retry_after_seconds < 0)
               for step in self.steps):
            raise ValueError("Negative delays are invalid")
        self.attempts = 0
        self.reads = 0
        self.cancelled = 0
        self.statuses: list[int] = []

    async def read(self, tenant_id: str, service: str, environment: str) -> dict | None:
        index = self.attempts
        self.attempts += 1
        # Last scripted response repeats, allowing bounded repeated-failure tests.
        step = self.steps[min(index, len(self.steps) - 1)]
        self.statuses.append(step.status)
        try:
            await asyncio.sleep(step.delay_seconds)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if step.status != 200:
            raise DependencyFailure(step.status, step.retry_after_seconds)
        self.reads += 1
        record = RECORDS.get((tenant_id, service, environment))
        return dict(record) if record is not None else None


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    per_attempt_timeout_seconds: float = .07
    operation_deadline_seconds: float = .15
    backoff_seconds: float = .015

    def __post_init__(self):
        if (self.max_attempts < 1 or self.per_attempt_timeout_seconds <= 0 or
                self.operation_deadline_seconds <= 0 or self.backoff_seconds < 0):
            raise ValueError("Retry policy must have positive limits and nonnegative backoff")


class RetryExecutor:
    """Validates principal and tool arguments BEFORE invoking the synthetic dependency."""
    def __init__(self, identity: LabIdentity, tool: ScriptedReadOnlyTool,
                 policy: RetryPolicy | None = None):
        self.identity = identity
        self.tool = tool
        self.policy = policy or RetryPolicy()
        self.audit: list[dict[str, Any]] = []

    def _validate(self, token: str | None, call: dict) -> tuple[Principal | None, dict | None, dict | None]:
        try:
            principal: Principal = self.identity.verify(token)
        except AuthenticationError:
            self.audit.append({"decision": "unauthenticated"})
            return None, None, result(401, error="UNAUTHENTICATED")
        if not isinstance(call, dict) or call.get("name") != TOOL_NAME:
            self.audit.append({"decision": "invalid_tool"})
            return None, None, result(502, error="INVALID_TOOL_CALL")
        args = call.get("arguments")
        if (not isinstance(args, dict)
                or set(args) != {"tenant_id", "service", "environment"}
                or any(not isinstance(v, str) or not v or len(v) > 128
                       for v in args.values())):
            self.audit.append({"decision": "invalid_arguments"})
            return None, None, result(502, error="INVALID_TOOL_ARGUMENTS")
        if principal.tenant_id != args["tenant_id"]:
            self.audit.append({"decision": "denied"})
            return None, None, result(403, error="ACCESS_DENIED")
        return principal, args, None

    async def execute(self, *, token: str | None, tool_call: dict,
                      mode: str = "hardened") -> dict:
        if mode not in ("baseline", "hardened"):
            raise ValueError("Unknown mode")
        principal, args, error = self._validate(token, tool_call)
        if error is not None:
            return error
        assert principal is not None and args is not None
        hardened = mode == "hardened"
        # The deliberately flawed baseline remains capped at 4, never infinite.
        max_attempts = self.policy.max_attempts if hardened else 4
        cutoff = time.monotonic() + self.policy.operation_deadline_seconds

        for i in range(1, max_attempts + 1):
            remaining = cutoff - time.monotonic()
            if hardened and remaining <= 0:
                self.audit.append({"decision": "deadline_exceeded", "before_attempt": i})
                return result(504, error="TOOL_OPERATION_DEADLINE")
            try:
                task = self.tool.read(principal.tenant_id, args["service"], args["environment"])
                if hardened:
                    evidence = await asyncio.wait_for(
                        task, timeout=min(remaining, self.policy.per_attempt_timeout_seconds))
                else:
                    evidence = await task
            except asyncio.TimeoutError:
                self.audit.append({"decision": "attempt_timeout", "attempt": i})
                return result(504, error="TOOL_TIMEOUT")
            except DependencyFailure as failure:
                self.audit.append({"decision": "dependency_error", "attempt": i,
                                   "dependency_status": failure.status})
                # 401/403, non-transient and unknown statuses must not be retried.
                if hardened and failure.status not in (429, 503):
                    return result(503 if failure.status >= 500 else failure.status,
                                  error="NON_RETRYABLE_DEPENDENCY_ERROR")
                if i == max_attempts:
                    return result(503, error="RETRY_BUDGET_EXHAUSTED")
                if hardened:
                    delay = (failure.retry_after_seconds
                             if failure.retry_after_seconds is not None
                             else self.policy.backoff_seconds)
                    # Do not begin an attempt if the requested delay leaves no budget.
                    remaining = cutoff - time.monotonic()
                    if delay >= remaining:
                        self.audit.append({"decision": "retry_delay_exceeds_deadline",
                                           "attempt": i, "delay_seconds": delay})
                        return result(503, error="RETRY_DELAY_EXCEEDS_DEADLINE")
                    self.audit.append({"decision": "retry_scheduled", "after_attempt": i,
                                       "delay_seconds": delay})
                    await asyncio.sleep(delay)
                # Baseline intentionally retries everything without backoff.
                continue

            if evidence is None:
                self.audit.append({"decision": "not_found"})
                return result(404, error="DEPLOYMENT_NOT_FOUND")
            fields = ("service", "environment", "status", "reason", "revision", "observed_at")
            if not isinstance(evidence, dict) or any(not evidence.get(k) for k in fields):
                self.audit.append({"decision": "incomplete_evidence"})
                return result(502, error="INCOMPLETE_TOOL_EVIDENCE")
            self.audit.append({"decision": "verified", "after_attempt": i})
            return result(200, verification="verified", deployment=evidence)
        raise RuntimeError("unreachable")


async def run_scenario(call: dict, identity: LabIdentity, *, mode: str,
                       scenario: str, steps: list[ResponseStep],
                       principal_subject: str = "alice",
                       principal_tenant: str = "tenant_alpha",
                       policy: RetryPolicy | None = None) -> dict[str, Any]:
    dependency = ScriptedReadOnlyTool(steps)
    executor = RetryExecutor(identity, dependency, policy=policy)
    token = identity.issue(principal_subject, principal_tenant)
    start = time.monotonic()
    response = await executor.execute(token=token, tool_call=call, mode=mode)
    return {
        "scenario": scenario,
        "mode": mode,
        "principal": principal_subject,
        "elapsed_ms": round((time.monotonic() - start) * 1000, 3),
        "http_status": response["http_status"],
        "verification": response["verification"],
        "error_code": (response["error"] or {}).get("code"),
        "returned_deployment": response["deployment"],
        "tool_attempts": dependency.attempts,
        "tool_reads": dependency.reads,
        "dependency_statuses": dependency.statuses,
        "tool_cancellations": dependency.cancelled,
        "audit": executor.audit,
    }
