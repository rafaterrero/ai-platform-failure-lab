import unittest
from unittest.mock import patch

from llm_failure_lab.core import DEMO_KEY, LabIdentity, tool_call
from llm_failure_lab.f03 import (ResponseStep, RetryPolicy, RetryExecutor,
                                 ScriptedReadOnlyTool, run_scenario)
from llm_failure_lab.live_f03 import evaluate, acceptance, run_live
from llm_failure_lab.live_f01 import ToolRequestError

CALL = tool_call("tenant_alpha", "payments-api", "staging")


class RetryUnitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.identity = LabIdentity(DEMO_KEY)
        self.token = self.identity.issue("alice", "tenant_alpha")

    async def run_steps(self, *steps, mode="hardened", policy=None, call=CALL, token=None):
        backend = ScriptedReadOnlyTool(steps)
        executor = RetryExecutor(self.identity, backend, policy)
        response = await executor.execute(token=self.token if token is None else token,
                                          tool_call=call, mode=mode)
        return response, backend, executor.audit

    async def test_hardened_success_first_attempt(self):
        r, b, _ = await self.run_steps(ResponseStep(200))
        self.assertEqual((r["http_status"], b.attempts, b.reads), (200, 1, 1))
        self.assertEqual(r["verification"], "verified")

    async def test_hardened_retries_one_503_and_recovers(self):
        r, b, audit = await self.run_steps(ResponseStep(503), ResponseStep(200))
        self.assertEqual((r["http_status"], b.attempts, b.reads), (200, 2, 1))
        self.assertTrue(any(e["decision"] == "retry_scheduled" for e in audit))

    async def test_hardened_recovers_one_429(self):
        r, b, _ = await self.run_steps(ResponseStep(429, retry_after_seconds=.01), ResponseStep(200))
        self.assertEqual((r["http_status"], b.attempts), (200, 2))

    async def test_repeated_503_exhausts_two_attempt_budget(self):
        r, b, _ = await self.run_steps(ResponseStep(503), ResponseStep(503), ResponseStep(200))
        self.assertEqual((r["http_status"], b.attempts, b.reads), (503, 2, 0))
        self.assertEqual(r["error"]["code"], "RETRY_BUDGET_EXHAUSTED")
        self.assertEqual(r["verification"], "unverified")
        self.assertIsNone(r["deployment"])

    async def test_baseline_makes_four_attempts_before_recovery(self):
        r, b, _ = await self.run_steps(ResponseStep(503), ResponseStep(503),
                                       ResponseStep(503), ResponseStep(200), mode="baseline")
        self.assertEqual((r["http_status"], b.attempts, b.reads), (200, 4, 1))

    async def test_baseline_is_capped_for_permanent_failure(self):
        r, b, _ = await self.run_steps(ResponseStep(503), mode="baseline")
        self.assertEqual((r["http_status"], b.attempts), (503, 4))

    async def test_hardened_does_not_retry_403(self):
        r, b, _ = await self.run_steps(ResponseStep(403), ResponseStep(200))
        self.assertEqual((r["http_status"], b.attempts, b.reads), (403, 1, 0))
        self.assertEqual(r["error"]["code"], "NON_RETRYABLE_DEPENDENCY_ERROR")

    async def test_baseline_naively_retries_403(self):
        r, b, _ = await self.run_steps(ResponseStep(403), ResponseStep(200), mode="baseline")
        self.assertEqual((r["http_status"], b.attempts), (200, 2))

    async def test_retry_after_longer_than_budget_short_circuits(self):
        r, b, audit = await self.run_steps(ResponseStep(429, retry_after_seconds=.5),
                                           ResponseStep(200))
        self.assertEqual((r["http_status"], b.attempts), (503, 1))
        self.assertEqual(r["error"]["code"], "RETRY_DELAY_EXCEEDS_DEADLINE")
        self.assertTrue(any(a["decision"] == "retry_delay_exceeds_deadline" for a in audit))

    async def test_timeout_is_cooperatively_cancelled(self):
        r, b, _ = await self.run_steps(ResponseStep(200, delay_seconds=.08),
                                       policy=RetryPolicy(per_attempt_timeout_seconds=.015))
        self.assertEqual(r["http_status"], 504)
        self.assertEqual(r["error"]["code"], "TOOL_TIMEOUT")
        self.assertEqual((b.attempts, b.reads, b.cancelled), (1, 0, 1))

    async def test_cumulative_deadline_is_not_reset_after_retry(self):
        policy = RetryPolicy(operation_deadline_seconds=.025,
                             per_attempt_timeout_seconds=.03, backoff_seconds=.02)
        r, b, audit = await self.run_steps(ResponseStep(503, delay_seconds=.01),
                                           ResponseStep(200), policy=policy)
        self.assertEqual((r["http_status"], b.attempts), (503, 1))
        self.assertEqual(r["error"]["code"], "RETRY_DELAY_EXCEEDS_DEADLINE")

    async def test_wrong_tenant_rejected_before_dependency(self):
        token = self.identity.issue("bob", "tenant_beta")
        r, b, _ = await self.run_steps(ResponseStep(200), token=token)
        self.assertEqual(r["http_status"], 403)
        self.assertEqual(b.attempts, 0)

    async def test_unknown_tool_not_executed(self):
        r, b, _ = await self.run_steps(ResponseStep(200), call={"name": "drop_all", "arguments": {}})
        self.assertEqual(r["http_status"], 502)
        self.assertEqual(b.attempts, 0)

    async def test_missing_identity_does_not_call_dependency(self):
        backend = ScriptedReadOnlyTool([ResponseStep(200)])
        r = await RetryExecutor(self.identity, backend).execute(token=None, tool_call=CALL)
        self.assertEqual(r["http_status"], 401)
        self.assertEqual(backend.attempts, 0)

    async def test_scenarios_acceptance(self):
        rows = await evaluate(CALL)
        self.assertTrue(acceptance(rows))


class RetryContractTests(unittest.TestCase):
    def test_invalid_policy_rejected(self):
        with self.assertRaises(ValueError):
            RetryPolicy(max_attempts=0)
        with self.assertRaises(ValueError):
            RetryPolicy(backoff_seconds=-1)

    def test_invalid_script_rejected(self):
        with self.assertRaises(ValueError):
            ScriptedReadOnlyTool([])
        with self.assertRaises(ValueError):
            ScriptedReadOnlyTool([ResponseStep(200, delay_seconds=-1)])

    def test_live_bridge_never_replaces_unexpected_model_args(self):
        output = {"model": "qwen3:4b", "message": {"tool_calls": [{"function": {
            "name": "get_deployment_status", "arguments": {
                "tenant_id": "tenant_beta", "service": "orders-api", "environment": "production"}}}]}}
        with patch("llm_failure_lab.live_f03.fetch_ollama", return_value=output):
            with self.assertRaises(ToolRequestError):
                run_live("qwen3:4b", "http://localhost:11434/api/chat", 1024)

    def test_contract_rejects_missing_scenarios(self):
        self.assertFalse(acceptance([]))


if __name__ == "__main__":
    unittest.main()
