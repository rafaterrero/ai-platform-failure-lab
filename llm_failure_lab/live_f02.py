"""Live F02: real Qwen3 tool call + controlled local tool latency, baseline vs hardened.

This uses one REAL model-generated request but only synthetic tool responses. No production
service data, no actual network timeouts, no real cloud operations or actual HTTP API.
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
from .f02 import run_scenario
from .live_f01 import fetch_ollama, parse_exactly_one_tool_call, ToolRequestError
from .ollama_smoke import MODEL, URL, build_payload


async def evaluate(call: dict, *, slow_seconds: float = .3,
                   tool_timeout_seconds: float = .08) -> list[dict]:
    if not (0 < tool_timeout_seconds < slow_seconds):
        raise ValueError("Require positive timeout < slow dependency delay")
    identity = LabIdentity(DEMO_KEY)
    outputs = []
    for mode, delay in (("baseline", .005), ("hardened", .005),
                        ("baseline", slow_seconds), ("hardened", slow_seconds)):
        item = await run_scenario(call, identity, mode=mode, delay_seconds=delay,
                                  timeout_seconds=tool_timeout_seconds)
        item["scenario"] = "healthy_tool" if delay < tool_timeout_seconds else "slow_tool"
        outputs.append(item)
    return outputs


def acceptance(scenarios: list[dict], *, slow_seconds: float,
               tool_timeout_seconds: float) -> bool:
    rows = {(s["scenario"], s["mode"]): s for s in scenarios}
    if len(rows) != 4:
        return False
    for mode in ("baseline", "hardened"):
        row = rows.get(("healthy_tool", mode), {})
        if not (row.get("http_status") == 200 and row.get("verification") == "verified"
                and row.get("tool_reads") == 1 and row.get("tool_attempts") == 1):
            return False
    naive = rows.get(("slow_tool", "baseline"), {})
    guarded = rows.get(("slow_tool", "hardened"), {})
    # Tolerance is generous for CI scheduling; illustrative, NOT a hard realtime guarantee.
    return (naive.get("http_status") == 200 and naive.get("tool_reads") == 1
            and naive.get("elapsed_ms", 0) >= slow_seconds * 750
            and guarded.get("http_status") == 504
            and guarded.get("verification") == "unverified"
            and guarded.get("error_code") == "TOOL_TIMEOUT"
            and guarded.get("returned_deployment") is None
            and guarded.get("tool_reads") == 0
            and guarded.get("tool_cancellations") == 1
            and guarded.get("tool_attempts") == 1
            and guarded.get("elapsed_ms", float("inf")) < slow_seconds * 1000)


def run_live(model: str, url: str, max_predict: int,
             model_timeout: float = 120, slow_seconds: float = .3,
             tool_timeout_seconds: float = .08) -> dict:
    t0 = time.monotonic()
    output = fetch_ollama(build_payload(model, max_predict, think=False), url, model_timeout)
    model_elapsed = time.monotonic() - t0
    call = parse_exactly_one_tool_call(output)
    # We do not silently replace model args with a fixture for a convenient outcome.
    if call != tool_call("tenant_alpha", "payments-api", "staging"):
        raise ToolRequestError(f"Unexpected model tool arguments: {call}")
    scenarios = asyncio.run(evaluate(call, slow_seconds=slow_seconds,
                                     tool_timeout_seconds=tool_timeout_seconds))
    return {
        "experiment": "F02-live-synthetic-tool-timeout",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "model_call_source": "real_ollama_api_chat",
        "model": output.get("model"),
        "model_elapsed_seconds": round(model_elapsed, 3),
        "model_proposed_tool_call": call,
        "synthetic_tool_injection_only": True,
        "same_model_tool_call_for_all_scenarios": True,
        "timeout_contract_scope": "cooperative async tool simulation, NOT external service cancellation",
        "configured_slow_seconds": slow_seconds,
        "configured_tool_timeout_seconds": tool_timeout_seconds,
        "scenarios": scenarios,
        "f02_acceptance_passed": acceptance(scenarios, slow_seconds=slow_seconds,
                                            tool_timeout_seconds=tool_timeout_seconds),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--url", default=URL)
    parser.add_argument("--max-predict", type=int, default=1024)
    parser.add_argument("--model-timeout", type=float, default=120)
    parser.add_argument("--slow-seconds", type=float, default=.3)
    parser.add_argument("--tool-timeout-seconds", type=float, default=.08)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        evidence = run_live(args.model, args.url, args.max_predict,
                            model_timeout=args.model_timeout,
                            slow_seconds=args.slow_seconds,
                            tool_timeout_seconds=args.tool_timeout_seconds)
    except (ToolRequestError, ValueError, HTTPError, URLError, TimeoutError,
            OSError, json.JSONDecodeError) as exc:
        print(f"NOT PASSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(evidence, indent=2, ensure_ascii=False))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Evidence saved to: {args.report}")
    if evidence["f02_acceptance_passed"]:
        print("PASSED: same real LLM tool call, bounded cooperative timeout in hardened mode.")
        return 0
    print("NOT PASSED: F02 comparison failed its acceptance contract.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
