import unittest
from typing import Any

from app.modules.brain_core import Brain
from app.modules.llm_interface import CLOUD_OFFLINE_MESSAGE


class FakeAutomation:
    def __init__(self) -> None:
        self.open_calls: list[str] = []
        self.search_calls: list[str] = []
        self.control_calls: list[str] = []
        self.shell_calls: list[tuple[str, str, float]] = []
        self.open_result = True
        self.search_result = True
        self.control_result = True
        self.shell_result: tuple[bool, str, str, int | None] = (True, "ok", "", 0)

    def open_application(self, app_name: str) -> bool:
        self.open_calls.append(app_name)
        return self.open_result

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
        self.ephemeral_calls: list[tuple[str, str | None]] = []
        self.query_response = "fallback chat"
        self.ephemeral_response = '{"intent":"chat","reply":"router chat","confidence":0.9}'

    def query(self, user_text: str) -> str:
        self.query_calls.append(user_text)
        return self.query_response

    def query_ephemeral(self, user_text: str, system_prompt: str | None = None) -> str:
        self.ephemeral_calls.append((user_text, system_prompt))
        return self.ephemeral_response

    def get_health(self) -> dict[str, Any]:
        return {
            "status": "online",
            "provider": "gemini",
            "reason": "ok",
            "detail": "",
        }


def make_brain(
    callbacks: list[str],
    *,
    llm_enabled: bool = True,
    intent_enabled: bool = True,
    verbose: bool = False,
    allow_shell: bool = True,
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

    def test_search_command_routes_to_web_search(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=False)
        fake_automation = FakeAutomation()
        brain.automation = fake_automation

        response = brain.process_command("search for python threading tutorial please")

        self.assertEqual("Searching for python threading tutorial on the web.", response)
        self.assertEqual(["python threading tutorial"], fake_automation.search_calls)

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
        fake_llm.ephemeral_response = (
            '{"intent":"open_app","target":"notepad","confidence":0.98}'
        )
        brain.automation = fake_automation
        brain.llm = fake_llm

        response = brain.process_command("could you help me with notes")

        self.assertEqual("Opening notepad, Sir.", response)
        self.assertEqual(["notepad"], fake_automation.open_calls)
        self.assertEqual([], fake_llm.query_calls)

    def test_low_confidence_intent_falls_back_to_chat(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=True, intent_enabled=True)
        fake_llm = FakeLLM()
        fake_llm.ephemeral_response = (
            '{"intent":"open_app","target":"spotify","confidence":0.10}'
        )
        fake_llm.query_response = "fallback chat answer"
        brain.llm = fake_llm

        response = brain.process_command("play music")

        self.assertEqual("fallback chat answer", response)
        self.assertEqual(["play music"], fake_llm.query_calls)

    def test_cloud_offline_message_is_returned(self) -> None:
        callbacks: list[str] = []
        brain = make_brain(callbacks, llm_enabled=True, intent_enabled=True)
        fake_llm = FakeLLM()
        fake_llm.ephemeral_response = CLOUD_OFFLINE_MESSAGE
        brain.llm = fake_llm

        response = brain.process_command("hello dave")

        self.assertEqual(CLOUD_OFFLINE_MESSAGE, response)

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


if __name__ == "__main__":
    unittest.main()
