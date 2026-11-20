# F03 — Bounded read-only retries

**Experiment version:** v0.5. **Live on local test workstation:** pending. **Scope:** in-process asynchronous synthetic dependency. No exposed network API or production credentials.

## Question

When a model makes the *correct* read-only tool request, how many times should an AI orchestrator repeat it after 429/503? A tool retry is a platform decision, not something the LLM should improvise.

## Controlled variables

The exact `message.tool_calls` object returned by Ollama `/api/chat` must specify `get_deployment_status(tenant_alpha, payments-api, staging)`; otherwise the experiment fails. The SAME tool request is used in all ten scenarios; principal = synthetic Alice; identity and tenant authorization are identical across baseline and hardened paths. Only the retry policy differs.

- Baseline: immediate retry of any synthetic error, **up to 4 attempts** (intentionally poor strategy; still safely capped).
- Hardened: maximum 2 attempts; retry only 429/503; fixed 15 ms backoff when no Retry-After; 70 ms attempt timeout; 150 ms **tool operation** deadline that is never reset. `Retry-After: 0.3s` does not fit the remaining budget and is not attempted.
- All dataset records are synthetic; attempts/reads are in-memory counters.

## Acceptance criteria

1. Healthy request: both implementations return `200`, verified and one read.
2. One 503 followed by success: both return `200`, verified with 2 attempts.
3. Three consecutive 503s followed by success: baseline retries four times and recovers; hardened makes only 2 attempts, returns `503`, `RETRY_BUDGET_EXHAUSTED`, `verification=unverified`, and **does not read** data.
4. A 403 followed by hypothetical success: baseline needlessly retries; hardened returns 403 after one attempt.
5. A synthetic 429 with 300 ms Retry-After: baseline ignores it and retries; hardened does not retry because its 150 ms operation budget is insufficient. Hardened returns `RETRY_DELAY_EXCEEDS_DEADLINE`, `verification=unverified` and zero reads.
6. No unauthorized/malformed model tool arguments may invoke the synthetic tool.
7. No errors may generate a verified deployment state.

## Limits and careful interpretation

This is **not** a real throttling, outage or concurrency test. The dependency is synthetic, not an HTTP service. The global timer in F03 starts **after** Ollama returns its tool request, and thus does not cover the model's inference time. Sleeps/cancellation occur cooperatively inside one Python process, not across a network. Deterministic fixed backoff is for test repeatability, not a recommendation to omit jitter at scale. A read-only API must still be verified side-effect-free before treating it as safe to retry. No billing, token cost, or GPU saturation is measured.

A bounded retry policy may deliberately return an error while an aggressive retry recovers eventually. The benefit being tested is controlling retries and their side effects on shared capacity, **not** increasing every request's success rate.
