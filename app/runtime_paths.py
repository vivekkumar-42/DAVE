from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

APP_NAME = "DAVE"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Directory containing the running app (project root when source, exe dir when frozen)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # This file lives in app/, so parents[1] is the project root.
    return Path(__file__).resolve().parents[1]


def user_data_root(app_name: str = APP_NAME) -> Path:
    """Per-user writable root directory for runtime data."""
    base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or str(Path.home())
    return Path(base).resolve() / app_name


def _can_write_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".write_probe_{uuid.uuid4().hex}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def runtime_data_dir(app_name: str = APP_NAME, *, prefer_portable: bool = True) -> Path:
    """
    Returns a directory safe for logs/runtime files.

    - When running from a writable folder (dev/portable), uses `app_dir()/data`.
    - When installed under protected locations (e.g. Program Files), uses `%LOCALAPPDATA%/DAVE/data`.
    """
    portable = app_dir() / "data"
    per_user = user_data_root(app_name) / "data"

    chosen = portable if (prefer_portable and _can_write_dir(portable)) else per_user
    chosen.mkdir(parents=True, exist_ok=True)
    return chosen


def config_candidates(app_name: str = APP_NAME) -> list[Path]:
    """Ordered list of config.json locations to try."""
    candidates = [
        user_data_root(app_name) / "config.json",
        app_dir() / "config.json",
        Path.cwd() / "config.json",
    ]
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique

