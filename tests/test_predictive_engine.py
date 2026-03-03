import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from app.modules.predictive_engine import HabitTracker, Predictor, SKLEARN_AVAILABLE


class PredictiveEngineTests(unittest.TestCase):
    def test_habit_tracker_logs_and_fetches_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "dave_habits.db"
            tracker = HabitTracker(db_path=db_path)
            try:
                tracker.log_execution(
                    target_intent="open_app:spotify",
                    last_command_intent="open_app:chrome",
                    active_window_title="Spotify",
                )
                samples = tracker.fetch_samples()
            finally:
                tracker.close()

        self.assertEqual(1, len(samples))
        self.assertEqual("open_app:spotify", samples[0].target_intent)
        self.assertEqual("open_app:chrome", samples[0].last_command_intent)
        self.assertEqual("spotify", samples[0].active_window_title)

    def test_predictor_disables_gracefully_without_sklearn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tracker = HabitTracker(db_path=Path(tmp_dir) / "dave_habits.db")
            try:
                with mock.patch("app.modules.predictive_engine.SKLEARN_AVAILABLE", False):
                    predictor = Predictor(
                        habit_tracker=tracker,
                        model_path=Path(tmp_dir) / "dave_habits_model.pkl",
                        auto_start=False,
                    )
                self.assertFalse(predictor.enabled)
                self.assertFalse(predictor.train_now())
                self.assertIsNone(
                    predictor.predict_next_action(
                        {
                            "hour_of_day": "10",
                            "day_of_week": "1",
                            "active_window_title": "spotify",
                            "last_command_intent": "none",
                        }
                    )
                )
            finally:
                tracker.close()

    @unittest.skipUnless(SKLEARN_AVAILABLE, "scikit-learn not installed")
    def test_predictor_trains_and_predicts_high_confidence_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tracker = HabitTracker(db_path=Path(tmp_dir) / "dave_habits.db")
            fixed_time = datetime(2026, 2, 23, 9, 0, 0)
            try:
                for _ in range(30):
                    tracker.log_execution(
                        target_intent="open_app:spotify",
                        last_command_intent="none",
                        active_window_title="spotify premium",
                        observed_at=fixed_time,
                    )

                predictor = Predictor(
                    habit_tracker=tracker,
                    model_path=Path(tmp_dir) / "dave_habits_model.pkl",
                    confidence_threshold=0.85,
                    min_training_samples=10,
                    train_interval_seconds=86400.0,
                    auto_start=False,
                )
                trained = predictor.train_now()
                context = tracker.build_context(
                    last_command_intent="none",
                    active_window_title="spotify premium",
                    observed_at=fixed_time,
                )
                suggestion = predictor.predict_next_action(context)
            finally:
                tracker.close()

        self.assertTrue(trained)
        self.assertEqual("open_app:spotify", suggestion)


if __name__ == "__main__":
    unittest.main()
