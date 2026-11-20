"""F01 live LLM -> untrusted structured tool call -> baseline/hardened executor.

No non-synthetic data. Intentionally insecure baseline is *in-process only*.
Requires existing Ollama service; no third-party Python dependencies.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .core import DEMO_KEY, LabIdentity, DeploymentStore, Executor, TOOL_NAME
from .ollama_smoke import MODEL, URL, build_payload


class ToolRequestError(ValueError):
    """A model output is not exactly one actionable structured tool request."""


def parse_exactly_one_tool_call(output: dict) -> dict:
    """No action is taken here. Reject missing/multiple/wrong/malformed tool calls.

    The Executor independently checks the tool/arguments and authorizes the tenant.
    """
    if not isinstance(output, dict):
        raise ToolRequestError("model response is not an object")
    message = output.get("message")
    if not isinstance(message, dict):
        raise ToolRequestError("missing message object")
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        raise ToolRequestError("expected exactly one structured tool call")
    call = calls[0]
    if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
        raise ToolRequestError("malformed function tool call")
    fn = call["function"]
    if fn.get("name") != TOOL_NAME:
        raise ToolRequestError("unexpected tool name")
    if not isinstance(fn.get("arguments"), dict):
        raise ToolRequestError("tool arguments are not an object")
    # NO authorization here; never assume these model-supplied fields are trusted.
    return {"name": fn["name"], "arguments": fn["arguments"]}


def evaluate_call(call: dict, identity: LabIdentity | None = None) -> list[dict]:
    """Use the SAME model tool call for each principal and execution mode.

    A separate synthetic store for every scenario makes `datastore_reads` comparable.
    """
    identity = identity or LabIdentity(DEMO_KEY)
    scenarios = (
        ("alice", "tenant_alpha", "baseline"),
        ("alice", "tenant_alpha", "hardened"),
        ("bob", "tenant_beta", "baseline"),
        ("bob", "tenant_beta", "hardened"),
    )
    outcomes = []
    for subject, tenant, mode in scenarios:
        store = DeploymentStore()
        executor = Executor(identity, store)
        token = identity.issue(subject, tenant)
        response = executor.execute(token=token, tool_call=call, mode=mode)
        outcomes.append({
            "principal": subject,
            "principal_tenant": tenant,
            "mode": mode,
            "http_status": response["http_status"],
            "verification": response["verification"],
            "error_code": (response["error"] or {}).get("code"),
            "returned_deployment": response["deployment"],
            "datastore_reads": store.reads,
            "audit": executor.audit,
        })
    return outcomes


def verify_f01(outcomes: list[dict]) -> bool:
    observed = {(s["principal"], s["mode"]): s for s in outcomes}
    if len(observed) != 4:
        return False
    for mode in ("baseline", "hardened"):
        alice = observed.get(("alice", mode), {})
        if not (alice.get("http_status") == 200
                and alice.get("datastore_reads") == 1
                and alice.get("verification") == "verified"):
            return False
    baseline = observed.get(("bob", "baseline"), {})
    hardened = observed.get(("bob", "hardened"), {})
    return (baseline.get("http_status") == 200
            and baseline.get("datastore_reads") == 1
            and baseline.get("verification") == "verified"
            and hardened.get("http_status") == 403
            and hardened.get("verification") == "unverified"
            and hardened.get("error_code") == "ACCESS_DENIED"
            and hardened.get("returned_deployment") is None
            and hardened.get("datastore_reads") == 0)


def fetch_ollama(payload: dict, url: str, timeout: float) -> dict:
    req = Request(url, data=json.dumps(payload).encode("utf-8"),
                  headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=timeout) as response:
        return json.load(response)


def run_live(model: str, url: str, max_predict: int, timeout: float = 120) -> dict:
    started = time.monotonic()
    payload = build_payload(model=model, max_predict=max_predict, think=False)
    output = fetch_ollama(payload, url=url, timeout=timeout)
    elapsed = time.monotonic() - started
    call = parse_exactly_one_tool_call(output)
    outcomes = evaluate_call(call)
    return {
        "experiment": "F01-live-LLM-tool-boundary",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "model_call_source": "real_ollama_api_chat",
        "model": output.get("model"),
        "done_reason": output.get("done_reason"),
        "prompt_eval_count": output.get("prompt_eval_count"),
        "eval_count": output.get("eval_count"),
        "model_request_elapsed_seconds": round(elapsed, 3),
        "model_proposed_tool_call": call,
        "tool_executed": True,
        "synthetic_records_only": True,
        "same_model_tool_call_for_all_scenarios": True,
        "scenarios": outcomes,
        "f01_acceptance_passed": verify_f01(outcomes),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--url", default=URL)
    parser.add_argument("--max-predict", type=int, default=1024)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--report", type=Path, help="Optional path to save JSON evidence")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    try:
        evidence = run_live(args.model, args.url, args.max_predict, args.timeout)
    except (ToolRequestError, ValueError, HTTPError, URLError, TimeoutError,
            OSError, json.JSONDecodeError) as exc:
        print(f"NOT PASSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Evidence saved to: {args.report}")
    if evidence["f01_acceptance_passed"]:
        print("PASSED: a real LLM tool call was executed against both boundaries (synthetic data).")
        return 0
    print("NOT PASSED: live output did not satisfy all F01 acceptance checks.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
