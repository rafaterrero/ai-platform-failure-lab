# Publication checklist (maintainer)

- [ ] `python3 -m unittest discover -s tests -v` passes on local test workstation and in CI.
- [ ] Preserve **actual original** `reports/f01-live.json`, `f02-live.json`, `f03-live.json`, `f04-f05-live.json` from local test workstation. These are local artifacts, not bundled in the RC.
- [ ] Inspect reports for identities, secrets, private prompts or real infrastructure data before publication.
- [ ] Decide whether to publish sanitized reports directly or archive them with SHA-256 hashes and a short provenance note.
- [ ] Ensure README says **in-process result**, not HTTP response.
- [ ] Ensure every claim distinguishes actual model tool calls from replayed synthetic proposals and synthetic dependency faults.
- [ ] Run `git diff --check`, secret scan, and review dependencies and license before first public push.
- [ ] Confirm Apache-2.0 is the intended public repository license (LICENSE is staged locally).
- [ ] Never claim production deployment, hallucination prevention, enterprise-scale isolation or benchmarked reliability.
- [ ] Final LinkedIn claim should refer to the evidence boundary actually tested: same valid LLM proposal, different enforcement policy.
