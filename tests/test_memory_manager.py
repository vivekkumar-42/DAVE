import tempfile
import unittest
from pathlib import Path

from app.modules.memory_manager import MemoryManager


class MemoryManagerTests(unittest.TestCase):
    def test_logs_and_retrieves_similar_successful_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "dave_memory.db"
            manager = MemoryManager(db_path=db_path, max_scan_rows=100)
            try:
                manager.log_command(
                    user_input="open chrome and search cars",
                    resolved_intent={
                        "intent": "open_and_search",
                        "route": "automation",
                        "target": "chrome",
                        "query": "cars",
                    },
                    success=True,
                )
                manager.log_command(
                    user_input="open calculator",
                    resolved_intent={
                        "intent": "open_app",
                        "route": "automation",
                        "target": "calculator",
                    },
                    success=True,
                )
                manager.log_command(
                    user_input="play music",
                    resolved_intent={
                        "intent": "chat",
                        "route": "llm_intent",
                    },
                    success=False,
                )

                matches = manager.find_similar_successes(
                    "open chrome search cars",
                    limit=3,
                    min_similarity=0.2,
                )
                self.assertGreaterEqual(len(matches), 1)
                self.assertEqual("open chrome and search cars", matches[0].user_input)
                self.assertEqual("open_and_search", matches[0].resolved_intent.get("intent"))
            finally:
                manager.close()

    def test_bootstrap_examples_render_from_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "dave_memory.db"
            manager = MemoryManager(db_path=db_path, max_scan_rows=100)
            try:
                manager.log_command(
                    user_input="run powershell Get-Date",
                    resolved_intent={
                        "intent": "run_command",
                        "route": "automation",
                        "command": "Get-Date",
                        "shell_mode": "powershell",
                    },
                    success=True,
                )
                examples = manager.get_bootstrap_examples(
                    "run command date",
                    limit=3,
                    min_similarity=0.2,
                )
                self.assertEqual(1, len(examples))
                self.assertEqual("run powershell Get-Date", examples[0][0])
                self.assertIn("Resolved successful action:", examples[0][1])
                self.assertIn("intent=run_command", examples[0][1])
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
