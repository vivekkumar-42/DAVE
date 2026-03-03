from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import logging
import os
import random
import sys
import threading
import time
import importlib
from pathlib import Path
from typing import Any, Callable

import requests

from app.modules.memory_manager import MemoryManager
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
    "You are DAVE, a highly optimized desktop automation engine. "
    "You communicate strictly in clear, technical, and highly respectful terms. "
    "Omit conversational pleasantries unless explicitly greeted. "
    "Deliver precise, actionable data and execute commands with absolute efficiency. "
    "You are running on a Windows PC."
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
INTENT_ALLOWED_VALUES: tuple[str, ...] = (
    "open_app",
    "web_search",
    "system_control",
    "run_command",
    "set_timer",
    "set_alarm",
    "chat",
)
INTENT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENT_ALLOWED_VALUES)},
        "target": {"type": "string"},
        "query": {"type": "string"},
        "command": {"type": "string"},
        "shell_mode": {"type": "string", "enum": ["powershell", "cmd"]},
        "reply": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": [
        "intent",
        "target",
        "query",
        "command",
        "shell_mode",
        "reply",
        "confidence",
    ],
    "additionalProperties": False,
}
INTENT_ROUTER_TOOL_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "route_intent",
        "description": (
            "Route a desktop command into one structured intent payload for local execution."
        ),
        "parameters": INTENT_RESPONSE_SCHEMA,
    },
}
INTENT_ROUTER_SYSTEM_PROMPT = (
    "You are an intent router for a Windows desktop assistant named DAVE. "
    "Classify user input into exactly one intent and return only structured output. "
    "Allowed intents: open_app, web_search, system_control, run_command, set_timer, set_alarm, chat. "
    "Use target for app names, query for web searches, command for system/shell commands, "
    "shell_mode as powershell or cmd, command for timer/alarm schedule text, "
    "reply for chat responses, and confidence from 0 to 1."
)


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
        self.retry_jitter_ratio = max(
            0.0,
            min(1.0, self._coerce_float(llm_config.get("retry_jitter_ratio", 0.35), 0.35)),
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
        self.response_cache_enabled = self._coerce_bool(
            llm_config.get("response_cache_enabled", True),
            True,
        )
        self.response_cache_ttl_seconds = max(
            5.0,
            min(
                3600.0,
                self._coerce_float(
                    llm_config.get("response_cache_ttl_seconds", 120.0),
                    120.0,
                ),
            ),
        )
        self.response_cache_max_entries = max(
            16,
            min(
                1024,
                self._coerce_int(
                    llm_config.get("response_cache_max_entries", 256),
                    256,
                ),
            ),
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
        memory_cfg_raw = self.config.get("memory", {})
        memory_cfg = memory_cfg_raw if isinstance(memory_cfg_raw, dict) else {}
        self.memory_enabled = self._coerce_bool(memory_cfg.get("enabled", True), True)
        self.memory_example_limit = max(
            0,
            min(10, self._coerce_int(memory_cfg.get("top_k", 3), 3)),
        )
        self.memory_min_similarity = max(
            0.0,
            min(1.0, self._coerce_float(memory_cfg.get("min_similarity", 0.35), 0.35)),
        )
        self.memory_scan_rows = max(
            20,
            self._coerce_int(memory_cfg.get("scan_rows", 250), 250),
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
        self._response_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
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
                "state": "closed",
                "consecutive_failures": 0,
                "open_until": 0.0,
                "last_trip_reason": "",
                "half_open_in_flight": False,
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
        self.memory_manager: MemoryManager | None = None
        if self.memory_enabled and self.memory_example_limit > 0:
            try:
                self.memory_manager = MemoryManager(max_scan_rows=self.memory_scan_rows)
            except Exception as exc:
                self._logger.warning("Memory manager unavailable for LLM context: %s", exc)

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

    def route_intent(self, prompt: str) -> dict[str, Any] | None:
        return self._route_intent_internal(prompt)

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
        memory_examples = self._fetch_dynamic_bootstrap_examples(clean_text) if use_history else []
        provider_input = (
            self._compose_history_prompt(clean_text, memory_examples=memory_examples)
            if use_history
            else clean_text
        )
        cache_key = self._build_cache_key(
            "query",
            effective_prompt,
            provider_input,
        )
        cached_query = self._cache_get(cache_key)
        if cached_query is not None:
            cached_value, cached_provider, cached_age = cached_query
            if isinstance(cached_value, str):
                final_text = cached_value.strip()
                if final_text:
                    if remember_response:
                        self._append_history(clean_text, final_text)
                    self._set_provider(cached_provider)
                    self._set_health(
                        status="online",
                        provider=cached_provider,
                        reason="cache_hit",
                        detail=f"response cache hit ({cached_age:.1f}s old)",
                    )
                    return final_text

        failures: list[tuple[str, str, str]] = []
        provider_sequence = self._build_provider_sequence()
        primary_provider = provider_sequence[0] if provider_sequence else self.provider
        self._set_provider(primary_provider)

        for provider in provider_sequence:
            allowed, gate_reason, half_open_trial = self._reserve_provider_attempt(provider)
            if not allowed:
                failures.append((provider, "circuit_open", gate_reason))
                continue

            started_at = time.perf_counter()
            try:
                if provider == "groq":
                    response_text = self._run_with_retries(
                        provider_name="groq",
                        func=lambda: self._query_groq(provider_input, effective_prompt),
                        max_attempts=1 if half_open_trial else None,
                    )
                elif provider == "ollama":
                    response_text = self._run_with_retries(
                        provider_name="ollama",
                        func=lambda: self._query_ollama(provider_input, effective_prompt),
                        max_attempts=1 if half_open_trial else None,
                    )
                elif provider == "gemini":
                    response_text = self._run_with_retries(
                        provider_name="gemini",
                        func=lambda: self._query_gemini(provider_input, effective_prompt),
                        max_attempts=1 if half_open_trial else None,
                    )
                else:
                    continue

                final_text = response_text.strip()
                if not final_text:
                    raise RuntimeError("Empty provider response.")

                elapsed = max(0.0, time.perf_counter() - started_at)
                self._record_provider_result(provider, success=True, latency=elapsed, error_text="")
                self._cache_set(cache_key, final_text, provider=provider)

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

    def _route_intent_internal(self, user_text: str) -> dict[str, Any] | None:
        clean_text = user_text.strip() if isinstance(user_text, str) else ""
        if not clean_text:
            return None

        if not self.enabled:
            self._set_health(
                status="disabled",
                provider=self.provider,
                reason="llm_disabled",
                detail="LLM disabled in config.",
            )
            return None

        failures: list[tuple[str, str, str]] = []
        provider_sequence = self._build_provider_sequence()
        primary_provider = provider_sequence[0] if provider_sequence else self.provider
        self._set_provider(primary_provider)
        memory_examples = self._fetch_dynamic_bootstrap_examples(clean_text)
        intent_input = self._compose_intent_router_input(clean_text, memory_examples)
        cache_key = self._build_cache_key(
            "intent",
            INTENT_ROUTER_SYSTEM_PROMPT,
            intent_input,
        )
        cached_intent = self._cache_get(cache_key)
        if cached_intent is not None:
            cached_value, cached_provider, cached_age = cached_intent
            if isinstance(cached_value, dict):
                self._set_provider(cached_provider)
                self._set_health(
                    status="online",
                    provider=cached_provider,
                    reason="cache_hit",
                    detail=f"intent cache hit ({cached_age:.1f}s old)",
                )
                return dict(cached_value)

        for provider in provider_sequence:
            allowed, gate_reason, half_open_trial = self._reserve_provider_attempt(provider)
            if not allowed:
                failures.append((provider, "circuit_open", gate_reason))
                continue

            started_at = time.perf_counter()
            try:
                if provider == "groq":
                    payload = self._run_with_retries(
                        provider_name="groq",
                        func=lambda: self._query_groq_intent(intent_input),
                        max_attempts=1 if half_open_trial else None,
                    )
                elif provider == "ollama":
                    payload = self._run_with_retries(
                        provider_name="ollama",
                        func=lambda: self._query_ollama_intent(intent_input),
                        max_attempts=1 if half_open_trial else None,
                    )
                elif provider == "gemini":
                    payload = self._run_with_retries(
                        provider_name="gemini",
                        func=lambda: self._query_gemini_intent(intent_input),
                        max_attempts=1 if half_open_trial else None,
                    )
                else:
                    continue

                normalized_payload = self._normalize_intent_payload(payload)
                if normalized_payload is None:
                    raise RuntimeError("Invalid structured intent payload.")

                elapsed = max(0.0, time.perf_counter() - started_at)
                self._record_provider_result(provider, success=True, latency=elapsed, error_text="")
                self._cache_set(cache_key, normalized_payload, provider=provider)
                if provider == primary_provider:
                    self._set_health(
                        status="online",
                        provider=provider,
                        reason="ok",
                        detail="Primary provider routed intent.",
                    )
                else:
                    self._set_health(
                        status="degraded",
                        provider=provider,
                        reason=f"fallback_from_{primary_provider}",
                        detail=self._summarize_failures(failures),
                    )
                return normalized_payload
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

        summary = self._summarize_failures(failures)
        self._set_health(
            status="offline",
            provider="none",
            reason="all_providers_offline",
            detail=summary,
        )
        self._logger.error("All intent providers failed: %s", summary)
        return None

    def _run_with_retries(
        self,
        provider_name: str,
        func: Callable[[], Any],
        *,
        max_attempts: int | None = None,
    ) -> Any:
        last_exc: Exception | None = None
        attempts = max_attempts if isinstance(max_attempts, int) and max_attempts > 0 else (self.provider_retries + 1)

        for attempt in range(attempts):
            try:
                return func()
            except Exception as exc:
                last_exc = exc
                if attempt >= attempts - 1:
                    break
                base_delay = self.retry_backoff_seconds * (2 ** attempt)
                jitter_multiplier = 1.0 + random.uniform(-self.retry_jitter_ratio, self.retry_jitter_ratio)
                delay = max(0.0, base_delay * jitter_multiplier)
                self._logger.debug(
                    "Provider %s attempt %s/%s failed, retrying in %.2fs (base %.2fs): %s",
                    provider_name,
                    attempt + 1,
                    attempts,
                    delay,
                    base_delay,
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
                "timer",
                "alarm",
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
                "Use explicit forms like 'open notepad', 'start a timer for 1 min', "
                "or 'run powershell Get-Date'."
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

    def _query_groq_intent(self, prompt: str) -> dict[str, Any]:
        if not self.groq_enabled:
            raise RuntimeError("groq provider disabled")
        if not self.groq_api_key:
            raise RuntimeError("groq missing_api_key")
        groq_class = _load_groq_class()
        if groq_class is None:
            raise RuntimeError("groq_library_missing")

        client = groq_class(api_key=self.groq_api_key)
        messages = [
            {"role": "system", "content": INTENT_ROUTER_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        def run_completion(model: str) -> Any:
            return client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.0,
                max_tokens=220,
                tools=[INTENT_ROUTER_TOOL_SPEC],
                tool_choice={"type": "function", "function": {"name": "route_intent"}},
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
            raise RuntimeError("groq structured output missing choices")

        message = getattr(choices[0], "message", None)
        tool_calls = getattr(message, "tool_calls", None) or []
        for tool_call in tool_calls:
            function = getattr(tool_call, "function", None)
            function_name = getattr(function, "name", "")
            arguments = getattr(function, "arguments", None)
            if function_name != "route_intent" or not isinstance(arguments, str):
                continue
            parsed = json.loads(arguments)
            if isinstance(parsed, dict):
                return parsed

        raise RuntimeError("groq structured output missing route_intent arguments")

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

    def _query_ollama_intent(self, prompt: str) -> dict[str, Any]:
        if not self.ollama_enabled:
            raise RuntimeError("ollama provider disabled")
        if not self.ollama_url:
            raise RuntimeError("ollama missing url")

        payload = {
            "model": self.ollama_model,
            "prompt": prompt,
            "system": INTENT_ROUTER_SYSTEM_PROMPT,
            "stream": False,
            "format": INTENT_RESPONSE_SCHEMA,
            "options": {
                "temperature": 0.0,
                "num_predict": 220,
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
            raise RuntimeError("ollama structured output empty response")

        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise RuntimeError("ollama structured output invalid json")
        return parsed

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

    def _query_gemini_intent(self, prompt: str) -> dict[str, Any]:
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
                    "temperature": 0.0,
                    "max_output_tokens": 220,
                    "system_instruction": INTENT_ROUTER_SYSTEM_PROMPT,
                    "response_mime_type": "application/json",
                    "response_schema": INTENT_RESPONSE_SCHEMA,
                    "http_options": {"timeout": self.gemini_timeout_seconds},
                },
            )
        finally:
            try:
                client.close()
            except Exception:
                pass

        raw_json_text = self._extract_result_text(result)
        if not raw_json_text:
            raise RuntimeError("gemini structured output empty response")
        parsed = json.loads(raw_json_text)
        if not isinstance(parsed, dict):
            raise RuntimeError("gemini structured output invalid json")
        return parsed

    def _append_history(self, user_text: str, assistant_text: str) -> None:
        with self._lock:
            self._history.append((user_text, assistant_text))
            if len(self._history) > self.max_history_turns:
                self._history = self._history[-self.max_history_turns :]

    def _compose_history_prompt(
        self,
        user_text: str,
        memory_examples: list[tuple[str, str]] | None = None,
    ) -> str:
        with self._lock:
            history_snapshot = list(self._history)
        lines: list[str] = []
        seen_examples: set[tuple[str, str]] = set()
        if self.bootstrap_example_count > 0:
            lines.append("Behavior examples:")
            for sample_user, sample_assistant in self.bootstrap_examples[
                : self.bootstrap_example_count
            ]:
                pair = (sample_user, sample_assistant)
                if pair in seen_examples:
                    continue
                seen_examples.add(pair)
                lines.append(f"User: {sample_user}")
                lines.append(f"DAVE: {sample_assistant}")

        dynamic_examples = memory_examples if isinstance(memory_examples, list) else []
        if dynamic_examples:
            lines.append("Learned successful patterns:")
            for sample_user, sample_assistant in dynamic_examples:
                pair = (sample_user, sample_assistant)
                if pair in seen_examples:
                    continue
                seen_examples.add(pair)
                lines.append(f"User: {sample_user}")
                lines.append(f"DAVE: {sample_assistant}")

        if history_snapshot:
            lines.append("Conversation context:")
        for previous_user, previous_assistant in history_snapshot:
            lines.append(f"User: {previous_user}")
            lines.append(f"DAVE: {previous_assistant}")

        if not lines:
            return user_text

        lines.append(f"User: {user_text}")
        lines.append("DAVE:")
        return "\n".join(lines)

    def _fetch_dynamic_bootstrap_examples(self, user_text: str) -> list[tuple[str, str]]:
        manager = self.memory_manager
        if manager is None or self.memory_example_limit <= 0:
            return []
        try:
            return manager.get_bootstrap_examples(
                user_input=user_text,
                limit=self.memory_example_limit,
                min_similarity=self.memory_min_similarity,
            )
        except Exception as exc:
            self._logger.debug("Memory retrieval failed: %s", exc)
            return []

    @staticmethod
    def _compose_intent_router_input(
        user_text: str,
        memory_examples: list[tuple[str, str]],
    ) -> str:
        if not memory_examples:
            return user_text

        lines: list[str] = ["Successful command memories:"]
        for sample_user, sample_assistant in memory_examples:
            lines.append(f"Past user: {sample_user}")
            lines.append(f"Past resolution: {sample_assistant}")
        lines.append(f"Current user: {user_text}")
        return "\n".join(lines)

    @staticmethod
    def _build_cache_key(mode: str, system_prompt: str, input_text: str) -> str:
        hasher = hashlib.sha256()
        hasher.update(mode.encode("utf-8", errors="ignore"))
        hasher.update(b"\x1f")
        hasher.update(system_prompt.encode("utf-8", errors="ignore"))
        hasher.update(b"\x1f")
        hasher.update(input_text.encode("utf-8", errors="ignore"))
        return hasher.hexdigest()

    def _cache_get(self, key: str) -> tuple[Any, str, float] | None:
        if not self.response_cache_enabled:
            return None
        now = time.time()
        with self._lock:
            entry = self._response_cache.get(key)
            if not isinstance(entry, dict):
                return None
            expires_at = float(entry.get("expires_at", 0.0) or 0.0)
            if expires_at <= now:
                self._response_cache.pop(key, None)
                return None
            self._response_cache.move_to_end(key)
            provider = str(entry.get("provider", "cache") or "cache")
            created_at = float(entry.get("created_at", now) or now)
            value = entry.get("value")
            if isinstance(value, dict):
                value = dict(value)
            return value, provider, max(0.0, now - created_at)

    def _cache_set(self, key: str, value: Any, *, provider: str) -> None:
        if not self.response_cache_enabled:
            return
        if not isinstance(value, (str, dict)):
            return
        now = time.time()
        stored_value: Any = dict(value) if isinstance(value, dict) else str(value)
        entry = {
            "value": stored_value,
            "provider": provider or "cache",
            "created_at": now,
            "expires_at": now + self.response_cache_ttl_seconds,
        }
        with self._lock:
            self._response_cache[key] = entry
            self._response_cache.move_to_end(key)
            while len(self._response_cache) > self.response_cache_max_entries:
                self._response_cache.popitem(last=False)

    def _set_health(self, status: str, provider: str, reason: str, detail: str) -> None:
        with self._lock:
            self._last_health = {
                "status": status,
                "provider": provider,
                "reason": reason,
                "detail": detail,
                "updated_at": time.time(),
            }

    def _set_provider(self, provider: str) -> None:
        with self._lock:
            self.provider = provider

    def _build_provider_sequence(self) -> list[str]:
        with self._lock:
            ordered = list(self.provider_order)
            metrics_snapshot = {
                name: dict(values) for name, values in self._provider_metrics.items()
            }
            circuit_snapshot = {
                name: dict(values) for name, values in self._provider_circuit.items()
            }
        if not self.dynamic_provider_selection:
            return ordered

        score_ready = any(
            (int(entry.get("successes", 0)) + int(entry.get("failures", 0)))
            >= self.provider_sample_threshold
            for entry in metrics_snapshot.values()
        )
        if not score_ready:
            return ordered

        base_index = {provider: index for index, provider in enumerate(ordered)}
        ordered.sort(
            key=lambda provider: (
                self._is_provider_circuit_open_snapshot(circuit_snapshot, provider),
                -self._provider_score_from_metric(metrics_snapshot.get(provider, {})),
                self._provider_latency_from_metric(metrics_snapshot.get(provider, {})),
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
        with self._lock:
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
                metric["avg_latency"] = (float(previous_latency) * 0.72) + (latency * 0.28)
            metric["last_used_at"] = time.time()
            self._update_provider_circuit_locked(
                provider=provider,
                success=success,
                error_text=error_text,
            )
            circuit_state = self._provider_circuit.get(provider, {})
            metric["circuit_open_until"] = float(circuit_state.get("open_until", 0.0) or 0.0)

    def _update_provider_circuit(self, provider: str, success: bool, error_text: str) -> None:
        with self._lock:
            self._update_provider_circuit_locked(
                provider=provider,
                success=success,
                error_text=error_text,
            )

    def _update_provider_circuit_locked(self, provider: str, success: bool, error_text: str) -> None:
        circuit = self._provider_circuit.get(provider)
        if circuit is None:
            circuit = {
                "state": "closed",
                "consecutive_failures": 0,
                "open_until": 0.0,
                "last_trip_reason": "",
                "half_open_in_flight": False,
            }
            self._provider_circuit[provider] = circuit

        state = str(circuit.get("state", "closed") or "closed").lower()
        if success:
            circuit["state"] = "closed"
            circuit["consecutive_failures"] = 0
            circuit["open_until"] = 0.0
            circuit["last_trip_reason"] = ""
            circuit["half_open_in_flight"] = False
            return

        if state == "half_open":
            circuit["state"] = "open"
            circuit["consecutive_failures"] = max(
                int(circuit.get("consecutive_failures", 0)) + 1,
                self.circuit_breaker_failure_threshold,
            )
            circuit["open_until"] = time.time() + self.circuit_breaker_cooldown_seconds
            circuit["last_trip_reason"] = error_text
            circuit["half_open_in_flight"] = False
            return

        circuit["consecutive_failures"] = int(circuit.get("consecutive_failures", 0)) + 1
        circuit["half_open_in_flight"] = False
        if not self.circuit_breaker_enabled:
            circuit["state"] = "closed"
            circuit["open_until"] = 0.0
            return
        if int(circuit["consecutive_failures"]) < self.circuit_breaker_failure_threshold:
            circuit["state"] = "closed"
            return

        circuit["state"] = "open"
        circuit["open_until"] = time.time() + self.circuit_breaker_cooldown_seconds
        circuit["last_trip_reason"] = error_text

    def _reserve_provider_attempt(self, provider: str) -> tuple[bool, str, bool]:
        if not self.circuit_breaker_enabled:
            return True, "", False

        now = time.time()
        with self._lock:
            circuit = self._provider_circuit.get(provider)
            if circuit is None:
                circuit = {
                    "state": "closed",
                    "consecutive_failures": 0,
                    "open_until": 0.0,
                    "last_trip_reason": "",
                    "half_open_in_flight": False,
                }
                self._provider_circuit[provider] = circuit

            state = str(circuit.get("state", "closed") or "closed").lower()
            open_until = float(circuit.get("open_until", 0.0) or 0.0)
            in_flight = bool(circuit.get("half_open_in_flight", False))

            if state == "open":
                remaining = max(0.0, open_until - now)
                if remaining > 0:
                    return False, f"cooldown {remaining:.1f}s", False
                circuit["state"] = "half_open"
                circuit["half_open_in_flight"] = True
                circuit["open_until"] = 0.0
                return True, "half_open_trial", True

            if state == "half_open":
                if in_flight:
                    return False, "half-open trial in progress", False
                circuit["half_open_in_flight"] = True
                return True, "half_open_trial", True

            circuit["state"] = "closed"
            circuit["half_open_in_flight"] = False
            return True, "", False

    def _provider_circuit_remaining(self, provider: str) -> float:
        if not self.circuit_breaker_enabled:
            return 0.0
        with self._lock:
            circuit = self._provider_circuit.get(provider, {})
            open_until = float(circuit.get("open_until", 0.0) or 0.0)
            state = str(circuit.get("state", "closed") or "closed").lower()
        if state != "open":
            return 0.0
        return max(0.0, open_until - time.time())

    def _is_provider_circuit_open(self, provider: str) -> bool:
        return self._provider_circuit_remaining(provider) > 0.0

    def _provider_score(self, provider: str) -> float:
        with self._lock:
            metric = dict(self._provider_metrics.get(provider, {}))
        return self._provider_score_from_metric(metric)

    def _provider_latency(self, provider: str) -> float:
        with self._lock:
            metric = dict(self._provider_metrics.get(provider, {}))
        return self._provider_latency_from_metric(metric)

    @staticmethod
    def _is_provider_circuit_open_snapshot(
        circuits: dict[str, dict[str, Any]],
        provider: str,
    ) -> bool:
        entry = circuits.get(provider, {})
        state = str(entry.get("state", "closed") or "closed").lower()
        if state != "open":
            return False
        open_until = float(entry.get("open_until", 0.0) or 0.0)
        return open_until > time.time()

    @staticmethod
    def _provider_score_from_metric(metric: dict[str, Any]) -> float:
        successes = int(metric.get("successes", 0))
        failures = int(metric.get("failures", 0))
        total = successes + failures
        if total <= 0:
            return 0.5
        return (successes + 1.0) / (total + 2.0)

    @staticmethod
    def _provider_latency_from_metric(metric: dict[str, Any]) -> float:
        latency = metric.get("avg_latency")
        if isinstance(latency, (float, int)):
            return max(0.0, float(latency))
        return 999.0

    @staticmethod
    def _extract_result_text(result: Any) -> str | None:
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
        return None

    @staticmethod
    def _normalize_intent_payload(payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        raw_intent = str(payload.get("intent", "")).strip().lower()
        if raw_intent not in INTENT_ALLOWED_VALUES:
            return None

        def _string_field(value: Any) -> str:
            if isinstance(value, str):
                return value.strip()
            return ""

        confidence_raw = payload.get("confidence")
        if isinstance(confidence_raw, (int, float)):
            confidence = max(0.0, min(1.0, float(confidence_raw)))
        elif isinstance(confidence_raw, str):
            try:
                confidence = max(0.0, min(1.0, float(confidence_raw.strip())))
            except Exception:
                confidence = 0.0
        else:
            confidence = 0.0

        shell_mode_raw = _string_field(payload.get("shell_mode")).lower()
        shell_mode = "cmd" if shell_mode_raw == "cmd" else "powershell"
        return {
            "intent": raw_intent,
            "target": _string_field(payload.get("target")),
            "query": _string_field(payload.get("query")),
            "command": _string_field(payload.get("command")),
            "shell_mode": shell_mode,
            "reply": _string_field(payload.get("reply")),
            "confidence": confidence,
        }

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
