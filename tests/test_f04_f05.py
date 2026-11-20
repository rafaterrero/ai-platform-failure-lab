import unittest
from unittest.mock import patch

from llm_failure_lab.core import DEMO_KEY, LabIdentity, DeploymentStore, RECORDS, tool_call
from llm_failure_lab.f04_f05 import (
    BudgetedToolRunner, EvidenceBoundary, ExecutionBudget, ScriptedEvidenceTool,
    build_evidence_scenarios, run_f04, run_f05,
)
from llm_failure_lab.live_f04_f05 import (
    acceptance_f04, acceptance_f05, evaluate_call, run_live,
)
from llm_failure_lab.live_f01 import ToolRequestError

CALL = tool_call("tenant_alpha", "payments-api", "staging")


class BudgetTests(unittest.TestCase):
    def test_normal_execution(self):
        r = run_f04(CALL, "hardened", proposals=1)
        self.assertEqual((r["http_statuses"], r["datastore_reads"]), ([200], 1))

    def test_baseline_replays_three(self):
        r = run_f04(CALL, "baseline")
        self.assertEqual((r["http_statuses"], r["tool_invocations"], r["datastore_reads"]),
                         ([200, 200, 200], 3, 3))

    def test_hardened_blocks_before_dispatch(self):
        r = run_f04(CALL, "hardened")
        self.assertEqual(r["http_statuses"], [200, 429, 429])
        self.assertEqual((r["tool_invocations"], r["datastore_reads"], r["budget_rejections"]),
                         (1, 1, 2))

    def test_hardened_can_be_configured_for_two(self):
        r = run_f04(CALL, "hardened", budget=ExecutionBudget(2))
        self.assertEqual((r["http_statuses"], r["datastore_reads"]), ([200, 200, 429], 2))

    def test_bad_budget_or_replay_count(self):
        for bad in (0, -1, True, 1.5):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ExecutionBudget(bad)
        with self.assertRaises(ValueError):
            run_f04(CALL, "hardened", proposals=4)

    def test_invalid_token_never_reads(self):
        identity, store = LabIdentity(DEMO_KEY), DeploymentStore()
        runner = BudgetedToolRunner(identity, store)
        r = runner.submit(token=None, call=CALL, mode="hardened")
        self.assertEqual(r["http_status"], 401)
        self.assertEqual(store.reads, 0)

    def test_cross_tenant_never_reads_in_budget_experiment(self):
        identity, store = LabIdentity(DEMO_KEY), DeploymentStore()
        runner = BudgetedToolRunner(identity, store)
        r = runner.submit(token=identity.issue("bob", "tenant_beta"), call=CALL, mode="baseline")
        self.assertEqual(r["http_status"], 403)
        self.assertEqual(store.reads, 0)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.identity = LabIdentity(DEMO_KEY)
        self.token = self.identity.issue("alice", "tenant_alpha")
        self.records = build_evidence_scenarios(CALL)

    def invoke(self, evidence, mode="hardened", call=CALL, token=None):
        dep = ScriptedEvidenceTool(evidence)
        boundary = EvidenceBoundary(self.identity, dep)
        result = boundary.execute(self.token if token is None else token, call, mode)
        return result, dep

    def test_complete_evidence_verified(self):
        r, dep = self.invoke(self.records["complete"])
        self.assertEqual((r["http_status"], r["verification"], dep.reads),
                         (200, "verified", 1))

    def test_missing_reason_rejected_and_no_deployment(self):
        r, _ = self.invoke(self.records["missing_reason"])
        self.assertEqual((r["http_status"], r["verification"]), (502, "unverified"))
        self.assertIsNone(r["deployment"])
        self.assertEqual(r["error"]["code"], "INCOMPLETE_TOOL_EVIDENCE")

    def test_mismatched_service_rejected(self):
        r, _ = self.invoke(self.records["mismatched_service"])
        self.assertEqual(r["error"]["code"], "TOOL_RESPONSE_MISMATCH")

    def test_invalid_status_rejected(self):
        r, _ = self.invoke(self.records["invalid_status"])
        self.assertEqual(r["error"]["code"], "INVALID_DEPLOYMENT_STATUS")

    def test_unparseable_timestamp_rejected(self):
        evidence = dict(self.records["complete"], observed_at="not a date")
        r, _ = self.invoke(evidence)
        self.assertEqual(r["error"]["code"], "INVALID_EVIDENCE_TIMESTAMP")

    def test_missing_explicit_timezone_rejected(self):
        evidence = dict(self.records["complete"], observed_at="2026-11-19T09:00:00")
        r, _ = self.invoke(evidence)
        self.assertEqual(r["error"]["code"], "INVALID_EVIDENCE_TIMESTAMP")

    def test_baseline_accepts_incomplete_evidence(self):
        r, _ = self.invoke(self.records["missing_reason"], "baseline")
        self.assertEqual((r["http_status"], r["verification"]), (200, "verified"))

    def test_cross_tenant_rejected_even_in_baseline(self):
        r, dep = self.invoke(self.records["complete"], mode="baseline",
                             token=self.identity.issue("bob", "tenant_beta"))
        self.assertEqual((r["http_status"], dep.reads), (403, 0))

    def test_unknown_tool_rejected_before_tool(self):
        r, dep = self.invoke(self.records["complete"], call={"name": "other", "arguments": {}})
        self.assertEqual((r["http_status"], dep.reads), (502, 0))

    def test_incomplete_fixture_is_not_made_up(self):
        wrong = tool_call("tenant_alpha", "nonexistent", "staging")
        with self.assertRaises(ValueError):
            build_evidence_scenarios(wrong)


class AcceptanceTests(unittest.TestCase):
    def test_full_acceptance(self):
        r = evaluate_call(CALL)
        self.assertTrue(r["f04_acceptance_passed"])
        self.assertTrue(r["f05_acceptance_passed"])

    def test_missing_rows_fail(self):
        self.assertFalse(acceptance_f04([]))
        self.assertFalse(acceptance_f05([]))

    def test_modified_fixture_fails(self):
        rows = evaluate_call(CALL)["f04"]
        rows[1]["datastore_reads"] = 3
        self.assertFalse(acceptance_f04(rows))

    def test_wrong_model_arguments_rejected(self):
        wrong = tool_call("tenant_beta", "orders-api", "production")
        with self.assertRaises(ToolRequestError):
            evaluate_call(wrong)

    def test_live_path_uses_real_structured_output_not_text(self):
        output = {"model": "qwen3:4b", "message": {"content": "get_deployment_status(...)"}}
        with patch("llm_failure_lab.live_f04_f05.fetch_ollama", return_value=output):
            with self.assertRaises(ToolRequestError):
                run_live()

    def test_mocked_model_tool_proposal(self):
        output = {"model": "qwen3:4b", "message": {"tool_calls": [{"function": {
            "name": "get_deployment_status", "arguments": CALL["arguments"]}}]},
            "eval_count": 37, "prompt_eval_count": 120}
        with patch("llm_failure_lab.live_f04_f05.fetch_ollama", return_value=output):
            evidence = run_live()
        self.assertEqual(evidence["real_model_proposals"], 1)
        self.assertEqual(evidence["f04_subsequent_proposals"], "synthetic_replay_of_same_model_call")
        self.assertFalse(evidence["final_llm_answer_generated"])
        self.assertTrue(evidence["f04_acceptance_passed"] and evidence["f05_acceptance_passed"])


if __name__ == "__main__":
    unittest.main()
