from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable

import psutil

from app.modules.automation_engine import AutomationEngine
from app.modules.llm_interface import CLOUD_OFFLINE_MESSAGE, LLMClient
from app.modules.memory_manager import MemoryManager
from app.modules.predictive_engine import HabitTracker, Predictor
from app.modules.workflow_engine import WorkflowEngine

DEFAULT_BLOCKED_SHELL_PATTERNS: tuple[str, ...] = (
    r"\bshutdown(?:\.exe)?\b",
    r"\brestart(?:-computer)?\b",
    r"\bformat(?:\.com)?\b",
    r"\bdiskpart\b",
    r"\bbcdedit\b",
    r"\breg(?:\.exe)?\s+delete\b",
    r"\bcipher(?:\.exe)?\s+/w\b",
)
DEFAULT_CONFIRM_SHELL_PATTERNS: tuple[str, ...] = (
    r"\b(?:remove-item|rm|del|erase)\b",
    r"\b(?:stop-process|taskkill)\b",
    r"\b(?:sc(?:\.exe)?\s+stop|net\s+stop)\b",
    r"\bset-executionpolicy\b",
)


class Brain:
    def __init__(
        self,
        gui_callback: Callable[[str], None] | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.gui_callback = gui_callback
        self.config = config if isinstance(config, dict) else {}
        self._logger = logging.getLogger(__name__)

        self.verbose_mode = True
        self.system_state = "NORMAL"
        self._pending_critical_action: str | None = None
        self._pending_critical_action_since: float | None = None
        self._pending_shell_command: str | None = None
        self._pending_shell_mode: str = "powershell"
        self._pending_shell_since: float | None = None
        self._workflow_depth = 0

        memory_cfg_raw = self.config.get("memory", {})
        memory_cfg = memory_cfg_raw if isinstance(memory_cfg_raw, dict) else {}
        self.memory_enabled = self._coerce_bool(memory_cfg.get("enabled", True), True)
        self.memory_manager: MemoryManager | None = None
        if self.memory_enabled:
            try:
                self.memory_manager = MemoryManager()
            except Exception as exc:
                self._logger.warning("Memory manager unavailable: %s", exc)

        predictive_cfg_raw = self.config.get("predictive", {})
        predictive_cfg = predictive_cfg_raw if isinstance(predictive_cfg_raw, dict) else {}
        self.predictive_enabled = self._coerce_bool(predictive_cfg.get("enabled", True), True)
        self.predictive_confidence_threshold = max(
            0.0,
            min(
                1.0,
                self._coerce_float(
                    predictive_cfg.get("confidence_threshold", 0.85),
                    0.85,
                ),
            ),
        )
        self.predictive_idle_poll_seconds = max(
            10.0,
            self._coerce_float(predictive_cfg.get("idle_poll_seconds", 45.0), 45.0),
        )
        self.predictive_suggestion_cooldown_seconds = max(
            30.0,
            self._coerce_float(
                predictive_cfg.get("suggestion_cooldown_seconds", 240.0),
                240.0,
            ),
        )
        self.predictive_train_interval_seconds = max(
            3600.0,
            self._coerce_float(
                predictive_cfg.get("train_interval_seconds", 86400.0),
                86400.0,
            ),
        )
        self.predictive_min_training_samples = max(
            5,
            self._coerce_int(
                predictive_cfg.get("min_training_samples", 25),
                25,
            ),
        )
        self.habit_tracker: HabitTracker | None = None
        self.predictor: Predictor | None = None
        self._last_successful_intent = "none"
        self._last_suggestion_intent = ""
        self._last_suggestion_at = 0.0
        self._predictive_thread: threading.Thread | None = None
        self._predictive_stop_event = threading.Event()
        if self.predictive_enabled:
            try:
                self.habit_tracker = HabitTracker()
                self.predictor = Predictor(
                    habit_tracker=self.habit_tracker,
                    confidence_threshold=self.predictive_confidence_threshold,
                    min_training_samples=self.predictive_min_training_samples,
                    train_interval_seconds=self.predictive_train_interval_seconds,
                    auto_start=True,
                )
                if self.predictor.enabled:
                    self._start_predictive_idle_loop()
                else:
                    self._logger.warning(
                        "Predictive engine inactive; scikit-learn unavailable."
                    )
            except Exception as exc:
                self._logger.warning("Predictive engine unavailable: %s", exc)

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
        policy_raw = self.config.get("policy", {})
        policy_cfg = policy_raw if isinstance(policy_raw, dict) else {}
        self.policy_enabled = self._coerce_bool(policy_cfg.get("enabled", True), True)
        self.confirmation_timeout_seconds = max(
            10.0,
            self._coerce_float(
                policy_cfg.get("pending_confirmation_timeout_seconds", 30.0),
                30.0,
            ),
        )
        self.shell_max_command_length = max(
            80,
            self._coerce_int(policy_cfg.get("shell_max_command_length", 900), 900),
        )
        self.shell_block_patterns = self._compile_regex_list(
            policy_cfg.get("blocked_shell_patterns"),
            DEFAULT_BLOCKED_SHELL_PATTERNS,
        )
        self.shell_confirm_patterns = self._compile_regex_list(
            policy_cfg.get("confirm_shell_patterns"),
            DEFAULT_CONFIRM_SHELL_PATTERNS,
        )
        workflow_cfg_raw = self.config.get("workflows", {})
        workflow_cfg = workflow_cfg_raw if isinstance(workflow_cfg_raw, dict) else {}
        self.workflow_engine = WorkflowEngine(workflow_cfg)
        self.workflow_max_depth = max(
            1,
            min(3, self._coerce_int(workflow_cfg.get("max_depth", 1), 1)),
        )
        self.workflow_stop_on_error = self._coerce_bool(
            workflow_cfg.get("stop_on_error", True),
            True,
        )

        brain_config_raw = self.config.get("brain", {})
        brain_config = brain_config_raw if isinstance(brain_config_raw, dict) else {}
        self.verbose_mode = self._coerce_bool(brain_config.get("verbose_mode", True), True)

        llm_config_raw = self.config.get("llm", {})
        llm_config = llm_config_raw if isinstance(llm_config_raw, dict) else {}
        self.llm_enabled = self._coerce_bool(llm_config.get("enabled", True), True)
        self.llm_intent_routing_enabled = self._coerce_bool(
            llm_config.get("intent_routing_enabled", True),
            True,
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
            pending_response = self._handle_pending_confirmation(text, normalized)
            if pending_response is not None:
                response = pending_response
                if self.verbose_mode:
                    return self._append_system_stats(response)
                return response

            workflow_response = self._route_workflow_request(text)
            if workflow_response is not None:
                response = workflow_response
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
                    if self._is_llm_offline():
                        fallback_response = self._route_automation_request(text)
                        response = (
                            fallback_response
                            if fallback_response is not None
                            else CLOUD_OFFLINE_MESSAGE
                        )
                    else:
                        response = self.llm.query(text)

        if self.verbose_mode:
            return self._append_system_stats(response)
        return response

    def _route_workflow_request(self, command_text_raw: str) -> str | None:
        command_text = (command_text_raw or "").strip()
        if not command_text:
            return None

        normalized = command_text.lower()
        if normalized in {"list workflows", "show workflows", "workflows"}:
            if not self.workflow_engine.enabled:
                return "Workflow engine is disabled in config."
            names = self.workflow_engine.list_workflows()
            if not names:
                return "No workflows are configured."
            return "Available workflows: " + ", ".join(names)

        workflow_match = re.match(
            r"^(?:please\s+)?(?:run|execute|start)\s+(?:workflow|routine)\s+(.+)$",
            command_text,
            flags=re.IGNORECASE,
        )
        if workflow_match is None:
            workflow_match = re.match(
                r"^(?:workflow|routine)\s+(.+)$",
                command_text,
                flags=re.IGNORECASE,
            )
        if workflow_match is None:
            return None

        if not self.workflow_engine.enabled:
            return "Workflow engine is disabled in config."

        workflow_name = self._clean_workflow_name(workflow_match.group(1))
        if not workflow_name:
            return "Specify a workflow name."
        return self._run_workflow(workflow_name, user_input=command_text)

    def _run_workflow(self, workflow_name: str, *, user_input: str) -> str:
        if self._workflow_depth >= self.workflow_max_depth:
            return "Workflow recursion limit reached. Nested workflow execution is blocked."

        workflow = self.workflow_engine.get_workflow(workflow_name)
        if workflow is None:
            known = self.workflow_engine.list_workflows()
            if known:
                return (
                    f"Workflow '{workflow_name}' was not found. "
                    f"Available workflows: {', '.join(known)}"
                )
            return f"Workflow '{workflow_name}' was not found."

        total_steps = len(workflow.steps)
        if total_steps <= 0:
            return f"Workflow '{workflow.name}' has no runnable steps."

        self._workflow_depth += 1
        started_at = time.perf_counter()
        completed = 0
        failed = 0
        stop_index = total_steps
        try:
            for index, step in enumerate(workflow.steps, start=1):
                self._notify_gui(
                    f"WORKFLOW_STEP:{workflow.name}:{index}:{total_steps}:{step}"
                )
                step_response = self._execute_workflow_step(step)
                if self._is_workflow_step_success(step_response):
                    completed += 1
                    continue
                failed += 1
                stop_index = index
                if self.workflow_stop_on_error:
                    break
        finally:
            self._workflow_depth -= 1

        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        self._notify_gui(
            f"WORKFLOW_DONE:{workflow.name}:{completed}:{failed}:{elapsed_ms}"
        )
        workflow_success = failed == 0 and completed == total_steps
        self._record_command_memory(
            user_input=user_input,
            resolved_intent=self._compact_memory_payload(
                {
                    "intent": "workflow_run",
                    "route": "automation",
                    "target": workflow.name,
                    "steps_total": total_steps,
                    "steps_completed": completed,
                    "steps_failed": failed,
                    "elapsed_ms": elapsed_ms,
                }
            ),
            success=workflow_success,
        )
        if workflow_success:
            return (
                f"Workflow '{workflow.name}' completed successfully "
                f"({completed}/{total_steps} steps) in {elapsed_ms} ms."
            )
        if self.workflow_stop_on_error:
            return (
                f"Workflow '{workflow.name}' stopped at step {stop_index}/{total_steps}. "
                f"Completed {completed}, failed {failed}."
            )
        return (
            f"Workflow '{workflow.name}' finished with issues. "
            f"Completed {completed}/{total_steps}, failed {failed}."
        )

    def _execute_workflow_step(self, command_text: str) -> str:
        workflow_response = self._route_workflow_request(command_text)
        if workflow_response is not None:
            return workflow_response
        automation_response = self._route_automation_request(command_text)
        if automation_response is not None:
            return automation_response
        if not self.llm_enabled:
            return "I'm sorry, DAVE. I'm processing that request."
        llm_intent_response = self._route_llm_intent_request(command_text)
        if llm_intent_response is not None:
            return llm_intent_response
        if self._is_llm_offline():
            fallback_response = self._route_automation_request(command_text)
            return fallback_response if fallback_response is not None else CLOUD_OFFLINE_MESSAGE
        return self.llm.query(command_text)

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
            self._record_command_memory(
                user_input=command_text,
                resolved_intent=self._compact_memory_payload(
                    {
                        "intent": "open_and_search",
                        "route": "automation",
                        "target": app_name,
                        "query": query,
                        "open_success": opened,
                        "search_success": searched,
                    }
                ),
                success=opened or searched,
            )
            if opened and searched:
                return f"Opening {app_name} and searching for {query} on the web."
            if opened and not searched:
                return f"Opened {app_name}, but I could not start the web search."
            if not opened and searched:
                return f"I could not open {app_name}, but I started searching for {query} on the web."
            return f"I could not open {app_name} and search for {query}."

        clock_request = self._extract_clock_request(command_text)
        if clock_request is not None:
            clock_kind, trigger, schedule_value = clock_request
            opened = self.automation.open_clock_page(clock_kind)
            intent_name = "set_timer" if clock_kind == "timer" else "set_alarm"
            if opened:
                payload = {
                    "intent": intent_name,
                    "route": "automation",
                    "target": "ClockApp",
                    "clock_page": clock_kind,
                }
                if trigger:
                    payload["trigger"] = trigger
                if schedule_value:
                    payload["duration" if trigger == "after" else "time"] = schedule_value
                self._record_command_memory(
                    user_input=command_text,
                    resolved_intent=self._compact_memory_payload(payload),
                    success=True,
                )
                if schedule_value:
                    preposition = "for" if trigger == "after" else "at"
                    return f"Opening Clock {clock_kind} {preposition} {schedule_value}, Sir."
                return f"Opening Clock {clock_kind}, Sir."
            return f"I could not open the Clock {clock_kind}."

        shell_request = self._extract_shell_request(command_text)
        if shell_request is not None:
            shell_mode, shell_command = shell_request
            if not self.allow_shell_commands:
                return "Shell execution is disabled in config."
            policy_action, policy_reason = self._evaluate_shell_command_policy(shell_command)
            if policy_action == "block":
                return f"Blocked by safety policy: {policy_reason}"
            if policy_action == "confirm":
                self._set_pending_shell_command(shell_command, shell_mode=shell_mode)
                return (
                    "Guarded shell command requires confirmation. "
                    "Say 'confirm' to execute or 'cancel'."
                )
            shell_response, executed = self._execute_shell_command_with_result(
                shell_command,
                shell_mode=shell_mode,
            )
            self._record_command_memory(
                user_input=command_text,
                resolved_intent=self._compact_memory_payload(
                    {
                        "intent": "run_command",
                        "route": "automation",
                        "command": shell_command,
                        "shell_mode": shell_mode,
                    }
                ),
                success=executed,
            )
            return shell_response

        open_match = re.match(
            r"^(?:please\s+)?(?:open|launch|start)\s+(.+)$",
            command_text,
            flags=re.IGNORECASE,
        )
        if open_match:
            open_target_text = open_match.group(1)
            app_name = self._clean_app_target(open_target_text)
            if not app_name:
                return "Specify an application to open."
            launched = self.automation.open_application(app_name)
            if launched:
                self._record_command_memory(
                    user_input=command_text,
                    resolved_intent=self._compact_memory_payload(
                        {
                            "intent": "open_app",
                            "route": "automation",
                            "target": app_name,
                        }
                    ),
                    success=True,
                )
                return f"Opening {app_name}, Sir."

            file_target = self._clean_file_target(open_target_text)
            if file_target and self.automation.open_file(file_target):
                self._record_command_memory(
                    user_input=command_text,
                    resolved_intent=self._compact_memory_payload(
                        {
                            "intent": "open_file",
                            "route": "automation",
                            "target": file_target,
                        }
                    ),
                    success=True,
                )
                return f"Opening file {file_target}, Sir."
            return f"I could not open {app_name}."

        query = self._extract_search_query(command_text)
        if query:
            opened = self.automation.web_search(query)
            if opened:
                self._record_command_memory(
                    user_input=command_text,
                    resolved_intent=self._compact_memory_payload(
                        {
                            "intent": "web_search",
                            "route": "automation",
                            "query": query,
                        }
                    ),
                    success=True,
                )
                return f"Searching for {query} on the web."
            return "I could not start the web search."

        if self._is_system_control_command(normalized):
            critical_action = self._extract_critical_action(normalized)
            if critical_action is not None:
                self._set_pending_critical_action(critical_action)
                return (
                    f"Safety check: confirm {critical_action} by saying "
                    f"'confirm {critical_action}' or 'cancel'."
                )

            executed = self.automation.system_control(normalized)
            action_label = self._describe_system_action(normalized)
            if executed:
                self._record_command_memory(
                    user_input=command_text,
                    resolved_intent=self._compact_memory_payload(
                        {
                            "intent": "system_control",
                            "route": "automation",
                            "command": normalized,
                        }
                    ),
                    success=True,
                )
                return f"{action_label}, Sir."
            return f"I could not complete {action_label.lower()}."

        return None

    def _route_llm_intent_request(self, command_text: str) -> str | None:
        if not self.llm_intent_routing_enabled:
            return None

        payload = self.llm.route_intent(command_text)
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
                self._record_command_memory(
                    user_input=command_text,
                    resolved_intent=self._compact_memory_payload(
                        {
                            "intent": "open_app",
                            "route": "llm_intent",
                            "target": target,
                            "confidence": confidence,
                        }
                    ),
                    success=True,
                )
                return f"Opening {target}, Sir."

            file_target = self._clean_file_target(target_raw or target)
            if file_target and self.automation.open_file(file_target):
                self._record_command_memory(
                    user_input=command_text,
                    resolved_intent=self._compact_memory_payload(
                        {
                            "intent": "open_file",
                            "route": "llm_intent",
                            "target": file_target,
                            "confidence": confidence,
                        }
                    ),
                    success=True,
                )
                return f"Opening file {file_target}, Sir."
            return f"I could not open {target}."

        if intent in {"web_search", "search"}:
            query_raw = self._first_non_empty(payload.get("query"), payload.get("target"))
            query = self._clean_search_query(query_raw)
            if not query:
                return None
            opened = self.automation.web_search(query)
            if opened:
                self._record_command_memory(
                    user_input=command_text,
                    resolved_intent=self._compact_memory_payload(
                        {
                            "intent": "web_search",
                            "route": "llm_intent",
                            "query": query,
                            "confidence": confidence,
                        }
                    ),
                    success=True,
                )
                return f"Searching for {query} on the web."
            return "I could not start the web search."

        if intent in {"system_control", "system"}:
            command = self._first_non_empty(payload.get("command"), payload.get("target"))
            if not command:
                return None

            normalized = command.lower()
            critical_action = self._extract_critical_action(normalized)
            if critical_action is not None:
                self._set_pending_critical_action(critical_action)
                return (
                    f"Safety check: confirm {critical_action} by saying "
                    f"'confirm {critical_action}' or 'cancel'."
                )

            executed = self.automation.system_control(normalized)
            action_label = self._describe_system_action(normalized)
            if executed:
                self._record_command_memory(
                    user_input=command_text,
                    resolved_intent=self._compact_memory_payload(
                        {
                            "intent": "system_control",
                            "route": "llm_intent",
                            "command": normalized,
                            "confidence": confidence,
                        }
                    ),
                    success=True,
                )
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
            policy_action, policy_reason = self._evaluate_shell_command_policy(raw_shell_command)
            if policy_action == "block":
                return f"Blocked by safety policy: {policy_reason}"
            if policy_action == "confirm":
                self._set_pending_shell_command(raw_shell_command, shell_mode=shell_mode)
                return (
                    "Guarded shell command requires confirmation. "
                    "Say 'confirm' to execute or 'cancel'."
                )
            shell_response, executed = self._execute_shell_command_with_result(
                raw_shell_command,
                shell_mode=shell_mode,
            )
            self._record_command_memory(
                user_input=command_text,
                resolved_intent=self._compact_memory_payload(
                    {
                        "intent": "run_command",
                        "route": "llm_intent",
                        "command": raw_shell_command,
                        "shell_mode": shell_mode,
                        "confidence": confidence,
                    }
                ),
                success=executed,
            )
            return shell_response

        if intent in {"set_timer", "timer"}:
            return self._execute_clock_intent(
                command_text=command_text,
                payload=payload,
                clock_kind="timer",
                confidence=confidence,
            )

        if intent in {"set_alarm", "alarm"}:
            return self._execute_clock_intent(
                command_text=command_text,
                payload=payload,
                clock_kind="alarm",
                confidence=confidence,
            )

        if intent == "chat":
            return self._first_non_empty(payload.get("reply"))

        return None

    def _handle_pending_confirmation(self, raw_text: str, normalized_text: str) -> str | None:
        self._expire_pending_actions()

        if self._pending_shell_command is not None:
            if self._is_cancel_intent(normalized_text):
                self._clear_pending_shell_command()
                return "Guarded shell command cancelled."

            if self._is_confirm_intent(normalized_text, "shell"):
                shell_command = self._pending_shell_command
                shell_mode = self._pending_shell_mode
                self._clear_pending_shell_command()
                shell_response, executed = self._execute_shell_command_with_result(
                    shell_command,
                    shell_mode=shell_mode,
                )
                self._record_command_memory(
                    user_input=raw_text,
                    resolved_intent=self._compact_memory_payload(
                        {
                            "intent": "run_command",
                            "route": "automation",
                            "command": shell_command,
                            "shell_mode": shell_mode,
                        }
                    ),
                    success=executed,
                )
                return (
                    f"Confirmed. {shell_response}"
                    if executed
                    else f"Confirmed, but {shell_response}"
                )

            self._clear_pending_shell_command()
            return None

        if not self._pending_critical_action:
            return None

        pending = self._pending_critical_action
        if self._is_cancel_intent(normalized_text):
            self._clear_pending_critical_action()
            return "Critical action cancelled."

        if self._is_confirm_intent(normalized_text, pending):
            self._clear_pending_critical_action()
            if self.automation.system_control(pending):
                self._record_command_memory(
                    user_input=raw_text,
                    resolved_intent=self._compact_memory_payload(
                        {
                            "intent": "system_control",
                            "route": "automation",
                            "command": pending,
                        }
                    ),
                    success=True,
                )
                if pending == "shutdown":
                    return "Confirmed. Shutting down now."
                return "Confirmed. Restarting now."
            return f"Confirmed, but I could not execute {pending}."

        if self._is_system_control_command(normalized_text):
            next_critical = self._extract_critical_action(normalized_text)
            if next_critical:
                self._set_pending_critical_action(next_critical)
                return (
                    f"Safety check: confirm {next_critical} by saying "
                    f"'confirm {next_critical}' or 'cancel'."
                )

        self._clear_pending_critical_action()
        return None

    def _execute_clock_intent(
        self,
        *,
        command_text: str,
        payload: dict[str, Any],
        clock_kind: str,
        confidence: float | None,
    ) -> str:
        schedule_source = self._first_non_empty(
            payload.get("command"),
            payload.get("target"),
            payload.get("query"),
        )
        parsed = self._extract_clock_request(schedule_source or command_text)
        trigger = ""
        schedule_value: str | None = None
        if parsed is not None:
            _, trigger, schedule_value = parsed

        opened = self.automation.open_clock_page(clock_kind)
        intent_name = "set_timer" if clock_kind == "timer" else "set_alarm"
        if opened:
            record = {
                "intent": intent_name,
                "route": "llm_intent",
                "target": "ClockApp",
                "clock_page": clock_kind,
                "confidence": confidence,
            }
            if trigger:
                record["trigger"] = trigger
            if schedule_value:
                record["duration" if trigger == "after" else "time"] = schedule_value
            self._record_command_memory(
                user_input=command_text,
                resolved_intent=self._compact_memory_payload(record),
                success=True,
            )
            if schedule_value:
                preposition = "for" if trigger == "after" else "at"
                return f"Opening Clock {clock_kind} {preposition} {schedule_value}, Sir."
            return f"Opening Clock {clock_kind}, Sir."
        return f"I could not open the Clock {clock_kind}."

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
        response, _ = self._execute_shell_command_with_result(command, shell_mode=shell_mode)
        return response

    def _execute_shell_command_with_result(
        self,
        command: str,
        shell_mode: str = "powershell",
    ) -> tuple[str, bool]:
        mode = "cmd" if shell_mode == "cmd" else "powershell"
        ok, stdout, stderr, return_code = self.automation.run_shell_command(
            command,
            shell_mode=mode,
            timeout_seconds=self.shell_timeout_seconds,
        )

        if ok:
            if stdout:
                rendered = self._truncate_text(stdout, self.shell_output_limit)
                return f"Command executed ({mode}). Output: {rendered}", True
            return f"Command executed ({mode}).", True

        return_code_text = f" (code {return_code})" if return_code is not None else ""
        if stderr:
            rendered_error = self._truncate_text(stderr, self.shell_output_limit)
            return f"Command failed{return_code_text}: {rendered_error}", False
        return f"Command failed{return_code_text}.", False

    def _set_pending_critical_action(self, action: str) -> None:
        self._clear_pending_shell_command()
        self._pending_critical_action = action
        self._pending_critical_action_since = time.monotonic()

    def _clear_pending_critical_action(self) -> None:
        self._pending_critical_action = None
        self._pending_critical_action_since = None

    def _set_pending_shell_command(self, command: str, *, shell_mode: str) -> None:
        self._clear_pending_critical_action()
        self._pending_shell_command = (command or "").strip()
        self._pending_shell_mode = "cmd" if shell_mode == "cmd" else "powershell"
        self._pending_shell_since = time.monotonic()

    def _clear_pending_shell_command(self) -> None:
        self._pending_shell_command = None
        self._pending_shell_mode = "powershell"
        self._pending_shell_since = None

    def _expire_pending_actions(self) -> None:
        now = time.monotonic()
        if self._pending_critical_action_since is not None:
            if (now - self._pending_critical_action_since) > self.confirmation_timeout_seconds:
                self._clear_pending_critical_action()
        if self._pending_shell_since is not None:
            if (now - self._pending_shell_since) > self.confirmation_timeout_seconds:
                self._clear_pending_shell_command()

    def _evaluate_shell_command_policy(self, command: str) -> tuple[str, str]:
        clean = (command or "").strip()
        if not clean:
            return "block", "empty shell command."
        if not self.policy_enabled:
            return "allow", ""
        if len(clean) > self.shell_max_command_length:
            return (
                "block",
                f"command length exceeds {self.shell_max_command_length} characters.",
            )
        normalized = " ".join(clean.lower().split())
        for pattern in self.shell_block_patterns:
            if pattern.search(normalized):
                return "block", "command matches a restricted shell pattern."
        for pattern in self.shell_confirm_patterns:
            if pattern.search(normalized):
                return "confirm", "high-risk shell command detected."
        return "allow", ""

    @staticmethod
    def _is_workflow_step_success(response_text: str) -> bool:
        text = (response_text or "").strip()
        if not text:
            return False
        lowered = text.lower()
        if lowered.startswith("safety check:"):
            return False
        if "blocked by safety policy" in lowered:
            return False
        if lowered.startswith("command failed"):
            return False
        if "i could not" in lowered:
            return False
        if "disabled in config" in lowered:
            return False
        if lowered.startswith("critical action cancelled"):
            return False
        if lowered == CLOUD_OFFLINE_MESSAGE.lower():
            return False
        return True

    @staticmethod
    def _clean_workflow_name(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        text = text.strip().strip("\"'")
        text = re.sub(r"[.!?]+$", "", text).strip()
        lowered = text.lower()
        for suffix in (" workflow", " routine", " please", " now"):
            if lowered.endswith(suffix):
                text = text[: -len(suffix)].strip()
                lowered = text.lower()
        return text or None

    @staticmethod
    def _compile_regex_list(raw_values: Any, defaults: tuple[str, ...]) -> list[re.Pattern[str]]:
        source = raw_values if isinstance(raw_values, list) else list(defaults)
        compiled: list[re.Pattern[str]] = []
        for item in source:
            if not isinstance(item, str):
                continue
            pattern_text = item.strip()
            if not pattern_text:
                continue
            try:
                compiled.append(re.compile(pattern_text, flags=re.IGNORECASE))
            except Exception:
                continue
        return compiled

    @staticmethod
    def _truncate_text(text: str, limit: int) -> str:
        clean = (text or "").strip()
        if len(clean) <= limit:
            return clean
        return clean[:limit].rstrip() + "...(truncated)"

    @staticmethod
    def _compact_memory_payload(payload: dict[str, Any]) -> dict[str, Any]:
        compact: dict[str, Any] = {}
        for key, value in payload.items():
            if value is None:
                continue
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    continue
                compact[key] = text
                continue
            compact[key] = value
        return compact

    def _record_command_memory(
        self,
        *,
        user_input: str,
        resolved_intent: dict[str, Any],
        success: bool,
    ) -> None:
        if not success:
            return
        manager = self.memory_manager
        if manager is not None:
            try:
                manager.log_command(
                    user_input=user_input,
                    resolved_intent=resolved_intent,
                    success=True,
                )
            except Exception as exc:
                self._logger.debug("Memory logging skipped due to error: %s", exc)
        self._record_predictive_habit(resolved_intent)

    def _record_predictive_habit(self, resolved_intent: dict[str, Any]) -> None:
        tracker = self.habit_tracker
        if tracker is None:
            return

        target_intent = self._derive_target_intent(resolved_intent)
        if not target_intent:
            return

        try:
            tracker.log_execution(
                target_intent=target_intent,
                last_command_intent=self._last_successful_intent,
            )
            self._last_successful_intent = target_intent
        except Exception as exc:
            self._logger.debug("Predictive logging failed: %s", exc)

    def _derive_target_intent(self, resolved_intent: dict[str, Any]) -> str:
        if not isinstance(resolved_intent, dict):
            return ""

        intent = str(resolved_intent.get("intent", "")).strip().lower()
        target = str(resolved_intent.get("target", "")).strip().lower()
        command = str(resolved_intent.get("command", "")).strip().lower()
        if intent in {"open_app", "open_and_search"} and target:
            return f"{intent}:{target}"
        if intent == "open_file" and target:
            return f"open_file:{target}"
        if intent == "workflow_run" and target:
            return f"workflow_run:{target}"
        if intent == "system_control" and command:
            return f"system_control:{command}"
        if intent == "run_command" and command:
            head = command.split(maxsplit=1)[0]
            return f"run_command:{head}"
        return intent

    def _start_predictive_idle_loop(self) -> None:
        if self._predictive_thread is not None and self._predictive_thread.is_alive():
            return
        self._predictive_thread = threading.Thread(
            target=self._predictive_idle_loop,
            name="dave-predictive-idle",
            daemon=True,
        )
        self._predictive_thread.start()

    def _predictive_idle_loop(self) -> None:
        while not self._predictive_stop_event.wait(self.predictive_idle_poll_seconds):
            if self.system_state != "NORMAL":
                continue
            if self._pending_critical_action is not None:
                continue
            if self._pending_shell_command is not None:
                continue

            predictor = self.predictor
            tracker = self.habit_tracker
            if predictor is None or tracker is None:
                continue

            try:
                current_context = tracker.build_context(
                    last_command_intent=self._last_successful_intent,
                )
                suggestion = predictor.predict_next_action(current_context)
            except Exception as exc:
                self._logger.debug("Predictive suggestion loop failed: %s", exc)
                continue

            if not suggestion:
                continue

            now = time.monotonic()
            if (now - self._last_suggestion_at) < self.predictive_suggestion_cooldown_seconds:
                continue
            if suggestion == self._last_suggestion_intent:
                continue

            self._last_suggestion_at = now
            self._last_suggestion_intent = suggestion
            self._notify_gui(f"PREDICT_SUGGEST:{suggestion}")

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
    def _clean_file_target(target: Any) -> str | None:
        if not isinstance(target, str):
            return None
        value = target.strip()
        if not value:
            return None

        quoted_match = re.search(r'"([^"]+)"', value)
        if quoted_match:
            value = quoted_match.group(1).strip()
        else:
            single_quoted_match = re.search(r"'([^']+)'", value)
            if single_quoted_match:
                value = single_quoted_match.group(1).strip()
            else:
                marker_match = re.search(
                    r"\b(?:named|naming|called)\b\s+(.+)$",
                    value,
                    flags=re.IGNORECASE,
                )
                if marker_match:
                    value = marker_match.group(1).strip()

        value = value.strip().strip("\"'")
        value = re.sub(r"[.!?]+$", "", value).strip()
        if not value:
            return None
        return value

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
    def _extract_clock_request(command_text_raw: str) -> tuple[str, str, str | None] | None:
        text = (command_text_raw or "").strip()
        if not text:
            return None
        lowered = text.lower()
        if "timer" not in lowered and "alarm" not in lowered:
            return None

        command_like = (
            bool(re.search(r"\b(?:set|start|create|begin|schedule|add)\b", lowered))
            or lowered.startswith("timer")
            or lowered.startswith("alarm")
        )
        if not command_like:
            return None

        clock_kind = "timer" if "timer" in lowered else "alarm"
        relative = Brain._extract_relative_time_phrase(text)
        if relative is not None:
            return clock_kind, "after", relative
        absolute = Brain._extract_absolute_time_phrase(text)
        if absolute is not None:
            return clock_kind, "at", absolute
        return clock_kind, "", None

    @staticmethod
    def _extract_relative_time_phrase(text: str) -> str | None:
        lowered = text.lower()
        with_unit = re.search(
            r"\b(?:for|after|in|within|till|til|until)?\s*(\d{1,3})\s*"
            r"(seconds?|secs?|minutes?|mins?|hours?|hrs?|hr|h)\b",
            lowered,
        )
        if with_unit:
            amount = with_unit.group(1)
            unit = with_unit.group(2)
            if unit.startswith("sec"):
                normalized_unit = "sec"
            elif unit.startswith("hour") or unit.startswith("hr") or unit == "h":
                normalized_unit = "hour"
            else:
                normalized_unit = "min"
            if amount != "1":
                if normalized_unit == "sec":
                    normalized_unit = "secs"
                elif normalized_unit == "hour":
                    normalized_unit = "hours"
                else:
                    normalized_unit = "mins"
            return f"{amount} {normalized_unit}"

        bare = re.search(r"\b(?:for|after|in|within|till|til|until)\s+(\d{1,3})\b", lowered)
        if bare:
            return f"{bare.group(1)} mins"
        return None

    @staticmethod
    def _extract_absolute_time_phrase(text: str) -> str | None:
        lowered = text.lower()
        exact = re.search(r"\bat\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", lowered)
        if exact:
            return exact.group(1).strip()
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

    def _is_llm_offline(self) -> bool:
        health = self.llm.get_health()
        return str(health.get("status", "")).strip().lower() == "offline"

    def shutdown(self) -> None:
        self._predictive_stop_event.set()
        predictor = self.predictor
        if predictor is not None:
            try:
                predictor.stop()
            except Exception:
                pass
        tracker = self.habit_tracker
        if tracker is not None:
            try:
                tracker.close()
            except Exception:
                pass
        memory = self.memory_manager
        if memory is not None:
            try:
                memory.close()
            except Exception:
                pass

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
