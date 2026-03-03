import unittest
from typing import Any

from app.modules.brain_core import Brain
from app.modules.llm_interface import CLOUD_OFFLINE_MESSAGE


class FakeAutomation:
    def __init__(self) -> None:
        self.open_calls: list[str] = []
        self.open_file_calls: list[str] = []
        self.open_clock_calls: list[str] = []
        self.search_calls: list[str] = []
        self.control_calls: list[str] = []
        self.shell_calls: list[tuple[str, str, float]] = []
        self.open_result = True
        self.open_file_result = True
        self.open_clock_result = True
        self.search_result = True
        self.control_result = True
        self.shell_result: tuple[bool, str, str, int | None] = (True, "ok", "", 0)

    def open_application(self, app_name: str) -> bool:
        self.open_calls.append(app_name)
        return self.open_result

    def open_file(self, file_reference: str) -> bool:
        self.open_file_calls.append(file_reference)
        return self.open_file_result

    def open_clock_page(self, page: str) -> bool:
        self.open_clock_calls.append(page)
        return self.open_clock_result

    def web_search(self, query: str) -> bool:
        self.search_calls.append(query)
        return self.search_result

    def system_control(self, command: str) -> bool:
        self.control_calls.append(command)
        return self.control_result

    def run_shell_command(
        self,
        command: str,
        *,
        shell_mode: str = "powershell",
        timeout_seconds: float = 45.0,
    ) -> tuple[bool, str, str, int | None]:
        self.shell_calls.append((command, shell_mode, timeout_seconds))
        return self.shell_result


class FakeLLM:
    def __init__(self) -> None:
        self.provider = "gemini"
        self.query_calls: list[str] = []
        self.route_intent_calls: list[str] = []
        self.query_response = "fallback chat"
        self.route_intent_response: dict[str, Any] | None = {
            "intent": "chat",
            "target": "",
            "query": "",
            "command": "",
            "shell_mode": "powershell",
            "reply": "router chat",
            "confidence": 0.9,
        }
        self.health_status = "online"
        self.health_reason = "ok"
        self.health_detail = ""

    def query(self, user_text: str) -> str:
        self.query_calls.append(user_text)
        return self.query_response

    def route_intent(self, user_text: str) -> dict[str, Any] | None:
        self.route_intent_calls.append(user_text)
        return self.route_intent_response

    def get_health(self) -> dict[str, Any]:
        return {
            "status": self.health_status,
            "provider": "gemini",
            "reason": self.health_reason,
            "detail": self.health_detail,
        }


class FakeMemoryManager:
    def __init__(self) -> None:
        self.logged: list[dict[str, Any]] = []

    def log_command(
        self,
        *,
        user_input: str,
        resolved_intent: dict[str, Any],
        success: bool,
        timestamp: str | None = None,
    ) -> None:
        self.logged.append(
            {
                "user_input": user_input,
                "resolved_intent": resolved_intent,
                "success": success,
                "timestamp": timestamp,
            }
        )


class FakeHabitTracker:
    def __init__(self) -> None:
        self.logs: list[dict[str, Any]] = []

    def log_execution(
        self,
        *,
        target_intent: str,
        last_command_intent: str,
        active_window_title: str | None = None,
        observed_at: Any = None,
    ) -> None:
        self.logs.append(
            {
                "target_intent": target_intent,
                "last_command_intent": last_command_intent,
                "active_window_title": active_window_title,
                "observed_at": observed_at,
            }
        )


