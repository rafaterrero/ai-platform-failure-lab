"""Deterministic tests for interpreting Ollama responses."""
import unittest
from llm_failure_lab.ollama_smoke import build_payload, inspect_output


class OllamaDiagnosticsTests(unittest.TestCase):
    def test_payload_has_real_tool_definition_and_larger_budget(self):
        payload = build_payload("qwen3:4b", 1024, False)
        self.assertEqual(payload["tools"][0]["function"]["name"], "get_deployment_status")
        self.assertEqual(payload["options"]["num_predict"], 1024)
        self.assertIs(payload["think"], False)

    def test_exhausted_budget_does_not_pass(self):
        report, passed = inspect_output({"message": {"content": "planning..."},
                                         "eval_count": 256, "done_reason": "length"}, 256)
        self.assertFalse(passed)
        self.assertTrue(report["generation_budget_reached"])

    def test_text_tool_call_not_accepted(self):
        report, passed = inspect_output({"message": {"content": '{"name":"get_deployment_status"}'},
                                         "eval_count": 42}, 1024)
        self.assertFalse(passed)
        self.assertEqual(report["tool_calls"], [])

    def test_structured_tool_call_passes(self):
        output = {"message": {"tool_calls": [{"function": {
            "name": "get_deployment_status",
            "arguments": {"tenant_id": "tenant_alpha", "service": "payments-api", "environment": "staging"}
        }}]}, "eval_count": 40, "done_reason": "stop"}
        report, passed = inspect_output(output, 1024)
        self.assertTrue(passed)
        self.assertFalse(report["tool_execution_performed"])

    def test_wrong_tool_rejected(self):
        output = {"message": {"tool_calls": [{"function": {"name": "delete_service", "arguments": {}}}]}}
        _, passed = inspect_output(output, 1024)
        self.assertFalse(passed)


if __name__ == "__main__":
    unittest.main()
