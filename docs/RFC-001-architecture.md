# RFC-001 — Architecture and trust boundaries

**Status:** Implemented as an in-process Python lab (v0.7-rc1, 19 Nov 2026); network service architecture below is **future scope**.

## Architecture as implemented

```text
CLI runner ---> Ollama /api/chat (qwen3:4b; one live model proposal)
     |                 |
     |          structured tool_calls
     |                 |
     +-----> exact tool-call parser + expected argument validation
                          |
                   synthetic scenarios
                          |
             +------------+-------------+
             |                          |
     deliberately naive            guarded executor
       application path           (authz, deadlines,
             |                   retries, budgets,
             |                  evidence validation)
             |                          |
             +-- synthetic in-memory datastore / fault fixtures
                          |
                 in-process result records
                          |
                     JSON reports
```

The intentionally naive path has **different scoped deficiencies** in each experiment. It is not a single deployable application, and this repo does not start an HTTP server. Run unit tests without GPU; live tests need a local Ollama server.

## Trust boundary invariants

1. An authenticated *lab identity fixture* binds the principal to its allowed tenant.
2. A model-proposed tenant identifier is untrusted. Guarded F01 checks it against the verified principal, then scopes storage again.
3. Only an allowlisted tool with exact typed arguments may be executed. Text inside `message.content` is never treated as an executable call.
4. The guard on F02 bounds **cooperative** tool waiting; it does not prove cancellation of external work.
5. F03 limits **read-only synthetic tool** retries, not the end-to-end LLM request.
6. F04 limits tool dispatch submissions, not actual agent iterations or token expenses.
7. F05 rejects malformed/inconsistent synthetic evidence; it does not guarantee the truth or freshness of the underlying observation, or validate an LLM-generated final answer.

## Next architecture (unimplemented; do not imply otherwise)

A future genuine service would require an HTTP API (e.g., FastAPI), production identity/IAM, an orchestrator with a global request deadline, a telemetry exporter, policy-enforced tool services, and deployment packaging. **None is needed to reproduce the published findings here.**

The current lab demonstrates specific application boundaries that would need to be present in such a service, not a full operational AI platform.
