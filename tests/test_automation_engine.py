import unittest
import subprocess
import tempfile
from pathlib import Path
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

    def test_open_file_opens_direct_existing_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "BIBEK_KUMAR_YADAV_CV (2).pdf"
            file_path.write_text("cv", encoding="utf-8")
            with mock.patch("app.modules.automation_engine.os.startfile", create=True) as startfile_mock:
                startfile_mock.return_value = None
                opened = self.engine.open_file(str(file_path))

        self.assertTrue(opened)
        startfile_mock.assert_called_once_with(str(file_path))

    def test_open_file_finds_filename_without_extension_in_search_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            nested = root / "docs"
            nested.mkdir(parents=True, exist_ok=True)
            file_path = nested / "BIBEK_KUMAR_YADAV_CV (2).pdf"
            file_path.write_text("cv", encoding="utf-8")

            with mock.patch.object(
                AutomationEngine,
                "_file_search_roots",
                return_value=[root],
            ):
                with mock.patch(
                    "app.modules.automation_engine.os.startfile",
                    create=True,
                ) as startfile_mock:
                    startfile_mock.return_value = None
                    opened = self.engine.open_file("BIBEK_KUMAR_YADAV_CV (2)")

        self.assertTrue(opened)
        startfile_mock.assert_called_once_with(str(file_path))

    def test_open_file_negative_cache_avoids_repeat_tree_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            with mock.patch.object(
                AutomationEngine,
                "_file_search_roots",
                return_value=[root],
            ):
                with mock.patch.object(
                    AutomationEngine,
                    "_find_file_in_tree",
                    return_value=None,
                ) as finder_mock:
                    first = self.engine.open_file("missing_resume_file")
                    second = self.engine.open_file("missing_resume_file")

        self.assertFalse(first)
        self.assertFalse(second)
        finder_mock.assert_called_once()

    def test_open_clock_page_opens_timer_uri(self) -> None:
        with mock.patch("app.modules.automation_engine.os.startfile", create=True) as startfile_mock:
            startfile_mock.return_value = None
            opened = self.engine.open_clock_page("timer")

        self.assertTrue(opened)
        startfile_mock.assert_called_once_with("ms-clock:timer")

    def test_open_clock_page_falls_back_to_base_uri(self) -> None:
        with mock.patch(
            "app.modules.automation_engine.os.startfile",
            side_effect=[OSError("no timer uri"), None],
            create=True,
        ) as startfile_mock:
            opened = self.engine.open_clock_page("alarm")

        self.assertTrue(opened)
        self.assertEqual(2, startfile_mock.call_count)
        self.assertEqual("ms-clock:alarm", startfile_mock.call_args_list[0].args[0])
        self.assertEqual("ms-clock:", startfile_mock.call_args_list[1].args[0])


if __name__ == "__main__":
    unittest.main()
