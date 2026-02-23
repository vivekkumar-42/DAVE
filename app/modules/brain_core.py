from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

import psutil

from app.modules.automation_engine import AutomationEngine
from app.modules.llm_interface import CLOUD_OFFLINE_MESSAGE, LLMClient


class Brain:
    def __init__(
        self,
        gui_callback: Callable[[str], None] | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.gui_callback = gui_callback
        self.config = config if isinstance(config, dict) else {}

        self.verbose_mode = True
        self.system_state = "NORMAL"
        self._pending_critical_action: str | None = None
        self._pending_critical_action_since: float | None = None

        self.automation = AutomationEngine()
        automation_cfg_raw = self.config.get("automation", {})
        automation_cfg = automation_cfg_raw if isinstance(automation_cfg_raw, dict) else {}
        self.allow_shell_commands = self._coerce_bool(
            automation_cfg.get("allow_shell_commands", True),
            True,
        )
        self.shell_timeout_seconds = max(
            5.0,
            self._coerce_float(automation_cfg.get("shell_timeout_seconds", 45), 45.0),
        )
        self.shell_output_limit = max(
            180,
            self._coerce_int(automation_cfg.get("shell_output_limit", 900), 900),
        )

        brain_config_raw = self.config.get("brain", {})
        brain_config = brain_config_raw if isinstance(brain_config_raw, dict) else {}
        self.verbose_mode = self._coerce_bool(brain_config.get("verbose_mode", True), True)

        llm_config_raw = self.config.get("llm", {})
        llm_config = llm_config_raw if isinstance(llm_config_raw, dict) else {}
        self.llm_enabled = self._coerce_bool(llm_config.get("enabled", True), True)
        self.llm_intent_routing_enabled = self._coerce_bool(
            llm_config.get("intent_routing_enabled", False),
            False,
        )
        self.llm_intent_min_confidence = max(
            0.0,
            min(
                1.0,
                self._coerce_float(llm_config.get("intent_min_confidence", 0.58), 0.58),
            ),
        )
        system_prompt = self._resolve_system_prompt(llm_config=llm_config)
        self.llm = LLMClient(
            system_prompt=system_prompt,
            max_history_turns=max(
                1,
                self._coerce_int(llm_config.get("history_turns", 5), 5),
            ),
            timeout_seconds=max(
                5.0,
                self._coerce_float(llm_config.get("timeout_seconds", 30), 30.0),
            ),
            config=self.config,
        )

    def process_command(self, text: str) -> str:
        normalized = (text or "").strip().lower()

        if "code red" in normalized or "danger" in normalized:
            self.system_state = "TACTICAL"
            self._notify_gui("ALERT_ON")
            response = (
                "Alert confirmed, DAVE. Shields up. "
                "Systems switched to tactical mode."
            )
        elif "stand down" in normalized or "relax" in normalized:
            self.system_state = "NORMAL"
            self._notify_gui("ALERT_OFF")
            response = (
                "Disengaging combat protocols. "
                "Returning to standard configuration."
            )
        else:
            pending_response = self._handle_pending_confirmation(normalized)
            if pending_response is not None:
                response = pending_response
                if self.verbose_mode:
                    return self._append_system_stats(response)
                return response

            automation_response = self._route_automation_request(text)
            if automation_response is not None:
                response = automation_response
            elif not self.llm_enabled:
                response = "I'm sorry, DAVE. I'm processing that request."
            else:
                llm_intent_response = self._route_llm_intent_request(text)
                if llm_intent_response is not None:
                    response = llm_intent_response
                else:
                    response = self.llm.query(text)

        if self.verbose_mode:
            return self._append_system_stats(response)
        return response

    def _route_automation_request(self, command_text_raw: str) -> str | None:
        command_text = (command_text_raw or "").strip()
        normalized = command_text.lower()
        if not command_text:
            return None

        chained_request = self._extract_open_and_search_request(command_text)
        if chained_request is not None:
            app_name, query = chained_request
            opened = self.automation.open_application(app_name)
            searched = self.automation.web_search(query)
            if opened and searched:
                return f"Opening {app_name} and searching for {query} on the web."
            if opened and not searched:
                return f"Opened {app_name}, but I could not start the web search."
            if not opened and searched:
                return f"I could not open {app_name}, but I started searching for {query} on the web."
            return f"I could not open {app_name} and search for {query}."

        shell_request = self._extract_shell_request(command_text)
        if shell_request is not None:
            shell_mode, shell_command = shell_request
            if not self.allow_shell_commands:
                return "Shell execution is disabled in config."
            return self._execute_shell_command(shell_command, shell_mode=shell_mode)

        open_match = re.match(
            r"^(?:please\s+)?(?:open|launch|start)\s+(.+)$",
            command_text,
            flags=re.IGNORECASE,
        )
        if open_match:
            app_name = self._clean_app_target(open_match.group(1))
            if not app_name:
                return "Specify an application to open."
            launched = self.automation.open_application(app_name)
            if launched:
                return f"Opening {app_name}, Sir."
            return f"I could not open {app_name}."

        query = self._extract_search_query(command_text)
        if query:
            opened = self.automation.web_search(query)
            if opened:
                return f"Searching for {query} on the web."
            return "I could not start the web search."

        if self._is_system_control_command(normalized):
            critical_action = self._extract_critical_action(normalized)
            if critical_action is not None:
                self._pending_critical_action = critical_action
                self._pending_critical_action_since = time.monotonic()
                return (
                    f"Safety check: confirm {critical_action} by saying "
                    f"'confirm {critical_action}' or 'cancel'."
                )

            executed = self.automation.system_control(normalized)
            action_label = self._describe_system_action(normalized)
            if executed:
                return f"{action_label}, Sir."
            return f"I could not complete {action_label.lower()}."

        return None

    def _route_llm_intent_request(self, command_text: str) -> str | None:
        if not self.llm_intent_routing_enabled:
            return None

        intent_prompt = (
            "You are an intent router for a Windows desktop assistant. "
            "Return ONLY valid JSON with keys: "
            "intent, target, query, command, shell_mode, reply, confidence. "
            "Allowed intents: open_app, web_search, system_control, run_command, chat. "
            "For open_app set target. For web_search set query. "
            "For system_control set command to one of: shutdown, restart, lock, "
            "volume up, volume down, mute. "
            "For run_command set command to the exact shell command and shell_mode "
            "to either powershell or cmd. "
            "For chat set reply with a concise assistant response. "
            "confidence must be a number from 0 to 1. Do not include markdown."
        )

        raw_result = self.llm.query_ephemeral(command_text, system_prompt=intent_prompt)
        if raw_result == CLOUD_OFFLINE_MESSAGE:
            return raw_result

        payload = self._extract_intent_payload(raw_result)
        if payload is None:
            return None

        confidence = self._coerce_confidence(payload.get("confidence"))
        if confidence is not None and confidence < self.llm_intent_min_confidence:
            return None

        intent = str(payload.get("intent", "")).strip().lower()
        if intent in {"open_app", "open"}:
            target_raw = self._first_non_empty(
                payload.get("target"),
                payload.get("app"),
                payload.get("application"),
            )
            target = self._clean_app_target(target_raw)
            if not target:
                return None
            launched = self.automation.open_application(target)
            if launched:
                return f"Opening {target}, Sir."
            return f"I could not open {target}."

        if intent in {"web_search", "search"}:
            query_raw = self._first_non_empty(payload.get("query"), payload.get("target"))
            query = self._clean_search_query(query_raw)
            if not query:
                return None
            opened = self.automation.web_search(query)
            if opened:
                return f"Searching for {query} on the web."
            return "I could not start the web search."

        if intent in {"system_control", "system"}:
            command = self._first_non_empty(payload.get("command"), payload.get("target"))
            if not command:
                return None

            normalized = command.lower()
            critical_action = self._extract_critical_action(normalized)
            if critical_action is not None:
                self._pending_critical_action = critical_action
                self._pending_critical_action_since = time.monotonic()
                return (
                    f"Safety check: confirm {critical_action} by saying "
                    f"'confirm {critical_action}' or 'cancel'."
                )

            executed = self.automation.system_control(normalized)
            action_label = self._describe_system_action(normalized)
            if executed:
                return f"{action_label}, Sir."
            return f"I could not complete {action_label.lower()}."

        if intent in {"run_command", "shell", "execute"}:
            if not self.allow_shell_commands:
                return "Shell execution is disabled in config."
            raw_shell_command = self._first_non_empty(payload.get("command"), payload.get("target"))
            if not raw_shell_command:
                return None
            shell_mode_value = self._first_non_empty(payload.get("shell_mode")) or "powershell"
            shell_mode = "cmd" if shell_mode_value.lower() == "cmd" else "powershell"
            return self._execute_shell_command(raw_shell_command, shell_mode=shell_mode)

        if intent == "chat":
            return self._first_non_empty(payload.get("reply"))

        return None

    def _handle_pending_confirmation(self, normalized_text: str) -> str | None:
        if not self._pending_critical_action:
            return None

        if self._pending_critical_action_since is not None:
            age = time.monotonic() - self._pending_critical_action_since
            if age > 30:
                self._pending_critical_action = None
                self._pending_critical_action_since = None

        pending = self._pending_critical_action
        if not pending:
            return None

        if self._is_cancel_intent(normalized_text):
            self._pending_critical_action = None
            self._pending_critical_action_since = None
            return "Critical action cancelled."

        if self._is_confirm_intent(normalized_text, pending):
            self._pending_critical_action = None
            self._pending_critical_action_since = None
            if self.automation.system_control(pending):
                if pending == "shutdown":
                    return "Confirmed. Shutting down now."
                return "Confirmed. Restarting now."
            return f"Confirmed, but I could not execute {pending}."

        if self._is_system_control_command(normalized_text):
            next_critical = self._extract_critical_action(normalized_text)
            if next_critical:
                self._pending_critical_action = next_critical
                self._pending_critical_action_since = time.monotonic()
                return (
                    f"Safety check: confirm {next_critical} by saying "
                    f"'confirm {next_critical}' or 'cancel'."
                )

        self._pending_critical_action = None
        self._pending_critical_action_since = None
        return None

    @staticmethod
    def _extract_search_query(command_text: str) -> str | None:
        match = re.search(r"\bsearch for\b", command_text, flags=re.IGNORECASE)
        if not match:
            return None
        query = Brain._clean_search_query(command_text[match.end() :])
        return query if query else None

    @staticmethod
    def _extract_open_and_search_request(command_text: str) -> tuple[str, str] | None:
        match = re.match(
            r"^(?:please\s+)?(?:open|launch|start)\s+(.+?)\s+(?:and|then)\s+(?:search(?:\s+for)?|google)\s+(.+)$",
            command_text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None

        app_name = Brain._clean_app_target(match.group(1))
        query = Brain._clean_search_query(match.group(2))
        if not app_name or not query:
            return None
        return app_name, query

    @staticmethod
    def _extract_shell_request(command_text: str) -> tuple[str, str] | None:
        cleaned = (command_text or "").strip()
        if not cleaned:
            return None

        powershell_match = re.match(
            r"^(?:please\s+)?(?:run|execute)\s+(?:powershell|ps)\s*[:\-]?\s*(.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if powershell_match:
            command = powershell_match.group(1).strip()
            if command:
                return "powershell", command

        cmd_match = re.match(
            r"^(?:please\s+)?(?:run|execute)\s+cmd\s*[:\-]?\s*(.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if cmd_match:
            command = cmd_match.group(1).strip()
            if command:
                return "cmd", command

        generic_match = re.match(
            r"^(?:please\s+)?(?:run|execute)\s+(.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if generic_match:
            command = generic_match.group(1).strip()
            if command:
                return "powershell", command

        explicit_shell_match = re.match(r"^(?:powershell|ps)\s+(.+)$", cleaned, flags=re.IGNORECASE)
        if explicit_shell_match:
            command = explicit_shell_match.group(1).strip()
            if command:
                return "powershell", command

        explicit_cmd_match = re.match(r"^cmd\s+(.+)$", cleaned, flags=re.IGNORECASE)
        if explicit_cmd_match:
            command = explicit_cmd_match.group(1).strip()
            if command:
                return "cmd", command

        if cleaned.startswith("!"):
            command = cleaned[1:].strip()
            if command:
                return "powershell", command

        return None

    def _execute_shell_command(self, command: str, shell_mode: str = "powershell") -> str:
        mode = "cmd" if shell_mode == "cmd" else "powershell"
        ok, stdout, stderr, return_code = self.automation.run_shell_command(
            command,
            shell_mode=mode,
            timeout_seconds=self.shell_timeout_seconds,
        )

        if ok:
            if stdout:
                rendered = self._truncate_text(stdout, self.shell_output_limit)
                return f"Command executed ({mode}). Output: {rendered}"
            return f"Command executed ({mode})."

        return_code_text = f" (code {return_code})" if return_code is not None else ""
        if stderr:
            rendered_error = self._truncate_text(stderr, self.shell_output_limit)
            return f"Command failed{return_code_text}: {rendered_error}"
        return f"Command failed{return_code_text}."

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        clean = (text or "").strip()
        if len(clean) <= limit:
            return clean
        return clean[:limit].rstrip() + "...(truncated)"

    @staticmethod
    def _clean_app_target(target: Any) -> str | None:
        if not isinstance(target, str):
            return None
        value = target.strip()
        if not value:
            return None

        value = re.sub(r"[.!?]+$", "", value).strip()
        trailing_noise = (
            " please",
            " now",
            " for me",
            " thanks",
            " thank you",
            " app",
            " application",
        )
        lowered = value.lower()
        changed = True
        while changed and lowered:
            changed = False
            for suffix in trailing_noise:
                if lowered.endswith(suffix):
                    value = value[: -len(suffix)].strip()
                    lowered = value.lower()
                    changed = True
                    break

        leading_noise = ("a ", "an ", "the ")
        lowered = value.lower()
        for prefix in leading_noise:
            if lowered.startswith(prefix):
                value = value[len(prefix) :].strip()
                break
        return value or None

    @staticmethod
    def _clean_search_query(query: Any) -> str | None:
        if not isinstance(query, str):
            return None
        value = query.strip()
        if not value:
            return None
        value = re.sub(r"[.!?]+$", "", value).strip()
        trailing_noise = (" please", " now", " thanks", " thank you")
        lowered = value.lower()
        changed = True
        while changed and lowered:
            changed = False
            for suffix in trailing_noise:
                if lowered.endswith(suffix):
                    value = value[: -len(suffix)].strip()
                    lowered = value.lower()
                    changed = True
                    break
        return value or None

    @staticmethod
    def _extract_intent_payload(raw_text: str) -> dict[str, Any] | None:
        candidates: list[str] = [raw_text.strip()]

        fenced_matches = re.findall(
            r"```(?:json)?\s*([\s\S]*?)```",
            raw_text,
            flags=re.IGNORECASE,
        )
        for block in fenced_matches:
            cleaned = block.strip()
            if cleaned:
                candidates.append(cleaned)

        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(raw_text[start : end + 1].strip())

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    @staticmethod
    def _coerce_confidence(value: Any) -> float | None:
        if isinstance(value, (int, float)):
            return max(0.0, min(1.0, float(value)))
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return max(0.0, min(1.0, float(text)))
            except Exception:
                return None
        return None

    @staticmethod
    def _is_system_control_command(normalized_text: str) -> bool:
        return any(
            token in normalized_text
            for token in (
                "shutdown",
                "shut down",
                "restart",
                "reboot",
                "lock",
                "volume",
                "volume up",
                "volume down",
                "increase volume",
                "decrease volume",
                "mute",
            )
        )

    @staticmethod
    def _extract_critical_action(normalized_text: str) -> str | None:
        if "shutdown" in normalized_text or "shut down" in normalized_text:
            return "shutdown"
        if "restart" in normalized_text or "reboot" in normalized_text:
            return "restart"
        return None

    @staticmethod
    def _is_confirm_intent(normalized_text: str, action: str) -> bool:
        confirm_tokens = ("confirm", "yes", "proceed", "do it", "execute")
        if any(token in normalized_text for token in confirm_tokens):
            if action in normalized_text:
                return True
            if "shutdown" not in normalized_text and "restart" not in normalized_text:
                return True
        return False

    @staticmethod
    def _is_cancel_intent(normalized_text: str) -> bool:
        return any(token in normalized_text for token in ("cancel", "abort", "stop", "no"))

    @staticmethod
    def _describe_system_action(normalized_text: str) -> str:
        if "lock" in normalized_text:
            return "Locking workstation"
        if "mute" in normalized_text:
            return "Muting audio"
        if "volume up" in normalized_text or "increase volume" in normalized_text:
            return "Increasing volume"
        if "volume down" in normalized_text or "decrease volume" in normalized_text:
            return "Lowering volume"
        if "volume" in normalized_text:
            return "Adjusting volume"
        return "Executing system control"

    def get_llm_health(self) -> dict[str, Any]:
        if not self.llm_enabled:
            return {
                "status": "disabled",
                "provider": self.llm.provider,
                "reason": "llm_disabled",
                "detail": "LLM calls are disabled in config.",
            }
        return self.llm.get_health()

    def _notify_gui(self, state_signal: str) -> None:
        if callable(self.gui_callback):
            self.gui_callback(state_signal)

    def _append_system_stats(self, response: str) -> str:
        battery = psutil.sensors_battery()
        battery_text = "N/A"
        if battery is not None and battery.percent is not None:
            battery_text = f"{int(round(battery.percent))}%"

        ram_text = f"{psutil.virtual_memory().percent:.0f}%"
        return (
            f"{response} | Battery: {battery_text} | RAM: {ram_text} "
            f"| State: [{self.system_state}]"
        )

    def _resolve_system_prompt(self, llm_config: dict[str, Any]) -> str | None:
        prompt = llm_config.get("system_prompt")
        if isinstance(prompt, str) and prompt.strip():
            return prompt.strip()
        prompt_top = self.config.get("system_prompt")
        if isinstance(prompt_top, str) and prompt_top.strip():
            return prompt_top.strip()
        return None

    @staticmethod
    def _first_non_empty(*values: Any) -> str | None:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

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
