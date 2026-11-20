"""F02 deterministic offline tests. No Ollama, no GPU, no external dependencies."""
import asyncio
import unittest
from unittest.mock import patch

from llm_failure_lab.core import DEMO_KEY, LabIdentity, tool_call
from llm_failure_lab.f02 import DeadlineExecutor, SyntheticAsyncTool, run_scenario
from llm_failure_lab.live_f02 import acceptance, evaluate, run_live
from llm_failure_lab.live_f01 import ToolRequestError

CALL = tool_call("tenant_alpha", "payments-api", "staging")


class F02ToolTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_healthy_hardened_verified(self):
        row = await run_scenario(CALL, LabIdentity(DEMO_KEY), mode="hardened",
                                 delay_seconds=.001, timeout_seconds=.08)
        self.assertEqual(row["http_status"], 200)
        self.assertEqual(row["verification"], "verified")
        self.assertEqual(row["tool_reads"], 1)

    async def test_timeout_returns_unverified_without_read(self):
        row = await run_scenario(CALL, LabIdentity(DEMO_KEY), mode="hardened",
                                 delay_seconds=.2, timeout_seconds=.03)
        self.assertEqual(row["http_status"], 504)
        self.assertEqual(row["verification"], "unverified")
        self.assertIsNone(row["returned_deployment"])
        self.assertEqual(row["tool_reads"], 0)
        self.assertEqual(row["tool_cancellations"], 1)
        self.assertEqual(row["tool_attempts"], 1)

    async def test_baseline_waits_for_slow_result(self):
        row = await run_scenario(CALL, LabIdentity(DEMO_KEY), mode="baseline",
                                 delay_seconds=.045, timeout_seconds=.01)
        self.assertEqual(row["http_status"], 200)
        self.assertGreaterEqual(row["elapsed_ms"], 35)
        self.assertEqual(row["tool_reads"], 1)

    async def test_other_tenant_rejected_before_tool_invocation(self):
        row = await run_scenario(CALL, LabIdentity(DEMO_KEY), mode="hardened",
                                 delay_seconds=.1, timeout_seconds=.03,
                                 principal_subject="bob", principal_tenant="tenant_beta")
        self.assertEqual(row["http_status"], 403)
        self.assertEqual(row["tool_attempts"], 0)
        self.assertEqual(row["tool_reads"], 0)

    async def test_tool_failure_returns_unverified(self):
        backend = SyntheticAsyncTool(failure=True)
        executor = DeadlineExecutor(LabIdentity(DEMO_KEY), backend)
        response = await executor.execute(token=executor.identity.issue("alice", "tenant_alpha"),
                                          tool_call=CALL)
        self.assertEqual(response["http_status"], 503)
        self.assertEqual(response["verification"], "unverified")
        self.assertIsNone(response["deployment"])
        self.assertEqual(backend.attempts, 1)

    async def test_incomplete_evidence_is_never_verified(self):
        backend = SyntheticAsyncTool(incomplete=True)
        executor = DeadlineExecutor(LabIdentity(DEMO_KEY), backend)
        response = await executor.execute(token=executor.identity.issue("alice", "tenant_alpha"),
                                          tool_call=CALL)
        self.assertEqual(response["http_status"], 502)
        self.assertEqual(response["verification"], "unverified")
        self.assertIsNone(response["deployment"])

    async def test_bad_tool_shape_fails_before_tool_call(self):
        backend = SyntheticAsyncTool()
        executor = DeadlineExecutor(LabIdentity(DEMO_KEY), backend)
        response = await executor.execute(token=executor.identity.issue("alice", "tenant_alpha"),
                                          tool_call={"name": "run_shell", "arguments": {}})
        self.assertEqual(response["http_status"], 502)
        self.assertEqual(backend.attempts, 0)

    async def test_four_scenario_comparison(self):
        scenarios = await evaluate(CALL, slow_seconds=.13, tool_timeout_seconds=.025)
        self.assertTrue(acceptance(scenarios, slow_seconds=.13, tool_timeout_seconds=.025))


class LiveF02WiringTests(unittest.TestCase):
    def test_live_bridge_does_not_replace_model_arguments(self):
        output = {"model": "qwen3:4b", "message": {"tool_calls": [{"function": {
            "name": "get_deployment_status", "arguments": {
                "tenant_id": "tenant_beta", "service": "orders-api", "environment": "production"}}}]}}
        with patch("llm_failure_lab.live_f02.fetch_ollama", return_value=output):
            with self.assertRaises(ToolRequestError):
                run_live("qwen3:4b", "http://localhost:11434/api/chat", 1024)

    def test_acceptance_rejects_missing_scenario(self):
        self.assertFalse(acceptance([], slow_seconds=.3, tool_timeout_seconds=.08))


if __name__ == "__main__":
    unittest.main()
