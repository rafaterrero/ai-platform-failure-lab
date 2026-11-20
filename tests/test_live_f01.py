"""Offline deterministic checks of the live bridge. No Ollama or GPU needed."""
import unittest
from unittest.mock import patch

from llm_failure_lab.core import DEMO_KEY, LabIdentity, tool_call
from llm_failure_lab.live_f01 import (
    ToolRequestError, evaluate_call, parse_exactly_one_tool_call,
    run_live, verify_f01,
)


def synthetic_output(arguments=None):
    call = tool_call("tenant_alpha", "payments-api", "staging")
    if arguments is not None:
        call["arguments"] = arguments
    return {"model": "qwen3:4b", "done_reason": "stop", "eval_count": 376,
            "message": {"role": "assistant", "content": "non-executable text",
                        "tool_calls": [{"function": call}]}}


class LiveF01BridgeTests(unittest.TestCase):
    def test_accepts_structured_tool_call_not_content(self):
        self.assertEqual(parse_exactly_one_tool_call(synthetic_output()),
                         tool_call("tenant_alpha", "payments-api", "staging"))

    def test_rejects_missing_or_text_only_tool_call(self):
        for output in ({}, {"message": {"content": "get_deployment_status()"}},
                       {"message": {"tool_calls": []}}):
            with self.subTest(output=output), self.assertRaises(ToolRequestError):
                parse_exactly_one_tool_call(output)

    def test_rejects_multiple_calls(self):
        output = synthetic_output()
        output["message"]["tool_calls"].append(output["message"]["tool_calls"][0])
        with self.assertRaises(ToolRequestError):
            parse_exactly_one_tool_call(output)

    def test_rejects_wrong_name_and_arguments(self):
        output = synthetic_output()
        output["message"]["tool_calls"][0]["function"]["name"] = "delete_deployment"
        with self.assertRaises(ToolRequestError):
            parse_exactly_one_tool_call(output)
        output = synthetic_output(arguments="tenant_alpha,payments-api,staging")
        with self.assertRaises(ToolRequestError):
            parse_exactly_one_tool_call(output)

    def test_verified_boundary_contrast(self):
        scenarios = evaluate_call(tool_call("tenant_alpha", "payments-api", "staging"),
                                  LabIdentity(DEMO_KEY))
        self.assertTrue(verify_f01(scenarios))
        bob_hardened = next(x for x in scenarios
                            if x["principal"] == "bob" and x["mode"] == "hardened")
        self.assertEqual(bob_hardened["datastore_reads"], 0)
        self.assertIsNone(bob_hardened["returned_deployment"])

    def test_rejects_model_proposed_cross_tenant_unauthorized_for_alice(self):
        # Rejected for both principals when model requests unknown resource tenant_gamma.
        outcomes = evaluate_call(tool_call("tenant_gamma", "payments-api", "staging"))
        self.assertFalse(verify_f01(outcomes))
        for item in outcomes:
            if item["mode"] == "hardened":
                self.assertEqual(item["http_status"], 403)
                self.assertEqual(item["datastore_reads"], 0)

    def test_rejects_unexpected_arguments_before_storage_read(self):
        call = tool_call("tenant_alpha", "payments-api", "staging")
        call["arguments"]["shell"] = "not-allowed"
        results = evaluate_call(call)
        self.assertTrue(all(x["http_status"] == 502 for x in results))
        self.assertTrue(all(x["datastore_reads"] == 0 for x in results))
        self.assertFalse(verify_f01(results))

    def test_mocked_live_bridge_is_not_mislabeled_as_real_in_test(self):
        # Verifies wiring, but remains only a mock test; no evidence of actual inference.
        with patch("llm_failure_lab.live_f01.fetch_ollama", return_value=synthetic_output()):
            report = run_live("qwen3:4b", "http://127.0.0.1:11434/api/chat", 1024)
        self.assertTrue(report["f01_acceptance_passed"])
        self.assertTrue(report["tool_executed"])
        self.assertEqual(report["model_proposed_tool_call"]["name"], "get_deployment_status")


if __name__ == "__main__":
    unittest.main()
