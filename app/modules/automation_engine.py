from __future__ import annotations

from collections import OrderedDict
import ctypes
import os
import re
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
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
            "clock": "ms-clock:",
            "clock app": "ms-clock:",
            "alarm": "ms-clock:alarm",
            "alarms": "ms-clock:alarm",
            "timer": "ms-clock:timer",
            "timers": "ms-clock:timer",
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
        self._document_extensions: tuple[str, ...] = (
            ".pdf",
            ".doc",
            ".docx",
            ".txt",
            ".rtf",
            ".odt",
        )
        self._walk_excluded_dirs: set[str] = {
            "appdata",
            "programdata",
            "windows",
            ".git",
            ".venv",
            "venv",
            "__pycache__",
            "node_modules",
        }
        self._file_resolution_cache_ttl_seconds = 180.0
        self._file_resolution_cache_max_entries = 256
        self._file_resolution_cache: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._file_cache_lock = threading.Lock()

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

    def open_file(self, file_reference: str) -> bool:
        resolved = self._resolve_file_reference(file_reference)
        if resolved is None:
            return False
        return self._start_with_shell(str(resolved))

    def open_clock_page(self, page: str) -> bool:
        normalized = (page or "").strip().lower()
        if normalized.startswith("alarm"):
            candidates = ("ms-clock:alarm", "ms-clock:")
        else:
            candidates = ("ms-clock:timer", "ms-clock:")
        for target in candidates:
            if self._start_with_shell(target):
                return True
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

    def _resolve_file_reference(self, file_reference: str) -> Path | None:
        normalized = self._normalize_file_reference(file_reference)
        if not normalized:
            return None
        cache_key = normalized.lower()
        cached_hit, cached_result = self._get_cached_file_resolution(cache_key)
        if cached_hit:
            return cached_result

        expanded = Path(os.path.expanduser(os.path.expandvars(normalized)))
        if expanded.exists():
            self._set_cached_file_resolution(cache_key, expanded)
            return expanded

        exact_names, lower_names = self._candidate_file_names(normalized)
        if not exact_names:
            self._set_cached_file_resolution(cache_key, None)
            return None

        for root in self._file_search_roots():
            for candidate in exact_names:
                direct_path = root / candidate
                if direct_path.exists():
                    self._set_cached_file_resolution(cache_key, direct_path)
                    return direct_path
            found = self._find_file_in_tree(
                root,
                target_names_lower=lower_names,
                max_files=60000,
            )
            if found is not None:
                self._set_cached_file_resolution(cache_key, found)
                return found
        self._set_cached_file_resolution(cache_key, None)
        return None

    def _get_cached_file_resolution(self, key: str) -> tuple[bool, Path | None]:
        now = time.time()
        with self._file_cache_lock:
            entry = self._file_resolution_cache.get(key)
            if entry is None:
                return False, None
            expires_at, raw_path = entry
            if expires_at <= now:
                self._file_resolution_cache.pop(key, None)
                return False, None
            self._file_resolution_cache.move_to_end(key)
        if not raw_path:
            return True, None
        cached_path = Path(raw_path)
        if not cached_path.exists():
            with self._file_cache_lock:
                self._file_resolution_cache.pop(key, None)
            return False, None
        return True, cached_path

    def _set_cached_file_resolution(self, key: str, path: Path | None) -> None:
        if not key:
            return
        expires_at = time.time() + self._file_resolution_cache_ttl_seconds
        raw_path = ""
        if path is not None:
            try:
                raw_path = str(path.resolve())
            except Exception:
                raw_path = str(path)
        with self._file_cache_lock:
            self._file_resolution_cache[key] = (expires_at, raw_path)
            self._file_resolution_cache.move_to_end(key)
            while len(self._file_resolution_cache) > self._file_resolution_cache_max_entries:
                self._file_resolution_cache.popitem(last=False)

    def _file_search_roots(self) -> list[Path]:
        roots: list[Path] = []

        def _add(path: Path | None) -> None:
            if path is None:
                return
            try:
                candidate = path.resolve()
            except Exception:
                candidate = path
            if not candidate.exists() or not candidate.is_dir():
                return
            if candidate not in roots:
                roots.append(candidate)

        home = Path.home()
        _add(Path.cwd())
        _add(home)
        for folder in ("Desktop", "Documents", "Downloads"):
            _add(home / folder)

        for env_key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            raw_value = os.getenv(env_key, "").strip()
            if not raw_value:
                continue
            base = Path(raw_value)
            _add(base)
            for folder in ("Desktop", "Documents", "Downloads"):
                _add(base / folder)

        return roots

    def _find_file_in_tree(
        self,
        root: Path,
        *,
        target_names_lower: set[str],
        max_files: int,
    ) -> Path | None:
        inspected = 0
        try:
            iterator = os.walk(root)
        except Exception:
            return None

        for current_root, dirs, files in iterator:
            dirs[:] = [d for d in dirs if d.lower() not in self._walk_excluded_dirs]
            for file_name in files:
                inspected += 1
                if file_name.lower() in target_names_lower:
                    return Path(current_root) / file_name
                if inspected >= max_files:
                    return None
        return None

    def _candidate_file_names(self, file_reference: str) -> tuple[set[str], set[str]]:
        raw = file_reference.strip().strip("\"'")
        if not raw:
            return set(), set()

        name = Path(raw).name.strip()
        if not name:
            return set(), set()

        exact_names: set[str] = {name}
        if Path(name).suffix == "":
            for extension in self._document_extensions:
                exact_names.add(f"{name}{extension}")

        lowered = {candidate.lower() for candidate in exact_names}
        return exact_names, lowered

    @staticmethod
    def _normalize_file_reference(file_reference: str) -> str:
        text = (file_reference or "").strip()
        if not text:
            return ""

        quoted_match = re.search(r'"([^"]+)"', text)
        if quoted_match:
            text = quoted_match.group(1).strip()
        else:
            single_quoted_match = re.search(r"'([^']+)'", text)
            if single_quoted_match:
                text = single_quoted_match.group(1).strip()
            else:
                marker_match = re.search(
                    r"\b(?:named|naming|called)\b\s+(.+)$",
                    text,
                    flags=re.IGNORECASE,
                )
                if marker_match:
                    text = marker_match.group(1).strip()

        text = text.strip().strip("\"'")
        text = re.sub(r"[.!?]+$", "", text).strip()
        return text

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
