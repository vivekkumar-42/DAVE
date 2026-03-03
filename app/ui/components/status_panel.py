from __future__ import annotations

import math
from typing import Any

import customtkinter
import tkinter

from app.ui.theme import COLORS, FONTS, blend


class StatusCard(customtkinter.CTkFrame):
    LEVEL_COLOR: dict[str, str] = {
        "good": COLORS["success"],
        "warning": COLORS["warning"],
        "error": COLORS["error"],
        "active": COLORS["accent_primary"],
        "idle": COLORS["text_secondary"],
    }

    def __init__(self, master: Any, title: str) -> None:
        super().__init__(
            master,
            fg_color=blend(COLORS["panel_elevated"], COLORS["glass_tint"], 0.4),
            corner_radius=12,
            border_width=1,
            border_color=blend(COLORS["border"], COLORS["glass_edge"], 0.22),
            height=88,
        )
        self.pack_propagate(False)
        self._pulse_phase = 0.0
        self._active_color = COLORS["text_secondary"]
        self._after_id: str | None = None
        self._pulse_enabled = False
        self._animations_enabled = True

        top = customtkinter.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(8, 2))
        top.grid_columnconfigure(1, weight=1)

        self.indicator = tkinter.Canvas(
            top,
            width=14,
            height=14,
            bg=blend(COLORS["panel_elevated"], COLORS["glass_tint"], 0.4),
            highlightthickness=0,
            bd=0,
        )
        self.indicator.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.indicator_dot = self.indicator.create_oval(3, 3, 11, 11, fill=COLORS["text_secondary"], outline="")

        self.title_label = customtkinter.CTkLabel(
            top,
            text=title,
            font=FONTS["small"],
            text_color=COLORS["text_secondary"],
            anchor="w",
        )
        self.title_label.grid(row=0, column=1, sticky="w")

        self.value_label = customtkinter.CTkLabel(
            self,
            text="--",
            font=FONTS["body_bold"],
            text_color=COLORS["text_primary"],
            anchor="w",
            justify="left",
        )
        self.value_label.pack(fill="x", padx=10, pady=(1, 4))

    def set_value(self, value: str, level: str = "idle") -> None:
        normalized_level = level.strip().lower() if isinstance(level, str) else "idle"
        color = self.LEVEL_COLOR.get(normalized_level, COLORS["text_secondary"])
        self._active_color = color
        self.value_label.configure(text=value)
        self.indicator.itemconfig(self.indicator_dot, fill=color)
        border = blend(
            blend(COLORS["border"], COLORS["glass_edge"], 0.2),
            color,
            0.45 if normalized_level in {"good", "active"} else 0.3,
        )
        self.configure(border_color=border)
        self._pulse_enabled = normalized_level in {"active", "warning", "error"}
        if self._pulse_enabled and self._animations_enabled:
            self._ensure_pulse_loop()
        elif self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _tick_pulse(self) -> None:
        self._after_id = None
        if not self.winfo_exists():
            return
        if not self._pulse_enabled or not self._animations_enabled:
            return
        self._pulse_phase += 0.15
        ratio = 0.4 + ((math.sin(self._pulse_phase) + 1.0) * 0.5) * 0.45
        pulse_color = blend(COLORS["panel_elevated"], self._active_color, ratio)
        self.indicator.itemconfig(self.indicator_dot, fill=pulse_color)
        self._after_id = self.after(78, self._tick_pulse)

    def set_animation_active(self, active: bool) -> None:
        self._animations_enabled = bool(active)
        if not self._animations_enabled:
            if self._after_id is not None:
                try:
                    self.after_cancel(self._after_id)
                except Exception:
                    pass
                self._after_id = None
            self.indicator.itemconfig(self.indicator_dot, fill=self._active_color)
            return
        if self._pulse_enabled:
            self._ensure_pulse_loop()

    def _ensure_pulse_loop(self) -> None:
        if self._after_id is not None:
            return
        self._after_id = self.after(78, self._tick_pulse)

    def shutdown(self) -> None:
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None


class StatusPanel(customtkinter.CTkFrame):
    def __init__(self, master: Any) -> None:
        super().__init__(
            master,
            fg_color=blend(COLORS["panel"], COLORS["glass_tint"], 0.26),
            width=296,
            corner_radius=14,
            border_width=1,
            border_color=blend(COLORS["border"], COLORS["glass_edge"], 0.22),
        )
        self.pack_propagate(False)

        title = customtkinter.CTkLabel(
            self,
            text="System Status",
            font=FONTS["header"],
            text_color=COLORS["text_primary"],
        )
        title.pack(anchor="w", padx=12, pady=(12, 8))

        card_specs = [
            ("voice_engine", "Voice Engine"),
            ("llm_provider", "LLM Provider"),
            ("provider_reliability", "Provider Reliability"),
            ("automation_engine", "Automation Engine"),
            ("thread_status", "Thread Status"),
            ("execution_state", "Execution State"),
            ("system_readiness", "System Readiness"),
        ]
        self.cards: dict[str, StatusCard] = {}
        for key, card_title in card_specs:
            card = StatusCard(self, title=card_title)
            card.pack(fill="x", padx=10, pady=5)
            self.cards[key] = card
        self._card_cache: dict[str, tuple[str, str]] = {}

    def update_card(self, key: str, value: str, level: str = "idle") -> None:
        card = self.cards.get(key)
        if card is None:
            return
        normalized_level = level.strip().lower() if isinstance(level, str) else "idle"
        cache_key = (str(value), normalized_level)
        if self._card_cache.get(key) == cache_key:
            return
        self._card_cache[key] = cache_key
        card.set_value(value=value, level=normalized_level)

    def set_animation_active(self, active: bool) -> None:
        for card in self.cards.values():
            card.set_animation_active(active)

    def shutdown(self) -> None:
        for card in self.cards.values():
            card.shutdown()
