from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import importlib
from pathlib import Path
from typing import Any, Callable

import requests

from app.runtime_paths import config_candidates

Groq = None  # type: ignore[assignment]
genai_sdk = None  # type: ignore[assignment]
_GROQ_IMPORT_ATTEMPTED = False
_GENAI_IMPORT_ATTEMPTED = False

CRITICAL_FAILURE_MESSAGE = (
    "Fallback mode active: external models are unavailable right now. "
    "I can still help with local commands and practical guidance."
)
# Backward-compatible constant used by other modules/tests.
CLOUD_OFFLINE_MESSAGE = CRITICAL_FAILURE_MESSAGE

DEFAULT_SYSTEM_PROMPT = (
    "You are DAVE, a highly advanced, sarcastic, and efficient desktop assistant. "
    "You prefer concise answers. You are running on a Windows PC."
)
DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"
GROQ_MODEL_FALLBACKS: tuple[str, ...] = (
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
)
DEFAULT_BOOTSTRAP_EXAMPLES: list[tuple[str, str]] = [
    ("hello dave", "Online. What do you need?"),
    ("open calculator", "Opening Calculator, Sir."),
    ("search for python threading", "Searching for python threading on the web."),
]


def _load_groq_class() -> Any:
    global Groq, _GROQ_IMPORT_ATTEMPTED
    if _GROQ_IMPORT_ATTEMPTED:
        return Groq
    _GROQ_IMPORT_ATTEMPTED = True
    try:
        module = importlib.import_module("groq")
        Groq = getattr(module, "Groq", None)
    except Exception:
        Groq = None
    return Groq


def _load_genai_module() -> Any:
    global genai_sdk, _GENAI_IMPORT_ATTEMPTED
    if _GENAI_IMPORT_ATTEMPTED:
        return genai_sdk
    _GENAI_IMPORT_ATTEMPTED = True
    try:
        genai_sdk = importlib.import_module("google.genai")
    except Exception:
        genai_sdk = None
    return genai_sdk


class LLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        system_prompt: str | None = None,
        provider: str = "groq",
        model: str | None = None,
        max_history_turns: int = 5,
        timeout_seconds: float = 30.0,
        provider_configs: dict[str, dict[str, Any]] | None = None,
        fallback_providers: list[str] | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        del fallback_providers  # Kept only for compatibility with older callers.

        self.config = config if isinstance(config, dict) else self._load_config_from_disk()
        llm_config_raw = self.config.get("llm", {})
        llm_config = llm_config_raw if isinstance(llm_config_raw, dict) else {}

        self.enabled = self._coerce_bool(llm_config.get("enabled", True), True)
        self.temperature = self._coerce_float(llm_config.get("temperature", 0.2), 0.2)
        self.max_tokens = max(1, self._coerce_int(llm_config.get("max_tokens", 300), 300))
        self.timeout_seconds = max(
            5.0,
            self._coerce_float(llm_config.get("timeout_seconds", timeout_seconds), timeout_seconds),
        )
        self.max_history_turns = max(
            1,
            self._coerce_int(llm_config.get("history_turns", max_history_turns), max_history_turns),
        )
        self.provider_retries = max(
            0,
            self._coerce_int(llm_config.get("provider_retries", 1), 1),
        )
        self.retry_backoff_seconds = max(
            0.0,
            self._coerce_float(llm_config.get("retry_backoff_seconds", 0.6), 0.6),
        )
        self.circuit_breaker_enabled = self._coerce_bool(
            llm_config.get("circuit_breaker_enabled", True),
            True,
        )
        self.circuit_breaker_failure_threshold = max(
            1,
            self._coerce_int(llm_config.get("circuit_breaker_failure_threshold", 2), 2),
        )
        self.circuit_breaker_cooldown_seconds = max(
            1.0,
            self._coerce_float(llm_config.get("circuit_breaker_cooldown_seconds", 18.0), 18.0),
        )
        self.allow_config_secrets = self._coerce_bool(
            llm_config.get("allow_config_secrets", False),
            False,
        )
        self.prefer_local = self._coerce_bool(llm_config.get("prefer_local", False), False)
        self.dynamic_provider_selection = self._coerce_bool(
            llm_config.get("dynamic_provider_selection", True),
            True,
        )
        self.provider_sample_threshold = max(
            1,
            self._coerce_int(llm_config.get("provider_sample_threshold", 3), 3),
        )
        self.bootstrap_examples = self._load_bootstrap_examples(
            llm_config.get("bootstrap_examples")
        )
        self.bootstrap_example_count = max(
            0,
            min(
                len(self.bootstrap_examples),
                self._coerce_int(
                    llm_config.get("bootstrap_example_count", len(self.bootstrap_examples)),
                    len(self.bootstrap_examples),
                ),
            ),
        )

        configured_prompt = llm_config.get("system_prompt")
        if isinstance(system_prompt, str) and system_prompt.strip():
            self.system_prompt = system_prompt.strip()
        elif isinstance(configured_prompt, str) and configured_prompt.strip():
            self.system_prompt = configured_prompt.strip()
        else:
            self.system_prompt = DEFAULT_SYSTEM_PROMPT

        raw_order = llm_config.get("provider_order")
        self.provider_order = self._normalize_provider_order(raw_order)
        if self.prefer_local:
            self.provider_order = self._prioritize_local_provider(self.provider_order)
        self.provider = self.provider_order[0] if self.provider_order else "groq"

        preferred_provider = provider.strip().lower() if isinstance(provider, str) else ""
        preferred_model = model.strip() if isinstance(model, str) and model.strip() else None
        preferred_api_key = self._normalize_secret(api_key)

        groq_cfg = llm_config.get("groq", {})
        groq_block = groq_cfg if isinstance(groq_cfg, dict) else {}
        self.groq_enabled = self._coerce_bool(groq_block.get("enabled", True), True)
        groq_config_api_key = (
            groq_block.get("api_key") if self.allow_config_secrets else None
        )
        groq_top_level_api_key = (
            self.config.get("groq_api_key") if self.allow_config_secrets else None
        )
        self.groq_api_key = self._first_non_empty(
            self._nested_provider_value(provider_configs, "groq", "api_key"),
            groq_config_api_key,
            groq_top_level_api_key,
            os.getenv("GROQ_API_KEY"),
            preferred_api_key if preferred_provider == "groq" else None,
        )
        self.groq_model = self._first_non_empty(
            self._nested_provider_value(provider_configs, "groq", "model"),
            groq_block.get("model"),
            self.config.get("groq_model"),
            os.getenv("GROQ_MODEL"),
            preferred_model if preferred_provider == "groq" else None,
            DEFAULT_GROQ_MODEL,
        )

        ollama_cfg = llm_config.get("ollama", {})
        ollama_block = ollama_cfg if isinstance(ollama_cfg, dict) else {}
        self.ollama_enabled = self._coerce_bool(ollama_block.get("enabled", True), True)
        self.ollama_url = self._first_non_empty(
            ollama_block.get("url"),
            self.config.get("ollama_url"),
            "http://localhost:11434/api/generate",
        )
        self.ollama_model = self._first_non_empty(
            ollama_block.get("model"),
            self.config.get("ollama_model"),
            preferred_model if preferred_provider == "ollama" else None,
            "llama3",
        )
        self.ollama_timeout_seconds = max(
            5.0,
            self._coerce_float(
                ollama_block.get("timeout_seconds", self.timeout_seconds),
                self.timeout_seconds,
            ),
        )

        gemini_cfg = llm_config.get("gemini", {})
        gemini_block = gemini_cfg if isinstance(gemini_cfg, dict) else {}
        self.gemini_enabled = self._coerce_bool(gemini_block.get("enabled", True), True)
        gemini_config_api_key = (
            gemini_block.get("api_key") if self.allow_config_secrets else None
        )
        gemini_top_level_api_key = (
            self.config.get("gemini_api_key") if self.allow_config_secrets else None
        )
        self.gemini_api_key = self._first_non_empty(
            self._nested_provider_value(provider_configs, "gemini", "api_key"),
            gemini_config_api_key,
            gemini_top_level_api_key,
            os.getenv("GEMINI_API_KEY"),
            os.getenv("GOOGLE_API_KEY"),
            preferred_api_key if preferred_provider == "gemini" else None,
        )
        self.gemini_model = self._first_non_empty(
            self._nested_provider_value(provider_configs, "gemini", "model"),
            gemini_block.get("model"),
            self.config.get("gemini_model"),
            os.getenv("GEMINI_MODEL"),
            preferred_model if preferred_provider == "gemini" else None,
            "gemini-2.5-flash",
        )
        self.gemini_timeout_seconds = max(
            5.0,
            self._coerce_float(
                gemini_block.get("timeout_seconds", self.timeout_seconds),
                self.timeout_seconds,
            ),
        )

        self._history: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        self._logger = logging.getLogger(__name__)
        self._provider_metrics: dict[str, dict[str, Any]] = {
            provider_name: {
                "successes": 0,
                "failures": 0,
                "avg_latency": None,
                "last_error": "",
                "last_used_at": 0.0,
                "circuit_open_until": 0.0,
            }
            for provider_name in ("groq", "ollama", "gemini")
        }
        self._provider_circuit: dict[str, dict[str, Any]] = {
            provider_name: {
                "consecutive_failures": 0,
                "open_until": 0.0,
                "last_trip_reason": "",
            }
            for provider_name in ("groq", "ollama", "gemini")
        }
        self._last_health: dict[str, Any] = {
            "status": "idle",
            "provider": self.provider,
            "reason": "not_queried",
            "detail": "",
            "updated_at": time.time(),
        }

    def query(self, prompt: str) -> str:
        return self._query_internal(
            user_text=prompt,
            system_prompt_override=None,
            use_history=True,
            remember_response=True,
        )

    def query_ephemeral(self, prompt: str, system_prompt: str | None = None) -> str:
        return self._query_internal(
            user_text=prompt,
            system_prompt_override=system_prompt,
            use_history=False,
            remember_response=False,
        )

    def get_health(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._last_health)

    def _query_internal(
        self,
        user_text: str,
        system_prompt_override: str | None,
        use_history: bool,
        remember_response: bool,
    ) -> str:
        clean_text = user_text.strip() if isinstance(user_text, str) else ""
        if not clean_text:
            return ""

        if not self.enabled:
            self._set_health(
                status="disabled",
                provider=self.provider,
                reason="llm_disabled",
                detail="LLM disabled in config.",
            )
            return "LLM is disabled in config."

        effective_prompt = (
            system_prompt_override.strip()
            if isinstance(system_prompt_override, str) and system_prompt_override.strip()
            else self.system_prompt
        )
        provider_input = self._compose_history_prompt(clean_text) if use_history else clean_text

        with self._lock:
            failures: list[tuple[str, str, str]] = []
            provider_sequence = self._build_provider_sequence()
            primary_provider = provider_sequence[0] if provider_sequence else self.provider
            self.provider = primary_provider

            for provider in provider_sequence:
                if self._is_provider_circuit_open(provider):
                    remaining = self._provider_circuit_remaining(provider)
                    failures.append(
                        (
                            provider,
                            "circuit_open",
                            f"cooldown {remaining:.1f}s",
                        )
                    )
                    continue

                started_at = time.perf_counter()
                try:
                    if provider == "groq":
                        response_text = self._run_with_retries(
                            provider_name="groq",
                            func=lambda: self._query_groq(provider_input, effective_prompt),
                        )
                    elif provider == "ollama":
                        response_text = self._run_with_retries(
                            provider_name="ollama",
                            func=lambda: self._query_ollama(provider_input, effective_prompt),
                        )
                    elif provider == "gemini":
                        response_text = self._run_with_retries(
                            provider_name="gemini",
                            func=lambda: self._query_gemini(provider_input, effective_prompt),
                        )
                    else:
                        continue

                    final_text = response_text.strip()
                    if not final_text:
                        raise RuntimeError("Empty provider response.")

                    elapsed = max(0.0, time.perf_counter() - started_at)
                    self._record_provider_result(provider, success=True, latency=elapsed, error_text="")

                    if remember_response:
                        self._append_history(clean_text, final_text)

                    if provider == primary_provider:
                        self._set_health(
                            status="online",
                            provider=provider,
                            reason="ok",
                            detail="Primary provider succeeded.",
                        )
                    else:
                        self._set_health(
                            status="degraded",
                            provider=provider,
                            reason=f"fallback_from_{primary_provider}",
                            detail=self._summarize_failures(failures),
                        )
                    return final_text
                except Exception as exc:
                    reason = self._classify_error(exc)
                    compact_error = self._compact_error(exc)
                    elapsed = max(0.0, time.perf_counter() - started_at)
                    self._record_provider_result(
                        provider,
                        success=False,
                        latency=elapsed,
                        error_text=compact_error,
                    )
                    failures.append((provider, reason, compact_error))
                    if provider == "groq":
                        self._logger.warning("Groq unavailable, switching to Local...")
                    elif provider == "ollama":
                        self._logger.warning("Local Brain offline, switching to Reserve...")

            summary = self._summarize_failures(failures)
            self._set_health(
                status="offline",
                provider="none",
                reason="all_providers_offline",
                detail=summary,
            )
            self._logger.error("All cognitive providers failed: %s", summary)
            return self._build_offline_fallback(
                user_text=clean_text,
                system_prompt_override=system_prompt_override,
            )

    def _run_with_retries(self, provider_name: str, func: Callable[[], str]) -> str:
        last_exc: Exception | None = None
        attempts = self.provider_retries + 1

        for attempt in range(attempts):
            try:
                return func()
            except Exception as exc:
                last_exc = exc
                if attempt >= attempts - 1:
                    break
                delay = self.retry_backoff_seconds * (attempt + 1)
                self._logger.debug(
                    "Provider %s attempt %s/%s failed, retrying in %.2fs: %s",
                    provider_name,
                    attempt + 1,
                    attempts,
                    delay,
                    self._compact_error(exc),
                )
                if delay > 0:
                    time.sleep(delay)

        raise RuntimeError(
            f"{provider_name} failed after retries: "
            f"{self._compact_error(last_exc) if last_exc else 'unknown error'}"
        )

    def _build_offline_fallback(self, user_text: str, system_prompt_override: str | None) -> str:
        if self._is_intent_router_prompt(system_prompt_override):
            payload = {
                "intent": "chat",
                "target": "",
                "query": "",
                "command": "",
                "reply": (
                    "Fallback mode active, Sir. Cloud and local models are unavailable. "
                    "I can still execute direct local commands like open/search/volume/lock."
                ),
                "confidence": 0.99,
            }
            return json.dumps(payload)

        lowered = user_text.lower()
        if any(
            token in lowered
            for token in (
                "open ",
                "search for",
                "volume",
                "lock",
                "shutdown",
                "restart",
                "run ",
                "execute ",
                "powershell ",
                "cmd ",
            )
        ):
            return (
                "Fallback mode active, Sir. I can still run direct automation commands. "
                "Use explicit forms like 'open notepad' or 'run powershell Get-Date'."
            )
        if any(token in lowered for token in ("help", "what can you do", "capabilities")):
            return (
                "Fallback mode active, Sir. I can handle local automation: open apps, web search, "
                "volume control, and workstation lock while external models recover."
            )
        return CRITICAL_FAILURE_MESSAGE

    @staticmethod
    def _is_intent_router_prompt(system_prompt_override: str | None) -> bool:
        if not isinstance(system_prompt_override, str):
            return False
        lowered = system_prompt_override.lower()
        return "intent router" in lowered and "return only valid json" in lowered

    def _query_groq(self, prompt: str, system_prompt: str) -> str:
        if not self.groq_enabled:
            raise RuntimeError("groq provider disabled")
        if not self.groq_api_key:
            raise RuntimeError("groq missing_api_key")
        groq_class = _load_groq_class()
        if groq_class is None:
            raise RuntimeError("groq_library_missing")

        client = groq_class(api_key=self.groq_api_key)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        def run_completion(model: str) -> Any:
            return client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

        try:
            completion = run_completion(self.groq_model)
        except Exception as exc:
            error_code, error_message = self._extract_groq_error(exc)
            if error_code in {"model_decommissioned", "model_not_found"}:
                fallback_models = self._build_groq_fallback_models(current_model=self.groq_model)
                for fallback_model in fallback_models:
                    try:
                        completion = run_completion(fallback_model)
                    except Exception:
                        continue
                    self._logger.warning(
                        "Groq model '%s' unavailable (%s). Falling back to '%s'.",
                        self.groq_model,
                        error_code,
                        fallback_model,
                    )
                    self.groq_model = fallback_model
                    break
                else:
                    detail = error_message or error_code or "model unavailable"
                    raise RuntimeError(f"groq {detail}") from exc
            else:
                raise
        choices = getattr(completion, "choices", None) or []
        if not choices:
            raise RuntimeError("groq empty response")

        content = getattr(choices[0].message, "content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()
        raise RuntimeError("groq empty response")

    @staticmethod
    def _extract_groq_error(exc: Exception) -> tuple[str, str]:
        body = getattr(exc, "body", None)
        if not isinstance(body, dict):
            return "", ""
        error_block = body.get("error")
        if not isinstance(error_block, dict):
            return "", ""
        code = error_block.get("code")
        message = error_block.get("message")
        return (
            code.strip() if isinstance(code, str) else "",
            message.strip() if isinstance(message, str) else "",
        )

    @staticmethod
    def _build_groq_fallback_models(current_model: str) -> list[str]:
        candidates: list[str] = []
        env_override = os.getenv("GROQ_FALLBACK_MODEL")
        if isinstance(env_override, str) and env_override.strip():
            candidates.append(env_override.strip())
        candidates.extend(list(GROQ_MODEL_FALLBACKS))

        seen: set[str] = set()
        ordered: list[str] = []
        current = (current_model or "").strip()
        for model in candidates:
            clean = model.strip()
            if not clean or clean == current or clean in seen:
                continue
            seen.add(clean)
            ordered.append(clean)
        return ordered

    def _query_ollama(self, prompt: str, system_prompt: str) -> str:
        if not self.ollama_enabled:
            raise RuntimeError("ollama provider disabled")
        if not self.ollama_url:
            raise RuntimeError("ollama missing url")

        payload = {
            "model": self.ollama_model,
            "prompt": prompt,
            "system": system_prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        response = requests.post(
            self.ollama_url,
            json=payload,
            timeout=self.ollama_timeout_seconds,
        )
        response.raise_for_status()

        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("ollama invalid response payload")

        text = data.get("response")
        if not isinstance(text, str) or not text.strip():
            message = data.get("message")
            if isinstance(message, dict):
                fallback = message.get("content")
                if isinstance(fallback, str):
                    text = fallback

        if isinstance(text, str) and text.strip():
            return text.strip()
        raise RuntimeError("ollama empty response")

    def _query_gemini(self, prompt: str, system_prompt: str) -> str:
        if not self.gemini_enabled:
            raise RuntimeError("gemini provider disabled")
        if not self.gemini_api_key:
            raise RuntimeError("gemini missing_api_key")
        genai_module = _load_genai_module()
        if genai_module is None:
            raise RuntimeError("google_genai_missing")

        client = genai_module.Client(api_key=self.gemini_api_key)
        try:
            result = client.models.generate_content(
                model=self.gemini_model,
                contents=prompt,
                config={
                    "temperature": self.temperature,
                    "max_output_tokens": self.max_tokens,
                    "system_instruction": system_prompt,
                    "http_options": {"timeout": self.gemini_timeout_seconds},
                },
            )
        finally:
            try:
                client.close()
            except Exception:
                pass

        text = getattr(result, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()

        candidates = getattr(result, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                part_text = getattr(part, "text", None)
                if isinstance(part_text, str) and part_text.strip():
                    return part_text.strip()

        raise RuntimeError("gemini empty response")

    def _append_history(self, user_text: str, assistant_text: str) -> None:
        self._history.append((user_text, assistant_text))
        if len(self._history) > self.max_history_turns:
            self._history = self._history[-self.max_history_turns :]

    def _compose_history_prompt(self, user_text: str) -> str:
        lines: list[str] = []
        if self.bootstrap_example_count > 0:
            lines.append("Behavior examples:")
            for sample_user, sample_assistant in self.bootstrap_examples[
                : self.bootstrap_example_count
            ]:
                lines.append(f"User: {sample_user}")
                lines.append(f"DAVE: {sample_assistant}")

        if self._history:
            lines.append("Conversation context:")
        for previous_user, previous_assistant in self._history:
            lines.append(f"User: {previous_user}")
            lines.append(f"DAVE: {previous_assistant}")

        if not lines:
            return user_text

        lines.append(f"User: {user_text}")
        lines.append("DAVE:")
        return "\n".join(lines)

    def _set_health(self, status: str, provider: str, reason: str, detail: str) -> None:
        self._last_health = {
            "status": status,
            "provider": provider,
            "reason": reason,
            "detail": detail,
            "updated_at": time.time(),
        }

    def _build_provider_sequence(self) -> list[str]:
        ordered = list(self.provider_order)
        if not self.dynamic_provider_selection:
            return ordered

        metrics = self._provider_metrics
        score_ready = any(
            (entry["successes"] + entry["failures"]) >= self.provider_sample_threshold
            for entry in metrics.values()
        )
        if not score_ready:
            return ordered

        base_index = {provider: index for index, provider in enumerate(ordered)}
        ordered.sort(
            key=lambda provider: (
                self._is_provider_circuit_open(provider),
                -self._provider_score(provider),
                self._provider_latency(provider),
                base_index.get(provider, 999),
            )
        )
        return ordered

    def _record_provider_result(
        self,
        provider: str,
        *,
        success: bool,
        latency: float,
        error_text: str,
    ) -> None:
        metric = self._provider_metrics.get(provider)
        if metric is None:
            metric = {
                "successes": 0,
                "failures": 0,
                "avg_latency": None,
                "last_error": "",
                "last_used_at": 0.0,
                "circuit_open_until": 0.0,
            }
            self._provider_metrics[provider] = metric

        if success:
            metric["successes"] += 1
            metric["last_error"] = ""
        else:
            metric["failures"] += 1
            metric["last_error"] = error_text

        previous_latency = metric.get("avg_latency")
        if previous_latency is None:
            metric["avg_latency"] = latency
        else:
            metric["avg_latency"] = (previous_latency * 0.72) + (latency * 0.28)
        metric["last_used_at"] = time.time()
        self._update_provider_circuit(provider=provider, success=success, error_text=error_text)
        circuit_state = self._provider_circuit.get(provider, {})
        metric["circuit_open_until"] = float(circuit_state.get("open_until", 0.0) or 0.0)

    def _update_provider_circuit(self, provider: str, success: bool, error_text: str) -> None:
        circuit = self._provider_circuit.get(provider)
        if circuit is None:
            circuit = {
                "consecutive_failures": 0,
                "open_until": 0.0,
                "last_trip_reason": "",
            }
            self._provider_circuit[provider] = circuit

        if success:
            circuit["consecutive_failures"] = 0
            circuit["open_until"] = 0.0
            circuit["last_trip_reason"] = ""
            return

        circuit["consecutive_failures"] = int(circuit.get("consecutive_failures", 0)) + 1
        if not self.circuit_breaker_enabled:
            return
        if circuit["consecutive_failures"] < self.circuit_breaker_failure_threshold:
            return

        circuit["open_until"] = time.time() + self.circuit_breaker_cooldown_seconds
        circuit["last_trip_reason"] = error_text

    def _provider_circuit_remaining(self, provider: str) -> float:
        if not self.circuit_breaker_enabled:
            return 0.0
        circuit = self._provider_circuit.get(provider, {})
        open_until = float(circuit.get("open_until", 0.0) or 0.0)
        return max(0.0, open_until - time.time())

    def _is_provider_circuit_open(self, provider: str) -> bool:
        return self._provider_circuit_remaining(provider) > 0.0

    def _provider_score(self, provider: str) -> float:
        metric = self._provider_metrics.get(provider, {})
        successes = int(metric.get("successes", 0))
        failures = int(metric.get("failures", 0))
        total = successes + failures
        if total <= 0:
            return 0.5
        return (successes + 1.0) / (total + 2.0)

    def _provider_latency(self, provider: str) -> float:
        metric = self._provider_metrics.get(provider, {})
        latency = metric.get("avg_latency")
        if isinstance(latency, (float, int)):
            return max(0.0, float(latency))
        return 999.0

    @staticmethod
    def _normalize_provider_order(raw_order: Any) -> list[str]:
        default_order = ["groq", "ollama", "gemini"]
        if not isinstance(raw_order, list):
            return default_order

        allowed = {"groq", "ollama", "gemini"}
        normalized: list[str] = []
        for item in raw_order:
            provider = str(item).strip().lower()
            if provider in allowed and provider not in normalized:
                normalized.append(provider)

        for provider in default_order:
            if provider not in normalized:
                normalized.append(provider)
        return normalized

    @staticmethod
    def _prioritize_local_provider(order: list[str]) -> list[str]:
        if "ollama" not in order:
            return order
        reordered = ["ollama"]
        for provider in order:
            if provider != "ollama":
                reordered.append(provider)
        return reordered

    @staticmethod
    def _load_bootstrap_examples(raw_examples: Any) -> list[tuple[str, str]]:
        if not isinstance(raw_examples, list):
            return list(DEFAULT_BOOTSTRAP_EXAMPLES)

        parsed: list[tuple[str, str]] = []
        for item in raw_examples:
            if not isinstance(item, dict):
                continue
            user = item.get("user")
            assistant = item.get("assistant")
            if isinstance(user, str) and isinstance(assistant, str):
                clean_user = user.strip()
                clean_assistant = assistant.strip()
                if clean_user and clean_assistant:
                    parsed.append((clean_user, clean_assistant))

        if parsed:
            return parsed
        return list(DEFAULT_BOOTSTRAP_EXAMPLES)

    @staticmethod
    def _nested_provider_value(
        provider_configs: dict[str, dict[str, Any]] | None,
        provider: str,
        key: str,
    ) -> str | None:
        if not isinstance(provider_configs, dict):
            return None
        block_raw = provider_configs.get(provider, {})
        block = block_raw if isinstance(block_raw, dict) else {}
        value = block.get(key)
        return LLMClient._normalize_secret(value)

    @staticmethod
    def _first_non_empty(*values: Any) -> str | None:
        for value in values:
            normalized = LLMClient._normalize_secret(value)
            if normalized:
                return normalized
        return None

    @staticmethod
    def _normalize_secret(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        upper = text.upper()
        if upper.startswith("YOUR_") or upper.startswith("<YOUR_"):
            return None
        if "REPLACE_ME" in upper or "CHANGE_ME" in upper:
            return None
        return text

    @staticmethod
    def _coerce_bool(value: Any, fallback: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return fallback

    @staticmethod
    def _coerce_int(value: Any, fallback: int) -> int:
        try:
            return int(value)
        except Exception:
            return fallback

    @staticmethod
    def _coerce_float(value: Any, fallback: float) -> float:
        try:
            return float(value)
        except Exception:
            return fallback

    @staticmethod
    def _compact_error(exc: Exception) -> str:
        text = str(exc).replace("\n", " ").strip()
        if len(text) > 220:
            return text[:220] + "..."
        return text

    @staticmethod
    def _classify_error(exc: Exception) -> str:
        text = str(exc).lower()
        if "model_decommissioned" in text:
            return "model_decommissioned"
        if "model_not_found" in text:
            return "model_not_found"
        if "429" in text or "rate limit" in text or "quota" in text:
            return "rate_limited"
        if "timed out" in text or "timeout" in text:
            return "timeout"
        if "connection refused" in text or "connection error" in text:
            return "connection_error"
        if "missing_api_key" in text:
            return "missing_api_key"
        if "disabled" in text:
            return "provider_disabled"
        return "provider_error"

    @staticmethod
    def _summarize_failures(failures: list[tuple[str, str, str]]) -> str:
        if not failures:
            return "No providers attempted."
        return " | ".join(f"{provider}:{reason}" for provider, reason, _ in failures)

    @staticmethod
    def _load_config_from_disk() -> dict[str, Any]:
        candidates = config_candidates()

        checked: set[Path] = set()
        for path in candidates:
            if path in checked:
                continue
            checked.add(path)
            if not path.exists():
                continue
            try:
                parsed = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
        return {}
