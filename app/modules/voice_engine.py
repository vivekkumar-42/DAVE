from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any

import pyttsx3
import speech_recognition as sr

_MIC_INDEX_UNSET = object()


class VoiceEngine:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config if isinstance(config, dict) else {}
        voice_cfg_raw = self.config.get("voice", {})
        self.voice_config = voice_cfg_raw if isinstance(voice_cfg_raw, dict) else {}

        self.recognizer = sr.Recognizer()
        self.recognizer.pause_threshold = self._coerce_float(
            self.voice_config.get("pause_threshold", 0.8),
            0.8,
            minimum=0.05,
        )

        self.tts_backend = str(self.voice_config.get("tts_backend", "pyttsx3")).lower()
        if self.tts_backend != "pyttsx3":
            logging.warning("Unsupported tts_backend '%s'; falling back to pyttsx3.", self.tts_backend)
            self.tts_backend = "pyttsx3"

        self.microphone_device_index = self._coerce_optional_int(
            self.voice_config.get("microphone_device_index")
        )
        self.ambient_adjust_seconds = self._coerce_float(
            self.voice_config.get("ambient_adjust_seconds", 1.0),
            1.0,
            minimum=0.0,
        )
        self.listen_timeout = self._coerce_float(
            self.voice_config.get("listen_timeout", 3.0),
            3.0,
            minimum=0.25,
        )
        self.phrase_time_limit = self._coerce_float(
            self.voice_config.get("phrase_time_limit", 7.0),
            7.0,
            minimum=0.5,
        )
        self.mic_reprobe_interval_seconds = self._coerce_float(
            self.voice_config.get("mic_reprobe_interval_seconds", 8.0),
            8.0,
            minimum=1.0,
        )
        self._last_mic_reprobe_at = 0.0
        self._no_mic_logged = False
        self._resolved_microphone_index: int | None | object = _MIC_INDEX_UNSET

        self._tts_queue: queue.Queue[str | None] = queue.Queue()
        self._shutdown_event = threading.Event()
        self._tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
        self._tts_thread.start()

    def speak(self, text: str) -> None:
        if not text:
            return
        self._tts_queue.put(text)

    def listen(self) -> str | None:
        if self._resolved_microphone_index is _MIC_INDEX_UNSET:
            self._resolved_microphone_index = self._resolve_microphone_index(
                self.microphone_device_index
            )
        resolved_index = (
            self._resolved_microphone_index
            if isinstance(self._resolved_microphone_index, int) or self._resolved_microphone_index is None
            else None
        )
        try:
            return self._listen_once(resolved_index)
        except sr.WaitTimeoutError:
            return None
        except sr.UnknownValueError:
            return None
        except sr.RequestError as exc:
            logging.warning("Speech recognition service error: %s", exc)
            return None
        except (OSError, AssertionError, AttributeError) as exc:
            now = time.monotonic()
            if now - self._last_mic_reprobe_at >= self.mic_reprobe_interval_seconds:
                self._last_mic_reprobe_at = now
                logging.warning(
                    "Microphone capture failed on index %s: %s",
                    resolved_index,
                    exc,
                )
                refreshed_index = self._resolve_microphone_index(self.microphone_device_index)
                if refreshed_index != resolved_index:
                    self._resolved_microphone_index = refreshed_index
                    try:
                        return self._listen_once(self._resolved_microphone_index)
                    except Exception:
                        return None
            return None
        except Exception as exc:
            logging.exception("Voice listen failure: %s", exc)
            return None

    def shutdown(self) -> None:
        self._shutdown_event.set()
        self._tts_queue.put(None)
        if self._tts_thread.is_alive():
            self._tts_thread.join(timeout=2.0)

    def _tts_worker(self) -> None:
        try:
            engine = pyttsx3.init()
            engine.setProperty(
                "rate",
                self._coerce_int(self.voice_config.get("tts_rate", 185), 185, minimum=80),
            )
            engine.setProperty(
                "volume",
                self._coerce_float(
                    self.voice_config.get("tts_volume", 1.0),
                    1.0,
                    minimum=0.0,
                    maximum=1.0,
                ),
            )
            tts_voice_id = self.voice_config.get("tts_voice_id")
            if isinstance(tts_voice_id, str) and tts_voice_id.strip():
                engine.setProperty("voice", tts_voice_id.strip())
        except Exception as exc:
            logging.exception("Failed to initialize pyttsx3: %s", exc)
            return

        while not self._shutdown_event.is_set():
            try:
                text = self._tts_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if text is None:
                self._tts_queue.task_done()
                break

            try:
                engine.say(text)
                engine.runAndWait()
            except Exception as exc:
                logging.exception("TTS playback failure: %s", exc)
            finally:
                self._tts_queue.task_done()

        try:
            engine.stop()
        except Exception:
            pass

    def _listen_once(self, device_index: int | None) -> str | None:
        with sr.Microphone(device_index=device_index) as source:
            self.recognizer.adjust_for_ambient_noise(
                source, duration=self.ambient_adjust_seconds
            )
            audio = self.recognizer.listen(
                source,
                timeout=self.listen_timeout,
                phrase_time_limit=self.phrase_time_limit,
            )
        heard_text = self.recognizer.recognize_google(audio)
        return heard_text.strip() if heard_text else None

    def _resolve_microphone_index(self, preferred_index: Any) -> int | None:
        preferred = preferred_index if isinstance(preferred_index, int) and preferred_index >= 0 else None
        if preferred is not None and self._can_open_microphone(preferred):
            self._no_mic_logged = False
            return preferred

        if preferred is not None:
            logging.warning(
                "Configured microphone_device_index %s is unavailable. Falling back.",
                preferred,
            )

        if self._can_open_microphone(None):
            self._no_mic_logged = False
            return None

        try:
            for idx, _ in enumerate(sr.Microphone.list_microphone_names()):
                if self._can_open_microphone(idx):
                    self._no_mic_logged = False
                    logging.info("Using fallback microphone index %s.", idx)
                    return idx
        except Exception as exc:
            logging.warning("Failed to enumerate microphone devices: %s", exc)

        if not self._no_mic_logged:
            logging.warning("No working microphone input device detected.")
            self._no_mic_logged = True
        return None

    @staticmethod
    def _can_open_microphone(device_index: int | None) -> bool:
        try:
            with sr.Microphone(device_index=device_index) as source:
                return getattr(source, "stream", None) is not None
        except Exception:
            return False

    @staticmethod
    def _coerce_float(
        value: Any,
        fallback: float,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float:
        try:
            parsed = float(value)
        except Exception:
            parsed = fallback
        if minimum is not None and parsed < minimum:
            return minimum
        if maximum is not None and parsed > maximum:
            return maximum
        return parsed

    @staticmethod
    def _coerce_int(
        value: Any,
        fallback: int,
        *,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        try:
            parsed = int(value)
        except Exception:
            parsed = fallback
        if minimum is not None and parsed < minimum:
            return minimum
        if maximum is not None and parsed > maximum:
            return maximum
        return parsed

    @staticmethod
    def _coerce_optional_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(value)
        except Exception:
            return None
        return parsed if parsed >= 0 else None
