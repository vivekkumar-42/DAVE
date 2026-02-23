from __future__ import annotations

import math
import time
import tkinter
from typing import Any

import customtkinter

from app.ui.theme import COLORS, STATE_VISUALS, blend, clamp


class ReactorAnimation(customtkinter.CTkFrame):
    def __init__(self, master: Any) -> None:
        super().__init__(
            master,
            fg_color=COLORS["panel"],
            corner_radius=16,
            border_width=1,
            border_color=COLORS["border"],
        )
        self.pack_propagate(False)
        self._state = "NORMAL"
        self._running = True
        self._animations_enabled = True

        self._ring_angle = 0.0
        self._pulse_phase = 0.0
        self._wave_phase = 0.0
        self._last_tick = time.perf_counter()
        self._after_id: str | None = None

        self.canvas = tkinter.Canvas(
            self,
            bg=COLORS["panel"],
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True, padx=8, pady=8)
        self.canvas.bind("<Configure>", self._on_resize)
        self._canvas_width = 440
        self._canvas_height = 260
        self._animate()

    def set_state(self, state: str) -> None:
        normalized = state.strip().upper() if isinstance(state, str) else "NORMAL"
        if normalized not in STATE_VISUALS:
            normalized = "NORMAL"
        self._state = normalized

    def shutdown(self) -> None:
        self._running = False
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def set_animation_active(self, active: bool) -> None:
        self._animations_enabled = bool(active)
        self._last_tick = time.perf_counter()

    def _on_resize(self, event: Any) -> None:
        self._canvas_width = int(event.width)
        self._canvas_height = int(event.height)

    def _animate(self) -> None:
        if not self._running or not self.winfo_exists():
            return

        visual = STATE_VISUALS.get(self._state, STATE_VISUALS["NORMAL"])
        frame_delay = 40 if self._animations_enabled else 220
        if self._animations_enabled:
            now = time.perf_counter()
            dt = clamp(now - self._last_tick, 0.0, 0.09)
            self._last_tick = now
            self._ring_angle = (self._ring_angle + visual.ring_speed * 58.0 * dt) % 360.0
            self._pulse_phase += visual.pulse_speed * 7.0
            self._wave_phase += max(0.6, visual.ring_speed * 1.35)

        self._draw_frame(visual, animated=self._animations_enabled)
        self._after_id = self.after(frame_delay, self._animate)

    def _draw_frame(self, visual: Any, *, animated: bool) -> None:
        self.canvas.delete("all")
        width = max(120, self._canvas_width)
        height = max(120, self._canvas_height)
        center_x = width * 0.5
        center_y = height * 0.5

        core_radius = min(width, height) * 0.14
        pulse = (math.sin(self._pulse_phase) + 1.0) * 0.5 if animated else 0.5
        glow_boost = 0.0
        if self._state == "EXECUTING":
            glow_boost = 0.25
        elif self._state == "ERROR":
            glow_boost = 0.35

        glow_strength = 0.32 + pulse * 0.42 + glow_boost
        self._draw_glow(center_x, center_y, core_radius, visual.glow, glow_strength)
        self._draw_rings(center_x, center_y, core_radius, visual.accent)
        self._draw_core(center_x, center_y, core_radius, visual.accent, pulse)
        if animated:
            self._draw_state_waves(center_x, center_y, core_radius, visual)

    def _draw_glow(
        self,
        center_x: float,
        center_y: float,
        core_radius: float,
        glow_color: str,
        strength: float,
    ) -> None:
        layers = 6
        for layer in range(layers, 0, -1):
            ratio = layer / layers
            radius = core_radius + (layer * 13)
            color = blend(COLORS["panel"], glow_color, clamp(strength * ratio * 0.6, 0.0, 0.9))
            self.canvas.create_oval(
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius,
                outline="",
                fill=color,
            )

    def _draw_rings(
        self,
        center_x: float,
        center_y: float,
        core_radius: float,
        accent: str,
    ) -> None:
        ring_specs = [
            (core_radius + 34, 2, 120, self._ring_angle),
            (core_radius + 54, 2, 92, -self._ring_angle * 1.2 + 70),
            (core_radius + 76, 1, 60, self._ring_angle * 0.68 + 140),
        ]
        for radius, width, extent, start in ring_specs:
            self.canvas.create_arc(
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius,
                start=start,
                extent=extent,
                style="arc",
                outline=accent,
                width=width,
            )
            self.canvas.create_arc(
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius,
                start=start + 180,
                extent=max(22, extent * 0.42),
                style="arc",
                outline=blend(accent, COLORS["panel"], 0.42),
                width=max(1, width - 1),
            )

    def _draw_core(
        self,
        center_x: float,
        center_y: float,
        core_radius: float,
        accent: str,
        pulse: float,
    ) -> None:
        inner = core_radius * (0.86 + pulse * 0.08)
        fill_color = blend(COLORS["panel_elevated"], accent, 0.45 + pulse * 0.35)
        outline_color = blend(accent, "#FFFFFF", 0.22)
        self.canvas.create_oval(
            center_x - inner,
            center_y - inner,
            center_x + inner,
            center_y + inner,
            fill=fill_color,
            outline=outline_color,
            width=2,
        )
        inner_core = inner * 0.52
        self.canvas.create_oval(
            center_x - inner_core,
            center_y - inner_core,
            center_x + inner_core,
            center_y + inner_core,
            fill=blend(accent, "#FFFFFF", 0.28),
            outline="",
        )

    def _draw_state_waves(
        self,
        center_x: float,
        center_y: float,
        core_radius: float,
        visual: Any,
    ) -> None:
        max_radius = min(self._canvas_width, self._canvas_height) * 0.48
        if self._state in {"LISTENING", "SPEAKING"}:
            for idx in range(4):
                phase = ((self._wave_phase * 0.016) + idx * 0.24) % 1.0
                radius = core_radius + 8 + phase * (max_radius - core_radius)
                intensity = (1.0 - phase) * (0.55 if self._state == "LISTENING" else 0.74)
                color = blend(COLORS["panel"], visual.glow, intensity)
                self.canvas.create_oval(
                    center_x - radius,
                    center_y - radius,
                    center_x + radius,
                    center_y + radius,
                    outline=color,
                    width=2 if self._state == "SPEAKING" else 1,
                )
        elif self._state == "EXECUTING":
            radius = core_radius + ((math.sin(self._wave_phase * 0.3) + 1.0) * 0.5) * 40
            color = blend(COLORS["panel"], visual.accent, 0.8)
            self.canvas.create_oval(
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius,
                outline=color,
                width=3,
            )
        elif self._state == "ERROR":
            radius = core_radius + ((math.sin(self._wave_phase * 0.18) + 1.0) * 0.5) * 22
            color = blend(COLORS["panel"], COLORS["error"], 0.86)
            self.canvas.create_oval(
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius,
                outline=color,
                width=3,
            )
