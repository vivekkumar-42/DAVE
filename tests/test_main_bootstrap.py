import unittest
import types
from unittest import mock

import main


class _FakeMainWindow:
    last_instance: "_FakeMainWindow | None" = None

    def __init__(self, settings: dict) -> None:
        self.settings = settings
        self.after_calls: list[tuple[int, object]] = []
        self.shutdown_calls = 0
        self.mainloop_calls = 0
        self.destroy_calls = 0
        _FakeMainWindow.last_instance = self

    def after(self, delay_ms: int, callback) -> str:
        self.after_calls.append((delay_ms, callback))
        return "after-id"

    def shutdown_system(self) -> None:
        self.shutdown_calls += 1

    def mainloop(self) -> None:
        self.mainloop_calls += 1
        for _, callback in list(self.after_calls):
            callback()

    def destroy(self) -> None:
        self.destroy_calls += 1


class _FakeLock:
    def __init__(self) -> None:
        self.release_calls = 0

    def release(self) -> None:
        self.release_calls += 1


class MainBootstrapTests(unittest.TestCase):
    def test_sanitize_config_scrubs_inline_secrets(self) -> None:
        source = {
            "groq_api_key": "gsk_live_top",
            "gemini_api_key": "AIza_live_top",
            "llm": {
                "groq": {"api_key": "gsk_live_nested"},
                "gemini": {"api_key": "AIza_live_nested"},
            },
        }

        sanitized = main._sanitize_config(source)

        self.assertEqual("", sanitized.get("groq_api_key"))
        self.assertEqual("", sanitized.get("gemini_api_key"))
        self.assertEqual("", sanitized["llm"]["groq"]["api_key"])
        self.assertEqual("", sanitized["llm"]["gemini"]["api_key"])

    def test_placeholder_keys_not_treated_as_live_secrets(self) -> None:
        self.assertFalse(main._looks_like_secret("YOUR_GROQ_KEY"))
        self.assertFalse(main._looks_like_secret("<YOUR_GEMINI_KEY>"))
        self.assertFalse(main._looks_like_secret("REPLACE_ME"))
        self.assertFalse(main._looks_like_secret(""))
        self.assertTrue(main._looks_like_secret("gsk_live_value"))

    def test_parse_auto_exit_seconds_accepts_cli_forms(self) -> None:
        self.assertEqual(2.5, main._parse_auto_exit_seconds(["--auto-exit-seconds", "2.5"]))
        self.assertEqual(3.0, main._parse_auto_exit_seconds(["--auto-exit-seconds=3"]))

    def test_parse_auto_exit_seconds_uses_env_and_validates(self) -> None:
        with mock.patch.dict("os.environ", {"DAVE_AUTO_EXIT_SECONDS": "7.5"}, clear=False):
            self.assertEqual(7.5, main._parse_auto_exit_seconds([]))

        with mock.patch.dict("os.environ", {"DAVE_AUTO_EXIT_SECONDS": "bad"}, clear=False):
            self.assertIsNone(main._parse_auto_exit_seconds([]))

    def test_main_auto_exit_closes_window_and_releases_lock(self) -> None:
        _FakeMainWindow.last_instance = None
        fake_lock = _FakeLock()
        fake_ui_module = types.SimpleNamespace(MainWindow=_FakeMainWindow)

        with mock.patch.dict(main.sys.modules, {"app.ui": fake_ui_module}):
            with mock.patch.object(main, "configure_logging"):
                with mock.patch.object(main, "load_config", return_value={}):
                    with mock.patch.object(main, "_acquire_single_instance", return_value=fake_lock):
                        with mock.patch.object(main, "WindowsSingleInstanceLock", _FakeLock):
                            with mock.patch.object(
                                main.sys, "argv", ["main.py", "--auto-exit-seconds=0.01"]
                            ):
                                exit_code = main.main()

        self.assertEqual(0, exit_code)
        window = _FakeMainWindow.last_instance
        self.assertIsNotNone(window)
        if window is None:
            self.fail("Main window instance was not created")
        self.assertEqual(1, window.mainloop_calls)
        self.assertEqual(1, window.shutdown_calls)
        self.assertEqual(1, window.destroy_calls)
        self.assertTrue(window.after_calls)
        self.assertGreaterEqual(window.after_calls[0][0], 1)
        self.assertEqual(1, fake_lock.release_calls)


if __name__ == "__main__":
    unittest.main()
