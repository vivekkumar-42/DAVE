from __future__ import annotations

import math
import time
import tkinter
from typing import Any

import customtkinter

from app.ui.theme import COLORS, FONTS, STATE_VISUALS, blend


class TopBar(customtkinter.CTkFrame):
    def __init__(self, master: Any) -> None:
        super().__init__(
            master,
            fg_color=blend(COLORS["panel"], COLORS["glass_tint"], 0.28),
            corner_radius=0,
            height=50,
            border_width=1,
            border_color=blend(COLORS["border"], COLORS["glass_edge"], 0.24),
        )
        self.pack_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=1)

        self._identity_phase = 0.0
        self._state = "NORMAL"
        self._glow_after_id: str | None = None
        self._activity_tick_counter = 0
        self._animations_enabled = True
        self._state_label_cache = "SYSTEM: NORMAL"
        self._indicator_cache: dict[str, tuple[str, str]] = {}
        self._identity_color_cache = COLORS["accent_primary"]

        self.left_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        self.left_frame.grid(row=0, column=0, sticky="w", padx=(14, 0), pady=4)

        self.identity_label = customtkinter.CTkLabel(
            self.left_frame,
            text="DAVE",
            font=FONTS["header"],
            text_color=self._identity_color_cache,
        )
        self.identity_label.pack(anchor="w")

        self.center_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        self.center_frame.grid(row=0, column=1, sticky="nsew")

        self.state_pill = customtkinter.CTkFrame(
            self.center_frame,
            fg_color=blend(COLORS["panel_elevated"], COLORS["glass_tint"], 0.42),
            border_width=1,
            border_color=blend(COLORS["border"], COLORS["glass_edge"], 0.26),
            corner_radius=16,
            height=34,
        )
        self.state_pill.pack(pady=8)

        self.state_canvas = tkinter.Canvas(
            self.state_pill,
            width=18,
            height=18,
            highlightthickness=0,
            bg=blend(COLORS["panel_elevated"], COLORS["glass_tint"], 0.42),
        )
        self.state_canvas.pack(side="left", padx=(10, 6), pady=7)
        self.state_dot = self.state_canvas.create_oval(4, 4, 14, 14, outline="", fill=COLORS["accent_primary"])

        self.state_label = customtkinter.CTkLabel(
            self.state_pill,
            text="SYSTEM: NORMAL",
            font=FONTS["body_bold"],
            text_color=COLORS["text_primary"],
        )
        self.state_label.pack(side="left", padx=(0, 12))

        self.right_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        self.right_frame.grid(row=0, column=2, sticky="e", padx=(0, 12), pady=6)

        self.indicators: dict[str, customtkinter.CTkLabel] = {}
        self._indicator_meta: dict[str, tuple[str, str]] = {
            "microphone": ("MIC", "OFF"),
            "provider": ("LLM", "N/A"),
            "latency": ("LAT", "-- ms"),
            "activity": ("ACT", "IDLE"),
        }
        for key, (prefix, default) in self._indicator_meta.items():
            label = customtkinter.CTkLabel(
                self.right_frame,
                text=f"{prefix}: {default}",
                font=FONTS["small"],
                text_color=COLORS["text_secondary"],
                fg_color=blend(COLORS["panel_elevated"], COLORS["glass_tint"], 0.34),
                corner_radius=10,
                padx=10,
                pady=4,
            )
            label.pack(side="left", padx=4)
            self.indicators[key] = label

        self._tick_glow()

    def set_state(self, state: str) -> None:
        normalized = state.strip().upper() if isinstance(state, str) else "NORMAL"
        if normalized not in STATE_VISUALS:
            normalized = "NORMAL"
        if normalized == self._state:
            return
        self._state = normalized
        visual = STATE_VISUALS[normalized]
        label_text = f"SYSTEM: {visual.label}"
        if label_text != self._state_label_cache:
            self.state_label.configure(text=label_text)
            self._state_label_cache = label_text
        self.state_canvas.itemconfig(self.state_dot, fill=visual.accent)
        self.state_pill.configure(border_color=blend(COLORS["border"], visual.accent, 0.55))
        if not self._animations_enabled and self._identity_color_cache != visual.accent:
            self._identity_color_cache = visual.accent
            self.identity_label.configure(text_color=self._identity_color_cache)

    def set_indicator(self, key: str, value: str, level: str = "neutral") -> None:
        label = self.indicators.get(key)
        if label is None:
            return
        prefix, _ = self._indicator_meta.get(key, ("", ""))
        color = COLORS["text_secondary"]
        normalized_level = (level or "").lower()
        cache_key = (str(value), normalized_level)
        if self._indicator_cache.get(key) == cache_key:
            return
        if normalized_level == "success":
            color = COLORS["success"]
        elif normalized_level == "warning":
            color = COLORS["warning"]
        elif normalized_level == "error":
            color = COLORS["error"]
        elif normalized_level == "active":
            color = STATE_VISUALS[self._state].accent
        label.configure(text=f"{prefix}: {value}", text_color=color)
        self._indicator_cache[key] = cache_key

    def set_animation_active(self, active: bool) -> None:
        self._animations_enabled = bool(active)

    def _tick_glow(self) -> None:
        if not self.winfo_exists():
            return
        delay = 72 if self._animations_enabled else 260
        state_visual = STATE_VISUALS.get(self._state, STATE_VISUALS["NORMAL"])

        if self._animations_enabled:
            self._activity_tick_counter += 1
            self._identity_phase += 0.09
            glow_ratio = 0.38 + ((math.sin(self._identity_phase) + 1.0) * 0.5) * 0.52
            color = blend(COLORS["accent_secondary"], state_visual.glow, glow_ratio)
            if color != self._identity_color_cache:
                self._identity_color_cache = color
                self.identity_label.configure(text_color=color)

            if self._state in {"PROCESSING", "EXECUTING", "SPEAKING"} and self._activity_tick_counter % 8 == 0:
                activity = f"LIVE {time.strftime('%H:%M:%S')}"
                self.set_indicator("activity", activity, level="active")
        else:
            if self._identity_color_cache != state_visual.accent:
                self._identity_color_cache = state_visual.accent
                self.identity_label.configure(text_color=state_visual.accent)

        self._glow_after_id = self.after(delay, self._tick_glow)

    def shutdown(self) -> None:
        if self._glow_after_id:
            try:
                self.after_cancel(self._glow_after_id)
            except Exception:
                pass
            self._glow_after_id = None
