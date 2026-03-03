from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import customtkinter

from app.ui.theme import COLORS, FONTS, blend


@dataclass(slots=True)
class SectionSpec:
    key: str
    title: str
    subtitle: str
    default_enabled: bool = True


class ControlSection(customtkinter.CTkFrame):
    def __init__(
        self,
        master: Any,
        spec: SectionSpec,
        on_select: Callable[[str], None],
        on_toggle: Callable[[str, bool], None],
    ) -> None:
        super().__init__(
            master,
            fg_color=blend(COLORS["panel_elevated"], COLORS["glass_tint"], 0.38),
            corner_radius=12,
            border_width=1,
            border_color=blend(COLORS["border"], COLORS["glass_edge"], 0.22),
            height=78,
        )
        self.pack_propagate(False)
        self.spec = spec
        self._on_select = on_select
        self._on_toggle = on_toggle
        self._hover_ratio = 0.0
        self._hover_target = 0.0
        self._active = False
        self._hover_after_id: str | None = None

        self.grid_columnconfigure(0, weight=1)

        self.top_sheen = customtkinter.CTkFrame(
            self,
            fg_color=blend(COLORS["skeuo_highlight"], COLORS["panel"], 0.82),
            height=1,
            corner_radius=2,
        )
        self.top_sheen.grid(row=0, column=0, sticky="new", padx=8, pady=(2, 0))

        header = customtkinter.CTkFrame(self, fg_color="transparent")
        header.grid(row=1, column=0, sticky="ew", padx=10, pady=(8, 0))
        header.grid_columnconfigure(0, weight=1)

        self.title_label = customtkinter.CTkLabel(
            header,
            text=spec.title,
            font=FONTS["body_bold"],
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        self.title_label.grid(row=0, column=0, sticky="w")

        self.switch = customtkinter.CTkSwitch(
            header,
            text="",
            width=42,
            progress_color=COLORS["accent_primary"],
            button_color=COLORS["panel"],
            button_hover_color=COLORS["panel"],
            command=self._emit_toggle,
        )
        self.switch.grid(row=0, column=1, sticky="e")

        self.subtitle_label = customtkinter.CTkLabel(
            self,
            text=spec.subtitle,
            font=FONTS["small"],
            text_color=COLORS["text_secondary"],
            anchor="w",
        )
        self.subtitle_label.grid(row=2, column=0, sticky="w", padx=10, pady=(3, 8))

        if spec.default_enabled:
            self.switch.select()
        else:
            self.switch.deselect()

        self._bind_click(self)
        self._bind_click(self.title_label)
        self._bind_click(self.subtitle_label)
        self._bind_click(header)
        self.bind("<Enter>", self._handle_enter)
        self.bind("<Leave>", self._handle_leave)
        header.bind("<Enter>", self._handle_enter)
        header.bind("<Leave>", self._handle_leave)
        self._apply_hover_style()

    def _bind_click(self, widget: Any) -> None:
        widget.bind("<Button-1>", lambda _event: self._on_select(self.spec.key))

    def _emit_toggle(self) -> None:
        self._on_toggle(self.spec.key, bool(self.switch.get()))

    def _handle_enter(self, _event: Any) -> None:
        self._hover_target = 1.0
        self._ensure_hover_animation()

    def _handle_leave(self, _event: Any) -> None:
        self._hover_target = 0.0
        self._ensure_hover_animation()

    def set_active(self, active: bool) -> None:
        self._active = active
        border = blend(COLORS["glass_edge"], COLORS["accent_primary"], 0.52) if active else blend(COLORS["border"], COLORS["glass_edge"], 0.2)
        self.configure(border_color=border)
        title_color = COLORS["accent_primary"] if active else COLORS["text_primary"]
        self.title_label.configure(text_color=title_color)
        self._apply_hover_style()

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.switch.select()
        else:
            self.switch.deselect()

    def is_enabled(self) -> bool:
        return bool(self.switch.get())

    def _animate_hover(self) -> None:
        self._hover_after_id = None
        if not self.winfo_exists():
            return
        self._hover_ratio += (self._hover_target - self._hover_ratio) * 0.2
        if abs(self._hover_ratio - self._hover_target) < 0.01:
            self._hover_ratio = self._hover_target
        self._apply_hover_style()
        if abs(self._hover_ratio - self._hover_target) < 0.01:
            return
        self._hover_after_id = self.after(16, self._animate_hover)

    def _apply_hover_style(self) -> None:
        active_boost = 0.26 if self._active else 0.0
        blend_ratio = min(1.0, self._hover_ratio * 0.65 + active_boost)
        bg_color = blend(
            blend(COLORS["panel_elevated"], COLORS["glass_tint"], 0.36),
            COLORS["accent_secondary"],
            blend_ratio * 0.36,
        )
        self.configure(fg_color=bg_color)

    def _ensure_hover_animation(self) -> None:
        if self._hover_after_id is not None:
            return
        self._hover_after_id = self.after(16, self._animate_hover)

    def shutdown(self) -> None:
        if self._hover_after_id:
            try:
                self.after_cancel(self._hover_after_id)
            except Exception:
                pass
            self._hover_after_id = None


class SidePanel(customtkinter.CTkFrame):
    def __init__(
        self,
        master: Any,
        on_select: Callable[[str], None],
        on_toggle: Callable[[str, bool], None],
    ) -> None:
        super().__init__(
            master,
            fg_color=blend(COLORS["panel"], COLORS["glass_tint"], 0.28),
            width=262,
            corner_radius=14,
            border_width=1,
            border_color=blend(COLORS["border"], COLORS["glass_edge"], 0.22),
        )
        self.pack_propagate(False)

        self.header = customtkinter.CTkLabel(
            self,
            text="CONTROL MATRIX",
            font=FONTS["header"],
            text_color=COLORS["text_primary"],
        )
        self.header.pack(anchor="w", padx=14, pady=(12, 8))

        self.sections: dict[str, ControlSection] = {}
        specs = [
            SectionSpec("assistant_control", "Assistant Control", "Master runtime enable"),
            SectionSpec("voice_engine", "Voice Engine", "Speech input and output"),
            SectionSpec("automation_engine", "Automation Engine", "Local action routing"),
            SectionSpec("llm_providers", "LLM Providers", "Cloud/local language models"),
            SectionSpec("system_monitoring", "System Monitoring", "Live diagnostics pipeline"),
            SectionSpec("settings", "Settings", "Extended diagnostics mode", default_enabled=False),
        ]

        for spec in specs:
            section = ControlSection(
                self,
                spec=spec,
                on_select=on_select,
                on_toggle=on_toggle,
            )
            section.pack(fill="x", padx=10, pady=5)
            self.sections[spec.key] = section

        self.set_active("assistant_control")

    def set_active(self, section_key: str) -> None:
        for key, section in self.sections.items():
            section.set_active(key == section_key)

    def set_section_enabled(self, section_key: str, enabled: bool) -> None:
        section = self.sections.get(section_key)
        if section is None:
            return
        section.set_enabled(enabled)

    def get_section_enabled(self, section_key: str) -> bool:
        section = self.sections.get(section_key)
        return section.is_enabled() if section is not None else False

    def shutdown(self) -> None:
        for section in self.sections.values():
            section.shutdown()
