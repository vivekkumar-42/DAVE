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
            fg_color=blend(COLORS["panel"], COLORS["glass_tint"], 0.3),
            corner_radius=16,
            border_width=1,
            border_color=blend(COLORS["border"], COLORS["glass_edge"], 0.22),
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
        self._needs_static_redraw = True
        self._items_ready = False
        self._shadow_item: int | None = None
        self._glow_items: list[int] = []
        self._ring_items: list[int] = []
        self._wave_items: list[int] = []
        self._core_outer_item: int | None = None
        self._core_inner_item: int | None = None
        self._core_shine_item: int | None = None
        self._bevel_top_item: int | None = None
        self._bevel_bottom_item: int | None = None

        self.canvas = tkinter.Canvas(
            self,
            bg=blend(COLORS["panel"], COLORS["glass_tint"], 0.24),
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
        if normalized == self._state:
            return
        self._state = normalized
        self._needs_static_redraw = True
        visual = STATE_VISUALS.get(self._state, STATE_VISUALS["NORMAL"])
        self._draw_frame(visual, animated=self._animations_enabled)
        self._needs_static_redraw = False

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
        self._needs_static_redraw = True
        visual = STATE_VISUALS.get(self._state, STATE_VISUALS["NORMAL"])
        self._draw_frame(visual, animated=self._animations_enabled)
        self._needs_static_redraw = False

    def _on_resize(self, event: Any) -> None:
        self._canvas_width = int(event.width)
        self._canvas_height = int(event.height)
        self._needs_static_redraw = True

    def _animate(self) -> None:
        if not self._running or not self.winfo_exists():
            return

        visual = STATE_VISUALS.get(self._state, STATE_VISUALS["NORMAL"])
        frame_delay = 34 if self._animations_enabled else 260
        if self._animations_enabled:
            now = time.perf_counter()
            dt = clamp(now - self._last_tick, 0.0, 0.08)
            self._last_tick = now
            self._ring_angle = (self._ring_angle + visual.ring_speed * 72.0 * dt) % 360.0
            self._pulse_phase = (self._pulse_phase + visual.pulse_speed * 6.3) % (math.tau * 6.0)
            self._wave_phase = (self._wave_phase + max(0.55, visual.ring_speed * 1.28)) % 10000.0
            self._needs_static_redraw = True

        if self._animations_enabled or self._needs_static_redraw:
            self._draw_frame(visual, animated=self._animations_enabled)
            self._needs_static_redraw = False
        self._after_id = self.after(frame_delay, self._animate)

    def _ensure_items(self) -> None:
        if self._items_ready:
            return
        panel_mix = blend(COLORS["panel"], COLORS["glass_tint"], 0.3)
        self._shadow_item = self.canvas.create_oval(0, 0, 0, 0, outline="", fill=blend(COLORS["glass_shadow"], panel_mix, 0.2))
        self._glow_items = [self.canvas.create_oval(0, 0, 0, 0, outline="", fill=panel_mix) for _ in range(6)]
        self._wave_items = [
            self.canvas.create_oval(0, 0, 0, 0, outline=COLORS["accent_primary"], width=1, state="hidden")
            for _ in range(4)
        ]
        self._ring_items = [
            self.canvas.create_arc(0, 0, 0, 0, style="arc", outline=COLORS["accent_primary"], width=1)
            for _ in range(6)
        ]
        self._core_outer_item = self.canvas.create_oval(0, 0, 0, 0, outline=COLORS["accent_primary"], fill=COLORS["panel_elevated"], width=2)
        self._bevel_top_item = self.canvas.create_arc(0, 0, 0, 0, start=26, extent=148, style="arc", outline=COLORS["skeuo_highlight"], width=2)
        self._bevel_bottom_item = self.canvas.create_arc(0, 0, 0, 0, start=212, extent=126, style="arc", outline=COLORS["skeuo_shadow"], width=2)
        self._core_inner_item = self.canvas.create_oval(0, 0, 0, 0, outline="", fill=COLORS["accent_primary"])
        self._core_shine_item = self.canvas.create_oval(0, 0, 0, 0, outline="", fill=COLORS["skeuo_highlight"])
        self._items_ready = True

    def _draw_frame(self, visual: Any, *, animated: bool) -> None:
        self._ensure_items()
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
        self._draw_shadow(center_x, center_y, core_radius)
        self._draw_glow(center_x, center_y, core_radius, visual.glow, glow_strength)
        self._draw_rings(center_x, center_y, core_radius, visual.accent)
        self._draw_core(center_x, center_y, core_radius, visual.accent, pulse)
        self._draw_state_waves(center_x, center_y, core_radius, visual, animated=animated)

    def _draw_shadow(
        self,
        center_x: float,
        center_y: float,
        core_radius: float,
    ) -> None:
        if self._shadow_item is None:
            return
        shadow_rx = core_radius * 1.75
        shadow_ry = core_radius * 0.55
        self.canvas.coords(
            self._shadow_item,
            center_x - shadow_rx,
            center_y + core_radius * 0.92 - shadow_ry,
            center_x + shadow_rx,
            center_y + core_radius * 0.92 + shadow_ry,
        )
        self.canvas.itemconfig(
            self._shadow_item,
            fill=blend(COLORS["glass_shadow"], COLORS["panel"], 0.24),
        )

    def _draw_glow(
        self,
        center_x: float,
        center_y: float,
        core_radius: float,
        glow_color: str,
        strength: float,
    ) -> None:
        panel_mix = blend(COLORS["panel"], COLORS["glass_tint"], 0.3)
        total_layers = len(self._glow_items)
        compact = min(self._canvas_width, self._canvas_height) < 190
        min_visible_layer = 2 if compact else 0
        for idx, item in enumerate(self._glow_items):
            layer = total_layers - idx
            if layer <= min_visible_layer:
                self.canvas.itemconfig(item, state="hidden")
                continue
            ratio = layer / total_layers
            radius = core_radius + (layer * 12)
            color = blend(COLORS["panel"], glow_color, clamp(strength * ratio * 0.6, 0.0, 0.9))
            self.canvas.coords(
                item,
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius,
            )
            self.canvas.itemconfig(item, fill=blend(panel_mix, color, 0.76), outline="", state="normal")

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
        for idx, (radius, width, extent, start) in enumerate(ring_specs):
            primary = self._ring_items[idx * 2]
            secondary = self._ring_items[idx * 2 + 1]
            self.canvas.coords(
                primary,
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius,
            )
            self.canvas.itemconfig(
                primary,
                start=start,
                extent=extent,
                outline=accent,
                width=width,
            )
            self.canvas.coords(
                secondary,
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius,
            )
            self.canvas.itemconfig(
                secondary,
                start=start + 180,
                extent=max(22, extent * 0.42),
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
        fill_color = blend(
            blend(COLORS["panel_elevated"], COLORS["glass_tint"], 0.22),
            accent,
            0.44 + pulse * 0.32,
        )
        outline_color = blend(accent, COLORS["skeuo_highlight"], 0.32)
        if self._core_outer_item is None or self._core_inner_item is None or self._core_shine_item is None:
            return
        self.canvas.coords(
            self._core_outer_item,
            center_x - inner,
            center_y - inner,
            center_x + inner,
            center_y + inner,
        )
        self.canvas.itemconfig(self._core_outer_item, fill=fill_color, outline=outline_color, width=2)

        if self._bevel_top_item is not None and self._bevel_bottom_item is not None:
            self.canvas.coords(
                self._bevel_top_item,
                center_x - inner,
                center_y - inner,
                center_x + inner,
                center_y + inner,
            )
            self.canvas.coords(
                self._bevel_bottom_item,
                center_x - inner,
                center_y - inner,
                center_x + inner,
                center_y + inner,
            )
            self.canvas.itemconfig(
                self._bevel_top_item,
                outline=blend(COLORS["skeuo_highlight"], accent, 0.35),
            )
            self.canvas.itemconfig(
                self._bevel_bottom_item,
                outline=blend(COLORS["skeuo_shadow"], COLORS["panel"], 0.25),
            )

        inner_core = inner * 0.52
        self.canvas.coords(
            self._core_inner_item,
            center_x - inner_core,
            center_y - inner_core,
            center_x + inner_core,
            center_y + inner_core,
        )
        self.canvas.itemconfig(
            self._core_inner_item,
            fill=blend(accent, COLORS["skeuo_highlight"], 0.24),
            outline="",
        )
        shine_radius = inner_core * 0.56
        shine_x = center_x - inner_core * 0.24
        shine_y = center_y - inner_core * 0.34
        self.canvas.coords(
            self._core_shine_item,
            shine_x - shine_radius,
            shine_y - shine_radius,
            shine_x + shine_radius,
            shine_y + shine_radius,
        )
        self.canvas.itemconfig(
            self._core_shine_item,
            fill=blend(COLORS["skeuo_highlight"], accent, 0.14),
            outline="",
        )

    def _draw_state_waves(
        self,
        center_x: float,
        center_y: float,
        core_radius: float,
        visual: Any,
        *,
        animated: bool,
    ) -> None:
        for item in self._wave_items:
            self.canvas.itemconfig(item, state="hidden")

        max_radius = min(self._canvas_width, self._canvas_height) * 0.48
        if not animated:
            radius = core_radius + 34
            self._set_wave(0, center_x, center_y, radius, blend(COLORS["panel"], visual.glow, 0.46), 1)
            return

        if self._state in {"LISTENING", "SPEAKING"}:
            for idx in range(4):
                phase = ((self._wave_phase * 0.016) + idx * 0.24) % 1.0
                radius = core_radius + 8 + phase * (max_radius - core_radius)
                intensity = (1.0 - phase) * (0.55 if self._state == "LISTENING" else 0.74)
                color = blend(COLORS["panel"], visual.glow, intensity)
                self._set_wave(idx, center_x, center_y, radius, color, 2 if self._state == "SPEAKING" else 1)
        elif self._state == "EXECUTING":
            radius = core_radius + ((math.sin(self._wave_phase * 0.3) + 1.0) * 0.5) * 40
            color = blend(COLORS["panel"], visual.accent, 0.8)
            self._set_wave(0, center_x, center_y, radius, color, 3)
            self._set_wave(1, center_x, center_y, radius + 14, blend(COLORS["panel"], visual.glow, 0.6), 1)
        elif self._state == "ERROR":
            radius = core_radius + ((math.sin(self._wave_phase * 0.18) + 1.0) * 0.5) * 22
            color = blend(COLORS["panel"], COLORS["error"], 0.86)
            self._set_wave(0, center_x, center_y, radius, color, 3)

    def _set_wave(
        self,
        index: int,
        center_x: float,
        center_y: float,
        radius: float,
        color: str,
        width: int,
    ) -> None:
        if index < 0 or index >= len(self._wave_items):
            return
        item = self._wave_items[index]
        self.canvas.coords(
            item,
            center_x - radius,
            center_y - radius,
            center_x + radius,
            center_y + radius,
        )
        self.canvas.itemconfig(item, outline=color, width=width, state="normal")