def make_brain(
    callbacks: list[str],
    *,
    llm_enabled: bool = True,
    intent_enabled: bool = True,
    verbose: bool = False,
    allow_shell: bool = True,
    workflows: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> Brain:
    config = {
        "brain": {"verbose_mode": verbose},
        "automation": {"allow_shell_commands": allow_shell},
        "llm": {
            "enabled": llm_enabled,
            "intent_routing_enabled": intent_enabled,
            "intent_min_confidence": 0.58,
            "provider": "gemini",
        },
        "predictive": {
            "enabled": False,
        },
        "workflows": workflows if isinstance(workflows, dict) else {"enabled": False, "definitions": {}},
        "policy": policy if isinstance(policy, dict) else {"enabled": True},
    }
    return Brain(gui_callback=callbacks.append, config=config)


class BrainCoreTests(unittest.TestCase):
    def test_malformed_numeric_config_uses_safe_defaults(self) -> None:
        config = {
            "brain": {"verbose_mode": "maybe"},
            "automation": {
                "allow_shell_commands": "false",
                "shell_timeout_seconds": "invalid",
                "shell_output_limit": "invalid",
            },
            "llm": {
                "enabled": "true",
                "intent_routing_enabled": "yes",
                "intent_min_confidence": "bad",
                "history_turns": "bad",
                "timeout_seconds": "bad",
            },
        }

        brain = Brain(config=config)

        self.assertFalse(brain.allow_shell_commands)
        self.assertEqual(45.0, brain.shell_timeout_seconds)
        self.assertEqual(900, brain.shell_output_limit)
        self.assertEqual(0.58, brain.llm_intent_min_confidence)
        self.assertGreaterEqual(brain.llm.max_history_turns, 1)
        self.assertGreaterEqual(brain.llm.timeout_seconds, 5.0)

    def test_code_red_enables_tactical_state(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=False)

        response = brain.process_command("Code red now")

        self.assertIn("Alert confirmed", response)
        self.assertEqual("TACTICAL", brain.system_state)
        self.assertEqual(["ALERT_ON"], callbacks)

    def test_stand_down_returns_to_normal(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=False)
        brain.process_command("code red")

        response = brain.process_command("stand down")

        self.assertIn("Disengaging combat protocols", response)
        self.assertEqual("NORMAL", brain.system_state)
        self.assertEqual(["ALERT_ON", "ALERT_OFF"], callbacks)

    def test_open_command_routes_to_automation(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=False)
        fake_automation = FakeAutomation()
        brain.automation = fake_automation

        response = brain.process_command("Please open Calculator app please")

        self.assertEqual("Opening Calculator, Sir.", response)
        self.assertEqual(["Calculator"], fake_automation.open_calls)
        self.assertEqual([], fake_automation.open_file_calls)

    def test_open_command_falls_back_to_file_open_and_learns(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=False)
        fake_automation = FakeAutomation()
        fake_memory = FakeMemoryManager()
        fake_automation.open_result = False
        fake_automation.open_file_result = True
        brain.automation = fake_automation
        brain.memory_manager = fake_memory

        response = brain.process_command('open my cv file naming "BIBEK_KUMAR_YADAV_CV (2)"')

        self.assertEqual("Opening file BIBEK_KUMAR_YADAV_CV (2), Sir.", response)
        self.assertEqual(["my cv file naming \"BIBEK_KUMAR_YADAV_CV (2)\""], fake_automation.open_calls)
        self.assertEqual(["BIBEK_KUMAR_YADAV_CV (2)"], fake_automation.open_file_calls)
        self.assertEqual(1, len(fake_memory.logged))
        self.assertEqual("open_file", fake_memory.logged[0]["resolved_intent"].get("intent"))

    def test_successful_automation_command_is_logged_to_memory(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=False)
        fake_automation = FakeAutomation()
        fake_memory = FakeMemoryManager()
        brain.automation = fake_automation
        brain.memory_manager = fake_memory

        response = brain.process_command("open calculator")

        self.assertEqual("Opening calculator, Sir.", response)
        self.assertEqual(1, len(fake_memory.logged))
        self.assertEqual("open calculator", fake_memory.logged[0]["user_input"])
        self.assertEqual("open_app", fake_memory.logged[0]["resolved_intent"].get("intent"))
        self.assertEqual("automation", fake_memory.logged[0]["resolved_intent"].get("route"))
        self.assertTrue(fake_memory.logged[0]["success"])

    def test_successful_command_is_logged_to_predictive_habits(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=False)
        fake_automation = FakeAutomation()
        fake_habits = FakeHabitTracker()
        brain.automation = fake_automation
        brain.habit_tracker = fake_habits
        brain._last_successful_intent = "none"

        response = brain.process_command("open calculator")

        self.assertEqual("Opening calculator, Sir.", response)
        self.assertEqual(1, len(fake_habits.logs))
        self.assertEqual("open_app:calculator", fake_habits.logs[0]["target_intent"])
        self.assertEqual("none", fake_habits.logs[0]["last_command_intent"])
        self.assertEqual("open_app:calculator", brain._last_successful_intent)

    def test_search_command_routes_to_web_search(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=False)
        fake_automation = FakeAutomation()
        brain.automation = fake_automation

        response = brain.process_command("search for python threading tutorial please")

        self.assertEqual("Searching for python threading tutorial on the web.", response)
        self.assertEqual(["python threading tutorial"], fake_automation.search_calls)

    def test_timer_command_routes_to_clock_timer(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=False)
        fake_automation = FakeAutomation()
        brain.automation = fake_automation

        response = brain.process_command("start a timer till 1 min")

        self.assertEqual("Opening Clock timer for 1 min, Sir.", response)
        self.assertEqual(["timer"], fake_automation.open_clock_calls)
        self.assertEqual([], fake_automation.open_calls)

    def test_chained_open_and_search_routes_to_both_actions(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=False)
        fake_automation = FakeAutomation()
        brain.automation = fake_automation

        response = brain.process_command("open chrome and search cars")

        self.assertEqual("Opening chrome and searching for cars on the web.", response)
        self.assertEqual(["chrome"], fake_automation.open_calls)
        self.assertEqual(["cars"], fake_automation.search_calls)

    def test_chained_open_and_search_reports_partial_failure(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=False)
        fake_automation = FakeAutomation()
        fake_automation.open_result = False
        brain.automation = fake_automation

        response = brain.process_command("open chrome and search for cars")

        self.assertEqual(
            "I could not open chrome, but I started searching for cars on the web.",
            response,
        )
        self.assertEqual(["chrome"], fake_automation.open_calls)
        self.assertEqual(["cars"], fake_automation.search_calls)

    def test_explicit_automation_request_bypasses_llm_intent_router(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=True, intent_enabled=True)
        fake_automation = FakeAutomation()
        fake_llm = FakeLLM()
        fake_llm.route_intent_response = {
            "intent": "chat",
            "target": "",
            "query": "",
            "command": "",
            "shell_mode": "powershell",
            "reply": "router chat",
            "confidence": 0.99,
        }
        brain.automation = fake_automation
        brain.llm = fake_llm

        response = brain.process_command("open chrome and search cars")

        self.assertEqual("Opening chrome and searching for cars on the web.", response)
        self.assertEqual(["chrome"], fake_automation.open_calls)
        self.assertEqual(["cars"], fake_automation.search_calls)
        self.assertEqual([], fake_llm.route_intent_calls)

    def test_system_control_routes_volume_commands(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=False)
        fake_automation = FakeAutomation()
        brain.automation = fake_automation

        response = brain.process_command("volume up 3")

        self.assertEqual("Increasing volume, Sir.", response)
        self.assertEqual(["volume up 3"], fake_automation.control_calls)

    def test_shutdown_requires_confirmation(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=False)
        fake_automation = FakeAutomation()
        brain.automation = fake_automation

        initial = brain.process_command("shutdown")
        confirmed = brain.process_command("confirm shutdown")

        self.assertIn("Safety check: confirm shutdown", initial)
        self.assertEqual("Confirmed. Shutting down now.", confirmed)
        self.assertEqual(["shutdown"], fake_automation.control_calls)

    def test_restart_cancel_flow(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=False)
        fake_automation = FakeAutomation()
        brain.automation = fake_automation

        initial = brain.process_command("restart")
        cancelled = brain.process_command("cancel")

        self.assertIn("Safety check: confirm restart", initial)
        self.assertEqual("Critical action cancelled.", cancelled)
        self.assertEqual([], fake_automation.control_calls)

    def test_fallback_to_llm_query_for_chat(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=True, intent_enabled=False)
        fake_llm = FakeLLM()
        fake_llm.query_response = "hello from llm"
        brain.llm = fake_llm

        response = brain.process_command("hello")

        self.assertEqual("hello from llm", response)
        self.assertEqual(["hello"], fake_llm.query_calls)

    def test_llm_intent_open_app_executes_action(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=True, intent_enabled=True)
        fake_automation = FakeAutomation()
        fake_llm = FakeLLM()
        fake_llm.route_intent_response = {
            "intent": "open_app",
            "target": "notepad",
            "query": "",
            "command": "",
            "shell_mode": "powershell",
            "reply": "",
            "confidence": 0.98,
        }
        brain.automation = fake_automation
        brain.llm = fake_llm

        response = brain.process_command("could you help me with notes")

        self.assertEqual("Opening notepad, Sir.", response)
        self.assertEqual(["notepad"], fake_automation.open_calls)
        self.assertEqual([], fake_llm.query_calls)

    def test_llm_intent_set_alarm_executes_clock_alarm(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=True, intent_enabled=True)
        fake_automation = FakeAutomation()
        fake_llm = FakeLLM()
        fake_llm.route_intent_response = {
            "intent": "set_alarm",
            "target": "",
            "query": "",
            "command": "set an alarm for 2 min",
            "shell_mode": "powershell",
            "reply": "",
            "confidence": 0.95,
        }
        brain.automation = fake_automation
        brain.llm = fake_llm

        response = brain.process_command("set alarm for 2")

        self.assertEqual("Opening Clock alarm for 2 mins, Sir.", response)
        self.assertEqual(["alarm"], fake_automation.open_clock_calls)

    def test_successful_llm_intent_action_is_logged_to_memory(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=True, intent_enabled=True)
        fake_automation = FakeAutomation()
        fake_llm = FakeLLM()
        fake_llm.route_intent_response = {
            "intent": "open_app",
            "target": "notepad",
            "query": "",
            "command": "",
            "shell_mode": "powershell",
            "reply": "",
            "confidence": 0.93,
        }
        fake_memory = FakeMemoryManager()
        brain.automation = fake_automation
        brain.llm = fake_llm
        brain.memory_manager = fake_memory

        response = brain.process_command("could you help me with notes")

        self.assertEqual("Opening notepad, Sir.", response)
        self.assertEqual(1, len(fake_memory.logged))
        self.assertEqual(
            "llm_intent",
            fake_memory.logged[0]["resolved_intent"].get("route"),
        )
        self.assertEqual(
            "open_app",
            fake_memory.logged[0]["resolved_intent"].get("intent"),
        )
        self.assertEqual("notepad", fake_memory.logged[0]["resolved_intent"].get("target"))

    def test_low_confidence_intent_falls_back_to_chat(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=True, intent_enabled=True)
        fake_llm = FakeLLM()
        fake_llm.route_intent_response = {
            "intent": "open_app",
            "target": "spotify",
            "query": "",
            "command": "",
            "shell_mode": "powershell",
            "reply": "",
            "confidence": 0.10,
        }
        fake_llm.query_response = "fallback chat answer"
        brain.llm = fake_llm

        response = brain.process_command("play music")

        self.assertEqual("fallback chat answer", response)
        self.assertEqual(["play music"], fake_llm.query_calls)

    def test_cloud_offline_message_is_returned(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=True, intent_enabled=True)
        fake_llm = FakeLLM()
        fake_llm.route_intent_response = None
        fake_llm.health_status = "offline"
        fake_llm.health_reason = "all_providers_offline"
        brain.llm = fake_llm

        response = brain.process_command("hello dave")

        self.assertEqual(CLOUD_OFFLINE_MESSAGE, response)

    def test_offline_llm_falls_back_to_regex_automation(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=True, intent_enabled=True)
        fake_automation = FakeAutomation()
        fake_llm = FakeLLM()
        fake_llm.route_intent_response = None
        fake_llm.health_status = "offline"
        fake_llm.health_reason = "all_providers_offline"
        brain.automation = fake_automation
        brain.llm = fake_llm

        response = brain.process_command("open calculator")

        self.assertEqual("Opening calculator, Sir.", response)
        self.assertEqual(["calculator"], fake_automation.open_calls)

    def test_verbose_mode_appends_stats_suffix(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=False, verbose=True)

        response = brain.process_command("hello")

        self.assertIn("I'm sorry, DAVE. I'm processing that request.", response)
        self.assertIn("| Battery:", response)
        self.assertIn("| RAM:", response)
        self.assertIn("| State:", response)

    def test_run_command_executes_shell_via_automation(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=False, allow_shell=True)
        fake_automation = FakeAutomation()
        fake_automation.shell_result = (True, "done", "", 0)
        brain.automation = fake_automation

        response = brain.process_command("run powershell Write-Output done")

        self.assertEqual("Command executed (powershell). Output: done", response)
        self.assertEqual(
            [("Write-Output done", "powershell", brain.shell_timeout_seconds)],
            fake_automation.shell_calls,
        )

    def test_run_command_is_blocked_when_shell_disabled(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=False, allow_shell=False)
        fake_automation = FakeAutomation()
        brain.automation = fake_automation

        response = brain.process_command("run cmd dir")

        self.assertEqual("Shell execution is disabled in config.", response)
        self.assertEqual([], fake_automation.shell_calls)

    def test_list_workflows_returns_available_names(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(
            callbacks,
            llm_enabled=False,
            workflows={
                "enabled": True,
                "definitions": {
                    "morning boot": ["open chrome", "open vscode"],
                    "focus mode": ["open notepad"],
                },
            },
        )

        response = brain.process_command("list workflows")

        self.assertIn("Available workflows:", response)
        self.assertIn("focus mode", response)
        self.assertIn("morning boot", response)

    def test_run_workflow_executes_steps(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(
            callbacks,
            llm_enabled=False,
            workflows={
                "enabled": True,
                "definitions": {
                    "startup": ["open calculator", "search for release checklist"],
                },
            },
        )
        fake_automation = FakeAutomation()
        brain.automation = fake_automation

        response = brain.process_command("run workflow startup")

        self.assertIn("completed successfully", response.lower())
        self.assertEqual(["calculator"], fake_automation.open_calls)
        self.assertEqual(["release checklist"], fake_automation.search_calls)
        self.assertTrue(any(signal.startswith("WORKFLOW_STEP:startup:1:2:") for signal in callbacks))
        self.assertTrue(any(signal.startswith("WORKFLOW_DONE:startup:2:0:") for signal in callbacks))

    def test_shell_policy_blocks_restricted_command(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=False, allow_shell=True)
        fake_automation = FakeAutomation()
        brain.automation = fake_automation

        response = brain.process_command("run powershell shutdown /s /t 0")

        self.assertIn("Blocked by safety policy", response)
        self.assertEqual([], fake_automation.shell_calls)

    def test_shell_policy_requires_confirmation_for_high_risk_command(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=False, allow_shell=True)
        fake_automation = FakeAutomation()
        fake_automation.shell_result = (True, "removed", "", 0)
        brain.automation = fake_automation

        initial = brain.process_command("run powershell Remove-Item temp.txt")
        confirmed = brain.process_command("confirm")

        self.assertIn("requires confirmation", initial.lower())
        self.assertIn("Confirmed. Command executed", confirmed)
        self.assertEqual(1, len(fake_automation.shell_calls))


if __name__ == "__main__":
    unittest.main()
