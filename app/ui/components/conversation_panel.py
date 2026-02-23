from __future__ import annotations

import datetime as dt
from typing import Any

import customtkinter

from app.ui.theme import COLORS, FONTS, blend


class ConversationPanel(customtkinter.CTkFrame):
    ROLE_STYLES: dict[str, tuple[str, str]] = {
        "USER": ("USER", COLORS["accent_secondary"]),
        "DAVE": ("DAVE", COLORS["accent_primary"]),
        "SYSTEM": ("SYSTEM", COLORS["warning"]),
        "AUTOMATION": ("AUTOMATION", COLORS["success"]),
        "ERROR": ("ERROR", COLORS["error"]),
    }

    def __init__(self, master: Any) -> None:
        super().__init__(
            master,
            fg_color=COLORS["panel"],
            corner_radius=14,
            border_width=1,
            border_color=COLORS["border"],
        )
        self._message_index = 0
        self._stream_index = 0
        self._max_lines = 500
        self._animations_enabled = True
        self._active_streams: dict[str, dict[str, Any]] = {}

        header = customtkinter.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(10, 4))

        title = customtkinter.CTkLabel(
            header,
            text="Conversation Stream",
            font=FONTS["header"],
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        title.pack(side="left")

        self.textbox = customtkinter.CTkTextbox(
            self,
            fg_color=COLORS["panel_elevated"],
            text_color=COLORS["text_primary"],
            corner_radius=10,
            border_width=1,
            border_color=COLORS["border"],
            font=FONTS["mono"],
            wrap="word",
        )
        self.textbox.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self.textbox.configure(state="disabled")

    def append_message(self, role: str, text: str, show_timestamp: bool = True) -> None:
        normalized = role.strip().upper() if isinstance(role, str) else "SYSTEM"
        prefix, target_color = self.ROLE_STYLES.get(normalized, self.ROLE_STYLES["SYSTEM"])

        content = str(text or "").strip()
        if not content:
            return
        timestamp = f"[{dt.datetime.now().strftime('%H:%M:%S')}] " if show_timestamp else ""
        line = f"{timestamp}{prefix} > {content}\n"

        self.textbox.configure(state="normal")
        start_index = self.textbox.index("end-1c")
        self.textbox.insert("end", line)
        end_index = self.textbox.index("end-1c")
        tag = f"line_{self._message_index}"
        self._message_index += 1

        self.textbox.tag_add(tag, start_index, end_index)
        self.textbox.tag_config(tag, foreground=blend(COLORS["panel_elevated"], COLORS["text_secondary"], 0.2))
        self.textbox.configure(state="disabled")
        self.textbox.see("end")
        if self._animations_enabled:
            self._fade_tag(tag, target_color, step=0, total_steps=7)
        else:
            self.textbox.tag_config(tag, foreground=target_color)
        self._enforce_max_lines()

    def begin_stream(self, stream_id: str, role: str, show_timestamp: bool = True) -> None:
        key = stream_id.strip() if isinstance(stream_id, str) else ""
        if not key:
            key = f"stream_{self._stream_index}"
            self._stream_index += 1

        if key in self._active_streams:
            self.end_stream(key)

        normalized = role.strip().upper() if isinstance(role, str) else "DAVE"
        prefix, target_color = self.ROLE_STYLES.get(normalized, self.ROLE_STYLES["DAVE"])
        timestamp = f"[{dt.datetime.now().strftime('%H:%M:%S')}] " if show_timestamp else ""
        header = f"{timestamp}{prefix} > "

        self.textbox.configure(state="normal")
        start_index = self.textbox.index("end-1c")
        self.textbox.insert("end", header)
        end_index = self.textbox.index("end-1c")
        tag = f"line_{self._message_index}"
        self._message_index += 1
        self.textbox.tag_add(tag, start_index, end_index)
        self.textbox.tag_config(tag, foreground=blend(COLORS["panel_elevated"], COLORS["text_secondary"], 0.2))
        self.textbox.configure(state="disabled")
        self.textbox.see("end")

        self._active_streams[key] = {
            "start_index": start_index,
            "tag": tag,
            "target_color": target_color,
        }

    def append_stream(self, stream_id: str, chunk: str) -> None:
        stream = self._active_streams.get(stream_id)
        content = str(chunk or "")
        if stream is None or not content:
            return

        self.textbox.configure(state="normal")
        self.textbox.insert("end", content)
        end_index = self.textbox.index("end-1c")
        self.textbox.tag_add(stream["tag"], stream["start_index"], end_index)
        self.textbox.configure(state="disabled")
        self.textbox.see("end")

    def end_stream(self, stream_id: str) -> None:
        stream = self._active_streams.pop(stream_id, None)
        if stream is None:
            return

        self.textbox.configure(state="normal")
        self.textbox.insert("end", "\n")
        end_index = self.textbox.index("end-1c")
        self.textbox.tag_add(stream["tag"], stream["start_index"], end_index)
        self.textbox.configure(state="disabled")
        self.textbox.see("end")

        if self._animations_enabled:
            self._fade_tag(stream["tag"], stream["target_color"], step=0, total_steps=7)
        else:
            self.textbox.tag_config(stream["tag"], foreground=stream["target_color"])
        self._enforce_max_lines()

    def set_animation_active(self, active: bool) -> None:
        self._animations_enabled = bool(active)

    def clear(self) -> None:
        self._active_streams.clear()
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.configure(state="disabled")

    def _fade_tag(self, tag: str, target_color: str, step: int, total_steps: int) -> None:
        ratio = min(1.0, max(0.0, step / max(1, total_steps)))
        color = blend(COLORS["panel_elevated"], target_color, ratio)
        self.textbox.tag_config(tag, foreground=color)
        if step >= total_steps:
            return
        self.after(35, lambda: self._fade_tag(tag, target_color, step + 1, total_steps))

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
