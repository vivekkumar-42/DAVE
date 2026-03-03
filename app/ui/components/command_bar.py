from __future__ import annotations

from typing import Any, Callable

import customtkinter

from app.ui.theme import COLORS, FONTS, STATE_VISUALS, blend


class CommandBar(customtkinter.CTkFrame):
    def __init__(
        self,
        master: Any,
        on_execute: Callable[[str], None],
        on_toggle_mic: Callable[[], None],
        on_interrupt: Callable[[], None],
        on_clear: Callable[[], None],
    ) -> None:
        super().__init__(
            master,
            fg_color=blend(COLORS["panel"], COLORS["glass_tint"], 0.24),
            corner_radius=0,
            border_width=1,
            border_color=blend(COLORS["border"], COLORS["glass_edge"], 0.2),
            height=84,
        )
        self.pack_propagate(False)
        self._on_execute = on_execute
        self._on_toggle_mic = on_toggle_mic
        self._on_interrupt = on_interrupt
        self._on_clear = on_clear

        self._focus_ratio = 0.0
        self._focus_target = 0.0
        self._state = "NORMAL"
        self._focus_after_id: str | None = None

        self.history: list[str] = []
        self.history_index = 0

        inner = customtkinter.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=14, pady=12)
        inner.grid_columnconfigure(0, weight=1)

        self.command_entry = customtkinter.CTkEntry(
            inner,
            placeholder_text="Enter command...",
            font=FONTS["body"],
            fg_color=blend(COLORS["panel_elevated"], COLORS["glass_tint"], 0.35),
            text_color=COLORS["text_primary"],
            border_width=2,
            border_color=blend(COLORS["border"], COLORS["glass_edge"], 0.24),
            height=42,
        )
        self.command_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.command_entry.bind("<Return>", self._on_enter)
        self.command_entry.bind("<Up>", self._on_history_up)
        self.command_entry.bind("<Down>", self._on_history_down)
        self.command_entry.bind("<FocusIn>", lambda _event: self._set_focus(True))
        self.command_entry.bind("<FocusOut>", lambda _event: self._set_focus(False))

        self.execute_button = self._build_button(
            inner,
            text="Execute",
            command=lambda: self._emit_execute(),
            fg_color=COLORS["accent_primary"],
            hover_color=blend(COLORS["accent_primary"], "#FFFFFF", 0.2),
            text_color="#071018",
            column=1,
            width=102,
        )
        self.mic_button = self._build_button(
            inner,
            text="Mic Off",
            command=self._on_toggle_mic,
            fg_color=blend(COLORS["panel_elevated"], COLORS["glass_tint"], 0.34),
            hover_color=blend(COLORS["panel_elevated"], COLORS["accent_secondary"], 0.42),
            text_color=COLORS["text_primary"],
            column=2,
            width=92,
        )
        self.interrupt_button = self._build_button(
            inner,
            text="Interrupt",
            command=self._on_interrupt,
            fg_color=blend(COLORS["warning"], COLORS["panel"], 0.45),
            hover_color=blend(COLORS["warning"], COLORS["panel"], 0.2),
            text_color=COLORS["text_primary"],
            column=3,
            width=98,
        )
        self.clear_button = self._build_button(
            inner,
            text="Clear",
            command=self._on_clear,
            fg_color=blend(COLORS["panel_elevated"], COLORS["glass_tint"], 0.34),
            hover_color=blend(COLORS["panel_elevated"], COLORS["accent_secondary"], 0.4),
            text_color=COLORS["text_primary"],
            column=4,
            width=80,
        )

        self._apply_focus_border()

    def _build_button(
        self,
        master: Any,
        *,
        text: str,
        command: Callable[[], None],
        fg_color: str,
        hover_color: str,
        text_color: str,
        column: int,
        width: int,
    ) -> customtkinter.CTkButton:
        button = customtkinter.CTkButton(
            master,
            text=text,
            command=command,
            font=FONTS["body_bold"],
            height=42,
            width=width,
            fg_color=fg_color,
            hover_color=hover_color,
            text_color=text_color,
            corner_radius=10,
        )
        button.grid(row=0, column=column, padx=(0, 8 if column < 4 else 0))
        return button

    def _set_focus(self, focused: bool) -> None:
        self._focus_target = 1.0 if focused else 0.0
        self._ensure_focus_animation()

    def _animate_focus(self) -> None:
        self._focus_after_id = None
        if not self.winfo_exists():
            return
        self._focus_ratio += (self._focus_target - self._focus_ratio) * 0.22
        if abs(self._focus_ratio - self._focus_target) < 0.01:
            self._focus_ratio = self._focus_target
        self._apply_focus_border()
        if abs(self._focus_ratio - self._focus_target) < 0.01:
            return
        self._focus_after_id = self.after(16, self._animate_focus)

    def _on_enter(self, _event: Any) -> str:
        self._emit_execute()
        return "break"

    def _emit_execute(self) -> None:
        text = self.command_entry.get().strip()
        if not text:
            return
        self.push_history(text)
        self._on_execute(text)
        self.command_entry.delete(0, "end")

    def _on_history_up(self, _event: Any) -> str:
        if not self.history:
            return "break"
        self.history_index = max(0, self.history_index - 1)
        self._set_entry_value(self.history[self.history_index])
        return "break"

    def _on_history_down(self, _event: Any) -> str:
        if not self.history:
            return "break"
        if self.history_index >= len(self.history) - 1:
            self.history_index = len(self.history)
            self._set_entry_value("")
            return "break"
        self.history_index += 1
        self._set_entry_value(self.history[self.history_index])
        return "break"

    def _set_entry_value(self, text: str) -> None:
        self.command_entry.delete(0, "end")
        self.command_entry.insert(0, text)

    def push_history(self, command: str) -> None:
        if not command:
            return
        if not self.history or self.history[-1] != command:
            self.history.append(command)
        self.history_index = len(self.history)

    def set_mic_state(self, active: bool) -> None:
        if active:
            self.mic_button.configure(
                text="Mic On",
                fg_color=blend(COLORS["success"], COLORS["panel"], 0.5),
                hover_color=blend(COLORS["success"], COLORS["panel"], 0.3),
            )
        else:
            self.mic_button.configure(
                text="Mic Off",
                fg_color=blend(COLORS["panel_elevated"], COLORS["glass_tint"], 0.34),
                hover_color=blend(COLORS["panel_elevated"], COLORS["accent_secondary"], 0.4),
            )

    def set_state(self, state: str) -> None:
        normalized = state.strip().upper() if isinstance(state, str) else "NORMAL"
        if normalized not in STATE_VISUALS:
            normalized = "NORMAL"
        self._state = normalized
        accent = STATE_VISUALS[normalized].accent
        self.execute_button.configure(
            fg_color=accent,
            hover_color=blend(accent, "#FFFFFF", 0.25),
            text_color="#061018",
        )
        self._apply_focus_border()

    def focus_input(self) -> None:
        self.command_entry.focus_set()

    def _apply_focus_border(self) -> None:
        accent = STATE_VISUALS.get(self._state, STATE_VISUALS["NORMAL"]).accent
        border = blend(COLORS["border"], accent, self._focus_ratio * 0.8)
        self.command_entry.configure(border_color=border)

    def _ensure_focus_animation(self) -> None:
        if self._focus_after_id is not None:
            return
        self._focus_after_id = self.after(16, self._animate_focus)

    def shutdown(self) -> None:
        if self._focus_after_id:
            try:
                self.after_cancel(self._focus_after_id)
            except Exception:
                pass
            self._focus_after_id = None
