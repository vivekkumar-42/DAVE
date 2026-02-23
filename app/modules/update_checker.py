from __future__ import annotations

import re
from typing import Any

import requests


def check_for_update(
    *,
    current_version: str,
    manifest_url: str,
    timeout_seconds: float = 3.0,
) -> dict[str, Any]:
    url = str(manifest_url or "").strip()
    if not url:
        return {
            "status": "disabled",
            "detail": "No manifest URL configured.",
            "available": False,
        }

    try:
        response = requests.get(url, timeout=max(1.0, float(timeout_seconds)))
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return {
            "status": "error",
            "detail": f"Manifest request failed: {exc}",
            "available": False,
        }

    if not isinstance(payload, dict):
        return {
            "status": "error",
            "detail": "Manifest payload is not a JSON object.",
            "available": False,
        }

    latest_version_raw = payload.get("version")
    latest_version = str(latest_version_raw).strip() if latest_version_raw is not None else ""
    if not latest_version:
        return {
            "status": "error",
            "detail": "Manifest does not contain a valid version.",
            "available": False,
        }

    cmp = compare_versions(latest_version, current_version)
    available = cmp > 0
    return {
        "status": "available" if available else "up_to_date",
        "available": available,
        "current_version": current_version,
        "latest_version": latest_version,
        "channel": payload.get("channel", ""),
        "download_url": payload.get("download_url", ""),
        "sha256": payload.get("sha256", ""),
        "detail": "Update available." if available else "Already on latest version.",
    }


def compare_versions(left: str, right: str) -> int:
    left_tuple = _version_tuple(left)
    right_tuple = _version_tuple(right)
    max_len = max(len(left_tuple), len(right_tuple))
    padded_left = left_tuple + (0,) * (max_len - len(left_tuple))
    padded_right = right_tuple + (0,) * (max_len - len(right_tuple))
    if padded_left > padded_right:
        return 1
    if padded_left < padded_right:
        return -1
    return 0


def _version_tuple(version: str) -> tuple[int, ...]:
    clean = str(version or "").strip()
    if not clean:
        return (0,)
    parts: list[int] = []
    for token in clean.split("."):
        match = re.match(r"^(\d+)", token)
        if match:
            parts.append(int(match.group(1)))
        else:
            parts.append(0)
    return tuple(parts) if parts else (0,)
