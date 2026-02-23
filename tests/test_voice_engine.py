import unittest
from unittest import mock

import speech_recognition as sr

from app.modules.voice_engine import VoiceEngine


class _FakeTTSEngine:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def setProperty(self, _name: str, _value) -> None:
        return None

    def say(self, text: str) -> None:
        self.spoken.append(text)

    def runAndWait(self) -> None:
        return None

    def stop(self) -> None:
        return None


class VoiceEngineTests(unittest.TestCase):
    def test_constructor_handles_malformed_voice_config(self) -> None:
        fake_engine = _FakeTTSEngine()
        bad_config = {
            "voice": {
                "pause_threshold": "bad",
                "ambient_adjust_seconds": "bad",
                "listen_timeout": "bad",
                "phrase_time_limit": "bad",
                "mic_reprobe_interval_seconds": "bad",
                "tts_rate": "bad",
                "tts_volume": "bad",
                "microphone_device_index": "bad",
            }
        }
        with mock.patch("app.modules.voice_engine.pyttsx3.init", return_value=fake_engine):
            with mock.patch.object(VoiceEngine, "_resolve_microphone_index", return_value=None):
                engine = VoiceEngine(config=bad_config)
                try:
                    self.assertEqual(0.8, engine.recognizer.pause_threshold)
                    self.assertEqual(1.0, engine.ambient_adjust_seconds)
                    self.assertEqual(3.0, engine.listen_timeout)
                    self.assertEqual(7.0, engine.phrase_time_limit)
                    self.assertEqual(8.0, engine.mic_reprobe_interval_seconds)
                    self.assertIsNone(engine.microphone_device_index)
                    engine.speak("config safe")
                    engine._tts_queue.join()
                    self.assertEqual(["config safe"], fake_engine.spoken)
                finally:
                    engine.shutdown()

    def test_speak_queues_text_without_blocking(self) -> None:
        fake_engine = _FakeTTSEngine()
        with mock.patch("app.modules.voice_engine.pyttsx3.init", return_value=fake_engine):
            with mock.patch.object(VoiceEngine, "_resolve_microphone_index", return_value=None):
                engine = VoiceEngine(config={"voice": {}})
                try:
                    engine.speak("test line")
                    engine._tts_queue.join()
                    self.assertEqual(["test line"], fake_engine.spoken)
                finally:
                    engine.shutdown()

    def test_listen_returns_none_on_unknown_value(self) -> None:
        fake_engine = _FakeTTSEngine()
        with mock.patch("app.modules.voice_engine.pyttsx3.init", return_value=fake_engine):
            with mock.patch.object(VoiceEngine, "_resolve_microphone_index", return_value=None):
                engine = VoiceEngine(config={"voice": {}})
                try:
                    with mock.patch.object(
                        engine, "_listen_once", side_effect=sr.UnknownValueError()
                    ):
                        result = engine.listen()
                    self.assertIsNone(result)
                finally:
                    engine.shutdown()

    def test_listen_returns_none_on_request_error(self) -> None:
        fake_engine = _FakeTTSEngine()
        with mock.patch("app.modules.voice_engine.pyttsx3.init", return_value=fake_engine):
            with mock.patch.object(VoiceEngine, "_resolve_microphone_index", return_value=None):
                engine = VoiceEngine(config={"voice": {}})
                try:
                    with mock.patch.object(
                        engine, "_listen_once", side_effect=sr.RequestError("stt offline")
                    ):
                        result = engine.listen()
                    self.assertIsNone(result)
                finally:
                    engine.shutdown()


if __name__ == "__main__":
    unittest.main()
