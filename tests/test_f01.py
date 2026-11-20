"""Deterministic acceptance tests: run with python3 -m unittest discover -v."""
import base64
import json
import time
import unittest
from llm_failure_lab.core import (
    DEMO_KEY, LabIdentity, DeploymentStore, Executor, tool_call,
)


class F01AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.identity = LabIdentity(DEMO_KEY)
        self.store = DeploymentStore()
        self.executor = Executor(self.identity, self.store)
        self.alice = self.identity.issue("alice", "tenant_alpha")
        self.bob = self.identity.issue("bob", "tenant_beta")
        self.alice_tool = tool_call("tenant_alpha", "payments-api", "staging")

    def test_a01_authorized(self):
        result = self.executor.execute(token=self.alice, tool_call=self.alice_tool)
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["verification"], "verified")
        self.assertEqual(result["deployment"]["reason"], "READINESS_PROBE_TIMEOUT")
        self.assertEqual(self.store.reads, 1)

    def test_a02_denied_before_datastore_read(self):
        result = self.executor.execute(token=self.bob, tool_call=self.alice_tool)
        self.assertEqual(result["http_status"], 403)
        self.assertIsNone(result["deployment"])
        self.assertEqual(self.store.reads, 0)
        self.assertEqual(self.executor.audit[-1]["decision"], "denied")

    def test_a02_naive_baseline_exposes_synthetic_cross_tenant_data(self):
        result = self.executor.execute(token=self.bob,
                                       tool_call=self.alice_tool, mode="baseline")
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["verification"], "verified")
        self.assertEqual(self.store.reads, 1)
        self.assertEqual(result["deployment"]["service"], "payments-api")

    def test_a03_untrusted_header_cannot_change_tenant(self):
        result = self.executor.execute(token=self.alice,
                                       tool_call=tool_call("tenant_beta", "orders-api", "production"),
                                       untrusted_headers={"X-Tenant-ID": "tenant_beta"})
        self.assertEqual(result["http_status"], 403)
        self.assertEqual(self.store.reads, 0)

    def test_a04_unknown_tool_rejected_before_read(self):
        result = self.executor.execute(token=self.alice,
                                       tool_call={"name": "delete_deployment", "arguments": {}})
        self.assertEqual(result["http_status"], 502)
        self.assertEqual(result["error"]["code"], "INVALID_TOOL_CALL")
        self.assertEqual(self.store.reads, 0)

    def test_a11_missing_token_rejected(self):
        result = self.executor.execute(token=None, tool_call=self.alice_tool)
        self.assertEqual(result["http_status"], 401)
        self.assertEqual(self.store.reads, 0)

    def test_a11_expired_token_rejected(self):
        expired = self.identity.issue("alice", "tenant_alpha", ttl_seconds=-1)
        result = self.executor.execute(token=expired, tool_call=self.alice_tool)
        self.assertEqual(result["http_status"], 401)
        self.assertEqual(self.store.reads, 0)

    def test_a11_modified_token_rejected(self):
        payload, signature = self.alice.split(".")
        raw = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        raw["tenant"] = "tenant_beta"
        replacement = base64.urlsafe_b64encode(json.dumps(raw).encode()).rstrip(b"=").decode()
        result = self.executor.execute(token=f"{replacement}.{signature}", tool_call=self.alice_tool)
        self.assertEqual(result["http_status"], 401)
        self.assertEqual(self.store.reads, 0)

    def test_wrong_argument_shape_rejected(self):
        call = {"name": "get_deployment_status", "arguments": {
            "tenant_id": "tenant_alpha", "service": "payments-api", "environment": "staging",
            "cmd": "do-not-execute"}}
        result = self.executor.execute(token=self.alice, tool_call=call)
        self.assertEqual(result["http_status"], 502)
        self.assertEqual(self.store.reads, 0)

    def test_storage_defense_in_depth(self):
        from llm_failure_lab.core import Principal, AuthorizationError
        with self.assertRaises(AuthorizationError):
            self.store.read_scoped(Principal("bob", "tenant_beta"),
                                   "tenant_alpha", "payments-api", "staging")
        self.assertEqual(self.store.reads, 0)


if __name__ == "__main__":
    unittest.main()
