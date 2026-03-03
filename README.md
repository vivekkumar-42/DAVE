# DAVE (Desktop Automation & Virtual Engine) v0.3.0

<p align="center">
  <img src="https://img.shields.io/badge/version-0.3.0-blue?style=flat-square" alt="Version">
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11-green?style=flat-square" alt="Platform">
  <img src="https://img.shields.io/badge/python-3.11+-yellow?style=flat-square" alt="Python">
</p>

DAVE is a Windows desktop assistant built with `CustomTkinter`, voice input/output, local automation, and multi-provider LLM fallback (Ollama -> Groq -> Gemini by default).  
It is designed around a queue-driven UI update model so background work stays responsive.

---

## Table of Contents

1. [Overview](#overview)
2. [Features](#features)
3. [Project Layout](#project-layout)
4. [System Requirements](#system-requirements)
5. [Install and Run](#install-and-run)
6. [Configuration](#configuration)
7. [LLM Provider Behavior](#llm-provider-behavior)
8. [Command Routing and Syntax](#command-routing-and-syntax)
9. [UI Guide](#ui-guide)
10. [Logs, Telemetry, and Runtime Files](#logs-telemetry-and-runtime-files)
11. [Testing](#testing)
12. [Build and Packaging](#build-and-packaging)
13. [Troubleshooting](#troubleshooting)
14. [Contributing](#contributing)
15. [License](#license)

---

## Overview

DAVE combines:

- A modular desktop UI (`app/ui`)
- A core command brain (`app/modules/brain_core.py`)
- Local automation (`app/modules/automation_engine.py`)
- Voice engine (STT/TTS) (`app/modules/voice_engine.py`)
- Multi-provider LLM client with retries, circuit breaker, and offline fallback (`app/modules/llm_interface.py`)
- Long-term command memory manager (`app/modules/memory_manager.py`)
- Predictive action engine (`app/modules/predictive_engine.py`)

Entry point: `main.py`

Important runtime behavior:

- Single-instance lock on Windows via named mutex (`Local\DAVE_IntelligenceSystem_Singleton`)
- Config loaded from first existing path in this order:
  1. `%LOCALAPPDATA%\DAVE\config.json`
  2. `<app_dir>\config.json` (repo root in source mode, EXE folder in frozen mode)
  3. current working directory `config.json`
- Runtime data/logs written to:
  - portable mode: `<app_dir>\data` if writable
  - fallback: `%LOCALAPPDATA%\DAVE\data`

---

## Features

### Core

- Voice capture with ambient calibration and microphone reprobe fallback
- Async text-to-speech queue (`pyttsx3`)
- Local automation for app launch, web search, shell commands, and system controls
- Reusable workflow execution (`run workflow <name>`) for multi-step routines
- LLM orchestration across `ollama`, `groq`, and `gemini`
- Automatic provider health tracking and failover
- Response cache (TTL + bounded size) to reduce repeated LLM/intent latency
- Optional LLM intent-router mode (JSON-based action extraction)
- Level 1 memory bank (SQLite command history + similarity retrieval for dynamic few-shot context)
- Level 2 predictive habits engine (context-aware next-action suggestions using scikit-learn)

### UI

- Multi-panel control console
- Glassmorphism + skeuomorphic styling system (frosted panels, edge highlights, depth shading)
- Animated reactor with state visuals (`NORMAL`, `LISTENING`, `PROCESSING`, `EXECUTING`, `SPEAKING`, `ERROR`) using cached canvas rendering for smoother frame pacing
- Conversation stream with incremental response streaming
- Execution console with log levels, collapse/expand, and batched log rendering
- Live status cards (provider, reliability, thread status, readiness, etc.)
- Command latency and periodic performance reporting

### Safety

- Confirmation flow for destructive actions (`shutdown`, `restart`)
- Guarded shell policy (blocklist + confirm-required patterns + length cap)
- `cancel` support for pending critical actions
- Config secret scrubbing by default (environment variables preferred)
- UI toggles to disable assistant, voice, automation, LLM, and monitoring

---

## Project Layout

```text
DAVE/
|-- main.py
|-- config.json
|-- config.template.json
|-- requirements.txt
|-- run_dave.bat
|-- run_dave.ps1
|-- build_exe.ps1
|-- build_installer.ps1
|-- installer/
|   `-- DAVE.iss
|-- app/
|   |-- runtime_paths.py
|   |-- modules/
|   |   |-- automation_engine.py
|   |   |-- brain_core.py
|   |   |-- llm_interface.py
|   |   |-- memory_manager.py
|   |   |-- predictive_engine.py
|   |   |-- update_checker.py
|   |   |-- workflow_engine.py
|   |   `-- voice_engine.py
|   `-- ui/
|       |-- main_window.py
|       |-- theme.py
|       |-- events.py
|       `-- components/
|           |-- command_bar.py
|           |-- conversation_panel.py
|           |-- core_panel.py
|           |-- reactor_animation.py
|           |-- side_panel.py
|           |-- status_panel.py
|           `-- top_bar.py
`-- tests/
    |-- test_automation_engine.py
    |-- test_brain_core.py
    |-- test_llm_interface.py
    |-- test_main_bootstrap.py
    |-- test_memory_manager.py
    |-- test_predictive_engine.py
    |-- test_ui_theme.py
    |-- test_update_checker.py
    |-- test_voice_engine.py
    |-- test_workflow_engine.py
    |-- smoke_ui.ps1
    |-- smoke_release.ps1
    `-- installer_smoke.ps1
```

---

## System Requirements

| Item | Requirement |
|---|---|
| OS | Windows 10/11 |
| Python | 3.11+ |
| RAM | 4 GB minimum (8 GB recommended) |
| Microphone | Optional (needed for voice input) |
| Internet | Needed for Groq/Gemini providers and update checks |

### Python dependencies

Defined in `requirements.txt`:

- `customtkinter`
- `pillow`
- `requests`
- `groq`
- `openai` (installed, currently not an active DAVE provider)
- `google-genai`
- `SpeechRecognition`
- `pyttsx3`
- `pyaudio`
- `psutil`
- `edge-tts`
- `pygame`
- `pyinstaller`
- `scikit-learn`

---

## Install and Run

### 1. Install dependencies

```powershell
cd DAVE
py -3 -m pip install -r requirements.txt
```

### 2. Configure provider keys (optional but recommended)

```powershell
$env:GROQ_API_KEY = "your-groq-api-key"
$env:GEMINI_API_KEY = "your-gemini-api-key"
# or
$env:GOOGLE_API_KEY = "your-google-api-key"
```

If you use Ollama locally:

```powershell
ollama serve
```

### 3. Start DAVE

```powershell
py -3 main.py
```

or:

```powershell
.\run_dave.bat
```

### 4. Self-check (no UI loop)

```powershell
py -3 main.py --self-check
```

For release build:

```powershell
.\release\DAVE\DAVE.exe --self-check
```

### 5. Timed UI run (auto-close)

Useful for manual/UI smoke validation without leaving the app window open:

```powershell
py -3 main.py --auto-exit-seconds=5
```

You can also set `DAVE_AUTO_EXIT_SECONDS` as an environment variable.

---

## Configuration

Primary template: `config.template.json`  
Working local config: `config.json`

### Security model for secrets

At startup, `main.py` sanitizes inline secrets from config by default:

- top-level: `groq_api_key`, `gemini_api_key`, `openai_api_key`
- nested: `llm.groq.api_key`, `llm.gemini.api_key`, `llm.openai.api_key`

To allow secrets in config for local debugging only:

```json
{
  "llm": {
    "allow_config_secrets": true
  }
}
```

Environment variables are the recommended source of truth.

### Top-level config blocks

| Block | Purpose |
|---|---|
| `ui` | Window size, theme colors, state visuals, performance profiler |
| `app` | App version metadata |
| `brain` | Verbose mode for system stats suffix |
| `llm` | Provider order, retries, timeouts, model settings |
| `update` | Startup update-check behavior |
| `voice` | STT/TTS tuning and microphone settings |
| `automation` | Shell command allow/timeout/output limits |
| `policy` | Shell risk policy and confirmation guardrails |
| `workflows` | Named multi-step workflow definitions and execution behavior |
| `memory` | Level 1 command-history memory bank behavior |
| `predictive` | Level 2 predictive action engine settings |

### `ui` block

Key fields (defaults from `config.template.json`):

- `appearance_mode`: `"Dark"`
- `window_width`: `1540`
- `window_height`: `960`
- `min_width`: `1320`
- `min_height`: `820`
- `performance_profiler.enabled`: `true`
- `performance_profiler.report_interval_seconds`: `5`
- `performance_profiler.history_size`: `60`
- `performance_profiler.persist_to_file`: `true`
- `performance_profiler.file_name`: `"dave_perf_metrics.jsonl"`
- `performance_profiler.max_file_size_kb`: `1024`
- `visual_system.*`: color palette (including glass/skeuo keys such as `glass_tint`, `glass_edge`, `glass_shadow`, `skeuo_highlight`, `skeuo_shadow`)
- `state_visuals.NORMAL|LISTENING|PROCESSING|EXECUTING|SPEAKING|ERROR`: per-state accent/glow/speed

### `brain` block

- `verbose_mode` (`true` default): appends battery, RAM, and current state to responses.

### `llm` block

Global behavior:

- `enabled`: enable/disable LLM calls entirely
- `provider_order`: e.g. `["ollama", "groq", "gemini"]`
- `prefer_local`: if `true`, `ollama` is moved to front
- `timeout_seconds`: shared timeout baseline
- `provider_retries`: retries per provider (`retries + 1` attempts total)
- `retry_backoff_seconds`: exponential backoff base delay
- `retry_jitter_ratio`: randomized jitter ratio applied to retry delays
- `circuit_breaker_enabled`: skip unstable providers during cooldown
- `circuit_breaker_failure_threshold`: failures to trip breaker
- `circuit_breaker_cooldown_seconds`: breaker cooldown duration
- `dynamic_provider_selection`: rank providers by recent reliability/latency
- `provider_sample_threshold`: minimum sample count before reordering
- `response_cache_enabled`: enable/disable in-process response cache
- `response_cache_ttl_seconds`: cache lifetime in seconds
- `response_cache_max_entries`: maximum cached entries before LRU eviction
- `temperature`, `max_tokens`, `history_turns`
- `intent_routing_enabled`: enables structured intent routing (default `true`)
- `intent_min_confidence`: minimum accepted router confidence
- `system_prompt`: assistant system prompt
- `bootstrap_examples` + `bootstrap_example_count`: prepended behavior examples

Provider-specific fields:

- `llm.groq.enabled`, `llm.groq.model`
- `llm.ollama.enabled`, `llm.ollama.url`, `llm.ollama.model`, `llm.ollama.timeout_seconds`
- `llm.gemini.enabled`, `llm.gemini.model`, `llm.gemini.timeout_seconds`

### `voice` block

- `tts_backend`: currently `pyttsx3` only
- `tts_rate`, `tts_volume`, `tts_voice_id`
- `microphone_device_index` (`null` for auto-detect)
- `pause_threshold`
- `ambient_adjust_seconds`
- `mic_reprobe_interval_seconds`
- `listen_timeout`
- `phrase_time_limit`

### `automation` block

- `allow_shell_commands`: master switch for shell execution
- `shell_timeout_seconds`
- `shell_output_limit`

### `policy` block

- `enabled`: enable/disable shell safety policy
- `pending_confirmation_timeout_seconds`: expiry for pending confirmations
- `shell_max_command_length`: hard ceiling for shell command length
- `blocked_shell_patterns`: regex list that blocks dangerous shell commands
- `confirm_shell_patterns`: regex list that requires confirmation before execution

### `workflows` block

- `enabled`: enable/disable workflow commands
- `max_steps`: maximum steps allowed per workflow definition
- `max_depth`: nested workflow recursion limit
- `stop_on_error`: stop workflow execution on first failed step when `true`
- `definitions`: dictionary of workflow names to steps (array or object with `description` + `steps`)

### `memory` block

- `enabled`: enable/disable persistence and retrieval of successful command history
- `top_k`: number of similar successful commands injected into LLM context
- `min_similarity`: minimum similarity threshold (`difflib.SequenceMatcher`) for retrieval
- `scan_rows`: max recent successful rows scanned for similarity ranking

### `predictive` block

- `enabled`: enable/disable predictive action engine
- `confidence_threshold`: minimum probability needed to emit a suggestion
- `idle_poll_seconds`: how often idle inference runs
- `suggestion_cooldown_seconds`: minimum interval between suggestions
- `train_interval_seconds`: background retraining interval (default 86400)
- `min_training_samples`: minimum habit samples required before training

### `update` block

- `enabled`
- `channel` (default `stable`)
- `current_version`
- `manifest_url`
- `check_on_startup`
- `request_timeout_seconds`

---

## LLM Provider Behavior

Provider path is attempted in sequence (possibly reordered by reliability if enabled).  
For each provider:

1. optional retries with exponential backoff + jitter
2. error capture + classification
3. metrics update (success/failure/latency)
4. circuit-breaker update (`closed -> open -> half-open`)

When cooldown expires, only one half-open probe request is allowed through.  
If the probe fails, the breaker re-opens immediately.

If all providers fail, DAVE returns an offline fallback message and stays usable for local automation commands.

### Environment variables used

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | Groq auth |
| `GROQ_MODEL` | Override configured Groq model |
| `GROQ_FALLBACK_MODEL` | Optional fallback model when configured Groq model is unavailable |
| `GEMINI_API_KEY` | Gemini auth |
| `GOOGLE_API_KEY` | Alternate Gemini auth |
| `GEMINI_MODEL` | Override configured Gemini model |

### Groq decommission fallback

If Groq returns `model_decommissioned` or `model_not_found`, DAVE automatically retries with fallback models:

1. `GROQ_FALLBACK_MODEL` (if set)
2. `llama-3.1-8b-instant`
3. `llama-3.3-70b-versatile`

Default Groq model in config is `llama-3.1-8b-instant`.

---

## Command Routing and Syntax

`Brain` first evaluates workflow commands, then deterministic local automation syntax (`open`, `search for`, `run`, `volume`, etc.) for immediate execution.  
If no explicit automation pattern matches, it tries LLM intent routing.  
If LLM providers are offline, regex automation fallback still handles direct local command forms.

### Supported command forms

#### App launch

- `open calculator`
- `launch notepad`
- `start chrome`
- File fallback by name/path (if app launch fails):
  - `open "BIBEK_KUMAR_YADAV_CV (2)"`
  - `open my cv file naming "BIBEK_KUMAR_YADAV_CV (2)"`
  - `open C:\Users\<you>\Documents\resume.pdf`

Known aliases include: calculator, notepad, paint, cmd, powershell, terminal, task manager, explorer, settings, snipping tool, word, excel, chrome, edge, firefox, spotify, vscode, discord.

#### Web search

- `search for python threading tutorial`
- Chained pattern: `open chrome and search for cars`

#### Shell execution

- `run powershell Get-Date`
- `run cmd dir`
- `execute powershell Get-Process`
- `powershell Get-Service`
- `cmd ipconfig`
- `! Get-ChildItem`

Default shell mode is PowerShell unless explicitly set to `cmd`.
High-risk shell commands may require confirmation based on `policy.confirm_shell_patterns`.

#### Workflows

- `list workflows`
- `show workflows`
- `run workflow startup`
- `workflow startup`

#### System controls

- `volume up`
- `volume up 5` (repeat count clamped 1..10)
- `volume down`
- `mute`
- `lock`
- `shutdown` (requires confirmation)
- `restart` / `reboot` (requires confirmation)

#### Tactical state

- `code red` or `danger` -> `TACTICAL`
- `stand down` or `relax` -> `NORMAL`

### Confirmation flows

`shutdown` and `restart` require a second confirmation phrase, e.g.:

- `confirm shutdown`
- `confirm restart`
- `cancel`

Pending critical action expires after 30 seconds.
Guarded shell commands also use `confirm`/`cancel` when policy requires approval.

---

## UI Guide

The app UI has 4 major zones:

1. **Top Bar**
   - identity label (`DAVE`)
   - system state pill
   - live indicators: mic, provider, latency, activity
   - low-overhead glow/indicator updates when animations are paused

2. **Left Control Matrix**
   - Assistant Control
   - Voice Engine
   - Automation Engine
   - LLM Providers
   - System Monitoring
   - Settings (diagnostics mode; also expands console output)

3. **Center Core**
   - Reactor animation (glass/skeuo depth styling + cached canvas item updates)
   - Conversation stream (incremental streaming with lightweight fade and capped parallel transitions)
   - Execution console (collapsible + buffered log flush to reduce UI stutter)

4. **Right Status Panel**
   - Voice Engine
   - LLM Provider
   - Provider Reliability
   - Automation Engine
   - Thread Status
   - Execution State
   - System Readiness

Predictive suggestions:

- During idle `NORMAL` state, the predictive engine may emit suggestions.
- These appear in conversation/log stream as `Predictive suggestion: <intent>`.

Bottom command bar supports:

- Execute button
- Mic toggle
- Interrupt
- Clear streams
- Up/Down history navigation in input field

---

## Logs, Telemetry, and Runtime Files

### Main log

- File: `dave_system.log`
- Location: runtime data directory (`<app_dir>\data` or `%LOCALAPPDATA%\DAVE\data`)

### Performance snapshots

- File name from config: `ui.performance_profiler.file_name` (default `dave_perf_metrics.jsonl`)
- JSONL snapshots include queue, drain-loop, and command latency metrics
- File auto-truncates when exceeding configured max size

### ML/Memory state

- `data\dave_memory.db`: Level 1 successful-command memory bank
- `data\dave_habits.db`: Level 2 habit feature/label dataset
- `data\dave_habits_model.pkl`: serialized predictive model

### Update checks

If `update.enabled=true`, `update.check_on_startup=true`, and `update.manifest_url` is set, DAVE performs a startup manifest check and reports results in logs/conversation.

---

## Testing

### Full unit test suite

```powershell
py -3 -m unittest discover -s tests -p "test_*.py" -v
```

### Individual modules

```powershell
py -3 -m unittest tests.test_brain_core -v
py -3 -m unittest tests.test_automation_engine -v
py -3 -m unittest tests.test_llm_interface -v
py -3 -m unittest tests.test_memory_manager -v
py -3 -m unittest tests.test_predictive_engine -v
py -3 -m unittest tests.test_ui_theme -v
py -3 -m unittest tests.test_voice_engine -v
py -3 -m unittest tests.test_main_bootstrap -v
py -3 -m unittest tests.test_update_checker -v
```

### Release smoke test

```powershell
.\tests\smoke_release.ps1
```

### UI smoke test (auto-close + cleanup)

```powershell
.\tests\smoke_ui.ps1
```

### Installer smoke test

```powershell
.\tests\installer_smoke.ps1
```

---

## Build and Packaging

### Build release executable

Default (`onedir`, faster startup):

```powershell
.\build_exe.ps1
```

Single-file (`onefile`, slower startup):

```powershell
.\build_exe.ps1 -OneFile
```

Batch wrappers:

```powershell
.\build_exe.bat
.\build_installer.bat
```

Build outputs:

- `release\DAVE\DAVE.exe` (default onedir build)
- `release\DAVE.exe` (onefile build)
- `release\DAVE.sha256.txt`
- `release\channel-stable.json`

Build script also copies these files next to the built artifact:

- `config.json`
- `config.template.json`
- `README.md`

### Build installer

Requires Inno Setup (`iscc.exe`) available on PATH or `ISCC_PATH` env var.

```powershell
.\build_installer.ps1
```

Installer script: `installer\DAVE.iss`  
Output: `release\DAVE-Setup-<version>.exe`

The installer intentionally excludes runtime-generated `data\*` from packaged files.

### Optional code signing

If `DAVE_SIGN_CERT_PFX` is set, build scripts attempt to sign EXE/installer.

Supported env vars:

- `DAVE_SIGN_CERT_PFX`
- `DAVE_SIGN_CERT_PASS`
- `DAVE_SIGNTOOL_PATH`
- `DAVE_SIGN_TIMESTAMP_URL`

---

## Troubleshooting

### LLM is always offline

Check:

1. Ollama running and reachable at `llm.ollama.url`
2. `GROQ_API_KEY` / `GEMINI_API_KEY` set
3. Groq model not decommissioned (or set `GROQ_MODEL=llama-3.1-8b-instant`)
4. Firewall/proxy settings for outbound HTTPS

### `400 Bad Request` from Groq

Likely model mismatch or decommission. Use:

```powershell
$env:GROQ_MODEL = "llama-3.1-8b-instant"
```

### Microphone issues

- Set explicit `voice.microphone_device_index`
- Increase `voice.listen_timeout` / `voice.phrase_time_limit`
- Verify Windows microphone permission for Python/EXE

### Shell command blocked

Ensure `automation.allow_shell_commands=true` and UI toggle "Automation Engine" is enabled.

### No predictive suggestions appear

Check:

1. `predictive.enabled=true` in active config
2. `scikit-learn` is installed in the same Python environment used to run DAVE
3. Habit dataset is large enough (`predictive.min_training_samples`)
4. Confidence threshold is not too strict (`predictive.confidence_threshold`)

### Config edits not applying

Remember config load precedence:

1. `%LOCALAPPDATA%\DAVE\config.json`
2. app directory `config.json`
3. current working directory `config.json`

---

## Contributing

Suggested workflow:

1. Create a branch
2. Make focused changes
3. Add or update tests
4. Run full test suite
5. Open PR with clear reproduction and validation notes

---

## License

MIT License

Copyright (c) 2026 DAVE Team

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
