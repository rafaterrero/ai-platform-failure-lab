## GitHub preparation — 2026-11-20

- Staged original local test workstation reports in `evidence/`, preserving checksums; retained `reports/` as untracked local output.
- Added Apache-2.0 LICENSE and evidence provenance documentation. No changes to experiment logic.
- No remote repository, release tag, or publication yet.

# Changelog

## v0.7-rc1 — 2026-11-19

- Documentation-only consolidation of the v0.6 application and experiment modules (no changes to baseline/hardened behavioral code).
- Corrected stale v0.5 status in README; recorded user-reported local test workstation results for F01–F05, clearly distinguishing one live LLM proposal from synthetic replay and faults.
- Explicitly documented that all `http_status` values are in-process records, not network HTTP responses.
- Added `SECURITY.md`, `docs/RESULTS.md`, deterministic GitHub Actions test workflow, and `scripts/check.sh`.
- **Raw local test workstation JSON evidence is not bundled; obtain original reports from the workstation before public release.**

## v0.6 — 2026-11-19
- F04 tool-dispatch budget and F05 evidence-integrity experiments; 75 local deterministic tests reported.

## v0.5 — 2026-11-19
- F03 controlled retry and synthetic transient failure scenarios.

## v0.4 — 2026-11-19
- F02 synthetic cooperative async tool timeout scenarios.

## v0.3 — 2026-11-19
- F01 live LLM-proposed structured tool call and tenant-boundary replay.

## v0.2.1 — 2026-11-19
- Ollama tool-calling diagnostic improvements.
