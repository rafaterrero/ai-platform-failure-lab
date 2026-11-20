# RFC-002 — Failure contracts v0.1

**Status (v0.5):** F01 authorization and F02 cooperative synthetic tool-timeout tested with a real Qwen3 tool proposal on local test workstation on 2026-11-19. The F03 *experiment* adds synthetic read-only retry controls, locally tested but not yet live validated on local test workstation. The original matrix below uses internal trigger IDs (F05 = tool timeout), while the lab experiment milestone is named **F02**. All numbers are initial parameters, not measured production SLOs. F02 uses an 80 ms timeout and 300 ms synthetic delay; F03 uses a separate 150 ms **tool-operation** deadline, not an end-to-end application deadline.

## Two test profiles

| Setting | Deterministic CI (fixture) | Live local test workstation (Ollama, initial) |
|---|---:|---:|
| Overall request deadline | 1,500 ms | 30,000 ms |
| Model call timeout | 750 ms | 20,000 ms |
| Tool call timeout | 250 ms | 2,000 ms |
| Model calls per request | 2 | 2 |
| Tool executions per request | 1 successful invocation; at most 1 retry of same read-only call | Same |
| Retries on transient model/tool failures | Max 1, if deadline permits | Max 1, if deadline permits |
| Max generated tokens per model call | 256, using model generation cap | 256, using model generation cap |
| Input size | 4 KiB bytes, not equivalent to 4K tokens | Same |

`MAX_MODEL_CALLS=2` allows an initial tool request and an optional grounded explanation. `MAX_TOOL_CALLS=1` counts *logical* tool invocation; one retry of the same read-only invocation is separately capped and counted as an attempt.

For strict cost accounting, use provider-reported token usage where available. Model-level `num_predict` limits output per call; **do not claim a hard total-token cap before tokenization**. Local Ollama is not billed by tokens; tokens and elapsed GPU time are consumption proxies, not actual € charges.

## Failure / enforcement matrix

| ID | Trigger | Required behavior | HTTP | Stable error code | Evidence |
|---|---|---|---:|---|---|
| F01 | No/malformed/expired signed test token | Reject before model/tool invocation | 401 | `UNAUTHENTICATED` | `model_calls=0`, `tool_reads=0` |
| F02 | Authenticated tenant asks for another tenant's service | Reject before datastore read; generic error; no protected data | 403 | `ACCESS_DENIED` | `tool_reads=0`, authorization span denied |
| F03 | Model outputs unknown tool/invalid arguments | Do not execute; treat as unverified model action | 502 | `INVALID_MODEL_ACTION` | `tool_executions=0`, schema error trace |
| F04 | Tool returns 503 | Retry at most once if read-only and time remains; otherwise fail without generating deployment state | 503 | `TOOL_UNAVAILABLE` | attempts <= 2, no answer |
| F05 | Tool exceeds timeout | Stop waiting/cancel cooperatively; fail without status; do not claim downstream physical termination | 504 | `TOOL_TIMEOUT` | elapsed bounded within test tolerance, timeout span |
| F06 | Model returns 429 (injected at adapter) | Respect bounded retry policy, optional Retry-After only if deadline permits; otherwise explicit failure | 503 | `MODEL_THROTTLED` | attempts <= 2, retry decision recorded |
| F07 | Model times out | Fail; no tools run | 504 | `MODEL_TIMEOUT` | no tool exec, span timeout |
| F08 | Model repeatedly requests tools after tool budget spent | Refuse further calls and mark unverified | 429 | `EXECUTION_BUDGET_EXCEEDED` | tool calls bounded |
| F09 | Missing or incomplete tool evidence | Do not invent deployment state or return 200 verified | 503 | `EVIDENCE_UNAVAILABLE` | deployment null |
| F10 | Known good lookup for authorized principal | Return data-backed status only | 200 | — | exact expected synthetic record |

## Retry contract

The F03 experiment is detailed in `F03-retry-experiment.md`. It does not yet implement model-adapter retries or a deadline covering the Ollama request.

- Eligible: model adapter transient 429/503 and **read-only** tool transient 503, at most one retry, subject to remaining **overall** deadline.
- Not eligible: 401, 403, bad arguments, unknown tool names, missing evidence, and any future side-effecting tool without proven idempotency.
- Suggested backoff in live mode: 100–250 ms with jitter; deterministic seed or disabled jitter in CI. A `Retry-After` larger than the remaining budget means **do not retry**.
- No retry is permitted to reset the overall monotonic deadline.

## Verifiability contract

- `verification=verified` only when authorized, complete tool evidence was obtained and linked to the response.
- On errors: `verification=unverified`, `deployment=null`, `explanation=null` and a machine-readable code. No free-form LLM status explanation generated from a failed tool call.
- Fields describing service, environment, deployment status, revision and reason come from the trusted tool only.
- Live model summaries are subject to evaluation; the fact that evidence is attached does not make all natural-language interpretation guaranteed correct.

## Telemetry contract

Trace: `POST /v1/ask` → `model.chat` → `tool.validate` → `tool.authorize` → `tool.execute` → `response.build`.

Include `request_id`, operation, dependency, duration_ms, attempt_number, outcome, budget_remaining_ms, model name, token usage when supplied. **Default to no input/output prompt logging, no raw tool results, no bearer tokens, no JWTs, and no private data**. Pin instrumentation versions; OpenTelemetry GenAI semantic conventions are under active development.

Timeouts are a best-effort application boundary, not proof of guaranteed wall-clock termination or rollback of external effects. Tests must explicitly document their measured timing tolerance and whether the tool is cooperative with cancellation.
