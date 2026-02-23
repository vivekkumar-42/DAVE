from __future__ import annotations

import ctypes
import os
import re
import subprocess
import webbrowser
from typing import Literal
from urllib.parse import quote_plus


class AutomationEngine:
    def __init__(self) -> None:
        self._app_aliases: dict[str, str] = {
            "calculator": "calc.exe",
            "calc": "calc.exe",
            "notepad": "notepad.exe",
            "paint": "mspaint.exe",
            "cmd": "cmd.exe",
            "command prompt": "cmd.exe",
            "powershell": "powershell.exe",
            "terminal": "wt.exe",
            "task manager": "taskmgr.exe",
            "explorer": "explorer.exe",
            "file explorer": "explorer.exe",
            "control panel": "control.exe",
            "settings": "ms-settings:",
            "snipping tool": "snippingtool.exe",
            "word": "winword.exe",
            "excel": "excel.exe",
            "chrome": "chrome.exe",
            "google chrome": "chrome.exe",
            "edge": "msedge.exe",
            "microsoft edge": "msedge.exe",
            "firefox": "firefox.exe",
            "spotify": "spotify.exe",
            "vscode": "code.exe",
            "visual studio code": "code.exe",
            "discord": "discord.exe",
        }

    def open_application(self, app_name: str) -> bool:
        if not app_name or not app_name.strip():
            return False

        normalized = self._normalize_app_name(app_name)
        target = self._app_aliases.get(normalized, app_name.strip())

        if self._start_with_shell(target):
            return True

        try:
            subprocess.Popen([target], shell=False)
            return True
        except Exception:
            return False

    def system_control(self, command: str) -> bool:
        normalized = self._normalize_control_command(command)

        if "shutdown" in normalized:
            return os.system("shutdown /s /t 0") == 0
        if "restart" in normalized or "reboot" in normalized:
            return os.system("shutdown /r /t 0") == 0
        if "lock" in normalized:
            try:
                return bool(ctypes.windll.user32.LockWorkStation())
            except Exception:
                return False
        if "mute" in normalized:
            return self._send_volume_key(0xAD, times=1)
        if "volume up" in normalized or "increase volume" in normalized:
            return self._send_volume_key(0xAF, times=self._extract_repeat_count(normalized))
        if "volume down" in normalized or "decrease volume" in normalized:
            return self._send_volume_key(0xAE, times=self._extract_repeat_count(normalized))
        if "volume" in normalized:
            return self._send_volume_key(0xAF, times=1)
        return False

    def web_search(self, query: str) -> bool:
        cleaned = (query or "").strip()
        if not cleaned:
            return False
        url = f"https://www.google.com/search?q={quote_plus(cleaned)}"
        try:
            return bool(webbrowser.open(url, new=2))
        except Exception:
            return False

    def run_shell_command(
        self,
        command: str,
        *,
        shell_mode: Literal["powershell", "cmd"] = "powershell",
        timeout_seconds: float = 45.0,
    ) -> tuple[bool, str, str, int | None]:
        cleaned = (command or "").strip()
        if not cleaned:
            return False, "", "Empty command.", None

        timeout = max(1.0, float(timeout_seconds))
        if shell_mode == "cmd":
            invocation = ["cmd.exe", "/c", cleaned]
        else:
            invocation = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                cleaned,
            ]

        try:
            result = subprocess.run(
                invocation,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                encoding="utf-8",
                errors="replace",
            )
            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            ok = result.returncode == 0
            return ok, stdout, stderr, result.returncode
        except subprocess.TimeoutExpired:
            return False, "", f"Command timed out after {int(timeout)}s.", None
        except Exception as exc:
            return False, "", str(exc), None

    def _start_with_shell(self, target: str) -> bool:
        try:
            os.startfile(target)  # type: ignore[attr-defined]
            return True
        except Exception:
            return False

    @staticmethod
    def _normalize_app_name(app_name: str) -> str:
        normalized = app_name.strip().lower()
        for suffix in (" app", " application"):
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)].strip()
        return normalized

    @staticmethod
    def _normalize_control_command(command: str) -> str:
        lowered = (command or "").strip().lower()
        return lowered.replace("shut down", "shutdown")

    @staticmethod
    def _extract_repeat_count(command: str) -> int:
        match = re.search(r"(\d{1,2})", command)
        if match:
            return max(1, min(10, int(match.group(1))))
        return 2

    @staticmethod
    def _send_volume_key(vk_code: int, times: int = 1) -> bool:
        keybd_event = ctypes.windll.user32.keybd_event
        keyup_flag = 0x0002
        try:
            for _ in range(max(1, times)):
                keybd_event(vk_code, 0, 0, 0)
                keybd_event(vk_code, 0, keyup_flag, 0)
            return True
        except Exception:
            return False
