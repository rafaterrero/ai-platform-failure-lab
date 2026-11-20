# Security and scope

This repo deliberately includes an **insecure local baseline** to illustrate failures against fake tenant data. Do not deploy or expose that baseline or connect it to real application data, infrastructure, cloud credentials or user accounts.

- The `DEMO_KEY` in `core.py` is **public lab fixture material**. It is not a secret and must never be reused outside this demonstration.
- Model output is untrusted; accept only the structured allowlisted tool proposal in these tests.
- Identity checks are simulated and are **not** production authentication.
- Error/status values are **in-process result records**, not an HTTP server.
- Synthetic faults are not proof of real provider/service failure handling.
- Do not log or publicly upload unreviewed LLM prompts, token credentials, private customer data or production incident details.

To report a security problem, contact the repository maintainer through an appropriate private channel rather than posting exploitable details publicly.
