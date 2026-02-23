import unittest
from unittest import mock

import json
import os

from app.modules.llm_interface import CLOUD_OFFLINE_MESSAGE, LLMClient


class LLMClientFallbackTests(unittest.TestCase):
    def _client(self) -> LLMClient:
        config = {
            "llm": {
                "enabled": True,
                "provider_order": ["groq", "ollama", "gemini"],
                "provider_retries": 0,
                "groq": {"enabled": True, "api_key": "g-key", "model": "llama-3.1-8b-instant"},
                "ollama": {
                    "enabled": True,
                    "url": "http://localhost:11434/api/generate",
                    "model": "llama3",
                },
                "gemini": {"enabled": True, "api_key": "gm-key", "model": "gemini-2.5-flash"},
            }
        }
        return LLMClient(config=config)

    def test_uses_groq_first_when_available(self) -> None:
        client = self._client()
        with mock.patch.object(client, "_query_groq", return_value="from groq") as groq_mock:
            with mock.patch.object(client, "_query_ollama") as ollama_mock:
                with mock.patch.object(client, "_query_gemini") as gemini_mock:
                    reply = client.query("hello")

        self.assertEqual("from groq", reply)
        groq_mock.assert_called_once()
        ollama_mock.assert_not_called()
        gemini_mock.assert_not_called()
        health = client.get_health()
        self.assertEqual("online", health["status"])
        self.assertEqual("groq", health["provider"])

    def test_falls_back_to_ollama_when_groq_fails(self) -> None:
        client = self._client()
        with mock.patch.object(client, "_query_groq", side_effect=RuntimeError("429")):
            with mock.patch.object(client, "_query_ollama", return_value="from ollama") as ollama_mock:
                with mock.patch.object(client, "_query_gemini") as gemini_mock:
                    reply = client.query("hello")

        self.assertEqual("from ollama", reply)
        ollama_mock.assert_called_once()
        gemini_mock.assert_not_called()
        health = client.get_health()
        self.assertEqual("degraded", health["status"])
        self.assertEqual("ollama", health["provider"])

    def test_falls_back_to_gemini_when_groq_and_ollama_fail(self) -> None:
        client = self._client()
        with mock.patch.object(client, "_query_groq", side_effect=RuntimeError("groq down")):
            with mock.patch.object(client, "_query_ollama", side_effect=RuntimeError("conn refused")):
                with mock.patch.object(client, "_query_gemini", return_value="from gemini") as gemini_mock:
                    reply = client.query("hello")

        self.assertEqual("from gemini", reply)
        gemini_mock.assert_called_once()
        health = client.get_health()
        self.assertEqual("degraded", health["status"])
        self.assertEqual("gemini", health["provider"])

    def test_returns_critical_failure_when_all_providers_fail(self) -> None:
        client = self._client()
        with mock.patch.object(client, "_query_groq", side_effect=RuntimeError("groq down")):
            with mock.patch.object(client, "_query_ollama", side_effect=RuntimeError("local down")):
                with mock.patch.object(client, "_query_gemini", side_effect=RuntimeError("gemini down")):
                    reply = client.query("hello")

        self.assertEqual(CLOUD_OFFLINE_MESSAGE, reply)
        health = client.get_health()
        self.assertEqual("offline", health["status"])
        self.assertEqual("none", health["provider"])
        self.assertEqual("all_providers_offline", health["reason"])

    def test_query_ephemeral_does_not_store_history(self) -> None:
        client = self._client()
        with mock.patch.object(client, "_query_groq", side_effect=["intent-json", "chat-response"]):
            first = client.query_ephemeral("intent")
            second = client.query("chat")

        self.assertEqual("intent-json", first)
        self.assertEqual("chat-response", second)
        self.assertEqual(1, len(client._history))
        self.assertEqual(("chat", "chat-response"), client._history[0])

    def test_intent_router_gets_json_fallback_when_all_providers_fail(self) -> None:
        client = self._client()
        with mock.patch.object(client, "_query_groq", side_effect=RuntimeError("groq down")):
            with mock.patch.object(client, "_query_ollama", side_effect=RuntimeError("ollama down")):
                with mock.patch.object(client, "_query_gemini", side_effect=RuntimeError("gemini down")):
                    response = client.query_ephemeral(
                        "hello",
                        system_prompt=(
                            "You are an intent router. Return ONLY valid JSON with keys: "
                            "intent, target, query, command, reply, confidence."
                        ),
                    )

        payload = json.loads(response)
        self.assertEqual("chat", payload.get("intent"))
        self.assertIsInstance(payload.get("reply"), str)

    def test_dynamic_provider_selection_prefers_more_reliable_provider(self) -> None:
        config = {
            "llm": {
                "enabled": True,
                "provider_order": ["groq", "ollama", "gemini"],
                "provider_retries": 0,
                "dynamic_provider_selection": True,
                "provider_sample_threshold": 1,
                "groq": {"enabled": True, "api_key": "g-key", "model": "llama-3.1-8b-instant"},
                "ollama": {
                    "enabled": True,
                    "url": "http://localhost:11434/api/generate",
                    "model": "llama3",
                },
                "gemini": {"enabled": True, "api_key": "gm-key", "model": "gemini-2.5-flash"},
            }
        }
        client = LLMClient(config=config)

        with mock.patch.object(client, "_query_groq", side_effect=RuntimeError("groq down")):
            with mock.patch.object(client, "_query_ollama", return_value="from ollama"):
                first = client.query("hello one")
        self.assertEqual("from ollama", first)

        with mock.patch.object(client, "_query_ollama", return_value="local first") as ollama_mock:
            with mock.patch.object(client, "_query_groq") as groq_mock:
                second = client.query("hello two")

        self.assertEqual("local first", second)
        ollama_mock.assert_called_once()
        groq_mock.assert_not_called()

    def test_history_prompt_includes_bootstrap_examples(self) -> None:
        client = self._client()
        prompt = client._compose_history_prompt("status report")
        self.assertIn("Behavior examples:", prompt)
        self.assertIn("User: hello dave", prompt)
        self.assertIn("DAVE:", prompt)

    def test_circuit_breaker_skips_failing_provider_until_cooldown(self) -> None:
        config = {
            "llm": {
                "enabled": True,
                "provider_order": ["groq", "ollama", "gemini"],
                "provider_retries": 0,
                "dynamic_provider_selection": False,
                "circuit_breaker_enabled": True,
                "circuit_breaker_failure_threshold": 1,
                "circuit_breaker_cooldown_seconds": 999,
                "groq": {"enabled": True, "api_key": "g-key", "model": "llama-3.1-8b-instant"},
                "ollama": {
                    "enabled": True,
                    "url": "http://localhost:11434/api/generate",
                    "model": "llama3",
                },
                "gemini": {"enabled": True, "api_key": "gm-key", "model": "gemini-2.5-flash"},
            }
        }
        client = LLMClient(config=config)

        with mock.patch.object(client, "_query_groq", side_effect=RuntimeError("groq down")) as groq_first:
            with mock.patch.object(client, "_query_ollama", return_value="from ollama"):
                first = client.query("hello one")
        self.assertEqual("from ollama", first)
        self.assertEqual(1, groq_first.call_count)

        with mock.patch.object(client, "_query_groq", return_value="from groq") as groq_second:
            with mock.patch.object(client, "_query_ollama", return_value="still ollama") as ollama_second:
                second = client.query("hello two")
        self.assertEqual("still ollama", second)
        groq_second.assert_not_called()
        ollama_second.assert_called_once()

    def test_constructor_handles_malformed_numeric_config(self) -> None:
        config = {
            "llm": {
                "enabled": "true",
                "temperature": "bad",
                "max_tokens": "bad",
                "timeout_seconds": "bad",
                "history_turns": "bad",
                "provider_retries": "bad",
                "retry_backoff_seconds": "bad",
                "prefer_local": "yes",
                "dynamic_provider_selection": "no",
                "provider_sample_threshold": "bad",
                "bootstrap_example_count": "bad",
                "groq": {"enabled": "no"},
                "ollama": {"enabled": "yes", "timeout_seconds": "bad"},
                "gemini": {"enabled": "no", "timeout_seconds": "bad"},
            }
        }

        client = LLMClient(config=config)

        self.assertTrue(client.enabled)
        self.assertEqual(0.2, client.temperature)
        self.assertEqual(300, client.max_tokens)
        self.assertEqual(30.0, client.timeout_seconds)
        self.assertEqual(5, client.max_history_turns)
        self.assertEqual(1, client.provider_retries)
        self.assertEqual(0.6, client.retry_backoff_seconds)
        self.assertTrue(client.prefer_local)
        self.assertFalse(client.dynamic_provider_selection)
        self.assertEqual(3, client.provider_sample_threshold)
        self.assertFalse(client.groq_enabled)
        self.assertTrue(client.ollama_enabled)
        self.assertFalse(client.gemini_enabled)
        self.assertEqual(30.0, client.ollama_timeout_seconds)
        self.assertEqual(30.0, client.gemini_timeout_seconds)

    def test_config_secrets_are_ignored_by_default(self) -> None:
        config = {
            "llm": {
                "enabled": True,
                "groq": {"enabled": True, "api_key": "gsk_live", "model": "llama-3.1-8b-instant"},
                "gemini": {"enabled": True, "api_key": "AIza_live", "model": "gemini-2.5-flash"},
            },
            "groq_api_key": "gsk_top",
            "gemini_api_key": "AIza_top",
        }

        with mock.patch.dict(
            os.environ,
            {"GROQ_API_KEY": "", "GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""},
        ):
            client = LLMClient(config=config)

        self.assertIsNone(client.groq_api_key)
        self.assertIsNone(client.gemini_api_key)

    def test_config_secrets_can_be_enabled_explicitly(self) -> None:
        config = {
            "llm": {
                "enabled": True,
                "allow_config_secrets": True,
                "groq": {"enabled": True, "api_key": "gsk_live", "model": "llama-3.1-8b-instant"},
                "gemini": {"enabled": True, "api_key": "AIza_live", "model": "gemini-2.5-flash"},
            }
        }

        client = LLMClient(config=config)

        self.assertEqual("gsk_live", client.groq_api_key)
        self.assertEqual("AIza_live", client.gemini_api_key)


if __name__ == "__main__":
    unittest.main()
