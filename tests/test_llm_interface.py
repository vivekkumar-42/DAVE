import unittest
from unittest import mock

import json
import os
import time

from app.modules.llm_interface import CLOUD_OFFLINE_MESSAGE, LLMClient


class FakeMemoryManager:
    def __init__(self, examples: list[tuple[str, str]]) -> None:
        self.examples = examples
        self.calls: list[tuple[str, int, float]] = []

    def get_bootstrap_examples(
        self,
        user_input: str,
        *,
        limit: int = 3,
        min_similarity: float = 0.35,
    ) -> list[tuple[str, str]]:
        self.calls.append((user_input, limit, min_similarity))
        return list(self.examples[:limit])


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

    def test_query_injects_memory_examples_into_prompt(self) -> None:
        client = self._client()
        fake_memory = FakeMemoryManager(
            [
                (
                    "open chrome and search cars",
                    "Resolved successful action: intent=open_and_search | route=automation | target=chrome | query=cars",
                )
            ]
        )
        client.memory_manager = fake_memory
        with mock.patch.object(client, "_query_groq", return_value="from groq") as groq_mock:
            reply = client.query("open browser and search cars")

        self.assertEqual("from groq", reply)
        provider_prompt = groq_mock.call_args.args[0]
        self.assertIn("Learned successful patterns:", provider_prompt)
        self.assertIn("User: open chrome and search cars", provider_prompt)
        self.assertEqual(1, len(fake_memory.calls))

    def test_route_intent_injects_memory_examples_into_intent_prompt(self) -> None:
        client = self._client()
        fake_memory = FakeMemoryManager(
            [
                (
                    "open notepad",
                    "Resolved successful action: intent=open_app | route=automation | target=notepad",
                )
            ]
        )
        client.memory_manager = fake_memory
        payload = {
            "intent": "open_app",
            "target": "notepad",
            "query": "",
            "command": "",
            "shell_mode": "powershell",
            "reply": "",
            "confidence": 0.9,
        }
        with mock.patch.object(client, "_query_groq_intent", return_value=payload) as groq_mock:
            _ = client.route_intent("open notes")

        intent_prompt = groq_mock.call_args.args[0]
        self.assertIn("Successful command memories:", intent_prompt)
        self.assertIn("Past user: open notepad", intent_prompt)
        self.assertIn("Current user: open notes", intent_prompt)
        self.assertEqual(1, len(fake_memory.calls))

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

    def test_half_open_allows_single_probe_then_reopens_on_failure(self) -> None:
        config = {
            "llm": {
                "enabled": True,
                "provider_order": ["groq", "ollama", "gemini"],
                "provider_retries": 3,
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
        client._provider_circuit["groq"]["state"] = "open"
        client._provider_circuit["groq"]["open_until"] = time.time() - 1.0

        with mock.patch.object(client, "_query_groq", side_effect=RuntimeError("still down")) as groq_mock:
            with mock.patch.object(client, "_query_ollama", return_value="from ollama") as ollama_mock:
                response = client.query("hello")

        self.assertEqual("from ollama", response)
        self.assertEqual(1, groq_mock.call_count)
        ollama_mock.assert_called_once()
        self.assertEqual("open", client._provider_circuit["groq"]["state"])
        self.assertGreater(float(client._provider_circuit["groq"]["open_until"]), time.time())

    def test_retry_backoff_uses_exponential_with_jitter(self) -> None:
        config = {
            "llm": {
                "enabled": True,
                "provider_retries": 3,
                "retry_backoff_seconds": 0.6,
                "retry_jitter_ratio": 0.35,
            }
        }
        client = LLMClient(config=config)
        def always_fail() -> str:
            raise RuntimeError("429")

        with mock.patch("app.modules.llm_interface.random.uniform", return_value=0.0):
            with mock.patch("app.modules.llm_interface.time.sleep") as sleep_mock:
                with self.assertRaises(RuntimeError):
                    client._run_with_retries(
                        provider_name="groq",
                        func=always_fail,
                    )

        self.assertEqual(3, sleep_mock.call_count)
        delays = [round(call.args[0], 3) for call in sleep_mock.call_args_list]
        self.assertEqual([0.6, 1.2, 2.4], delays)

    def test_route_intent_uses_structured_provider_output(self) -> None:
        client = self._client()
        payload = {
            "intent": "open_app",
            "target": "notepad",
            "query": "",
            "command": "",
            "shell_mode": "powershell",
            "reply": "",
            "confidence": 0.92,
        }
        with mock.patch.object(client, "_query_groq_intent", return_value=payload) as groq_mock:
            with mock.patch.object(client, "_query_ollama_intent") as ollama_mock:
                routed = client.route_intent("open notepad")

        self.assertEqual("open_app", (routed or {}).get("intent"))
        self.assertEqual("notepad", (routed or {}).get("target"))
        groq_mock.assert_called_once()
        ollama_mock.assert_not_called()

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
        self.assertEqual(0.35, client.retry_jitter_ratio)
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

    def test_query_ephemeral_uses_response_cache_on_repeat_prompt(self) -> None:
        client = self._client()
        with mock.patch.object(client, "_query_groq", return_value="cached hello") as groq_mock:
            first = client.query_ephemeral("hello cache")
            second = client.query_ephemeral("hello cache")

        self.assertEqual("cached hello", first)
        self.assertEqual("cached hello", second)
        groq_mock.assert_called_once()
        health = client.get_health()
        self.assertEqual("cache_hit", health.get("reason"))

    def test_route_intent_uses_response_cache_on_repeat_prompt(self) -> None:
        client = self._client()
        payload = {
            "intent": "open_app",
            "target": "notepad",
            "query": "",
            "command": "",
            "shell_mode": "powershell",
            "reply": "",
            "confidence": 0.91,
        }
        with mock.patch.object(client, "_query_groq_intent", return_value=payload) as groq_mock:
            first = client.route_intent("open notes")
            second = client.route_intent("open notes")

        self.assertEqual("open_app", (first or {}).get("intent"))
        self.assertEqual("open_app", (second or {}).get("intent"))
        groq_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
