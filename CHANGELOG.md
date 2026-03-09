# Changelog

All notable changes to DAVE are documented in this file.

This repository did not have a prior changelog, so this first entry is a baseline snapshot of the current state as of February 14, 2026.

## [2026-03-09] - Dependency Refresh, Pinning, and Maintenance Validation

### Changed
- Upgraded direct provider/runtime dependencies:
  - `google-genai` `1.64.0 -> 1.66.0`
  - `groq` `1.0.0 -> 1.1.0`
  - `openai` `2.21.0 -> 2.26.0`
  - `SpeechRecognition` `3.14.5 -> 3.14.6`
- Pinned all direct dependencies in `requirements.txt` to installed, validated versions for reproducible environments.

### Validation
- `py -3 -m pip check`
- `py -3 -m pip_audit -r requirements.txt`
- `py -3 -m unittest discover -s tests -p "test_*.py" -v`
- `.\tests\smoke_ui.ps1 -AutoExitSeconds 5 -TimeoutSeconds 45`
- `.\tests\smoke_release.ps1 -TimeoutSeconds 90`
- `.\tests\installer_smoke.ps1 -ProcessTimeoutSeconds 300`
- Result: all checks passed; no known vulnerabilities; no broken requirements.

## [2026-03-03] - Release 0.3.0: UI Visual System Propagation and Smoother Rendering

### Changed
- UI components now consistently use the glass/skeuomorphic visual language across:
  - `top_bar.py`
  - `side_panel.py`
  - `status_panel.py`
  - `conversation_panel.py`
  - `command_bar.py`
  - `core_panel.py`
  - `reactor_animation.py`
  - `main_window.py`
- `reactor_animation.py` now uses cached canvas items instead of full per-frame redraws to reduce rendering overhead and improve frame smoothness.
- `core_panel.py` execution console now batches log writes to reduce textbox churn during high-frequency logging.
- `conversation_panel.py` stream updates now tag only appended chunk regions and track line bounds directly to reduce text-widget overhead.
- `config.json` and `config.template.json` (including `release/DAVE` copies) now include default glass/skeuo palette keys in `ui.visual_system`:
  - `glass_tint`
  - `glass_edge`
  - `glass_shadow`
  - `skeuo_highlight`
  - `skeuo_shadow`
- `README.md` UI/config documentation updated to reflect the visual system and rendering-performance changes.

### Validation
- `py -3 -m compileall app/ui`
- `py -3 -m pytest -q tests/test_main_bootstrap.py`

## [2026-03-03] - Release 0.3.0: Workflow Automation, Safety Policy, and Latency Improvements

### Added
- `app/modules/workflow_engine.py` with config-driven named workflow support.
- New workflow command paths in `app/modules/brain_core.py`:
  - `list workflows` / `show workflows`
  - `run workflow <name>` / `workflow <name>`
- Guarded shell execution policy in `brain_core.py` with:
  - command-length cap,
  - blocked pattern list,
  - confirm-required pattern list,
  - configurable pending-confirmation timeout.
- Workflow runtime UI signals in `app/ui/main_window.py`:
  - `WORKFLOW_STEP`
  - `WORKFLOW_DONE`
- New tests:
  - `tests/test_workflow_engine.py`
  - brain-core tests for workflow execution and shell safety confirmations/blocks
  - llm-interface tests for response cache hits
  - automation-engine test for negative file-resolution cache behavior

### Changed
- `app/modules/llm_interface.py` now includes bounded TTL response caching for:
  - normal responses,
  - structured intent-router responses.
- `app/modules/automation_engine.py` now caches positive/negative file-resolution lookups to avoid repeated deep tree scans for repeated requests.
- `config.json` and `config.template.json` now include:
  - `llm.response_cache_enabled`
  - `llm.response_cache_ttl_seconds`
  - `llm.response_cache_max_entries`
  - `policy` block (shell safety)
  - `workflows` block (workflow definitions and limits)

