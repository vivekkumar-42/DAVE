from __future__ import annotations

import json
import logging
import ctypes
import sys
import tkinter
from ctypes import wintypes
from tkinter import messagebox
from pathlib import Path
from typing import Any

from app.runtime_paths import config_candidates, runtime_data_dir

DATA_DIR = runtime_data_dir()
LOG_FILE = DATA_DIR / "dave_system.log"
CONFIG_CANDIDATES = config_candidates()
WINDOWS_MUTEX_NAME = r"Local\DAVE_IntelligenceSystem_Singleton"
ERROR_ALREADY_EXISTS = 183


def configure_logging() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=str(LOG_FILE),
            level=logging.INFO,
            format="%(asctime)s | %(levelname)s | %(message)s",
        )
    except Exception:
        # If the log file cannot be opened (e.g. protected install locations),
        # fall back to a basic handler instead of crashing the bootloader.
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)s | %(message)s",
        )
    logging.info("Boot Sequence Initiated")


def load_config() -> dict[str, Any]:
    for config_path in CONFIG_CANDIDATES:
        if not config_path.exists():
            continue
        try:
            raw = config_path.read_text(encoding="utf-8-sig")
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return _sanitize_config(parsed)
        except Exception as exc:
            logging.warning("Failed to load config from %s: %s", config_path, exc)
    return {}


def _sanitize_config(config: dict[str, Any]) -> dict[str, Any]:
    scrubbed = json.loads(json.dumps(config))
    top_level_secret_keys = ("groq_api_key", "gemini_api_key", "openai_api_key")
    for key in top_level_secret_keys:
        if _looks_like_secret(scrubbed.get(key)):
            scrubbed[key] = ""
            logging.warning("Config secret '%s' ignored. Use environment variables instead.", key)

    llm_raw = scrubbed.get("llm", {})
    llm_cfg = llm_raw if isinstance(llm_raw, dict) else {}
    for provider_name in ("groq", "gemini", "openai"):
        provider_raw = llm_cfg.get(provider_name, {})
        provider_cfg = provider_raw if isinstance(provider_raw, dict) else {}
        if _looks_like_secret(provider_cfg.get("api_key")):
            provider_cfg["api_key"] = ""
            llm_cfg[provider_name] = provider_cfg
            logging.warning(
                "Config secret 'llm.%s.api_key' ignored. Use environment variables instead.",
                provider_name,
            )

    scrubbed["llm"] = llm_cfg
    return scrubbed


def _looks_like_secret(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    upper = text.upper()
    if upper.startswith("YOUR_") or upper.startswith("<YOUR_"):
        return False
    if "REPLACE_ME" in upper or "CHANGE_ME" in upper:
        return False
    return True


def _show_already_running_notice() -> None:
    try:
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showwarning("DAVE", "DAVE is already running. Close the existing window first.")
        root.destroy()
    except Exception:
        # Avoid bubbling any UI error to the bootloader.
        pass


class WindowsSingleInstanceLock:
    def __init__(self, mutex_name: str) -> None:
        self._mutex_name = mutex_name
        self._handle: int | None = None
        self._kernel32 = None

    def acquire(self) -> bool:
        if sys.platform != "win32":
            return True
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = self._kernel32.CreateMutexW
        create_mutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        create_mutex.restype = wintypes.HANDLE
        handle = create_mutex(None, False, self._mutex_name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
        self._handle = int(handle)
        return ctypes.get_last_error() != ERROR_ALREADY_EXISTS

    def release(self) -> None:
        if sys.platform != "win32" or self._handle is None or self._kernel32 is None:
            return
        close_handle = self._kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        try:
            close_handle(wintypes.HANDLE(self._handle))
        except Exception:
            pass
        self._handle = None


def _acquire_single_instance() -> WindowsSingleInstanceLock | None | bool:
    lock = WindowsSingleInstanceLock(WINDOWS_MUTEX_NAME)
    try:
        is_primary = lock.acquire()
    except Exception as exc:
        logging.warning("Singleton protection unavailable: %s", exc)
        return None
    if is_primary:
        return lock
    logging.info("Secondary launch blocked by named mutex.")
    _show_already_running_notice()
    lock.release()
    return False


def run_self_check() -> int:
    settings = load_config()
    try:
        from app.modules.brain_core import Brain
        from app.modules.llm_interface import LLMClient
        from app.ui import MainWindow

        _ = MainWindow  # Import validation only.
        _ = LLMClient(config=settings)
        _ = Brain(gui_callback=None, config=settings)
    except Exception as exc:
        logging.exception("Self-check failed: %s", exc)
        return 1
    logging.info("Self-check completed successfully.")
    return 0


def main() -> int:
    configure_logging()
    args = {item.strip().lower() for item in sys.argv[1:] if isinstance(item, str)}
    if "--self-check" in args:
        return run_self_check()

    singleton_lock = _acquire_single_instance()
    if singleton_lock is False:
        return 0

    from app.ui import MainWindow

    settings = load_config()
    app = MainWindow(settings=settings)
    try:
        app.mainloop()
    finally:
        try:
            app.destroy()
        except Exception:
            pass
        try:
            if isinstance(singleton_lock, WindowsSingleInstanceLock):
                singleton_lock.release()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
