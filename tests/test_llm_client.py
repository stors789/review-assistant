import types
import unittest

from review_assistant import llm_client


def _response(content):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))]
    )


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        next_response = self.responses.pop(0)
        if isinstance(next_response, Exception):
            raise next_response
        return _response(next_response)


class FakeClient:
    def __init__(self, responses):
        self.completions = FakeCompletions(responses)
        self.chat = types.SimpleNamespace(completions=self.completions)


class LLMClientTests(unittest.TestCase):
    def test_call_json_accepts_top_level_array(self):
        client = FakeClient(['[{"severity": "warning", "issue": "check this"}]'])

        result = llm_client.call_json(client, "", "return json", "deepseek-v4-flash", retries=0)

        self.assertEqual(result, [{"severity": "warning", "issue": "check this"}])

    def test_call_json_extracts_fenced_array(self):
        client = FakeClient(['```json\n[{"ok": true}]\n```'])

        result = llm_client.call_json(client, "", "return json", "deepseek-v4-flash", retries=0)

        self.assertEqual(result, [{"ok": True}])

    def test_chat_completion_retries_with_max_completion_tokens(self):
        client = FakeClient([
            ValueError("Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."),
            '{"ok": true}',
        ])

        result = llm_client.call_json(client, "", "return json", "o3-mini", max_tokens=123, retries=0)

        self.assertEqual(result, {"ok": True})
        self.assertIn("max_tokens", client.completions.calls[0])
        self.assertNotIn("max_tokens", client.completions.calls[1])
        self.assertEqual(client.completions.calls[1]["max_completion_tokens"], 123)

    def test_chat_completion_strips_reasoning_params_when_unsupported(self):
        client = FakeClient([
            ValueError("Unknown parameter: reasoning_effort"),
            '{"ok": true}',
        ])

        result = llm_client.call_json(client, "", "return json", "deepseek-v4-pro", retries=0)

        self.assertEqual(result, {"ok": True})
        self.assertIn("reasoning_effort", client.completions.calls[0])
        self.assertNotIn("reasoning_effort", client.completions.calls[1])
        self.assertNotIn("extra_body", client.completions.calls[1])


if __name__ == "__main__":
    unittest.main()
