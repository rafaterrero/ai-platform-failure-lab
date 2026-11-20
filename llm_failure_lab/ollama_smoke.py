"""Ollama /api/chat tool calling diagnostics (no tool execution).

Does not use free-form text as proof of a tool call. Intended for local synthetic
lab data only. Pass --max-predict N if Qwen exhausts its generation budget.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MODEL = os.getenv("LAB_OLLAMA_MODEL", "qwen3:4b")
URL = os.getenv("LAB_OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
TOOL_NAME = "get_deployment_status"


def build_payload(model: str, max_predict: int, think: bool) -> dict:
    if max_predict < 32 or max_predict > 16384:
        raise ValueError("max_predict must be between 32 and 16384")
    return {
        "model": model,
        "stream": False,
        "think": think,
        "keep_alive": "5m",
        "options": {"temperature": 0, "num_predict": max_predict},
        "messages": [
            {"role": "system", "content": (
                "You are an operations assistant. To answer a deployment status question, "
                "use the get_deployment_status tool. Do not guess the status. "
                "Do not provide a status before obtaining a tool result.")},
            {"role": "user", "content": (
                "Check the deployment status for payments-api in staging for tenant_alpha. "
                "Call get_deployment_status now.")},
        ],
        "tools": [{"type": "function", "function": {
            "name": TOOL_NAME,
            "description": "Read synthetic deployment status. Does not grant authorization.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tenant_id": {"type": "string", "description": "Tenant from the request"},
                    "service": {"type": "string", "description": "Deployment service name"},
                    "environment": {"type": "string", "description": "Deployment environment"},
                },
                "required": ["tenant_id", "service", "environment"],
                "additionalProperties": False,
            },
        }}],
    }


def inspect_output(output: dict, max_predict: int) -> tuple[dict, bool]:
    """Return safe-to-print diagnostics and pass/fail. No tool execution."""
    message = output.get("message") or {}
    calls = message.get("tool_calls") or []
    if not isinstance(calls, list):
        calls = []
    valid = any(
        isinstance(call, dict)
        and isinstance(call.get("function"), dict)
        and call["function"].get("name") == TOOL_NAME
        and isinstance(call["function"].get("arguments"), dict)
        for call in calls
    )
    content = message.get("content") or ""
    thinking = message.get("thinking") or ""
    eval_count = output.get("eval_count")
    result = {
        "model": output.get("model"),
        "done": output.get("done"),
        "done_reason": output.get("done_reason"),
        "load_duration_seconds": round(output.get("load_duration", 0) / 1_000_000_000, 3),
        "prompt_eval_count": output.get("prompt_eval_count"),
        "eval_count": eval_count,
        "max_predict": max_predict,
        "generation_budget_reached": isinstance(eval_count, int) and eval_count >= max_predict,
        "message_fields": sorted(message),
        "content_excerpt": content[:800],
        "thinking_excerpt": thinking[:800],
        "tool_calls": calls,
        "structured_tool_call_detected": valid,
        "tool_execution_performed": False,
    }
    return result, valid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--url", default=URL)
    parser.add_argument("--max-predict", type=int, default=1024)
    parser.add_argument("--think", action="store_true", help="Enable reasoning for comparative diagnosis")
    args = parser.parse_args(argv)
    try:
        payload = build_payload(args.model, args.max_predict, args.think)
    except ValueError as exc:
        parser.error(str(exc))

    req = Request(args.url, data=json.dumps(payload).encode("utf-8"),
                  headers={"Content-Type": "application/json"}, method="POST")
    started = time.monotonic()
    try:
        with urlopen(req, timeout=180) as response:
            output = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f"LIVE INTEGRATION FAILED: {exc}", file=sys.stderr)
        return 2

    diagnostic, valid = inspect_output(output, args.max_predict)
    diagnostic["elapsed_seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(diagnostic, indent=2, ensure_ascii=False))
    if not valid:
        if diagnostic["generation_budget_reached"]:
            print("NOT PASSED: generation hit num_predict; output might be truncated.", file=sys.stderr)
        else:
            print("NOT PASSED: no valid structured tool call. Check content/thinking and Ollama version/template.", file=sys.stderr)
        return 1
    print("PASSED: real model returned a structured tool request (not executed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
