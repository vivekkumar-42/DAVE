from __future__ import annotations

import datetime as dt
from typing import Any

import customtkinter

from app.ui.components.conversation_panel import ConversationPanel
from app.ui.components.reactor_animation import ReactorAnimation
from app.ui.theme import COLORS, FONTS, blend


class ExecutionConsole(customtkinter.CTkFrame):
    def __init__(self, master: Any) -> None:
        super().__init__(
            master,
            fg_color=COLORS["panel"],
            corner_radius=14,
            border_width=1,
            border_color=COLORS["border"],
        )
        self._collapsed = False
        self._height = 170.0
        self._target_height = 170.0
        self._after_id: str | None = None
        self._max_lines = 700

        self.header = customtkinter.CTkFrame(self, fg_color="transparent")
        self.header.pack(fill="x", padx=10, pady=(8, 4))

        self.title = customtkinter.CTkLabel(
            self.header,
            text="Execution Console",
            font=FONTS["header"],
            text_color=COLORS["text_primary"],
        )
        self.title.pack(side="left")

        self.toggle_button = customtkinter.CTkButton(
            self.header,
            text="Collapse",
            width=90,
            height=28,
            corner_radius=8,
            fg_color=COLORS["panel_elevated"],
            hover_color=blend(COLORS["panel_elevated"], COLORS["accent_secondary"], 0.35),
            text_color=COLORS["text_primary"],
            font=FONTS["small"],
            command=self.toggle,
        )
        self.toggle_button.pack(side="right")

        self.body = customtkinter.CTkFrame(
            self,
            fg_color=COLORS["panel_elevated"],
            corner_radius=10,
            border_width=1,
            border_color=COLORS["border"],
            height=int(self._height),
        )
        self.body.pack(fill="x", padx=10, pady=(0, 10))
        self.body.pack_propagate(False)

        self.textbox = customtkinter.CTkTextbox(
            self.body,
            fg_color="transparent",
            text_color=COLORS["text_secondary"],
            font=FONTS["mono_small"],
            wrap="word",
        )
        self.textbox.pack(fill="both", expand=True, padx=6, pady=6)
        self.textbox.configure(state="disabled")

        self._configure_tags()

    def _configure_tags(self) -> None:
        self.textbox.tag_config("INFO", foreground=COLORS["text_secondary"])
        self.textbox.tag_config("DEBUG", foreground=blend(COLORS["text_secondary"], COLORS["accent_secondary"], 0.5))
        self.textbox.tag_config("WARN", foreground=COLORS["warning"])
        self.textbox.tag_config("ERROR", foreground=COLORS["error"])
        self.textbox.tag_config("SUCCESS", foreground=COLORS["success"])

    def append_log(self, text: str, level: str = "INFO") -> None:
        clean = str(text or "").strip()
        if not clean:
            return
        normalized = level.upper()
        if normalized not in {"INFO", "DEBUG", "WARN", "ERROR", "SUCCESS"}:
            normalized = "INFO"
        timestamp = dt.datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {normalized:<7} {clean}\n"

        self.textbox.configure(state="normal")
        start = self.textbox.index("end-1c")
        self.textbox.insert("end", line)
        end = self.textbox.index("end-1c")
        tag = f"{normalized}_{timestamp}_{start.replace('.', '_')}"
        self.textbox.tag_add(tag, start, end)
        self.textbox.tag_config(tag, foreground=self.textbox.tag_cget(normalized, "foreground"))
        self.textbox.configure(state="disabled")
        self.textbox.see("end")
        self._enforce_max_lines()

    def toggle(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        self._target_height = 0.0 if self._collapsed else 170.0
        self.toggle_button.configure(text="Expand" if self._collapsed else "Collapse")
        self._ensure_height_animation()

    def clear(self) -> None:
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.configure(state="disabled")

    def _animate_height(self) -> None:
        self._after_id = None
        if not self.winfo_exists():
            return
        self._height += (self._target_height - self._height) * 0.22
        if abs(self._height - self._target_height) < 0.5:
            self._height = self._target_height

        display_height = int(max(0.0, self._height))
        self.body.configure(height=display_height)
        if display_height == 0:
            self.textbox.pack_forget()
        elif not self.textbox.winfo_manager():
            self.textbox.pack(fill="both", expand=True, padx=6, pady=6)

        if abs(self._height - self._target_height) < 0.01:
            return
        self._after_id = self.after(16, self._animate_height)

    def _ensure_height_animation(self) -> None:
        if self._after_id is not None:
            return
        self._after_id = self.after(16, self._animate_height)

    def shutdown(self) -> None:
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _enforce_max_lines(self) -> None:
        try:
            total_lines = int(self.textbox.index("end-1c").split(".")[0])
        except Exception:
            return

        overflow = total_lines - self._max_lines
        if overflow <= 0:
            return

        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", f"{overflow + 1}.0")
        self.textbox.configure(state="disabled")


class CorePanel(customtkinter.CTkFrame):
    def __init__(self, master: Any) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.reactor = ReactorAnimation(self)
        self.reactor.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.reactor.configure(height=280)

        self.conversation = ConversationPanel(self)
        self.conversation.grid(row=1, column=0, sticky="nsew", pady=(0, 10))

        self.execution_console = ExecutionConsole(self)
        self.execution_console.grid(row=2, column=0, sticky="ew")

    def set_state(self, state: str) -> None:
        self.reactor.set_state(state)

    def add_message(self, role: str, text: str, show_timestamp: bool = True) -> None:
        self.conversation.append_message(role=role, text=text, show_timestamp=show_timestamp)

    def begin_stream_message(self, stream_id: str, role: str, show_timestamp: bool = True) -> None:
        self.conversation.begin_stream(stream_id=stream_id, role=role, show_timestamp=show_timestamp)

    def append_stream_message(self, stream_id: str, chunk: str) -> None:
        self.conversation.append_stream(stream_id=stream_id, chunk=chunk)

    def end_stream_message(self, stream_id: str) -> None:
        self.conversation.end_stream(stream_id=stream_id)

    def add_log(self, text: str, level: str = "INFO") -> None:
        self.execution_console.append_log(text=text, level=level)

    def clear_conversation(self) -> None:
        self.conversation.clear()

    def clear_console(self) -> None:
        self.execution_console.clear()

    def set_console_collapsed(self, collapsed: bool) -> None:
        self.execution_console.set_collapsed(collapsed)

    def set_animation_active(self, active: bool) -> None:
        self.reactor.set_animation_active(active)
        self.conversation.set_animation_active(active)

    def shutdown(self) -> None:
        self.execution_console.shutdown()
        self.reactor.shutdown()
