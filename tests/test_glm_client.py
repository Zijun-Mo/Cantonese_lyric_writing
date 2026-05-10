import unittest
from unittest import mock

from src.generation.glm_client import GLMClient


class _FakeResponse:
    status_code = 200
    text = '{"ok": true}'

    def __init__(self, content='{"ok": true}', finish_reason="stop"):
        self._content = content
        self._finish_reason = finish_reason

    def json(self):
        return {
            "choices": [
                {
                    "finish_reason": self._finish_reason,
                    "message": {
                        "content": self._content,
                        "reasoning_content": "internal reasoning",
                    }
                }
            ]
        }


class GLMClientTest(unittest.TestCase):
    def _mock_post(self, content='{"ok": true}', finish_reason="stop"):
        patcher = mock.patch("src.generation.glm_client.requests.post")
        post = patcher.start()
        self.addCleanup(patcher.stop)
        post.return_value = _FakeResponse(content=content, finish_reason=finish_reason)
        return post

    def test_glm_payload_does_not_include_deepseek_thinking(self):
        post = self._mock_post()
        client = GLMClient(api_key="glm-key")

        self.assertEqual(
            client.chat([{"role": "user", "content": "hi"}]),
            '{"ok": true}',
        )

        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "glm-4-flash")
        self.assertEqual(post.call_args.kwargs["timeout"], 60)
        self.assertNotIn("thinking", payload)
        self.assertNotIn("reasoning_effort", payload)
        self.assertEqual(payload["temperature"], 0.9)

    def test_deepseek_non_thinking_payload_and_json_parse(self):
        post = self._mock_post('{"answer": "ok"}')
        client = GLMClient(provider="deepseek", api_key="deepseek-key")

        result = client.chat_json([{"role": "user", "content": "hi"}])

        self.assertEqual(result, {"answer": "ok"})
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "deepseek-v4-pro")
        self.assertEqual(post.call_args.kwargs["timeout"], 120)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", payload)
        self.assertEqual(payload["response_format"], {"type": "json_object"})

    def test_deepseek_quality_retry_uses_thinking_mode(self):
        post = self._mock_post('{"answer": "ok"}')
        client = GLMClient(provider="deepseek", api_key="deepseek-key")
        retry_client = client.for_quality_retry()

        result = retry_client.chat_json([{"role": "user", "content": "hi"}])

        self.assertEqual(result, {"answer": "ok"})
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "deepseek-v4-pro")
        self.assertEqual(post.call_args.kwargs["timeout"], 300)
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "high")
        self.assertGreaterEqual(payload["max_tokens"], 4096)
        self.assertNotIn("temperature", payload)

    def test_empty_content_is_treated_as_failed_response(self):
        post = self._mock_post("", finish_reason="length")
        client = GLMClient(provider="deepseek", api_key="deepseek-key", thinking=True, max_retries=1)

        with self.assertLogs("src.generation.llm_client", level="WARNING"):
            result = client.chat_json([{"role": "user", "content": "hi"}], max_tokens=1024)

        self.assertIsNone(result)
        payload = post.call_args.kwargs["json"]
        self.assertGreaterEqual(payload["max_tokens"], 4096)

    @mock.patch("src.generation.glm_client.time.sleep", return_value=None)
    def test_chunked_connection_error_is_retried(self, _sleep):
        patcher = mock.patch("src.generation.glm_client.requests.post")
        post = patcher.start()
        self.addCleanup(patcher.stop)
        post.side_effect = [
            __import__("requests").exceptions.ChunkedEncodingError("InvalidChunkLength"),
            _FakeResponse('{"answer": "ok"}'),
        ]
        client = GLMClient(provider="deepseek", api_key="deepseek-key", max_retries=2)

        with self.assertLogs("src.generation.llm_client", level="WARNING") as logs:
            result = client.chat_json([{"role": "user", "content": "hi"}])

        self.assertEqual(result, {"answer": "ok"})
        self.assertEqual(post.call_count, 2)
        self.assertIn("连接中断", "\n".join(logs.output))

    def test_glm_quality_retry_uses_plus_model(self):
        client = GLMClient(api_key="glm-key")
        retry_client = client.for_quality_retry()

        self.assertEqual(retry_client.provider, "glm")
        self.assertEqual(retry_client.model, "glm-4-plus")


if __name__ == "__main__":
    unittest.main()
