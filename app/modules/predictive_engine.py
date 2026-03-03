from __future__ import annotations

import ctypes
import logging
import pickle
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.runtime_paths import runtime_data_dir

_LOGGER = logging.getLogger(__name__)

try:
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder

    SKLEARN_AVAILABLE = True
except Exception:  # pragma: no cover - covered by fallback behavior tests
    ColumnTransformer = None  # type: ignore[assignment]
    RandomForestClassifier = None  # type: ignore[assignment]
    Pipeline = None  # type: ignore[assignment]
    OneHotEncoder = None  # type: ignore[assignment]
    SKLEARN_AVAILABLE = False


@dataclass(frozen=True)
class HabitSample:
    hour_of_day: str
    day_of_week: str
    active_window_title: str
    last_command_intent: str
    target_intent: str


class HabitTracker:
    def __init__(self, db_path: str | Path | None = None) -> None:
        resolved = Path(db_path) if db_path is not None else runtime_data_dir() / "dave_habits.db"
        self.db_path = resolved.resolve()
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        self._initialize_schema()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def log_execution(
        self,
        *,
        target_intent: str,
        last_command_intent: str,
        active_window_title: str | None = None,
        observed_at: datetime | None = None,
    ) -> None:
        target = (target_intent or "").strip().lower()
        if not target:
            return

        context = self.build_context(
            last_command_intent=last_command_intent,
            active_window_title=active_window_title,
            observed_at=observed_at,
        )

        with self._lock:
            try:
                self._conn.execute(
                    (
                        "INSERT INTO habit_events "
                        "(timestamp, hour_of_day, day_of_week, active_window_title, "
                        "last_command_intent, target_intent) "
                        "VALUES (?, ?, ?, ?, ?, ?)"
                    ),
                    (
                        context["timestamp"],
                        context["hour_of_day"],
                        context["day_of_week"],
                        context["active_window_title"],
                        context["last_command_intent"],
                        target,
                    ),
                )
                self._conn.commit()
            except Exception as exc:
                _LOGGER.warning("Habit logging failed: %s", exc)

    def build_context(
        self,
        *,
        last_command_intent: str,
        active_window_title: str | None = None,
        observed_at: datetime | None = None,
    ) -> dict[str, str]:
        observed = observed_at or datetime.now()
        window_title = (
            self.get_active_window_title() if active_window_title is None else active_window_title
        )
        clean_window = self._normalize_window_title(window_title)
        return {
            "timestamp": observed.isoformat(timespec="seconds"),
            "hour_of_day": str(int(observed.hour)),
            "day_of_week": str(int(observed.weekday())),
            "active_window_title": clean_window,
            "last_command_intent": self._normalize_label(last_command_intent),
        }

    def fetch_samples(self, limit: int | None = None) -> list[HabitSample]:
        query = (
            "SELECT hour_of_day, day_of_week, active_window_title, "
            "last_command_intent, target_intent "
            "FROM habit_events "
            "ORDER BY id ASC"
        )
        params: tuple[Any, ...] = ()
        if isinstance(limit, int) and limit > 0:
            query += " LIMIT ?"
            params = (limit,)

        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = list(cursor.fetchall())

        samples: list[HabitSample] = []
        for row in rows:
            target = self._normalize_label(row["target_intent"])
            if not target:
                continue
            samples.append(
                HabitSample(
                    hour_of_day=self._normalize_label(row["hour_of_day"]),
                    day_of_week=self._normalize_label(row["day_of_week"]),
                    active_window_title=self._normalize_window_title(row["active_window_title"]),
                    last_command_intent=self._normalize_label(row["last_command_intent"]),
                    target_intent=target,
                )
            )
        return samples

    @staticmethod
    def get_active_window_title() -> str:
        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return "unknown"

            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return "unknown"

            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            value = str(buffer.value or "").strip()
            return value if value else "unknown"
        except Exception:
            return "unknown"

    def _initialize_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS habit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    hour_of_day TEXT NOT NULL,
                    day_of_week TEXT NOT NULL,
                    active_window_title TEXT NOT NULL,
                    last_command_intent TEXT NOT NULL,
                    target_intent TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_habit_events_timestamp
                ON habit_events (timestamp DESC)
                """
            )
            self._conn.commit()

    @staticmethod
    def _normalize_label(value: Any) -> str:
        text = str(value or "").strip().lower()
        return text if text else "none"

    @staticmethod
    def _normalize_window_title(value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return "unknown"
        if len(text) > 140:
            text = text[:140]
        return text


class Predictor:
    def __init__(
        self,
        habit_tracker: HabitTracker | None = None,
        *,
        model_path: str | Path | None = None,
        confidence_threshold: float = 0.85,
        min_training_samples: int = 25,
        train_interval_seconds: float = 86400.0,
        auto_start: bool = True,
    ) -> None:
        self.habit_tracker = habit_tracker or HabitTracker()
        resolved_model_path = (
            Path(model_path) if model_path is not None else runtime_data_dir() / "dave_habits_model.pkl"
        )
        self.model_path = resolved_model_path.resolve()
        self.confidence_threshold = max(0.0, min(1.0, float(confidence_threshold)))
        self.min_training_samples = max(5, int(min_training_samples))
        self.train_interval_seconds = max(60.0, float(train_interval_seconds))

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._training_thread: threading.Thread | None = None
        self._model: Any = None
        self._trained_at: float | None = None
        self._enabled = SKLEARN_AVAILABLE

        if not self._enabled:
            _LOGGER.warning("Predictive engine disabled: scikit-learn is unavailable.")
            return

        self._load_model()
        if auto_start:
            self.start_background_training()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start_background_training(self) -> None:
        if not self._enabled:
            return
        if self._training_thread is not None and self._training_thread.is_alive():
            return

        self._training_thread = threading.Thread(
            target=self._training_loop,
            name="dave-predictive-trainer",
            daemon=True,
        )
        self._training_thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def train_now(self) -> bool:
        if not self._enabled:
            return False
        return self._fit_from_history()

    def predict_next_action(self, current_context: dict[str, str]) -> str | None:
        if not self._enabled:
            return None
        with self._lock:
            model = self._model
        if model is None:
            return None

        row = self._context_row(current_context)
        try:
            probabilities = model.predict_proba([row])[0]
            classes = list(model.classes_)
        except Exception as exc:
            _LOGGER.debug("Predictive inference failed: %s", exc)
            return None

        if not classes:
            return None
        max_index = max(range(len(classes)), key=lambda idx: float(probabilities[idx]))
        max_probability = float(probabilities[max_index])
        if max_probability < self.confidence_threshold:
            return None
        return str(classes[max_index])

    def _training_loop(self) -> None:
        # Immediate startup fit.
        self._fit_from_history()
        while not self._stop_event.wait(self.train_interval_seconds):
            self._fit_from_history()

    def _fit_from_history(self) -> bool:
        samples = self.habit_tracker.fetch_samples()
        if len(samples) < self.min_training_samples:
            _LOGGER.debug(
                "Predictive training skipped: need %s samples, have %s.",
                self.min_training_samples,
                len(samples),
            )
            return False

        x_values = [
            [s.hour_of_day, s.day_of_week, s.active_window_title, s.last_command_intent]
            for s in samples
        ]
        y_values = [s.target_intent for s in samples]
        model = self._create_pipeline()
        if model is None:
            return False

        try:
            model.fit(x_values, y_values)
        except Exception as exc:
            _LOGGER.warning("Predictive model fit failed: %s", exc)
            return False

        with self._lock:
            self._model = model
            self._trained_at = time.time()
        self._persist_model(model)
        _LOGGER.info("Predictive model trained with %s samples.", len(samples))
        return True

    def _create_pipeline(self) -> Any:
        if not SKLEARN_AVAILABLE:
            return None
        preprocessor = ColumnTransformer(
            transformers=[
                ("categorical", OneHotEncoder(handle_unknown="ignore"), [0, 1, 2, 3]),
            ],
            remainder="drop",
        )
        classifier = RandomForestClassifier(
            n_estimators=120,
            max_depth=18,
            min_samples_leaf=1,
            random_state=42,
            n_jobs=1,
        )
        return Pipeline(
            steps=[
                ("preprocessor", preprocessor),
                ("classifier", classifier),
            ]
        )

    def _persist_model(self, model: Any) -> None:
        try:
            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            with self.model_path.open("wb") as handle:
                pickle.dump(model, handle, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as exc:
            _LOGGER.warning("Predictive model persistence failed: %s", exc)

    def _load_model(self) -> None:
        if not self.model_path.exists():
            return
        try:
            with self.model_path.open("rb") as handle:
                loaded = pickle.load(handle)
        except Exception as exc:
            _LOGGER.warning("Predictive model load failed: %s", exc)
            return

        with self._lock:
            self._model = loaded
            self._trained_at = time.time()

    @staticmethod
    def _context_row(context: dict[str, str]) -> list[str]:
        return [
            str(context.get("hour_of_day", "0")),
            str(context.get("day_of_week", "0")),
            str(context.get("active_window_title", "unknown")),
            str(context.get("last_command_intent", "none")),
        ]
