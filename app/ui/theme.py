from __future__ import annotations

from dataclasses import dataclass
from typing import Any


COLORS: dict[str, str] = {
    "background": "#0B0F17",
    "panel": "#121826",
    "panel_elevated": "#161D2E",
    "accent_primary": "#00F5FF",
    "accent_secondary": "#0090FF",
    "success": "#00FF9C",
    "warning": "#FFC857",
    "error": "#FF3B3B",
    "text_primary": "#E6EDF3",
    "text_secondary": "#7A8BA3",
    "border": "#22314A",
}
DEFAULT_COLORS: dict[str, str] = dict(COLORS)


FONTS: dict[str, tuple[str, int, str] | tuple[str, int]] = {
    "title": ("Bahnschrift", 18, "bold"),
    "header": ("Bahnschrift", 14, "bold"),
    "body": ("Segoe UI", 12),
    "body_bold": ("Segoe UI", 12, "bold"),
    "small": ("Segoe UI", 11),
    "mono": ("Cascadia Code", 11),
    "mono_small": ("Cascadia Code", 10),
}


@dataclass(frozen=True)
class StateVisual:
    accent: str
    glow: str
    label: str
    ring_speed: float
    pulse_speed: float


STATE_VISUALS: dict[str, StateVisual] = {
    "NORMAL": StateVisual(
        accent=COLORS["accent_primary"],
        glow="#43F6FF",
        label="NORMAL",
        ring_speed=0.45,
        pulse_speed=0.07,
    ),
    "LISTENING": StateVisual(
        accent="#57F8FF",
        glow="#8CFBFF",
        label="LISTENING",
        ring_speed=0.42,
        pulse_speed=0.1,
    ),
    "PROCESSING": StateVisual(
        accent=COLORS["accent_secondary"],
        glow="#47AFFF",
        label="PROCESSING",
        ring_speed=1.85,
        pulse_speed=0.09,
    ),
    "EXECUTING": StateVisual(
        accent=COLORS["success"],
        glow="#70FFBF",
        label="EXECUTING",
        ring_speed=1.2,
        pulse_speed=0.16,
    ),
    "SPEAKING": StateVisual(
        accent="#3FE8FF",
        glow="#86F3FF",
        label="SPEAKING",
        ring_speed=0.65,
        pulse_speed=0.12,
    ),
    "ERROR": StateVisual(
        accent=COLORS["error"],
        glow="#FF8181",
        label="ERROR",
        ring_speed=1.4,
        pulse_speed=0.2,
    ),
}
DEFAULT_STATE_VISUALS: dict[str, StateVisual] = dict(STATE_VISUALS)


def clamp(value: float, minimum: float, maximum: float) -> float:
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    cleaned = color.strip().lstrip("#")
    if len(cleaned) != 6:
        return 255, 255, 255
    return int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16)


def rgb_to_hex(red: int, green: int, blue: int) -> str:
    return f"#{max(0, min(255, red)):02X}{max(0, min(255, green)):02X}{max(0, min(255, blue)):02X}"


def blend(color_a: str, color_b: str, ratio: float) -> str:
    r = clamp(ratio, 0.0, 1.0)
    a_red, a_green, a_blue = hex_to_rgb(color_a)
    b_red, b_green, b_blue = hex_to_rgb(color_b)
    red = int(a_red + (b_red - a_red) * r)
    green = int(a_green + (b_green - a_green) * r)
    blue = int(a_blue + (b_blue - a_blue) * r)
    return rgb_to_hex(red, green, blue)


def apply_ui_theme(ui_config: dict[str, Any] | None) -> None:
    config = ui_config if isinstance(ui_config, dict) else {}
    _reset_defaults()

    visual_raw = config.get("visual_system")
    visual = visual_raw if isinstance(visual_raw, dict) else {}

    color_aliases = {
        "background": "background",
        "panel": "panel",
        "panel_color": "panel",
        "panel_elevated": "panel_elevated",
        "elevated_panel": "panel_elevated",
        "accent_primary": "accent_primary",
        "primary_accent": "accent_primary",
        "accent_secondary": "accent_secondary",
        "secondary_accent": "accent_secondary",
        "success": "success",
        "success_color": "success",
        "warning": "warning",
        "warning_color": "warning",
        "error": "error",
        "error_color": "error",
        "text_primary": "text_primary",
        "primary_text": "text_primary",
        "text_secondary": "text_secondary",
        "secondary_text": "text_secondary",
        "border": "border",
        "border_color": "border",
    }

    for source in (config, visual):
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            normalized = color_aliases.get(str(key).strip().lower())
            if normalized and _is_hex_color(value):
                COLORS[normalized] = value.strip().upper()

    legacy_accent = config.get("accent_color")
    if _is_hex_color(legacy_accent):
        COLORS["accent_primary"] = str(legacy_accent).strip().upper()
    legacy_alert = config.get("alert_color")
    if _is_hex_color(legacy_alert):
        COLORS["error"] = str(legacy_alert).strip().upper()

    state_visuals_raw = config.get("state_visuals")
    if isinstance(state_visuals_raw, dict):
        for state_name, raw_state in state_visuals_raw.items():
            state_key = str(state_name).strip().upper()
            if state_key not in STATE_VISUALS or not isinstance(raw_state, dict):
                continue
            current = STATE_VISUALS[state_key]
            accent = (
                str(raw_state.get("accent")).strip().upper()
                if _is_hex_color(raw_state.get("accent"))
                else current.accent
            )
            glow = (
                str(raw_state.get("glow")).strip().upper()
                if _is_hex_color(raw_state.get("glow"))
                else current.glow
            )
            ring_speed = _coerce_positive_float(raw_state.get("ring_speed"), current.ring_speed)
            pulse_speed = _coerce_positive_float(raw_state.get("pulse_speed"), current.pulse_speed)
            label_value = raw_state.get("label")
            label = str(label_value).strip().upper() if isinstance(label_value, str) and label_value.strip() else current.label
            STATE_VISUALS[state_key] = StateVisual(
                accent=accent,
                glow=glow,
                label=label,
                ring_speed=ring_speed,
                pulse_speed=pulse_speed,
            )

    if COLORS["panel_elevated"] == DEFAULT_COLORS["panel_elevated"] and COLORS["panel"] != DEFAULT_COLORS["panel"]:
        COLORS["panel_elevated"] = blend(COLORS["panel"], COLORS["accent_secondary"], 0.2)
    if COLORS["border"] == DEFAULT_COLORS["border"]:
        COLORS["border"] = blend(COLORS["panel"], COLORS["accent_secondary"], 0.3)


def _reset_defaults() -> None:
    COLORS.clear()
    COLORS.update(DEFAULT_COLORS)
    STATE_VISUALS.clear()
    STATE_VISUALS.update(DEFAULT_STATE_VISUALS)


def _is_hex_color(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if len(text) != 7 or not text.startswith("#"):
        return False
    try:
        int(text[1:], 16)
    except Exception:
        return False
    return True


def _coerce_positive_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return fallback
    if parsed <= 0:
        return fallback
    return parsed
