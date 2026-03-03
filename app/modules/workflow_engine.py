from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Workflow:
    name: str
    steps: tuple[str, ...]
    description: str = ""


class WorkflowEngine:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config if isinstance(config, dict) else {}
        self.enabled = self._coerce_bool(cfg.get("enabled", True), True)
        self.max_steps = max(1, min(50, self._coerce_int(cfg.get("max_steps", 10), 10)))
        self._workflows = self._parse_workflows(cfg.get("definitions"))

    def list_workflows(self) -> list[str]:
        return sorted(self._workflows.keys())

    def get_workflow(self, name: str) -> Workflow | None:
        key = self._normalize_name(name)
        if not key:
            return None
        return self._workflows.get(key)

    def has_workflows(self) -> bool:
        return bool(self._workflows)

    def _parse_workflows(self, raw_definitions: Any) -> dict[str, Workflow]:
        if not isinstance(raw_definitions, dict):
            return {}

        parsed: dict[str, Workflow] = {}
        for raw_name, raw_value in raw_definitions.items():
            name = str(raw_name).strip()
            key = self._normalize_name(name)
            if not key:
                continue

            description = ""
            raw_steps: Any = None
            if isinstance(raw_value, list):
                raw_steps = raw_value
            elif isinstance(raw_value, dict):
                raw_steps = raw_value.get("steps")
                raw_description = raw_value.get("description")
                if isinstance(raw_description, str):
                    description = raw_description.strip()
            if not isinstance(raw_steps, list):
                continue

            steps: list[str] = []
            for item in raw_steps:
                if not isinstance(item, str):
                    continue
                command = item.strip()
                if command:
                    steps.append(command)
                if len(steps) >= self.max_steps:
                    break

            if not steps:
                continue

            parsed[key] = Workflow(
                name=name,
                steps=tuple(steps),
                description=description,
            )
        return parsed

    @staticmethod
    def _normalize_name(name: str) -> str:
        text = (name or "").strip().lower()
        if not text:
            return ""
        return " ".join(text.split())

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

    @staticmethod
    def _coerce_int(value: Any, fallback: int) -> int:
        try:
            return int(value)
        except Exception:
            return fallback
