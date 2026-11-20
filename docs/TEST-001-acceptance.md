# TEST-001 — Acceptance criteria and evidence plan

**Status:** Planned tests, **zero observed results**. Do not publish a green status before running the implementation.

## Test data

- Signed test principal `alice` -> `tenant_alpha` -> authorized `payments-api` in `staging`; synthetic `FAILED / READINESS_PROBE_TIMEOUT`.
- Signed test principal `bob` -> `tenant_beta` -> authorized `orders-api` in `production`; synthetic `HEALTHY`.
- `payments-api` does not belong to `bob`; `orders-api` does not belong to `alice`.
- No real company systems, identities or credentials.

## Deterministic acceptance matrix (Hardened)

| Test | Stimulus / fault injection | Required assertion |
|---|---|---|
| A01 | Alice queries her payments deployment | 200; `verification=verified`; exact fixture status; 1 authorized logical tool call |
| A02 | Bob asks for Alice's payments deployment with model returning the corresponding tool call | 403; `deployment=null`; no datastore lookup; generic denial |
| A03 | Alice-signed request includes forged `X-Tenant-ID: tenant_beta` | Header ignored; request never gets Beta data |
| A04 | Model proposes a nonexistent `delete_deployment` tool | 502 invalid model action; no tool executes |
| A05 | Tool adapter raises transient 503 twice | 503; `deployment=null`; at most 2 attempts; no generated status |
| A06 | Tool sleeps beyond 250-ms tool timeout | 504; `deployment=null`; cancellation/timeout event present; tolerance documented |
| A07 | Model adapter responds 429 twice | 503 MODEL_THROTTLED; <=2 model attempts; no tool execution |
| A08 | Model adapter stalls beyond 750 ms | 504 MODEL_TIMEOUT; no tool execution |
| A09 | Model asks again for tool after logical tool budget exhausted | 429 EXECUTION_BUDGET_EXCEEDED; exactly 1 logical tool invocation maximum |
| A10 | Tool returns incomplete record without status or reason | Not verified; `deployment=null`; no invented state |
| A11 | Auth token missing/expired/malformed | 401 and zero model/tool calls |
| A12 | Observe one successful and one failed request | Correlated traces with model, authz, tool, final error; no raw prompts/tokens or data |

In timing tests on shared CI, assert **correct timeout decision and attempt count**, and record actual duration. Use generous tolerance rather than claiming a hard real-time deadline from Python cancellation.

## Contrasting the baseline

Re-run matching deterministic stimuli against the deliberately naive mode. Record **observed** differences in a single report; do not claim the LLM was successfully prompt-injected unless a live run actually shows that. The primary controlled comparison is authorization and failure-handling **at the application boundary**.

## Live local test workstation smoke test

- Verify Ollama service and GPU availability first.
- Use `qwen3:4b` and capture exact Ollama version, image/model digest or tag, driver, GPU, Python and Docker Compose versions.
- Execute the happy path 3 times. Each run must show a real `tool_calls` response from Ollama and a matching authorized tool execution for a successful live demo.
- Run one forced unavailable-tool failure through the same orchestration and verify unverified response + trace.
- Record load time, end-to-end latency and reported input/output tokens. These are **workstation measurements**, not production SLOs.
- If the model fails to use the tool, report failure honestly; do not silently swap in fixture output and label it live.

## Definition of Done for GitHub/DEV.to claims

**Required**: deterministic tests green; expected baseline/hardened comparison captured; at least one live tool-calling trace; clean public dataset; checked-in dependency versions; repeatable instructions; no private prompt contents or secrets in screenshots; limitations and environment documented.

**Not claimed**: full AI platform in production, tenant isolation under a hostile multihost deployment, exact € cost control, guaranteed cancellation of remote work, general LLM hallucination elimination, latency SLO, production incident experience.

## Draft publication claim (only if supported by tests)

> I used the same LLM, tool and synthetic deployment dataset in two implementations. A deterministic model fixture requested the same tool call; one execution path relied on the tool call while the other enforced identity-scoped authorization and bounded failure handling. The contrast was in the application guarantees, not the prompt.

Replace this with observed measurements before publishing.


## F02 lab milestone (implemented in v0.4; live local test workstation run still required)

Run: `python3 -m llm_failure_lab.live_f02 --report reports/f02-live.json`

Uses same real structured LLM call for both baseline/hardened, healthy/delayed dependency.
Acceptance: healthy cases 200 verified, 1 read; delayed baseline 200 after synthetic delay;
delayed hardened 504 `TOOL_TIMEOUT`, `verification=unverified`, `deployment=null`,
zero completed reads and one cooperative cancellation. Both modes authorize identically.
The configured timeout is not a proven hard realtime bound, and cancellation of an async
simulator is **not** proof of cancellation of external downstream operations.
