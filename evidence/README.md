# Evidence from local test workstation

These four JSON reports are the **original files** from the operator-run
`bash scripts/check.sh --live` on 19 November 2026, 21:10 UTC. Each report's
SHA-256 is recorded in `SHA256SUMS` and was verified before being copied.

Environment: Ubuntu 24.04.5 LTS, RTX 5080, Ollama 0.17.6, `qwen3:4b`.
The model supplied an actual structured tool proposal; the dataset, tenant
identities, injected faults and repeated tool proposals are synthetic.
`http_status` fields are in-process results, **not network HTTP responses**.
No autonomous multi-turn agent, production tenancy or real provider fault
was exercised. See [`docs/RESULTS.md`](../docs/RESULTS.md).

Verify file integrity from this directory:

```bash
sha256sum -c SHA256SUMS
```

The SHA-256 hashes identify the uploaded reports; they do not independently
prove the correctness of the test implementation or the security of a real
production service. Review all JSON before public release.
