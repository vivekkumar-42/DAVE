from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

import customtkinter

from app.ui.components.command_bar import CommandBar
from app.ui.components.core_panel import CorePanel
from app.ui.components.side_panel import SidePanel
from app.ui.components.status_panel import StatusPanel
from app.ui.components.top_bar import TopBar
from app.ui.events import UIEvent
from app.ui.theme import COLORS, STATE_VISUALS, apply_ui_theme

from app.runtime_paths import runtime_data_dir

if TYPE_CHECKING:
    from app.modules.brain_core import Brain
    from app.modules.voice_engine import VoiceEngine


class QueueLogHandler(logging.Handler):
    def __init__(self, sink: Any) -> None:
        super().__init__()
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        level = record.levelname.upper()
        normalized = "INFO"
        if level in {"WARNING", "WARN"}:
            normalized = "WARN"
        elif level in {"ERROR", "CRITICAL"}:
            normalized = "ERROR"
        elif level == "DEBUG":
            normalized = "DEBUG"
        self._sink("log", {"text": message, "level": normalized})


class MainWindow(customtkinter.CTk):
    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings if isinstance(settings, dict) else {}
        ui_raw = self.settings.get("ui", {})
        ui = ui_raw if isinstance(ui_raw, dict) else {}
        apply_ui_theme(ui)

        customtkinter.set_appearance_mode(ui.get("appearance_mode", "Dark"))
        customtkinter.set_default_color_theme("dark-blue")
        super().__init__(fg_color=COLORS["background"])

        self.ui_build = "DAVE"
        self.title("DAVE")
        logging.info("UI build loaded: %s", self.ui_build)
        window_width = self._coerce_int(ui.get("window_width"), 1540, minimum=1100, maximum=3840)
        window_height = self._coerce_int(ui.get("window_height"), 960, minimum=760, maximum=2160)
        min_width = self._coerce_int(ui.get("min_width"), 1320, minimum=900, maximum=window_width)
        min_height = self._coerce_int(ui.get("min_height"), 820, minimum=680, maximum=window_height)
        self.geometry(f"{window_width}x{window_height}")
        self.minsize(min_width, min_height)

        self._shutdown_event = threading.Event()
        self._ui_queue: queue.Queue[UIEvent] = queue.Queue(maxsize=3000)
        self._worker_lock = threading.Lock()
        self._command_lock = threading.Lock()
        self._active_worker_count = 0
        self._command_counter = 0
        self._interrupt_generation = 0
        self._state = "NORMAL"
        self._mic_active = False
        self._voice_thread: threading.Thread | None = None
        self._last_command_latency_ms: int | None = None
        self._latest_provider = "N/A"
        self._queue_after_id: str | None = None
        self._status_after_id: str | None = None
        self._perf_after_id: str | None = None
        self._deferred_ui_event: UIEvent | None = None
        self._runtime_init_started = False
        self._animation_active = False

        self.brain: "Brain | None" = None
        self.voice: "VoiceEngine | None" = None

        self.assistant_enabled = True
        self.voice_enabled = True
        self.automation_enabled = True
        self.monitoring_enabled = True
        self.diagnostics_mode = False

        perf_raw = ui.get("performance_profiler", {})
        perf_cfg = perf_raw if isinstance(perf_raw, dict) else {}
        self._perf_enabled = self._coerce_bool(perf_cfg.get("enabled", True), True)
        self._perf_persist_to_file = self._coerce_bool(perf_cfg.get("persist_to_file", True), True)
        self._perf_report_interval_seconds = max(
            2.0,
            min(
                30.0,
                self._coerce_float(perf_cfg.get("report_interval_seconds", 5.0), 5.0),
            ),
        )
        self._perf_history_size = self._coerce_int(
            perf_cfg.get("history_size", 60),
            60,
            minimum=10,
            maximum=300,
        )
        self._perf_file_name = str(perf_cfg.get("file_name", "dave_perf_metrics.jsonl") or "").strip()
        if not self._perf_file_name:
            self._perf_file_name = "dave_perf_metrics.jsonl"
        self._perf_file_max_bytes = self._coerce_int(
            perf_cfg.get("max_file_size_kb", 1024),
            1024,
            minimum=128,
            maximum=10240,
        ) * 1024

        update_raw = self.settings.get("update", {})
        update_cfg = update_raw if isinstance(update_raw, dict) else {}
        self._update_enabled = self._coerce_bool(update_cfg.get("enabled", True), True)
        self._update_channel = str(update_cfg.get("channel", "stable")).strip() or "stable"
        self._update_current_version = str(update_cfg.get("current_version", "2.0.0")).strip() or "2.0.0"
        self._update_manifest_url = str(update_cfg.get("manifest_url", "")).strip()
        self._update_check_on_startup = self._coerce_bool(
            update_cfg.get("check_on_startup", False),
            False,
        )
        self._update_timeout_seconds = max(
            1.0,
            min(
                15.0,
                self._coerce_float(update_cfg.get("request_timeout_seconds", 3.0), 3.0),
            ),
        )
        self._update_check_started = False

        self._runtime_data_dir = self._resolve_runtime_data_dir()
        self._perf_file_path = self._runtime_data_dir / self._perf_file_name
        self._queue_drop_count = 0
        self._command_perf_history: list[dict[str, Any]] = []
        self._reset_drain_metrics()

        self._build_layout()
        self._bind_window_events()
        self._install_log_handler()
        self._set_animation_active(True)

        llm_raw = self.settings.get("llm", {})
        llm_cfg = llm_raw if isinstance(llm_raw, dict) else {}
        self.llm_enabled = bool(llm_cfg.get("enabled", True))
        self.side_panel.set_section_enabled("llm_providers", self.llm_enabled)
        self._apply_initial_status()

        self.enqueue(
            "conversation",
            {
                "role": "SYSTEM",
                "text": "DAVE UI online. Diagnostics stream active.",
            },
        )
        self.enqueue(
            "log",
            {
                "text": "Frontend initialized with queue-driven update model | build=DAVE",
                "level": "SUCCESS",
            },
        )
        self.command_bar.focus_input()

        self._queue_after_id = self.after(24, self._drain_queue_loop)
        self._schedule_status_poll(900)
        self.after(20, self._initialize_runtime_components)
        self.after(280, self._sync_animation_activity)
        if self._perf_enabled:
            self._schedule_perf_report()

    def _initialize_runtime_components(self) -> None:
        if self._runtime_init_started or self._shutdown_event.is_set():
            return
        self._runtime_init_started = True
        threading.Thread(target=self._runtime_init_worker, daemon=True).start()

    def _runtime_init_worker(self) -> None:
        try:
            from app.modules.brain_core import Brain
            from app.modules.voice_engine import VoiceEngine

            brain = Brain(gui_callback=self._on_brain_signal, config=self.settings)
            voice = VoiceEngine(config=self.settings)
            self.enqueue("runtime_ready", {"brain": brain, "voice": voice})
        except Exception as exc:
            self.enqueue("runtime_error", {"error": str(exc)})

    def _resolve_runtime_data_dir(self) -> Path:
        # Prefer `app_dir()/data` when running portably, otherwise fall back to per-user storage.
        return runtime_data_dir()

    def _apply_runtime_components(self, brain: "Brain", voice: "VoiceEngine") -> None:
        if self._shutdown_event.is_set():
            try:
                voice.shutdown()
            except Exception:
                pass
            return
        self.brain = brain
        self.voice = voice
        self.brain.llm_enabled = self.llm_enabled
        self.side_panel.set_section_enabled("llm_providers", self.llm_enabled)
        self.status_panel.update_card("llm_provider", "Ready", "good")
        self.enqueue("log", {"text": "Runtime components initialized.", "level": "SUCCESS"})
        self._start_voice_loop()
        self._start_update_check()

    def _apply_runtime_init_error(self, error_text: str) -> None:
        if self._shutdown_event.is_set():
            return
        self.brain = None
        self.voice = None
        self.llm_enabled = False
        self.side_panel.set_section_enabled("llm_providers", False)
        self.enqueue("state", {"value": "ERROR"})
        self.enqueue(
            "conversation",
            {
                "role": "ERROR",
                "text": f"Runtime initialization failed: {error_text}",
            },
        )
        self.enqueue("log", {"text": f"Runtime init failed: {error_text}", "level": "ERROR"})

    def _build_layout(self) -> None:
        self.top_bar = TopBar(self)
        self.top_bar.pack(fill="x", side="top")

        self.command_bar = CommandBar(
            self,
            on_execute=self._on_manual_command,
            on_toggle_mic=self.toggle_microphone,
            on_interrupt=self.interrupt_execution,
            on_clear=self.clear_streams,
        )
        self.command_bar.pack(fill="x", side="bottom")

        body = customtkinter.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=10)

        self.side_panel = SidePanel(
            body,
            on_select=self._on_section_selected,
            on_toggle=self._on_section_toggled,
        )
        self.side_panel.pack(side="left", fill="y", padx=(0, 10))

        self.core_panel = CorePanel(body)
        self.core_panel.pack(side="left", fill="both", expand=True, padx=(0, 10))

        self.status_panel = StatusPanel(body)
        self.status_panel.pack(side="right", fill="y")

    def _bind_window_events(self) -> None:
        self.protocol("WM_DELETE_WINDOW", self.shutdown_system)
        self.bind("<FocusIn>", lambda _event: self._set_animation_active(True))
        self.bind("<FocusOut>", lambda _event: self.after(120, self._sync_animation_activity))
        self.bind("<Unmap>", lambda _event: self._set_animation_active(False))
        self.bind("<Map>", lambda _event: self.after(120, self._sync_animation_activity))

    def _sync_animation_activity(self) -> None:
        if self._shutdown_event.is_set():
            return
        focused = self.focus_displayof() is not None
        minimized = str(self.state()).lower() == "iconic"
        self._set_animation_active(focused and not minimized)

    def _set_animation_active(self, active: bool) -> None:
        normalized = bool(active)
        if self._animation_active == normalized:
            return
        self._animation_active = normalized
        if not hasattr(self, "top_bar"):
            return
        self.top_bar.set_animation_active(normalized)
        self.core_panel.set_animation_active(normalized)
        self.status_panel.set_animation_active(normalized)

    def _install_log_handler(self) -> None:
        self._log_handler = QueueLogHandler(self.enqueue)
        self._log_handler.setLevel(logging.INFO)
        self._log_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S")
        )
        logging.getLogger().addHandler(self._log_handler)

    def _apply_initial_status(self) -> None:
        self._set_state("NORMAL")
        self.command_bar.set_mic_state(False)
        self.top_bar.set_indicator("microphone", "OFF", "idle")
        self.top_bar.set_indicator("provider", "BOOT", "warning")
        self.top_bar.set_indicator("latency", "-- ms", "idle")
        self.top_bar.set_indicator("activity", "READY", "active")

        self.status_panel.update_card("voice_engine", "Ready (mic off)", "idle")
        self.status_panel.update_card("llm_provider", "Booting...", "warning")
        self.status_panel.update_card("provider_reliability", "Sampling...", "idle")
        self.status_panel.update_card("automation_engine", "Enabled", "good")
        self.status_panel.update_card("thread_status", "voice:starting | workers:0", "active")
        self.status_panel.update_card("execution_state", "NORMAL", "active")
        self.status_panel.update_card("system_readiness", "Operational", "good")

    def enqueue(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        event = UIEvent(event_type=event_type, payload=payload or {})
        try:
            self._ui_queue.put_nowait(event)
        except queue.Full:
            self._queue_drop_count += 1
            try:
                _ = self._ui_queue.get_nowait()
                self._ui_queue.put_nowait(event)
            except Exception:
                pass

    def _drain_queue_loop(self) -> None:
        if self._shutdown_event.is_set():
            return

        loop_started = time.perf_counter()
        queue_size = self._ui_queue.qsize()
        if queue_size > 700:
            max_events = 460
        elif queue_size > 300:
            max_events = 320
        elif queue_size > 90:
            max_events = 240
        else:
            max_events = 160

        loop_budget_ms = 10.5 if self._animation_active else 8.0
        processed = 0
        while processed < max_events:
            elapsed_ms = (time.perf_counter() - loop_started) * 1000.0
            if processed >= 60 and queue_size < 260 and elapsed_ms >= loop_budget_ms:
                break

            event: UIEvent | None = None
            if self._deferred_ui_event is not None:
                event = self._deferred_ui_event
                self._deferred_ui_event = None
            else:
                try:
                    event = self._ui_queue.get_nowait()
                except queue.Empty:
                    break

            if event.event_type == "conversation_stream_chunk":
                stream_id = str(event.payload.get("stream_id", ""))
                combined_chunks = [str(event.payload.get("text", ""))]
                while processed < max_events:
                    try:
                        next_event = self._ui_queue.get_nowait()
                    except queue.Empty:
                        break
                    if (
                        next_event.event_type == "conversation_stream_chunk"
                        and str(next_event.payload.get("stream_id", "")) == stream_id
                    ):
                        combined_chunks.append(str(next_event.payload.get("text", "")))
                        processed += 1
                        continue
                    self._deferred_ui_event = next_event
                    break
                self.core_panel.append_stream_message(stream_id=stream_id, chunk="".join(combined_chunks))
            else:
                self._handle_ui_event(event)
            processed += 1

        remaining = self._ui_queue.qsize()
        loop_elapsed_ms = (time.perf_counter() - loop_started) * 1000.0
        self._drain_metrics["loops"] += 1
        self._drain_metrics["events"] += processed
        self._drain_metrics["ms_sum"] += loop_elapsed_ms
        self._drain_metrics["peak_queue"] = max(self._drain_metrics["peak_queue"], queue_size)
        self._drain_metrics["last_queue"] = remaining
        if processed >= max_events:
            self._drain_metrics["busy_loops"] += 1

        if remaining > 600:
            delay_ms = 8
        elif remaining > 220:
            delay_ms = 12
        elif remaining > 50:
            delay_ms = 18
        else:
            delay_ms = 26 if self._animation_active else 34
        self._queue_after_id = self.after(delay_ms, self._drain_queue_loop)

    def _handle_ui_event(self, event: UIEvent) -> None:
        payload = event.payload
        if event.event_type == "state":
            self._set_state(str(payload.get("value", "NORMAL")))
            return
        if event.event_type == "conversation":
            self.core_panel.add_message(
                role=str(payload.get("role", "SYSTEM")),
                text=str(payload.get("text", "")),
                show_timestamp=bool(payload.get("timestamp", True)),
            )
            return
        if event.event_type == "conversation_stream_begin":
            self.core_panel.begin_stream_message(
                stream_id=str(payload.get("stream_id", "")),
                role=str(payload.get("role", "DAVE")),
                show_timestamp=bool(payload.get("timestamp", True)),
            )
            return
        if event.event_type == "conversation_stream_chunk":
            self.core_panel.append_stream_message(
                stream_id=str(payload.get("stream_id", "")),
                chunk=str(payload.get("text", "")),
            )
            return
        if event.event_type == "conversation_stream_chunks":
            self.core_panel.append_stream_message(
                stream_id=str(payload.get("stream_id", "")),
                chunk=str(payload.get("text", "")),
            )
            return
        if event.event_type == "conversation_stream_end":
            self.core_panel.end_stream_message(stream_id=str(payload.get("stream_id", "")))
            return
        if event.event_type == "log":
            self.core_panel.add_log(
                text=str(payload.get("text", "")),
                level=str(payload.get("level", "INFO")),
            )
            return
        if event.event_type == "indicator":
            self.top_bar.set_indicator(
                key=str(payload.get("key", "")),
                value=str(payload.get("value", "")),
                level=str(payload.get("level", "neutral")),
            )
            return
        if event.event_type == "status":
            self.status_panel.update_card(
                key=str(payload.get("key", "")),
                value=str(payload.get("value", "")),
                level=str(payload.get("level", "idle")),
            )
            return
        if event.event_type == "runtime_ready":
            brain = payload.get("brain")
            voice = payload.get("voice")
            if brain is not None and voice is not None:
                self._apply_runtime_components(brain=brain, voice=voice)
            return
        if event.event_type == "runtime_error":
            self._apply_runtime_init_error(str(payload.get("error", "unknown error")))
            return
        if event.event_type == "clear":
            self.core_panel.clear_conversation()
            self.core_panel.clear_console()
            return
        if event.event_type == "console":
            self.core_panel.set_console_collapsed(bool(payload.get("collapsed", False)))

    def _set_state(self, state: str) -> None:
        normalized = state.strip().upper() if isinstance(state, str) else "NORMAL"
        if normalized not in STATE_VISUALS:
            normalized = "NORMAL"
        self._state = normalized

        self.top_bar.set_state(normalized)
        self.core_panel.set_state(normalized)
        self.command_bar.set_state(normalized)

        level = "active"
        if normalized == "ERROR":
            level = "error"
        elif normalized == "EXECUTING":
            level = "good"
        elif normalized == "PROCESSING":
            level = "warning"
        self.status_panel.update_card("execution_state", normalized, level)

    def _on_section_selected(self, section_key: str) -> None:
        self.side_panel.set_active(section_key)
        names = {
            "assistant_control": "Assistant Control",
            "voice_engine": "Voice Engine",
            "automation_engine": "Automation Engine",
            "llm_providers": "LLM Providers",
            "system_monitoring": "System Monitoring",
            "settings": "Settings",
        }
        self.enqueue(
            "conversation",
            {"role": "SYSTEM", "text": f"Focus -> {names.get(section_key, section_key)}", "timestamp": True},
        )

    def _on_section_toggled(self, section_key: str, enabled: bool) -> None:
        if section_key == "assistant_control":
            self.assistant_enabled = enabled
            if not enabled:
                self._mic_active = False
                self.command_bar.set_mic_state(False)
                self.enqueue("state", {"value": "NORMAL"})
            self.enqueue("log", {"text": f"Assistant control set to {enabled}.", "level": "INFO"})
            return

        if section_key == "voice_engine":
            self.voice_enabled = enabled
            if not enabled:
                self._mic_active = False
                self.command_bar.set_mic_state(False)
            self.enqueue("log", {"text": f"Voice engine set to {enabled}.", "level": "INFO"})
            return

        if section_key == "automation_engine":
            self.automation_enabled = enabled
            self.enqueue("log", {"text": f"Automation engine set to {enabled}.", "level": "INFO"})
            return

        if section_key == "llm_providers":
            self.llm_enabled = enabled
            if self.brain is not None:
                self.brain.llm_enabled = enabled
            self.enqueue("log", {"text": f"LLM providers set to {enabled}.", "level": "INFO"})
            return

        if section_key == "system_monitoring":
            self.monitoring_enabled = enabled
            self.enqueue("log", {"text": f"Monitoring set to {enabled}.", "level": "INFO"})
            return

        if section_key == "settings":
            self.diagnostics_mode = enabled
            self.enqueue("console", {"collapsed": not enabled})
            level = "DEBUG" if enabled else "INFO"
            self.enqueue(
                "log",
                {
                    "text": f"Diagnostics mode {'enabled' if enabled else 'disabled'}.",
                    "level": level,
                },
            )
            if enabled and self._perf_enabled:
                report_text, report_level = self._build_perf_report()
                if report_text:
                    self.enqueue("log", {"text": report_text, "level": report_level})

    def _on_manual_command(self, text: str) -> None:
        self.enqueue("conversation", {"role": "USER", "text": text, "timestamp": True})
        self._start_command_worker(text=text, source="manual")

    def toggle_microphone(self) -> None:
        if not self.assistant_enabled or not self.voice_enabled:
            self.enqueue("conversation", {"role": "ERROR", "text": "Cannot activate microphone while disabled."})
            return
        self._mic_active = not self._mic_active
        mic_text = "ON" if self._mic_active else "OFF"
        mic_level = "good" if self._mic_active else "idle"
        self.command_bar.set_mic_state(self._mic_active)
        self.enqueue("indicator", {"key": "microphone", "value": mic_text, "level": mic_level})
        self.enqueue("conversation", {"role": "SYSTEM", "text": f"Microphone {mic_text}."})

    def interrupt_execution(self) -> None:
        self._interrupt_generation += 1
        self.enqueue("state", {"value": "NORMAL"})
        self.enqueue("log", {"text": "Interrupt requested. Pending response output will be discarded.", "level": "WARN"})
        self.enqueue("conversation", {"role": "SYSTEM", "text": "Interrupt signal sent."})

    def clear_streams(self) -> None:
        self.enqueue("clear")
        self.enqueue("conversation", {"role": "SYSTEM", "text": "Streams cleared."})

    def _start_command_worker(self, text: str, source: str) -> None:
        if not self.assistant_enabled:
            self.enqueue("conversation", {"role": "ERROR", "text": "Assistant control is disabled."})
            return
        if self.brain is None:
            self.enqueue(
                "conversation",
                {
                    "role": "SYSTEM",
                    "text": "Core is still initializing. Try again in a moment.",
                },
            )
            return

        self._command_counter += 1
        command_id = self._command_counter
        generation = self._interrupt_generation

        worker = threading.Thread(
            target=self._command_worker,
            args=(command_id, text, source, generation),
            daemon=True,
        )
        worker.start()

    def _command_worker(self, command_id: int, text: str, source: str, generation: int) -> None:
        if self.brain is None:
            return
        self._increment_workers(1)
        with self._command_lock:
            start = time.perf_counter()
            backend_ms = 0
            route = self._classify_route(text)
            self.enqueue("state", {"value": "PROCESSING"})
            self.enqueue(
                "log",
                {
                    "text": f"Command #{command_id} from {source}: route={route} | input={text}",
                    "level": "DEBUG",
                },
            )
            self.enqueue("indicator", {"key": "activity", "value": "PROCESSING", "level": "active"})
            self.enqueue("conversation", {"role": "SYSTEM", "text": f"Routing decision -> {route.upper()}."})

            if route == "automation" and not self.automation_enabled:
                self.enqueue("state", {"value": "ERROR"})
                self.enqueue("conversation", {"role": "ERROR", "text": "Automation engine is disabled."})
                self.enqueue("log", {"text": "Automation route blocked by UI toggle.", "level": "WARN"})
                self._increment_workers(-1)
                return

            pre_health = self.brain.get_llm_health()
            try:
                if route == "automation":
                    self.enqueue("state", {"value": "EXECUTING"})
                    self.enqueue("conversation", {"role": "AUTOMATION", "text": "Executing local automation action."})
                backend_started = time.perf_counter()
                response = self.brain.process_command(text)
                backend_ms = int((time.perf_counter() - backend_started) * 1000)
            except Exception as exc:
                response = f"Command failed: {exc}"
                self.enqueue("state", {"value": "ERROR"})
                self.enqueue("log", {"text": f"Command #{command_id} failed: {exc}", "level": "ERROR"})
                self.enqueue("conversation", {"role": "ERROR", "text": response})
                self._increment_workers(-1)
                return

            post_health = self.brain.get_llm_health()
            self._latest_provider = str(post_health.get("provider", "none")).upper()

            if generation != self._interrupt_generation:
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                self._record_command_profile(
                    command_id=command_id,
                    source=source,
                    route=route,
                    total_ms=elapsed_ms,
                    backend_ms=backend_ms,
                    stream_ms=0,
                    chunks=0,
                    response_length=len(str(response or "")),
                    interrupted=True,
                )
                self.enqueue(
                    "log",
                    {
                        "text": f"Command #{command_id} completed after interrupt; output suppressed.",
                        "level": "WARN",
                    },
                )
                self.enqueue("state", {"value": "NORMAL"})
                self._increment_workers(-1)
                return

            delivered, stream_ms, chunk_count = self._stream_response_to_conversation(
                response_text=response,
                command_id=command_id,
                generation=generation,
            )
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            self._last_command_latency_ms = elapsed_ms
            self._record_command_profile(
                command_id=command_id,
                source=source,
                route=route,
                total_ms=elapsed_ms,
                backend_ms=backend_ms,
                stream_ms=stream_ms,
                chunks=chunk_count,
                response_length=len(str(response or "")),
                interrupted=not delivered,
            )
            if not delivered:
                self.enqueue(
                    "log",
                    {
                        "text": f"Command #{command_id} output interrupted during stream.",
                        "level": "WARN",
                    },
                )
                self.enqueue("state", {"value": "NORMAL"})
                self._increment_workers(-1)
                return

            self.enqueue(
                "log",
                {
                    "text": (
                        f"Command #{command_id} complete in {elapsed_ms} ms "
                        f"(backend={backend_ms} ms | stream={stream_ms} ms | chunks={chunk_count}) "
                        f"| provider={self._latest_provider}"
                    ),
                    "level": "SUCCESS",
                },
            )
            self.enqueue("indicator", {"key": "latency", "value": f"{elapsed_ms} ms", "level": "active"})

            health_status = str(post_health.get("status", "unknown")).lower()
            provider_level = "warning" if health_status in {"degraded", "offline"} else "success"
            if health_status == "offline":
                provider_level = "error"
            self.enqueue("indicator", {"key": "provider", "value": self._latest_provider, "level": provider_level})

            self._publish_provider_log(pre_health, post_health)
            self._queue_speaking(response)

        self._increment_workers(-1)

    def _queue_speaking(self, text: str) -> None:
        if not self.voice_enabled or self.voice is None:
            self.enqueue("state", {"value": "NORMAL"})
            self.enqueue("indicator", {"key": "activity", "value": "IDLE", "level": "idle"})
            return

        self.enqueue("state", {"value": "SPEAKING"})
        self.enqueue("indicator", {"key": "activity", "value": "SPEAKING", "level": "active"})
        try:
            self.voice.speak(text)
        except Exception as exc:
            self.enqueue("log", {"text": f"TTS enqueue failed: {exc}", "level": "ERROR"})
            self.enqueue("state", {"value": "ERROR"})
            return

        duration = min(4.5, max(1.0, len(text) / 48.0))

        def _restore_state_after_delay() -> None:
            time.sleep(duration)
            if self._shutdown_event.is_set():
                return
            if self._state == "SPEAKING":
                self.enqueue("state", {"value": "NORMAL"})
                self.enqueue("indicator", {"key": "activity", "value": "IDLE", "level": "idle"})

        threading.Thread(target=_restore_state_after_delay, daemon=True).start()

    def _publish_provider_log(self, pre_health: dict[str, Any], post_health: dict[str, Any]) -> None:
        pre_provider = str(pre_health.get("provider", "none")).upper()
        post_provider = str(post_health.get("provider", "none")).upper()
        post_status = str(post_health.get("status", "unknown")).lower()
        reason = str(post_health.get("reason", ""))
        if post_provider != pre_provider:
            self.enqueue(
                "log",
                {
                    "text": f"Provider switch {pre_provider} -> {post_provider} ({post_status}).",
                    "level": "WARN" if post_status == "degraded" else "INFO",
                },
            )
        else:
            self.enqueue(
                "log",
                {
                    "text": f"Provider {post_provider} status={post_status} reason={reason}",
                    "level": "DEBUG",
                },
            )

    def _stream_response_to_conversation(
        self,
        response_text: str,
        command_id: int,
        generation: int,
    ) -> tuple[bool, int, int]:
        content = str(response_text or "").strip()
        if not content:
            return True, 0, 0

        stream_started = time.perf_counter()
        chunk_count = 0
        stream_id = f"cmd_{command_id}_{int(time.time() * 1000)}"
        self.enqueue(
            "conversation_stream_begin",
            {"stream_id": stream_id, "role": "DAVE", "timestamp": True},
        )
        for chunk in self._iter_response_chunks(content):
            chunk_count += 1
            if generation != self._interrupt_generation or self._shutdown_event.is_set():
                self.enqueue("conversation_stream_end", {"stream_id": stream_id})
                elapsed_ms = int((time.perf_counter() - stream_started) * 1000)
                return False, elapsed_ms, chunk_count
            self.enqueue("conversation_stream_chunk", {"stream_id": stream_id, "text": chunk})
            delay = self._stream_chunk_delay()
            if delay > 0.0:
                time.sleep(delay)
        self.enqueue("conversation_stream_end", {"stream_id": stream_id})
        elapsed_ms = int((time.perf_counter() - stream_started) * 1000)
        return True, elapsed_ms, chunk_count

    @staticmethod
    def _iter_response_chunks(text: str) -> list[str]:
        clean = str(text or "")
        if len(clean) <= 120:
            return [clean]

        chunks: list[str] = []
        cursor = 0
        length = len(clean)
        max_chunks = 44
        while cursor < length:
            if len(chunks) >= max_chunks:
                tail = clean[cursor:].strip()
                if tail:
                    chunks.append(tail)
                break

            window_end = min(length, cursor + 58)
            min_split = min(length, cursor + 22)
            break_at = -1
            for token in (". ", "! ", "? ", "; ", ": ", ", "):
                idx = clean.rfind(token, min_split, window_end)
                if idx > break_at:
                    break_at = idx + 1
            if break_at <= cursor:
                break_at = clean.rfind(" ", min_split, window_end)
            if break_at <= cursor:
                break_at = window_end
            chunk = clean[cursor:break_at]
            if chunk:
                chunks.append(chunk)
            cursor = break_at
            while cursor < length and clean[cursor] == " ":
                cursor += 1
        return chunks or [clean]

    def _stream_chunk_delay(self) -> float:
        if not self._animation_active:
            return 0.0
        queue_depth = self._ui_queue.qsize()
        if queue_depth > 200:
            return 0.0
        if queue_depth > 90:
            return 0.002
        return 0.006

    def _record_command_profile(
        self,
        *,
        command_id: int,
        source: str,
        route: str,
        total_ms: int,
        backend_ms: int,
        stream_ms: int,
        chunks: int,
        response_length: int,
        interrupted: bool,
    ) -> None:
        self._command_perf_history.append(
            {
                "id": command_id,
                "source": source,
                "route": route,
                "total_ms": max(0, int(total_ms)),
                "backend_ms": max(0, int(backend_ms)),
                "stream_ms": max(0, int(stream_ms)),
                "chunks": max(0, int(chunks)),
                "response_length": max(0, int(response_length)),
                "interrupted": bool(interrupted),
                "captured_at": time.time(),
            }
        )
        if len(self._command_perf_history) > self._perf_history_size:
            self._command_perf_history = self._command_perf_history[-self._perf_history_size :]

    def _classify_route(self, text: str) -> str:
        lowered = text.lower().strip()
        if not lowered:
            return "llm"
        automation_tokens = [
            "open ",
            "launch ",
            "start ",
            "search for",
            "shutdown",
            "shut down",
            "restart",
            "reboot",
            "lock",
            "volume",
            "mute",
            "run ",
            "execute ",
            "powershell ",
            "cmd ",
            "!",
        ]
        if any(token in lowered for token in automation_tokens):
            return "automation"
        if "code red" in lowered or "stand down" in lowered or "danger" in lowered:
            return "automation"
        return "llm"

    def _start_voice_loop(self) -> None:
        if self._voice_thread and self._voice_thread.is_alive():
            return
        self._voice_thread = threading.Thread(target=self._voice_loop, daemon=True)
        self._voice_thread.start()

    def _voice_loop(self) -> None:
        while not self._shutdown_event.is_set():
            voice = self.voice
            if voice is None:
                time.sleep(0.12)
                continue
            if not (self.assistant_enabled and self.voice_enabled and self._mic_active):
                time.sleep(0.12)
                continue
            with self._worker_lock:
                if self._active_worker_count > 0:
                    time.sleep(0.08)
                    continue

            self.enqueue("state", {"value": "LISTENING"})
            self.enqueue("indicator", {"key": "activity", "value": "LISTENING", "level": "active"})

            heard = voice.listen()
            if self._shutdown_event.is_set():
                break

            if not heard:
                continue

            self.enqueue("conversation", {"role": "USER", "text": heard})
            self._start_command_worker(text=heard, source="voice")
            time.sleep(0.04)

    def _start_update_check(self) -> None:
        if self._update_check_started:
            return
        if not self._update_enabled or not self._update_check_on_startup:
            return
        if not self._update_manifest_url:
            return
        self._update_check_started = True
        threading.Thread(target=self._update_check_worker, daemon=True).start()

    def _update_check_worker(self) -> None:
        try:
            from app.modules.update_checker import check_for_update

            result = check_for_update(
                current_version=self._update_current_version,
                manifest_url=self._update_manifest_url,
                timeout_seconds=self._update_timeout_seconds,
            )
        except Exception as exc:
            self.enqueue("log", {"text": f"Update check failed: {exc}", "level": "WARN"})
            return

        status = str(result.get("status", "unknown")).lower()
        if status == "available":
            latest_version = str(result.get("latest_version", "unknown"))
            download_url = str(result.get("download_url", "")).strip()
            detail = f"Update available: {latest_version} on channel {self._update_channel}."
            if download_url:
                detail += f" Download: {download_url}"
            self.enqueue("log", {"text": detail, "level": "WARN"})
            self.enqueue("conversation", {"role": "SYSTEM", "text": detail})
            return

        if status == "up_to_date":
            self.enqueue(
                "log",
                {
                    "text": f"Update check: current version {self._update_current_version} is up to date.",
                    "level": "DEBUG",
                },
            )
            return

        detail = str(result.get("detail", "")).strip() or "No additional detail."
        self.enqueue("log", {"text": f"Update check status={status}: {detail}", "level": "WARN"})

    def _schedule_perf_report(self) -> None:
        if self._shutdown_event.is_set() or not self._perf_enabled:
            return
        delay_ms = max(1000, int(self._perf_report_interval_seconds * 1000))
        self._perf_after_id = self.after(delay_ms, self._perf_report_loop)

    def _perf_report_loop(self) -> None:
        self._perf_after_id = None
        if self._shutdown_event.is_set() or not self._perf_enabled:
            return

        report_text, level = self._build_perf_report()
        if report_text:
            self.enqueue("log", {"text": report_text, "level": level})
        self._schedule_perf_report()

    def _build_perf_report(self) -> tuple[str, str]:
        captured_at = time.time()
        loops = int(self._drain_metrics["loops"])
        events = int(self._drain_metrics["events"])
        ms_sum = float(self._drain_metrics["ms_sum"])
        peak_queue = int(self._drain_metrics["peak_queue"])
        last_queue = int(self._drain_metrics["last_queue"])
        busy_loops = int(self._drain_metrics["busy_loops"])
        dropped = int(self._queue_drop_count)

        avg_events = (events / loops) if loops > 0 else 0.0
        avg_drain_ms = (ms_sum / loops) if loops > 0 else 0.0
        busy_pct = (busy_loops / loops * 100.0) if loops > 0 else 0.0

        recent_commands = [
            entry for entry in self._command_perf_history if isinstance(entry, dict)
        ]
        command_count = len(recent_commands)
        total_values = [int(entry.get("total_ms", 0)) for entry in recent_commands]
        backend_values = [int(entry.get("backend_ms", 0)) for entry in recent_commands]
        stream_values = [int(entry.get("stream_ms", 0)) for entry in recent_commands]
        chunk_values = [int(entry.get("chunks", 0)) for entry in recent_commands]

        avg_cmd_total = self._average(total_values)
        p95_cmd_total = self._percentile(total_values, 95)
        avg_cmd_backend = self._average(backend_values)
        avg_cmd_stream = self._average(stream_values)
        avg_chunks = self._average(chunk_values)

        severe = (
            dropped > 0
            or peak_queue >= 550
            or avg_drain_ms >= 15.0
            or p95_cmd_total >= 4200.0
        )
        warning = (
            peak_queue >= 240
            or avg_drain_ms >= 10.0
            or p95_cmd_total >= 2600.0
            or busy_pct >= 55.0
        )
        should_emit = self.diagnostics_mode or severe or warning
        level = "DEBUG" if self.diagnostics_mode else ("WARN" if severe else "INFO")
        snapshot = {
            "captured_at": captured_at,
            "queue": {
                "last": last_queue,
                "peak": peak_queue,
                "drops": dropped,
            },
            "drain": {
                "avg_ms": round(avg_drain_ms, 3),
                "events_per_loop": round(avg_events, 3),
                "busy_pct": round(busy_pct, 3),
                "loops": loops,
            },
            "commands": {
                "count": command_count,
                "avg_total_ms": round(avg_cmd_total, 3),
                "p95_total_ms": round(p95_cmd_total, 3),
                "avg_backend_ms": round(avg_cmd_backend, 3),
                "avg_stream_ms": round(avg_cmd_stream, 3),
                "avg_chunks": round(avg_chunks, 3),
            },
            "severity": "severe" if severe else ("warning" if warning else "normal"),
        }
        if self._perf_persist_to_file:
            self._persist_perf_snapshot(snapshot)

        self._reset_drain_metrics()
        self._queue_drop_count = 0

        if not should_emit:
            return "", level

        report = (
            "PERF queue{last=%d peak=%d drops=%d} drain{avg=%.2fms events=%.1f busy=%.0f%%} "
            "cmd{n=%d avg=%.0fms p95=%.0fms backend=%.0fms stream=%.0fms chunks=%.1f}"
            % (
                last_queue,
                peak_queue,
                dropped,
                avg_drain_ms,
                avg_events,
                busy_pct,
                command_count,
                avg_cmd_total,
                p95_cmd_total,
                avg_cmd_backend,
                avg_cmd_stream,
                avg_chunks,
            )
        )
        return report, level

    @staticmethod
    def _average(values: list[int]) -> float:
        if not values:
            return 0.0
        return float(sum(values)) / float(len(values))

    @staticmethod
    def _percentile(values: list[int], percentile: int) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        rank = int(round((max(0, min(100, percentile)) / 100.0) * (len(ordered) - 1)))
        return float(ordered[rank])

    def _persist_perf_snapshot(self, snapshot: dict[str, Any]) -> None:
        try:
            self._runtime_data_dir.mkdir(parents=True, exist_ok=True)
            self._truncate_perf_file_if_needed()
            payload = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=True)
            with self._perf_file_path.open("a", encoding="utf-8") as fp:
                fp.write(payload + "\n")
        except Exception:
            # Telemetry persistence must never impact app flow.
            pass

    def _truncate_perf_file_if_needed(self) -> None:
        if not self._perf_file_path.exists():
            return
        try:
            size = self._perf_file_path.stat().st_size
        except Exception:
            return
        if size < self._perf_file_max_bytes:
            return

        keep_bytes = max(1024, self._perf_file_max_bytes // 2)
        try:
            raw = self._perf_file_path.read_bytes()
        except Exception:
            return
        tail = raw[-keep_bytes:]
        newline_pos = tail.find(b"\n")
        if newline_pos != -1 and newline_pos + 1 < len(tail):
            tail = tail[newline_pos + 1 :]
        try:
            self._perf_file_path.write_bytes(tail)
        except Exception:
            pass

    def _status_poll_loop(self) -> None:
        if self._shutdown_event.is_set():
            return

        if not self.monitoring_enabled:
            self.status_panel.update_card("system_readiness", "Monitoring paused", "warning")
            self._schedule_status_poll()
            return

        self._refresh_status_cards()
        self._schedule_status_poll()

    def _schedule_status_poll(self, delay_ms: int | None = None) -> None:
        if self._shutdown_event.is_set():
            return
        next_delay = delay_ms if isinstance(delay_ms, int) and delay_ms > 0 else self._next_status_interval_ms()
        self._status_after_id = self.after(next_delay, self._status_poll_loop)

    def _next_status_interval_ms(self) -> int:
        if not self.monitoring_enabled:
            return 1300

        base = 980
        if self._state in {"PROCESSING", "EXECUTING", "SPEAKING"}:
            base = 760
        if not self._animation_active:
            base += 220
        queue_depth = self._ui_queue.qsize()
        if queue_depth > 200:
            base += 180
        elif queue_depth > 80:
            base += 90
        return max(700, min(1400, base))

    def _refresh_status_cards(self) -> None:
        mic_value = "Listening" if self._mic_active else "Idle"
        voice_mode = "Enabled" if self.voice_enabled else "Disabled"
        voice_level = "good" if self._mic_active else ("idle" if self.voice_enabled else "error")
        self.status_panel.update_card("voice_engine", f"{voice_mode} | {mic_value}", voice_level)

        if self.brain is None:
            self.status_panel.update_card("llm_provider", "Initializing...", "warning")
            self.status_panel.update_card("provider_reliability", "Warming up", "idle")
            self.status_panel.update_card(
                "thread_status",
                f"voice:{'up' if bool(self._voice_thread and self._voice_thread.is_alive()) else 'down'} | workers:{self._active_worker_count} | queue:{self._ui_queue.qsize()}",
                "warning",
            )
            self.status_panel.update_card("system_readiness", "Initializing core", "warning")
            return

        health = self.brain.get_llm_health()
        llm_status = str(health.get("status", "unknown")).lower()
        provider = str(health.get("provider", "none")).upper()
        reason = str(health.get("reason", ""))
        llm_level = "good"
        if llm_status in {"degraded", "idle"}:
            llm_level = "warning"
        elif llm_status in {"offline", "disabled"}:
            llm_level = "error"
        self.status_panel.update_card("llm_provider", f"{provider} | {llm_status}", llm_level)

        metrics = self._provider_metrics_snapshot()
        reliability_text, reliability_level = self._format_reliability(metrics)
        self.status_panel.update_card("provider_reliability", reliability_text, reliability_level)

        automation_level = "good" if self.automation_enabled else "error"
        self.status_panel.update_card(
            "automation_engine",
            "Enabled" if self.automation_enabled else "Disabled",
            automation_level,
        )

        voice_alive = bool(self._voice_thread and self._voice_thread.is_alive())
        queue_size = self._ui_queue.qsize()
        thread_level = "good" if voice_alive else "warning"
        self.status_panel.update_card(
            "thread_status",
            f"voice:{'up' if voice_alive else 'down'} | workers:{self._active_worker_count} | queue:{queue_size}",
            thread_level,
        )

        readiness_level = "good"
        readiness_text = "Operational"
        if not self.assistant_enabled:
            readiness_level = "warning"
            readiness_text = "Assistant paused"
        elif llm_status == "offline" and not self.automation_enabled:
            readiness_level = "error"
            readiness_text = "Limited capability"
        self.status_panel.update_card("system_readiness", readiness_text, readiness_level)

        self.top_bar.set_indicator("microphone", "ON" if self._mic_active else "OFF", "good" if self._mic_active else "idle")
        self.top_bar.set_indicator("provider", provider, "warning" if llm_status == "degraded" else ("error" if llm_status == "offline" else "success"))
        if self._last_command_latency_ms is not None:
            latency_level = "warning" if self._last_command_latency_ms > 2500 else "active"
            self.top_bar.set_indicator("latency", f"{self._last_command_latency_ms} ms", latency_level)

    def _provider_metrics_snapshot(self) -> dict[str, dict[str, Any]]:
        if self.brain is None:
            return {}
        metrics = getattr(self.brain.llm, "_provider_metrics", {})
        if not isinstance(metrics, dict):
            return {}
        snapshot: dict[str, dict[str, Any]] = {}
        for provider, raw in metrics.items():
            if not isinstance(raw, dict):
                continue
            snapshot[str(provider)] = dict(raw)
        return snapshot

    def _format_reliability(self, metrics: dict[str, dict[str, Any]]) -> tuple[str, str]:
        if not metrics:
            return "No metrics", "idle"
        parts: list[str] = []
        worst_rate = 1.0
        for provider in ("groq", "ollama", "gemini"):
            data = metrics.get(provider, {})
            success = int(data.get("successes", 0))
            failures = int(data.get("failures", 0))
            total = success + failures
            if total <= 0:
                parts.append(f"{provider}:--")
                continue
            rate = success / total
            worst_rate = min(worst_rate, rate)
            parts.append(f"{provider}:{int(rate * 100)}%")

        level = "good"
        if worst_rate < 0.8:
            level = "warning"
        if worst_rate < 0.5:
            level = "error"
        return " | ".join(parts), level

    def _on_brain_signal(self, signal: str) -> None:
        normalized = str(signal or "").strip().upper()
        if normalized == "ALERT_ON":
            self.enqueue("state", {"value": "EXECUTING"})
            self.enqueue("conversation", {"role": "SYSTEM", "text": "Alert mode activated by core."})
            self.enqueue("log", {"text": "Brain signal -> ALERT_ON", "level": "WARN"})
        elif normalized == "ALERT_OFF":
            self.enqueue("state", {"value": "NORMAL"})
            self.enqueue("conversation", {"role": "SYSTEM", "text": "Alert mode cleared by core."})
            self.enqueue("log", {"text": "Brain signal -> ALERT_OFF", "level": "INFO"})

    def _increment_workers(self, delta: int) -> None:
        with self._worker_lock:
            self._active_worker_count += delta
            if self._active_worker_count < 0:
                self._active_worker_count = 0

    def _reset_drain_metrics(self) -> None:
        self._drain_metrics = {
            "loops": 0,
            "events": 0,
            "ms_sum": 0.0,
            "peak_queue": 0,
            "last_queue": 0,
            "busy_loops": 0,
        }

    @staticmethod
    def _coerce_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except Exception:
            return fallback
        if parsed < minimum:
            return minimum
        if parsed > maximum:
            return maximum
        return parsed

    @staticmethod
    def _coerce_float(value: Any, fallback: float) -> float:
        try:
            return float(value)
        except Exception:
            return fallback

    @staticmethod
    def _coerce_bool(value: Any, fallback: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        return fallback

    def shutdown_system(self) -> None:
        if self._shutdown_event.is_set():
            return
        self._shutdown_event.set()
        if self._queue_after_id:
            try:
                self.after_cancel(self._queue_after_id)
            except Exception:
                pass
            self._queue_after_id = None
        if self._status_after_id:
            try:
                self.after_cancel(self._status_after_id)
            except Exception:
                pass
            self._status_after_id = None
        if self._perf_after_id:
            try:
                self.after_cancel(self._perf_after_id)
            except Exception:
                pass
            self._perf_after_id = None
        try:
            self.top_bar.shutdown()
        except Exception:
            pass
        try:
            self.side_panel.shutdown()
        except Exception:
            pass
        try:
            self.command_bar.shutdown()
        except Exception:
            pass
        try:
            self.status_panel.shutdown()
        except Exception:
            pass
        try:
            logging.getLogger().removeHandler(self._log_handler)
        except Exception:
            pass
        try:
            if self.voice is not None:
                self.voice.shutdown()
        except Exception:
            pass
        try:
            self.core_panel.shutdown()
        except Exception:
            pass
        try:
            self.withdraw()
        except Exception:
            pass
        try:
            self.quit()
        except Exception:
            pass
