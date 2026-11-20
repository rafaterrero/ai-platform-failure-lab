# LLM Platform Failure Lab

**GitHub-ready candidate based on v0.7-rc1 — validated on local test workstation, 19 November 2026.** A reproducible engineering experiment contrasting an intentionally naive tool executor with explicit application-enforced controls. **This is not a production AI platform and contains no commercial LLM deployment claims.**

> A correct LLM tool call does not imply authorized, bounded, or verified execution.

## What actually runs

1. `qwen3:4b` via Ollama `/api/chat` (optional for the live runs) emits **one real structured** `get_deployment_status(tenant_alpha, payments-api, staging)` proposal.
2. The parser accepts exactly one valid structured `message.tool_calls` proposal, **not** free-text output.
3. A **local Python runner** submits the same proposal to deliberately naive and guarded execution paths against **synthetic data and fault fixtures**.
4. Each path produces an **in-process** result record (`http_status` is a field, not the response from a running HTTP server).
5. The test runner compares documented operational invariants; it does **not** prove production security or reliability.

**Live status:** F01, F02, F03, F04 and F05 were run on the user's local test workstation workstation on 19 November 2026 with Ollama 0.17.6 / Qwen3 4B, and their acceptance flags were reported `true`. **The original local test workstation report JSON files are included in [evidence/](evidence/README.md), with verified SHA-256 hashes.** See [results and evidence provenance](docs/RESULTS.md).

## At a glance

| Experiment | Injected condition | Naive baseline | Guarded execution | Evidence limitation |
|---|---|---|---|---|
| F01: authorization | Bob executes a valid call targeted at Alice's tenant | Returns synthetic record, 1 read | Denies access, 0 reads | No adversarial prompt injection, no real tenant data |
| F02: deadline | 300 ms synthetic async tool, 80 ms timeout | Accepts result after ~300 ms | Returns unverified timeout after ~80 ms | Cooperative coroutine cancellation only |
| F03: retry policy | 3 synthetic 503 errors before recovery | Succeeds on attempt 4 | Stops after 2 attempts | Not a load test or actual provider throttling |
| F04: tool quota | Submit same real proposal 3 times | 3 tool dispatches | 1 dispatch, 2 rejections | Subsequent proposals are **synthetic replays** |
| F05: evidence gate | Missing reason, mismatched service, invalid status | Incorrectly marks data verified | Rejects malformed/inconsistent data | No LLM-generated final answer, no freshness guarantee |

## Requirements

- Python 3.10+ for deterministic tests (standard library; **no pip install needed**).
- Optional: local Ollama API, accessible at `http://127.0.0.1:11434`, with `qwen3:4b` for live tool proposal tests.
- No GPU required for unit tests. GPU required only if your local Ollama configuration uses it.

### CPU-only, deterministic tests

```bash
python3 -m unittest discover -s tests -v
```

### Live LLM experiments (optional)

```bash
python3 -m llm_failure_lab.ollama_smoke
python3 -m llm_failure_lab.live_f01 --report reports/f01-live.json
python3 -m llm_failure_lab.live_f02 --report reports/f02-live.json
python3 -m llm_failure_lab.live_f03 --report reports/f03-live.json
python3 -m llm_failure_lab.live_f04_f05 --report reports/f04-f05-live.json
```

Every live run makes a model request and then performs local synthetic scenarios. **It does not call an actual Kubernetes deployment API or replay actual production failures.** The live scripts fail rather than silently replacing unexpected LLM arguments.

`reports/` is gitignored. Inspect reports for sensitive content before sharing. The shipped code uses only synthetic fixture records and no real deployment credentials.

Run all checks with:

```bash
bash scripts/check.sh         # deterministic tests only
bash scripts/check.sh --live  # also runs live Ollama experiments and writes reports/
```

## Architecture and methodology

```text
Ollama/Qwen3 --(structured tool proposal)--> strict parser
                                               |
                                     locally injected test scenarios
                                               |
                          +--------------------+------------------+
                          |                                       |
                   Naive executor                          Guarded executor
                          |                                       |
             Synthetic records/failures             Synthetic records/failures
                          |                                       |
                          +----------> Result / metrics <-------+
```

- [Architecture: current vs. proposed](docs/RFC-001-architecture.md)
- [Failure contracts](docs/RFC-002-failure-contracts.md)
- [Test acceptance matrix](docs/TEST-001-acceptance.md)
- [F03: retries](docs/F03-retry-experiment.md)
- [F04/F05: quota and evidence validation](docs/F04-F05-budget-and-evidence.md)
- [Results, provenance and limitations](docs/RESULTS.md)
- [Security boundaries](SECURITY.md)

## What this does NOT implement

No HTTP/FastAPI service, actual OpenTelemetry spans, Jaeger dashboard, MCP, Bedrock, Kubernetes deployment, end-to-end latency SLO, real tenant isolation, production authn, concurrent load test, rate-control optimization, token/monetary budget enforcement, autonomous multi-turn agent, or verified final natural-language answer. An `http_status=403` record is **not an observed network HTTP 403**.

All intentionally insecure baseline behavior is **local and synthetic**. Do not expose it as a public service, connect it to live systems, or copy its fixed demonstration signing key into an application.

## Repository publication

Before publishing: review the original reports in `evidence/`, run deterministic tests, verify the evidence checksums, inspect all staged files for secrets, and confirm that Apache-2.0 is your intended license. CI must pass on GitHub before tagging a release. Treat reported findings as controlled lab observations, not enterprise-scale guarantees.

## License

Apache License, Version 2.0. See [LICENSE](LICENSE).
