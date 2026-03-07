# Playwright CLI — Agent Zero Browser Plugin

![Version](https://img.shields.io/badge/version-1.0.0-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![Agent Zero](https://img.shields.io/badge/Agent%20Zero-plugin-orange)

Microsoft Playwright CLI browser automation plugin for [Agent Zero](https://github.com/frdel/agent-zero). Gives every agent a `browser_agent` tool to navigate, interact with, and extract data from any website using structured DOM snapshots with stable element references.

---

## Features

- 🎭 **Playwright CLI backend** — structured YAML DOM snapshots with stable element refs (`e1`, `e2`, ...)
- 🤖 **Uses Agent Zero Browser Model** — no separate LLM config needed, inherits your Settings → Agent → Browser Model
- 🔧 **Auto-skill injection** — the full Playwright CLI skill is injected into the agent system prompt automatically
- 📋 **14 browser actions** — goto, click, fill, type, press, select, check, hover, go-back, reload, tab management, screenshots, and more
- 🔒 **Security validated** — URL allowlist (http/https only), element ref pattern validation
- 📱 **Mobile/device emulation** — emulate any device viewport
- 🕸️ **Network mocking** — intercept and mock HTTP requests
- 🎬 **DevTools tracing & video** — record sessions for debugging
- 🚀 **One-click initialization** — installs playwright-cli and Chromium automatically

---

## Installation

### 1. Copy the plugin

```bash
cp -r playwright_cli /path/to/agent-zero/usr/plugins/
```

### 2. Enable in Agent Zero

Go to **Settings → Plugins → Playwright CLI** and toggle it on.

### 3. Initialize (automatic)

Click the **Initialize** button on the plugin page. This will:
- Install `playwright-cli` via npm (`npm install -g @playwright/cli@latest`)
- Install Chromium binaries (`playwright-cli install`)
- Write `~/.playwright/cli.config.json` pointing to the discovered Chromium binary

### Manual install (fallback)

If initialization fails:
```bash
npm install -g @playwright/cli@latest
playwright-cli install
```

---

## Configuration

This plugin **inherits the Browser Model** from Agent Zero's built-in settings.

Go to **Settings → Agent → Browser Model** to configure:

| Setting | Description |
|---------|--------------|
| Provider | LLM provider for browser decisions (e.g. openrouter, openai) |
| Model | Model name (e.g. anthropic/claude-sonnet-4-5) |
| Vision | Enable vision for screenshot-based decisions |
| Rate limits | Optional request/token rate limiting |

> No plugin-specific config page needed — all browser model settings live in the standard Agent Zero settings.

---

## How It Works

```
Parent Agent
    │
    │  browser_agent tool call
    ▼
BrowserAgent (tools/browser_agent.py)
    │
    │  start_task(message)
    ▼
PlaywrightCliBackend (helpers/playwright_cli_backend.py)
    │
    ├─ open browser session via playwright-cli
    │
    └─ LOOP (up to 50 steps):
         │
         ├─ snapshot → YAML DOM with element refs (e1, e2, ...)
         │
         ├─ LLM decision (Browser Model)
         │    SystemMessage: browser_agent.system.md (action protocol)
         │    HumanMessage:  task + snapshot + action history
         │
         ├─ execute action (goto/click/fill/press/...)
         │
         └─ done? → return result to parent agent
```

---

## Available Actions

| Action | Description |
|--------|-------------|
| `goto` | Navigate to URL (http/https only) |
| `click` | Click element by ref |
| `dblclick` | Double-click element |
| `fill` | Clear and fill input field |
| `type` | Type text at cursor |
| `press` | Press keyboard key (Enter, Tab, ArrowDown...) |
| `select` | Select dropdown option |
| `check` | Check checkbox |
| `uncheck` | Uncheck checkbox |
| `hover` | Hover over element |
| `go-back` | Navigate back |
| `go-forward` | Navigate forward |
| `reload` | Reload page |
| `snapshot` | Force fresh DOM snapshot |
| `screenshot` | Take screenshot |
| `tab-new` | Open new tab |
| `tab-close` | Close current tab |
| `done` | Task complete — return result |

---

## Usage

The `browser_agent` tool is available to all agents when the plugin is enabled:

```json
{
  "tool_name": "browser_agent",
  "tool_args": {
    "message": "Go to https://example.com and return the page title",
    "reset": "true"
  }
}
```

```json
{
  "tool_name": "browser_agent",
  "tool_args": {
    "message": "Considering open pages, click the Submit button and confirm the result. End task.",
    "reset": "false"
  }
}
```

- `reset: true` — spawn a fresh browser session
- `reset: false` — continue the existing session (start message with "Considering open pages...")

---

## Plugin Structure

```
playwright_cli/
├── plugin.yaml                          # Plugin manifest (v1.0.0)
├── initialize.py                        # Auto-installer for playwright-cli + Chromium
├── default_config.yaml                  # Minimal config (inherits A0 browser model)
├── tools/
│   └── browser_agent.py                 # browser_agent tool
├── helpers/
│   ├── playwright_cli_backend.py        # Core agentic browser loop
│   └── playwright.py                    # Chromium binary discovery
├── extensions/
│   └── python/
│       ├── agent_init/
│       │   └── _20_browser_plugin_config.py   # Plugin init hook
│       └── system_prompt/
│           └── _16_playwright_cli_skill_prompt.py  # Skill auto-injection
├── prompts/
│   ├── browser_agent.system.md          # Internal browser LLM instructions
│   └── agent.system.tool.browser.md    # Parent agent tool description
├── webui/
│   └── config.html                      # Settings info card
└── skills/
    └── playwright-cli/                  # Bundled Playwright CLI skill
        ├── SKILL.md
        └── references/
```

---

## Requirements

- **Node.js** (for `npm install -g @playwright/cli`)
- **Agent Zero** with plugin support
- Browser model configured in Agent Zero Settings (any LLM provider)

---

## License

MIT