### Validation
- `py -3 -m unittest discover -s tests -p "test_*.py" -v`
- Result: 75 tests passed.
- `.\tests\smoke_release.ps1`
- `.\tests\installer_smoke.ps1`
- `.\build_exe.ps1`
- `.\build_installer.ps1`

## [2026-02-23] - Memory Bank and Predictive Action Integration

### Added
- `app/modules/memory_manager.py` with SQLite-backed `command_history` storage (`dave_memory.db`), successful command logging, and lightweight similarity retrieval for dynamic few-shot context.
- `app/modules/predictive_engine.py` with:
  - `HabitTracker` (`dave_habits.db`) to capture contextual habit features (`hour_of_day`, `day_of_week`, `active_window_title`, `last_command_intent`, `target_intent`).
  - `Predictor` using a scikit-learn pipeline (`OneHotEncoder` + `RandomForestClassifier`) with background daemon retraining and serialized model persistence (`dave_habits_model.pkl`).
- New tests:
  - `tests/test_memory_manager.py`
  - `tests/test_predictive_engine.py`

### Changed
- `app/modules/brain_core.py` now:
  - logs successful command outcomes into Memory Bank (Level 1),
  - logs successful command habits into Predictive Action dataset (Level 2),
  - emits high-confidence idle suggestions via `gui_callback` as `PREDICT_SUGGEST:<intent>`.
- `app/modules/automation_engine.py` now supports `open_file` fallback for `open/launch/start` commands when app launch fails (including quoted names like `BIBEK_KUMAR_YADAV_CV (2)` and direct file paths).
- `app/ui/main_window.py` now handles `PREDICT_SUGGEST` signals and surfaces suggestions in conversation and logs.
- `requirements.txt` now includes `scikit-learn`.
- `config.json` and `config.template.json` now include:
  - `memory` block (`enabled`, `top_k`, `min_similarity`, `scan_rows`)
  - `predictive` block (`enabled`, `confidence_threshold`, `idle_poll_seconds`, `suggestion_cooldown_seconds`, `train_interval_seconds`, `min_training_samples`)

### Validation
- `py -3.11 -m pytest -q`
- Result: 63 tests passed.

## [2026-02-23] - Concurrency, Failover, and Cognitive Routing Refactor

### Changed
- `app/modules/llm_interface.py` concurrency model updated so shared state lock scope only protects in-memory state (`history`, `health`, provider metrics/circuit state); provider network I/O is no longer executed inside a global lock.
- Circuit-breaker logic upgraded from simple open/closed flow to `closed -> open -> half-open` with a single probe request after cooldown.
- Retry strategy updated from linear delay to exponential backoff with jitter (`retry_jitter_ratio`) to reduce synchronized retry collisions under rate limiting.
- Added structured intent routing path (`route_intent`) in `LLMClient` with schema-based payload normalization.
- `Brain.process_command` now uses LLM intent routing as the primary command router; regex automation routing is retained as offline fallback when LLM health is offline.
- Removed regex JSON scraping path in `brain_core.py` for intent payload extraction.

### Configuration
- `config.json` and `config.template.json`:
  - Added `llm.retry_jitter_ratio`.
  - Set `llm.intent_routing_enabled` default to `true`.

### Documentation
- `README.md` updated to reflect:
  - Exponential+jitter retries.
  - Half-open breaker behavior.
  - LLM-first structured routing model with offline regex fallback.
- Release copies under `release/DAVE/` were synchronized with root `README.md`, `config.json`, and `config.template.json`.

### Testing
- Added/updated tests in:
  - `tests/test_llm_interface.py` for half-open breaker behavior, exponential jitter backoff, and structured intent routing.
  - `tests/test_brain_core.py` for offline-only regex fallback behavior.
- Validation executed on February 23, 2026:
  - `py -3 -m unittest discover -s tests -p "test_*.py" -v`
  - Result: 52 tests passed.

