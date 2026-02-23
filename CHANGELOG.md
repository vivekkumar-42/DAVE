# Changelog

All notable changes to DAVE are documented in this file.

This repository did not have a prior changelog, so this first entry is a baseline snapshot of the current state as of February 14, 2026.

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
