# F04 & F05 — Tool Execution Budget / Evidence Integrity

Status: **validated on local test workstation on 2026-11-19** using one real Qwen3 tool proposal plus synthetic execution fixtures. Original report remains on local test workstation; see `RESULTS.md`.

## Research question

Does the application impose hard constraints on **tool dispatch** and **result
verification**, regardless of what the LLM proposes or how a tool replies?

## F04: one LLM proposal, multiple synthetic submissions

1. Query Qwen3 4B through Ollama `/api/chat`, expecting exactly one structured
   `get_deployment_status(tenant_alpha, payments-api, staging)` proposal.
2. Reject malformed, extra, missing or unexpected model calls. Never auto-rewrite
   the model's arguments.
3. Replay the exact same proposed call **three times in the local test runner**.
4. Baseline: an intentionally missing quota means three dispatches and three
   synthetic datastore reads.
5. Hardened: tool budget **1** means one dispatch, one read, and two rejected
   proposals (`429` in-process result with `TOOL_CALL_BUDGET_EXCEEDED`).
6. Both modes enforce identity and tenant authorization in the underlying
   `Executor`; only the quota changes in F04.

What this **does not** demonstrate: spontaneous repeated proposals by a real
multi-turn agent, LLM call budgets, model token budgets or overall deadlines.
Those would require implementing and measuring a multi-turn orchestration loop.

## F05: tool results are not intrinsically trustworthy

Inject one of four synthetic tool payloads into the tool-result boundary:

| Payload | Baseline | Hardened |
|---|---|---|
| Complete | 200/verified | 200/verified |
| `reason` omitted | 200/verified (bad) | 502/unverified, no deployment |
| Service name does not match request | 200/verified (bad) | 502/unverified, no deployment |
| Unknown deployment status | 200/verified (bad) | 502/unverified, no deployment |

Both implementations authenticate the same fixture identity and check tenant
access before reading the synthetic response. Hardened also checks required
nonempty fields, response/request service and environment, recognized status,
and timestamp **format** with timezone. It does not verify result provenance,
contents against an independent source, or the freshness of observations.

No LLM-generated final answer is produced, so do **not** claim that this proves
hallucination prevention. It proves a specific failure at the *verification
contract boundary*: malformed evidence is never labelled as verified in the
hardened path. A future final-answer test must separately ensure that the
model's narrative cannot override the structured verification state.

## Reproducibility and reporting

- Python standard library; no external package installation.
- 75 deterministic local unit tests in v0.6 (52 prior + 23 added).
- One real model call only in `live_f04_f05`, with the exact structured proposal
  reported. The repeated submissions and tool responses are local synthetic tests.
- `reports/f04-f05-live.json` (generated in local test workstation) records exact counts and
  mode comparisons. The script exits nonzero if any acceptance condition fails.
- The in-process `http_status` field is not an actual network response.

## Next boundary after F04/F05

A realistic end-to-end incident workflow needs a full request budget spanning
model calls **and** tool calls, and a final-answer validator or deterministic
response renderer that carries `verification=unverified` to the client without
unwarranted factual claims. That is **not implemented in v0.6**.