## [2026-02-14] - Full Application UI Overhaul

### Added
- New modular UI package under `app/ui/`:
  - `app/ui/main_window.py` (`MainWindow`)
  - `app/ui/theme.py`
  - `app/ui/events.py`
  - `app/ui/components/top_bar.py`
  - `app/ui/components/side_panel.py`
  - `app/ui/components/core_panel.py`
  - `app/ui/components/reactor_animation.py`
  - `app/ui/components/conversation_panel.py`
  - `app/ui/components/status_panel.py`
  - `app/ui/components/command_bar.py`

### Changed
- `main.py` now launches the modular `MainWindow` application shell instead of the previous monolithic UI class.
- Frontend interaction layer redesigned into:
  - Top bar with live state and telemetry indicators.
  - Left subsystem control panel with active highlighting and toggles.
  - Center intelligence core with animated reactor canvas, live conversation stream, and collapsible execution console.
  - Right diagnostics panel with live status cards.
  - Bottom command interface with command history, execute, mic toggle, interrupt, and clear actions.
- UI update path now uses a thread-safe queue (`backend worker -> UI queue -> UI thread`) to avoid direct widget mutation from worker threads.
- Runtime status monitoring now surfaces provider selection, command latency, reliability metrics, worker activity, and readiness state.
- `README.md` updated to document the new application architecture and UI system.
- `config.template.json` UI section standardized around `ui.visual_system` and `ui.state_visuals`.
- Frontend theme loader now supports runtime overrides from config (including compatibility with legacy `ui.accent_color` and `ui.alert_color`).

### Preserved
- Backend logic in `app/modules/brain_core.py`, `app/modules/voice_engine.py`, `app/modules/automation_engine.py`, and `app/modules/llm_interface.py` remains intact.
- Existing test suite compatibility retained.

## [2026-02-14] - Baseline Snapshot

### Added
- `main.py`: Desktop UI using `CustomTkinter` with command entry, execute flow, microphone toggle, background voice loop, arc-reactor animation, alert visuals, and live LLM status display.
- `app/modules/brain_core.py`: Central routing for tactical triggers (`code red`, `stand down`), automation commands, critical-action confirmation (`shutdown`, `restart`), shell command handling, and optional LLM intent routing.
- `app/modules/automation_engine.py`: App alias launching, Windows system controls (shutdown/restart/lock/volume/mute), web search, and shell execution in PowerShell or CMD with timeout handling.
- `app/modules/voice_engine.py`: Asynchronous TTS queue (`pyttsx3`), speech recognition flow with ambient calibration, and microphone reprobe/fallback device resolution.
- `app/modules/llm_interface.py`: Hybrid provider pipeline across Ollama, Groq, and Gemini with retries, health state reporting, adaptive provider selection, and offline fallback behavior.
- Packaging workflow via `build_exe.ps1`, `build_exe.bat`, and `DAVE.spec`, with artifacts in `dist/` and `release/`.

### Changed
- Config structure in `config.json` supports nested `ui`, `brain`, `voice`, `automation`, and `llm` blocks, including provider-level settings and ordering.

### Testing
- Unit tests cover core modules in `tests/test_brain_core.py`, `tests/test_automation_engine.py`, `tests/test_llm_interface.py`, and `tests/test_voice_engine.py`.
- Verification executed on February 14, 2026 using `py -3 -m unittest discover -s tests -p "test_*.py" -v`.
- Result: 32 tests passed.

### Build Metadata
- Release checksum file present: `release/DAVE.sha256.txt`
- Current recorded SHA-256 (`release/DAVE.exe`): `A20BFCD35CFA02CC675E1EED19472C0C9DAC04FCF58E9CFE7CC20BF429F0FBA0`

### Notes
- This entry captures the current implemented state.
- Incremental historical changes before February 14, 2026 were not available in a prior changelog.
