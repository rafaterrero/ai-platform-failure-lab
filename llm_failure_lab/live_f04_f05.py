"""F04/F05 integrated experiment: one real model tool proposal; synthetic replays.

F04 replays the one LLM output as three proposals to test a tool-call budget.
F05 injects complete/invalid synthetic tool outputs and tests the verification gate.
No second/third model generation, no final LLM synthesis, no external tool I/O.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError

from .core import TOOL_NAME
from .f04_f05 import build_evidence_scenarios, run_f04, run_f05
from .live_f01 import fetch_ollama, parse_exactly_one_tool_call, ToolRequestError
from .ollama_smoke import MODEL, URL, build_payload


def acceptance_f04(rows: list[dict]) -> bool:
    by = {r.get("mode"): r for r in rows}
    b, h = by.get("baseline", {}), by.get("hardened", {})
    return (len(rows) == 2 and
            b.get("http_statuses") == [200, 200, 200] and
            b.get("tool_invocations") == b.get("datastore_reads") == 3 and
            b.get("verified_results") == 3 and
            h.get("http_statuses") == [200, 429, 429] and
            h.get("tool_invocations") == h.get("datastore_reads") == 1 and
            h.get("budget_rejections") == 2 and
            h.get("error_codes") == [None, "TOOL_CALL_BUDGET_EXCEEDED", "TOOL_CALL_BUDGET_EXCEEDED"])


def acceptance_f05(rows: list[dict]) -> bool:
    scenarios = {
        "complete": (200, "verified", None),
        "missing_reason": (502, "unverified", "INCOMPLETE_TOOL_EVIDENCE"),
        "mismatched_service": (502, "unverified", "TOOL_RESPONSE_MISMATCH"),
        "invalid_status": (502, "unverified", "INVALID_DEPLOYMENT_STATUS"),
    }
    by = {(r.get("scenario"), r.get("mode")): r for r in rows}
    if len(by) != 8 or len(rows) != 8:
        return False
    for scenario, (status, verification, error) in scenarios.items():
        b, h = by.get((scenario, "baseline"), {}), by.get((scenario, "hardened"), {})
        if not (b.get("http_status") == 200 and b.get("verification") == "verified"
                and b.get("returned_deployment") is not None and b.get("tool_reads") == 1):
            return False
        if not (h.get("http_status") == status and h.get("verification") == verification
                and h.get("error_code") == error and h.get("tool_reads") == 1
                and (h.get("returned_deployment") is not None) == (status == 200)):
            return False
    return True


def evaluate_call(call: dict) -> dict:
    expected = {"tenant_id": "tenant_alpha", "service": "payments-api", "environment": "staging"}
    if call.get("name") != TOOL_NAME or call.get("arguments") != expected:
        raise ToolRequestError("LLM proposed unexpected arguments; do not override the model output")
    f04 = [run_f04(call, mode) for mode in ("baseline", "hardened")]
    f05 = [run_f05(call, scenario=key, evidence=value, mode=mode)
           for key, value in build_evidence_scenarios(call).items()
           for mode in ("baseline", "hardened")]
    return {"f04": f04, "f05": f05,
            "f04_acceptance_passed": acceptance_f04(f04),
            "f05_acceptance_passed": acceptance_f05(f05)}


def run_live(model: str = MODEL, url: str = URL,
             max_predict: int = 1024, timeout: float = 120) -> dict:
    started = time.monotonic()
    output = fetch_ollama(build_payload(model, max_predict, False), url, timeout)
    elapsed = time.monotonic() - started
    call = parse_exactly_one_tool_call(output)
    evaluated = evaluate_call(call)
    return {
        "experiment": "F04-F05-live-model-proposal-synthetic-boundaries",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "model_call_source": "real_ollama_api_chat",
        "model": output.get("model"),
        "model_request_elapsed_seconds": round(elapsed, 3),
        "prompt_eval_count": output.get("prompt_eval_count"),
        "eval_count": output.get("eval_count"),
        "model_proposed_tool_call": call,
        "real_model_proposals": 1,
        "f04_subsequent_proposals": "synthetic_replay_of_same_model_call",
        "f05_tool_responses": "synthetic_fixtures",
        "final_llm_answer_generated": False,
        "http_status_fields_are_in_process_not_http_responses": True,
        **evaluated,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--url", default=URL)
    parser.add_argument("--max-predict", type=int, default=1024)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    try:
        output = run_live(args.model, args.url, args.max_predict, args.timeout)
    except (ToolRequestError, ValueError, HTTPError, URLError, OSError,
            TimeoutError, json.JSONDecodeError) as exc:
        print(f"NOT PASSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(output, indent=2, ensure_ascii=False))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Evidence saved to: {args.report}")
    if output["f04_acceptance_passed"] and output["f05_acceptance_passed"]:
        print("PASSED: real model proposal, tool-call budget and evidence-validation boundaries (synthetic).")
        return 0
    print("NOT PASSED: F04/F05 acceptance conditions not satisfied", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
