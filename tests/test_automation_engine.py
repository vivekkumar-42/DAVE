import unittest
import subprocess
from unittest import mock

from app.modules.automation_engine import AutomationEngine


class AutomationEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = AutomationEngine()

    def test_open_application_uses_alias_mapping(self) -> None:
        with mock.patch("app.modules.automation_engine.os.startfile", create=True) as startfile_mock:
            startfile_mock.return_value = None
            opened = self.engine.open_application("calculator")

        self.assertTrue(opened)
        startfile_mock.assert_called_once_with("calc.exe")

    def test_open_application_falls_back_to_subprocess(self) -> None:
        with mock.patch(
            "app.modules.automation_engine.os.startfile",
            side_effect=OSError("missing"),
            create=True,
        ):
            with mock.patch("app.modules.automation_engine.subprocess.Popen") as popen_mock:
                popen_mock.return_value = object()
                opened = self.engine.open_application("notepad")

        self.assertTrue(opened)
        popen_mock.assert_called_once_with(["notepad.exe"], shell=False)

    def test_system_control_shutdown(self) -> None:
        with mock.patch("app.modules.automation_engine.os.system", return_value=0) as system_mock:
            result = self.engine.system_control("shutdown now")

        self.assertTrue(result)
        system_mock.assert_called_once_with("shutdown /s /t 0")

    def test_system_control_volume_up_uses_keyboard_event(self) -> None:
        with mock.patch.object(AutomationEngine, "_send_volume_key", return_value=True) as key_mock:
            result = self.engine.system_control("volume up 4")

        self.assertTrue(result)
        key_mock.assert_called_once_with(0xAF, times=4)

    def test_web_search_builds_google_url(self) -> None:
        with mock.patch("app.modules.automation_engine.webbrowser.open", return_value=True) as web_mock:
            opened = self.engine.web_search("python unittest")

        self.assertTrue(opened)
        web_mock.assert_called_once()
        args, kwargs = web_mock.call_args
        self.assertIn("https://www.google.com/search?q=python+unittest", args[0])
        self.assertEqual(2, kwargs.get("new"))

    def test_run_shell_command_uses_powershell_by_default(self) -> None:
        fake_result = mock.Mock(returncode=0, stdout="hello\n", stderr="")
        with mock.patch("app.modules.automation_engine.subprocess.run", return_value=fake_result) as run_mock:
            ok, stdout, stderr, code = self.engine.run_shell_command("Write-Output hello")

        self.assertTrue(ok)
        self.assertEqual("hello", stdout)
        self.assertEqual("", stderr)
        self.assertEqual(0, code)
        run_mock.assert_called_once()
        called_command = run_mock.call_args.args[0]
        self.assertEqual(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "Write-Output hello"],
            called_command,
        )

    def test_run_shell_command_timeout_returns_error(self) -> None:
        with mock.patch(
            "app.modules.automation_engine.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="cmd", timeout=5),
        ):
            ok, stdout, stderr, code = self.engine.run_shell_command(
                "dir",
                shell_mode="cmd",
                timeout_seconds=5,
            )

        self.assertFalse(ok)
        self.assertEqual("", stdout)
        self.assertIn("timed out", stderr.lower())
        self.assertIsNone(code)


if __name__ == "__main__":
    unittest.main()
