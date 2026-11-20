"""F04/F05: In-process, synthetic operational contract experiments.

F04: replay one previously generated tool proposal to test a *tool-call* quota.
F05: inject malformed synthetic tool evidence and check the verification boundary.

These experiments do NOT test a complete multi-turn LLM agent, inference token
budgets, actual external tool I/O, real HTTP status codes, or production auth.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .core import (
    AuthenticationError, DEMO_KEY, DeploymentStore, Executor, LabIdentity,
    RECORDS, TOOL_NAME, result,
)


@dataclass(frozen=True)
class ExecutionBudget:
    max_tool_calls: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.max_tool_calls, int) or isinstance(self.max_tool_calls, bool) or self.max_tool_calls < 1:
            raise ValueError("max_tool_calls must be a positive integer")


class BudgetedToolRunner:
    """A local tool budget; rejects proposals BEFORE calling the real executor.

    The underlying Executor performs independent authorization/argument checks.
    Baseline intentionally omits the quota, but the driver submits at most three
    replayed proposals: there is no unbounded loop or network exposure.
    """
    def __init__(self, identity: LabIdentity, store: DeploymentStore,
                 budget: ExecutionBudget | None = None):
        self.executor = Executor(identity, store)
        self.store = store
        self.budget = budget or ExecutionBudget()
        self.tool_invocations = 0
        self.rejections = 0
        self.audit: list[dict[str, Any]] = []

    def submit(self, *, token: str | None, call: dict, mode: str) -> dict[str, Any]:
        if mode not in ("baseline", "hardened"):
            raise ValueError("Invalid mode")
        # Quota counts dispatched calls, not inbound proposals. Even a rejected
        # unauthorized tool invocation consumes quota if dispatched.
        if mode == "hardened" and self.tool_invocations >= self.budget.max_tool_calls:
            self.rejections += 1
            self.audit.append({"decision": "tool_budget_exhausted"})
            return result(429, error="TOOL_CALL_BUDGET_EXCEEDED")
        self.tool_invocations += 1
        response = self.executor.execute(token=token, tool_call=call,
                                         mode="hardened")
        self.audit.append({"decision": "dispatched", "status": response["http_status"]})
        return response


def run_f04(call: dict, mode: str, *, proposals: int = 3,
            budget: ExecutionBudget | None = None) -> dict[str, Any]:
    if not 1 <= proposals <= 3:
        raise ValueError("This safe local demo supports 1 to 3 proposals")
    identity = LabIdentity(DEMO_KEY)
    store = DeploymentStore()
    runner = BudgetedToolRunner(identity, store, budget)
    token = identity.issue("alice", "tenant_alpha")
    replies = [runner.submit(token=token, call=call, mode=mode)
               for _ in range(proposals)]
    return {
        "mode": mode,
        "proposals_submitted": proposals,
        "tool_invocations": runner.tool_invocations,
        "datastore_reads": store.reads,
        "budget_rejections": runner.rejections,
        "http_statuses": [r["http_status"] for r in replies],
        "error_codes": [(r["error"] or {}).get("code") for r in replies],
        "verified_results": sum(r["verification"] == "verified" for r in replies),
        "audit": runner.audit,
    }


REQUIRED_FIELDS = ("service", "environment", "status", "reason", "revision", "observed_at")
ALLOWED_STATUSES = {"HEALTHY", "FAILED", "DEPLOYING"}


def _evidence_valid(evidence: Any, call: dict) -> tuple[bool, str | None]:
    if not isinstance(evidence, dict):
        return False, "INCOMPLETE_TOOL_EVIDENCE"
    if any(not isinstance(evidence.get(k), str) or not evidence[k]
           for k in REQUIRED_FIELDS):
        return False, "INCOMPLETE_TOOL_EVIDENCE"
    a = call["arguments"]
    if evidence["service"] != a["service"] or evidence["environment"] != a["environment"]:
        return False, "TOOL_RESPONSE_MISMATCH"
    if evidence["status"] not in ALLOWED_STATUSES:
        return False, "INVALID_DEPLOYMENT_STATUS"
    try:
        # Timestamp *format* validation, not a freshness guarantee.
        parsed = datetime.fromisoformat(evidence["observed_at"].replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return False, "INVALID_EVIDENCE_TIMESTAMP"
    except (ValueError, TypeError, OverflowError):
        return False, "INVALID_EVIDENCE_TIMESTAMP"
    return True, None


class ScriptedEvidenceTool:
    """Exactly one fixture result per call; no external dependency."""
    def __init__(self, evidence: dict):
        self.evidence = dict(evidence)
        self.reads = 0

    def query(self) -> dict:
        self.reads += 1
        return dict(self.evidence)


class EvidenceBoundary:
    def __init__(self, identity: LabIdentity, tool: ScriptedEvidenceTool):
        self.identity, self.tool = identity, tool
        self.audit: list[dict[str, str]] = []

    def execute(self, token: str | None, call: dict, mode: str) -> dict[str, Any]:
        if mode not in ("baseline", "hardened"):
            raise ValueError("Invalid mode")
        try:
            principal = self.identity.verify(token)
        except AuthenticationError:
            self.audit.append({"decision": "unauthenticated"})
            return result(401, error="UNAUTHENTICATED")
        if not isinstance(call, dict) or call.get("name") != TOOL_NAME:
            self.audit.append({"decision": "invalid_tool"})
            return result(502, error="INVALID_TOOL_CALL")
        args = call.get("arguments")
        if (not isinstance(args, dict)
                or set(args) != {"tenant_id", "service", "environment"}
                or any(not isinstance(v, str) or not v or len(v) > 128
                       for v in args.values())):
            self.audit.append({"decision": "invalid_arguments"})
            return result(502, error="INVALID_TOOL_ARGUMENTS")
        # Both modes enforce tenant access. F05 isolates evidence validation.
        if args["tenant_id"] != principal.tenant_id:
            self.audit.append({"decision": "denied"})
            return result(403, error="ACCESS_DENIED")
        evidence = self.tool.query()
        if mode == "hardened":
            valid, error_code = _evidence_valid(evidence, call)
            if not valid:
                self.audit.append({"decision": "evidence_rejected"})
                return result(502, error=error_code)
        # Demonstration-only bug: baseline equates "some object returned" with
        # "deployment verified" even when status, reason or service is wrong.
        self.audit.append({"decision": "evidence_accepted"})
        return result(200, verification="verified", deployment=evidence)


def build_evidence_scenarios(call: dict) -> dict[str, dict]:
    a = call["arguments"]
    canonical = RECORDS.get((a["tenant_id"], a["service"], a["environment"]))
    if canonical is None:
        raise ValueError("Live model proposed an unknown synthetic fixture; do not fabricate or rewrite it")
    complete = dict(canonical)
    missing_reason = dict(canonical); missing_reason.pop("reason")
    mismatched_service = dict(canonical); mismatched_service["service"] = "unrelated-api"
    invalid_status = dict(canonical); invalid_status["status"] = "UNKNOWN"
    return {
        "complete": complete,
        "missing_reason": missing_reason,
        "mismatched_service": mismatched_service,
        "invalid_status": invalid_status,
    }


def run_f05(call: dict, *, scenario: str, evidence: dict, mode: str) -> dict[str, Any]:
    identity = LabIdentity(DEMO_KEY)
    tool = ScriptedEvidenceTool(evidence)
    boundary = EvidenceBoundary(identity, tool)
    token = identity.issue("alice", "tenant_alpha")
    response = boundary.execute(token, call, mode)
    return {
        "scenario": scenario,
        "mode": mode,
        "http_status": response["http_status"],
        "verification": response["verification"],
        "error_code": (response["error"] or {}).get("code"),
        "returned_deployment": response["deployment"],
        "tool_reads": tool.reads,
        "audit": boundary.audit,
    }
