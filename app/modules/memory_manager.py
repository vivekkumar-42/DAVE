from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.runtime_paths import runtime_data_dir

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryMatch:
    user_input: str
    resolved_intent: dict[str, Any]
    similarity: float
    timestamp: str


class MemoryManager:
    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        max_scan_rows: int = 250,
    ) -> None:
        resolved_db_path = Path(db_path) if db_path is not None else runtime_data_dir() / "dave_memory.db"
        self.db_path = resolved_db_path.resolve()
        self.max_scan_rows = max(20, int(max_scan_rows))
        self._lock = threading.Lock()

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=5.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._configure_connection()
        self._initialize_schema()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def log_command(
        self,
        *,
        user_input: str,
        resolved_intent: dict[str, Any],
        success: bool,
        timestamp: str | None = None,
    ) -> None:
        clean_input = (user_input or "").strip()
        if not clean_input:
            return

        payload = resolved_intent if isinstance(resolved_intent, dict) else {}
        payload_text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        ts = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds")

        with self._lock:
            try:
                self._conn.execute(
                    (
                        "INSERT INTO command_history "
                        "(user_input, resolved_intent, success, timestamp) "
                        "VALUES (?, ?, ?, ?)"
                    ),
                    (clean_input, payload_text, 1 if success else 0, ts),
                )
                self._conn.commit()
            except Exception as exc:
                _LOGGER.warning("Memory write failed: %s", exc)

    def find_similar_successes(
        self,
        user_input: str,
        *,
        limit: int = 3,
        min_similarity: float = 0.35,
    ) -> list[MemoryMatch]:
        clean_input = (user_input or "").strip()
        if not clean_input:
            return []

        normalized_query = self._normalize_text(clean_input)
        if not normalized_query:
            return []

        rows: list[sqlite3.Row]
        with self._lock:
            cursor = self._conn.execute(
                (
                    "SELECT user_input, resolved_intent, timestamp "
                    "FROM command_history "
                    "WHERE success = 1 "
                    "ORDER BY id DESC "
                    "LIMIT ?"
                ),
                (self.max_scan_rows,),
            )
            rows = list(cursor.fetchall())

        threshold = max(0.0, min(1.0, float(min_similarity)))
        top_k = max(1, int(limit))
        ranked: list[MemoryMatch] = []
        for row in rows:
            candidate_input = str(row["user_input"] or "").strip()
            if not candidate_input:
                continue

            candidate_normalized = self._normalize_text(candidate_input)
            if not candidate_normalized:
                continue

            score = SequenceMatcher(None, normalized_query, candidate_normalized).ratio()
            if score < threshold:
                continue

            ranked.append(
                MemoryMatch(
                    user_input=candidate_input,
                    resolved_intent=self._parse_intent_payload(row["resolved_intent"]),
                    similarity=score,
                    timestamp=str(row["timestamp"] or ""),
                )
            )

        ranked.sort(key=lambda item: (item.similarity, item.timestamp), reverse=True)
        return ranked[:top_k]

    def get_bootstrap_examples(
        self,
        user_input: str,
        *,
        limit: int = 3,
        min_similarity: float = 0.35,
    ) -> list[tuple[str, str]]:
        matches = self.find_similar_successes(
            user_input=user_input,
            limit=limit,
            min_similarity=min_similarity,
        )
        examples: list[tuple[str, str]] = []
        for match in matches:
            assistant_line = self.render_intent_summary(match.resolved_intent)
            if not assistant_line:
                continue
            examples.append((match.user_input, assistant_line))
        return examples

    @staticmethod
    def render_intent_summary(resolved_intent: dict[str, Any]) -> str:
        if not isinstance(resolved_intent, dict):
            return ""

        intent = str(resolved_intent.get("intent", "")).strip().lower()
        route = str(resolved_intent.get("route", "")).strip().lower()
        target = str(resolved_intent.get("target", "")).strip()
        query = str(resolved_intent.get("query", "")).strip()
        command = str(resolved_intent.get("command", "")).strip()
        shell_mode = str(resolved_intent.get("shell_mode", "")).strip().lower()

        parts: list[str] = []
        if intent:
            parts.append(f"intent={intent}")
        if route:
            parts.append(f"route={route}")
        if target:
            parts.append(f"target={target}")
        if query:
            parts.append(f"query={query}")
        if command:
            parts.append(f"command={command}")
        if shell_mode:
            parts.append(f"shell_mode={shell_mode}")

        if parts:
            return "Resolved successful action: " + " | ".join(parts)
        payload_text = json.dumps(resolved_intent, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        return f"Resolved successful action: {payload_text}"

    def _configure_connection(self) -> None:
        try:
            self._conn.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            pass
        try:
            self._conn.execute("PRAGMA synchronous=NORMAL;")
        except Exception:
            pass

    def _initialize_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS command_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_input TEXT NOT NULL,
                    resolved_intent TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_command_history_success_time
                ON command_history (success, timestamp DESC)
                """
            )
            self._conn.commit()

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join((value or "").strip().lower().split())

    @staticmethod
    def _parse_intent_payload(raw_payload: Any) -> dict[str, Any]:
        if not isinstance(raw_payload, str):
            return {}
        payload_text = raw_payload.strip()
        if not payload_text:
            return {}
        try:
            parsed = json.loads(payload_text)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return parsed
        return {}
