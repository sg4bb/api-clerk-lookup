"""Reintentos con NVIDIA: saturación (5xx) -> modelos de respaldo y espera; rechazo real -> error."""

import os
import unittest
from unittest import mock

import httpx
from openai import APIStatusError

from extractor import llm
from extractor.llm import ExtractionError, _create_with_retry, _retryable


def status_error(code: int, text: str = "Service temporarily overloaded") -> APIStatusError:
    request = httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions")
    return APIStatusError(text, response=httpx.Response(code, request=request), body={"detail": text})


class FakeClient:
    """Responde con los errores dados, en orden, y después con 'ok'."""

    def __init__(self, *errors):
        self.errors, self.models, self.timeouts = list(errors), [], []
        self.chat = mock.Mock()
        self.chat.completions.create = self.create

    def create(self, model, **kwargs):
        self.models.append(model)
        self.timeouts.append(kwargs.get("timeout"))
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


@mock.patch.dict(os.environ, {"NVIDIA_RETRY_MAX_S": "300"})
@mock.patch.object(llm.time, "sleep")
class RetryTest(unittest.TestCase):
    def test_which_codes_retry(self, _sleep):
        for code in (429, 500, 502, 503, 504, 529, None):
            self.assertTrue(_retryable(code), code)
        for code in (400, 401, 403, 422):
            self.assertFalse(_retryable(code), code)

    def test_529_moves_to_fallback_model(self, sleep):
        client = FakeClient(status_error(529))
        result, used = _create_with_retry(client, ["gemma", "glm"], messages=[])
        self.assertEqual((result, used), ("ok", "glm"))
        self.assertEqual(client.models, ["gemma", "glm"])
        sleep.assert_not_called()                   # el respaldo se prueba sin esperar

    def test_all_overloaded_waits_and_retries(self, sleep):
        client = FakeClient(status_error(529), status_error(503))
        result, used = _create_with_retry(client, ["gemma", "glm"], messages=[])
        self.assertEqual((result, used), ("ok", "gemma"))
        sleep.assert_called_once()

    def test_main_model_has_shorter_timeout(self, _sleep):
        """Gemma colgada: se pasa a glm a los 75 s, no a los 180 s (14/10/2026)."""
        from openai import APITimeoutError
        request = httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions")
        client = FakeClient(APITimeoutError(request))
        result, used = _create_with_retry(client, ["gemma", "glm"], messages=[])
        self.assertEqual(used, "glm")
        self.assertEqual(client.timeouts, [75.0, None])   # glm usa el del cliente (NVIDIA_TIMEOUT_S)

    def test_real_rejection_stops(self, sleep):
        client = FakeClient(status_error(401, "Unauthorized"))
        with self.assertRaises(ExtractionError) as ctx:
            _create_with_retry(client, ["gemma", "glm"], messages=[])
        self.assertIn("401", str(ctx.exception))
        self.assertEqual(client.models, ["gemma"])


if __name__ == "__main__":
    unittest.main()


class LocalModelTest(unittest.TestCase):
    """Bonilla (22/10/2026): qwen2.5:7b en Ollama mandó "evidence": null con json_object."""

    ANSWER = ('{"people": [{"first_name": "Alex", "last_name": "Bonilla", "age": 51, "role": "charged", '
              '"evidence": null}], "incident_date": "2019-07-14", "county": "Gilchrist", "state": "FL"}')

    def run_extract(self, mode):
        from extractor.llm import extract
        sent = []

        def create(model, **kwargs):
            sent.append(kwargs)
            message = mock.Mock(content=self.ANSWER)
            return mock.Mock(choices=[mock.Mock(message=message)])

        client = mock.Mock()
        client.chat.completions.create = create
        env = {"NVIDIA_API_KEY": "ollama", "NVIDIA_JSON_MODE": mode, "NVIDIA_FALLBACK_MODEL": ""}
        with mock.patch.dict(os.environ, env), mock.patch("openai.OpenAI", return_value=client):
            return extract("text", "title", "2021-03-03", "https://example.com"), sent

    def test_null_evidence_is_accepted(self):
        result, _ = self.run_extract("json_object")
        self.assertEqual(result.people[0].evidence, "")

    def test_json_schema_mode_sends_the_schema(self):
        _, sent = self.run_extract("json_schema")
        fmt = sent[0]["response_format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertIn("people", fmt["json_schema"]["schema"]["properties"])
