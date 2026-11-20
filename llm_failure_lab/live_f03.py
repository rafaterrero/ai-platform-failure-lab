"""Live F03: same real LLM-proposed call with scripted read-only retry failures.

Only synthetic tool failures: no real 429s/503s, network dependency or HTTP server.
Model request timing is separate from tool-operation timing; the operation deadline
applies only to the latter. Local research lab, not production software.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError

from .core import DEMO_KEY, LabIdentity, tool_call
from .f03 import ResponseStep, RetryPolicy, run_scenario
from .live_f01 import fetch_ollama, parse_exactly_one_tool_call, ToolRequestError
from .ollama_smoke import MODEL, URL, build_payload


SCENARIOS = {
    "healthy_tool": [ResponseStep(200)],
    "transient_once_503": [ResponseStep(503), ResponseStep(200)],
    "three_failures_then_success": [ResponseStep(503), ResponseStep(503),
                                    ResponseStep(503), ResponseStep(200)],
    "nonretryable_403_then_success": [ResponseStep(403), ResponseStep(200)],
    "rate_limited_retry_after": [ResponseStep(429, retry_after_seconds=.3),
                                 ResponseStep(200)],
}


async def evaluate(call: dict, policy: RetryPolicy | None = None) -> list[dict]:
    identity = LabIdentity(DEMO_KEY)
    outputs = []
    for scenario, steps in SCENARIOS.items():
        for mode in ("baseline", "hardened"):
            outputs.append(await run_scenario(call, identity, scenario=scenario,
                                              mode=mode, steps=steps, policy=policy))
    return outputs


def acceptance(rows: list[dict]) -> bool:
    found = {(r["scenario"], r["mode"]): r for r in rows}
    if len(found) != len(SCENARIOS) * 2:
        return False

    def check(scenario: str, mode: str, *, status: int, attempts: int,
              reads: int, verification: str, error: str | None = None) -> bool:
        item = found.get((scenario, mode), {})
        return (item.get("http_status") == status and
                item.get("tool_attempts") == attempts and
                item.get("tool_reads") == reads and
                item.get("verification") == verification and
                item.get("error_code") == error and
                (item.get("returned_deployment") is not None) == (verification == "verified"))

    return all([
        check("healthy_tool", "baseline", status=200, attempts=1, reads=1, verification="verified"),
        check("healthy_tool", "hardened", status=200, attempts=1, reads=1, verification="verified"),
        check("transient_once_503", "baseline", status=200, attempts=2, reads=1, verification="verified"),
        check("transient_once_503", "hardened", status=200, attempts=2, reads=1, verification="verified"),
        check("three_failures_then_success", "baseline", status=200, attempts=4, reads=1, verification="verified"),
        check("three_failures_then_success", "hardened", status=503, attempts=2, reads=0,
              verification="unverified", error="RETRY_BUDGET_EXHAUSTED"),
        check("nonretryable_403_then_success", "baseline", status=200, attempts=2, reads=1, verification="verified"),
        check("nonretryable_403_then_success", "hardened", status=403, attempts=1, reads=0,
              verification="unverified", error="NON_RETRYABLE_DEPENDENCY_ERROR"),
        check("rate_limited_retry_after", "baseline", status=200, attempts=2, reads=1, verification="verified"),
        check("rate_limited_retry_after", "hardened", status=503, attempts=1, reads=0,
              verification="unverified", error="RETRY_DELAY_EXCEEDS_DEADLINE"),
    ])


def run_live(model: str, url: str, max_predict: int, model_timeout: float = 120,
             policy: RetryPolicy | None = None) -> dict:
    started = time.monotonic()
    output = fetch_ollama(build_payload(model, max_predict, think=False), url,
                          model_timeout)
    model_elapsed = time.monotonic() - started
    call = parse_exactly_one_tool_call(output)
    # Do not replace unexpected model output with predefined tool args.
    if call != tool_call("tenant_alpha", "payments-api", "staging"):
        raise ToolRequestError(f"Unexpected model tool arguments: {call}")
    rows = asyncio.run(evaluate(call, policy))
    p = policy or RetryPolicy()
    return {
        "experiment": "F03-live-synthetic-read-only-retry-policy",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "model_call_source": "real_ollama_api_chat",
        "model": output.get("model"),
        "model_request_elapsed_seconds": round(model_elapsed, 3),
        "model_proposed_tool_call": call,
        "same_model_tool_call_for_all_scenarios": True,
        "synthetic_dependency_responses_only": True,
        "no_external_retries_performed": True,
        "baseline_is_capped_at_four_attempts": True,
        "tool_operation_policy": {
            "hardened_max_attempts": p.max_attempts,
            "hardened_per_attempt_timeout_seconds": p.per_attempt_timeout_seconds,
            "hardened_operation_deadline_seconds": p.operation_deadline_seconds,
            "hardened_backoff_seconds": p.backoff_seconds,
        },
        "scenarios": rows,
        "f03_acceptance_passed": acceptance(rows),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--url", default=URL)
    parser.add_argument("--max-predict", type=int, default=1024)
    parser.add_argument("--model-timeout", type=float, default=120)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        output = run_live(args.model, args.url, args.max_predict,
                          model_timeout=args.model_timeout)
    except (ToolRequestError, ValueError, HTTPError, URLError, TimeoutError,
            OSError, json.JSONDecodeError) as exc:
        print(f"NOT PASSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(output, indent=2, ensure_ascii=False))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")
        print(f"Evidence saved to: {args.report}")
    if output["f03_acceptance_passed"]:
        print("PASSED: same real LLM tool call, bounded retries with synthetic failures.")
        return 0
    print("NOT PASSED: F03 acceptance conditions not satisfied.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
