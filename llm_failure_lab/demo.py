"""Run the same fixture model-proposed tool call against two execution paths."""
import json
from .core import DEMO_KEY, LabIdentity, DeploymentStore, Executor, tool_call


def main():
    identity = LabIdentity(DEMO_KEY)
    bob = identity.issue("bob", "tenant_beta")
    alice = identity.issue("alice", "tenant_alpha")
    examples = [
        ("authorized: Alice -> Alice", alice,
         tool_call("tenant_alpha", "payments-api", "staging")),
        ("cross-tenant: Bob -> Alice", bob,
         tool_call("tenant_alpha", "payments-api", "staging")),
    ]
    print("EXPERIMENT F01 — SYNTHETIC DATA, FIXTURE TOOL CALL (NOT LIVE LLM)")
    for name, token, call in examples:
        print("\n" + name)
        for mode in ("baseline", "hardened"):
            store = DeploymentStore()
            exe = Executor(identity, store)
            outcome = exe.execute(token=token, tool_call=call, mode=mode)
            print(json.dumps({"mode": mode, "http_status": outcome["http_status"],
                              "verified": outcome["verification"],
                              "datastore_reads": store.reads,
                              "deployment": outcome["deployment"], "error": outcome["error"]},
                             ensure_ascii=False, sort_keys=True))
    print("\nThis is an intentionally vulnerable baseline on synthetic records, not a live LLM result.")


if __name__ == "__main__":
    main()
