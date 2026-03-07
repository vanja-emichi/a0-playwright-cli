"""PlaywrightCliBackend — Microsoft Playwright CLI browser automation backend.

Alternative to browser_use for structured DOM snapshots, element refs (e1/e2...),
mobile emulation, network mocking, and DevTools tracing.

Reuses existing Playwright browser binaries from the ms-playwright cache — zero additional downloads.
Binary path: 3x dirname from ensure_playwright_binary() = PLAYWRIGHT_BROWSERS_PATH.

Session ID: f"a0-{context_id_hex[:16]}" — 16 hex chars, negligible collision probability.

Interface contract (must match State class API used by BrowserAgent.execute()):
  start_task(task) -> PlaywrightCliTask   (with .is_ready(), .is_alive(), .result(), .kill(), .execute_inside())
  kill_task()                              (sync, cancels asyncio task + closes CLI session)
  use_agent = None                         (prevents AttributeError at execute() line 309)
  task: PlaywrightCliTask | None           (for execute_inside + is_ready checks in get_update)
  async get_page() -> None                 (stub — playwright_cli has no page object)
  async get_selector_map() -> dict         (stub — not used by playwright_cli path)

LLM decision format:
  {"action": "goto|click|fill|type|snapshot|done",
   "ref": "e1|e2|...",
   "value": "<url or text or final answer>",
   "reasoning": "<why>",
   "done": false}

Snapshot: saved to /tmp/pw-snap-<session_id>.yml via --filename flag, parsed with pyyaml.
Elements truncated to top SNAPSHOT_MAX_ELEMENTS before serialization (dict-level, not string-slice).
Total snapshot JSON capped at SNAPSHOT_MAX_BYTES to prevent LLM context overflow.

Security:
  - goto: only http:// and https:// URLs accepted (blocks file://, javascript:, chrome:// etc.)
  - click/fill ref: must match ^e\\d+$ pattern (blocks flag injection via --arg style refs)
  - Task string embedded in prompt — inherent prompt injection risk acknowledged.
    Parent agent secrets are masked before reaching this class. No additional boundary
    enforcement can be guaranteed; operators should restrict task content to trusted inputs.
"""
import asyncio
import json
import os
import re
import sys
import tempfile
import logging
from typing import Optional

log = logging.getLogger(__name__)

# Absolute imports via importlib — file loaded via importlib, relative imports forbidden
_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import importlib.util as _ilu

# Compiled patterns (module-level, not per-call)
_REF_PATTERN = re.compile(r'^e\d+$')
_URL_ALLOWED_SCHEMES = ('http://', 'https://')


def _load_module(name: str, relpath: str):
    """Load module by absolute path. Checks sys.modules first to prevent duplicate instances."""
    if name in sys.modules:
        return sys.modules[name]
    spec = _ilu.spec_from_file_location(name, os.path.join(_PLUGIN_ROOT, relpath))
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod  # Register before exec_module to prevent recursive double-load
    spec.loader.exec_module(mod)
    return mod




# ---------------------------------------------------------------------------
# Result wrapper — implements the interface BrowserAgent.execute() expects
# from DeferredTask.result() return value
# ---------------------------------------------------------------------------

class PlaywrightCliResult:
    """Result object returned by PlaywrightCliTask.result().
    Implements the interface expected by BrowserAgent.execute() after the wait loop.
    """

    def __init__(self, result_text: str):
        self._result_text = result_text or ""

    def is_done(self) -> bool:
        return True

    def final_result(self) -> str:
        return self._result_text

    def urls(self) -> list:
        return []


# ---------------------------------------------------------------------------
# Task wrapper — implements the interface BrowserAgent.execute() expects
# from State.start_task() return value (DeferredTask-compatible)
# ---------------------------------------------------------------------------

class PlaywrightCliTask:
    """Wraps an asyncio.Task and exposes the DeferredTask-compatible API
    that BrowserAgent.execute() uses to poll progress and retrieve results.

    Required by BrowserAgent.execute():
      .is_ready()           → True when task is done (loop exit condition)
      .is_alive()           → True while task is running
      .result()             → awaitable returning PlaywrightCliResult
      .kill()               → cancel the task
      .execute_inside(fn)   → run coroutine fn in current async context
    Also accessed via state.task:
      state.task.is_ready()          → at execute() line ~389
      state.task.execute_inside(fn)  → at execute() line ~390
    """

    def __init__(self, async_task: asyncio.Task, backend: "PlaywrightCliBackend"):
        self._async_task = async_task
        self._backend = backend

    def is_ready(self) -> bool:
        """True when asyncio task has completed (success, failure, or cancelled)."""
        return self._async_task.done()

    def is_alive(self) -> bool:
        """True while asyncio task is still running."""
        return not self._async_task.done()

    async def result(self) -> PlaywrightCliResult:
        """Await completion and return result. Safe to call after is_ready() is True."""
        if not self._async_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._async_task), timeout=30)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
        return PlaywrightCliResult(self._backend._result or "")

    def kill(self, terminate_thread: bool = False) -> None:
        """Cancel the asyncio task."""
        if not self._async_task.done():
            self._async_task.cancel()

    async def execute_inside(self, coro_fn) -> None:
        """Execute a coroutine inside this task's context.
        For PlaywrightCliBackend: run directly in current async context.
        No-op if task is already done.
        """
        if not self._async_task.done():
            try:
                await coro_fn()
            except Exception as e:
                log.debug("PlaywrightCliTask.execute_inside: %s", e)


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

class PlaywrightCliBackend:
    """Browser automation backend using Microsoft Playwright CLI.

    Implements the State-compatible interface expected by BrowserAgent:
      start_task(task) -> PlaywrightCliTask
      kill_task()
      use_agent = None
      task: PlaywrightCliTask | None
      async get_page() -> None
      async get_selector_map() -> dict
    """

    MAX_STEPS = 50
    SNAPSHOT_MAX_ELEMENTS = 50  # Truncate at dict level, never slice JSON string
    SNAPSHOT_MAX_BYTES = 16000  # Cap total snapshot JSON to prevent LLM context overflow
    TASK_TIMEOUT = 300  # seconds

    def __init__(self, agent):
        self.agent = agent
        # State interface compatibility attributes
        self.use_agent = None  # Prevents AttributeError at execute() line 309
        self.task: Optional[PlaywrightCliTask] = None  # Accessed at execute() line 389
        # Internal state
        self._async_task: Optional[asyncio.Task] = None
        self._result: Optional[str] = None

    # ── State interface stubs ─────────────────────────────────────────────────

    async def get_page(self):
        """Stub — PlaywrightCliBackend has no page object (CLI manages browser state).
        Returns None, causing get_update() to skip the screenshot/log branch.
        """
        return None

    async def get_selector_map(self) -> dict:
        """Stub — not used by PlaywrightCliBackend path."""
        return {}

    # ── Session helpers ──────────────────────────────────────────────────────

    def get_session_id(self) -> str:
        """Session ID for playwright-cli -s flag.
        Uses 16 hex chars (vs 8 in original) to reduce collision probability
        in concurrent multi-agent deployments.
        """
        raw = self.agent.context.id.replace('-', '')
        return f"a0-{raw[:16]}"

    def get_browsers_path(self) -> str:
        """Return PLAYWRIGHT_BROWSERS_PATH by traversing 3x dirname from the binary.

        binary:          ~/.cache/ms-playwright/chromium-1148/chrome-linux/chrome
        dirname x1:      ~/.cache/ms-playwright/chromium-1148/chrome-linux
        dirname x2:      ~/.cache/ms-playwright/chromium-1148
        dirname x3:      ~/.cache/ms-playwright   ← correct PLAYWRIGHT_BROWSERS_PATH

        2x dirname is WRONG — gives the version-specific dir, causing
        'Executable doesn't exist' errors in playwright-cli.
        """
        pw_helper = _load_module("playwright_helper", "helpers/playwright.py")
        binary_path = pw_helper.ensure_playwright_binary()
        return os.path.dirname(os.path.dirname(os.path.dirname(binary_path)))

    def _make_env(self) -> dict:
        """Build subprocess environment with PLAYWRIGHT_BROWSERS_PATH set."""
        return {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": self.get_browsers_path()}

    @staticmethod
    def validate_binary() -> bool:
        """Check that playwright-cli binary is available. Returns True if found."""
        import shutil
        return shutil.which("playwright-cli") is not None

    # ── CLI runner ────────────────────────────────────────────────────────────

    async def _run_cmd(self, args: list) -> str:
        """Run playwright-cli command, return stdout.
        Raises RuntimeError on non-zero exit. stderr captured up to 2000 chars.
        """
        cmd = ["playwright-cli"] + args
        log.debug("PlaywrightCliBackend: %s", " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._make_env(),
            cwd=os.path.expanduser("~"),  # playwright-cli reads .playwright/cli.config.json from CWD
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            # Capture 2000 chars of stderr — actionable detail is often at end
            stderr_text = stderr.decode(errors='replace')
            err_excerpt = stderr_text[-2000:] if len(stderr_text) > 2000 else stderr_text
            raise RuntimeError(
                f"playwright-cli error (exit {proc.returncode}): {err_excerpt}"
            )
        return stdout.decode()

    # ── Public lifecycle API ──────────────────────────────────────────────────

    def start_task(self, task: str) -> PlaywrightCliTask:
        """Schedule _run_task as an asyncio task. Must be called from async context.
        Returns PlaywrightCliTask (DeferredTask-compatible wrapper).
        """
        # Pre-flight check: binary must exist
        if not self.validate_binary():
            raise RuntimeError(
                "playwright-cli binary not found on PATH. "
                "Install with: npm install -g @playwright/cli@latest"
            )
        loop = asyncio.get_running_loop()
        self._async_task = loop.create_task(self._run_task(task))
        self.task = PlaywrightCliTask(self._async_task, self)
        return self.task

    def kill_task(self) -> None:
        """Cancel running asyncio task and close CLI session (non-blocking)."""
        if self._async_task and not self._async_task.done():
            self._async_task.cancel()
        if self.task:
            self.task.kill()
        # Close browser session — use to_thread to avoid blocking event loop
        sid = self.get_session_id()
        env = self._make_env()
        import subprocess
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                asyncio.to_thread(
                    subprocess.run,
                    ["playwright-cli", f"-s={sid}", "close"],
                    env=env,
                    capture_output=True,
                    timeout=10,
                )
            )
        except RuntimeError:
            # No running loop (e.g. called from __del__) — run synchronously
            try:
                subprocess.run(
                    ["playwright-cli", f"-s={sid}", "close"],
                    env=env,
                    capture_output=True,
                    timeout=10,
                )
            except Exception:
                pass

    async def get_response(self) -> str:
        """Await task completion and return result string.
        Note: BrowserAgent.execute() uses the PlaywrightCliTask.result() path instead.
        This method is retained as a convenience for direct usage.
        """
        if self._async_task is None:
            return "Error: PlaywrightCliBackend task was never started."
        try:
            await asyncio.wait_for(self._async_task, timeout=self.TASK_TIMEOUT)
        except asyncio.TimeoutError:
            self.kill_task()
            return f"Error: Playwright CLI task timed out after {self.TASK_TIMEOUT} seconds."
        except asyncio.CancelledError:
            return "Error: Playwright CLI task was cancelled."
        except Exception as e:
            return f"Error: Playwright CLI task failed: {e}"
        return self._result or "Task completed with no result."

    # ── Snapshot helpers ──────────────────────────────────────────────────────

    async def _get_snapshot(self, sid: str) -> dict:
        """Take a structured snapshot, save to /tmp/pw-snap-<sid>.yml, parse YAML."""
        snap_path = os.path.join(tempfile.gettempdir(), f"pw-snap-{sid}.yml")
        try:
            await self._run_cmd([f"-s={sid}", "snapshot", f"--filename={snap_path}"])
        except RuntimeError as e:
            log.warning("PlaywrightCliBackend: snapshot command failed: %s", e)
            return {}
        if not os.path.exists(snap_path):
            log.warning("PlaywrightCliBackend: snapshot file not created at %s", snap_path)
            return {}
        try:
            import yaml
            with open(snap_path) as f:
                data = yaml.safe_load(f) or {}
            os.unlink(snap_path)  # clean up temp file
            return data
        except ImportError:
            # pyyaml not available — return raw text for LLM to interpret
            with open(snap_path) as f:
                raw = f.read()
            os.unlink(snap_path)
            return {"raw_snapshot": raw[:4000]}  # cap raw text
        except Exception as e:
            log.warning("PlaywrightCliBackend: snapshot parse error: %s", e)
            try:
                os.unlink(snap_path)
            except Exception:
                pass
            return {}

    def _truncate_snapshot(self, snapshot: dict) -> dict:
        """Limit elements to SNAPSHOT_MAX_ELEMENTS at dict level before serialization.
        Prevents invalid JSON from string-slicing and keeps prompts token-efficient.
        """
        result = dict(snapshot)
        # Try common element list keys from playwright-cli snapshot format
        for key in ("elements", "nodes", "items"):
            elements = result.get(key)
            if isinstance(elements, list) and len(elements) > self.SNAPSHOT_MAX_ELEMENTS:
                result[key] = elements[: self.SNAPSHOT_MAX_ELEMENTS]
                result["_truncated"] = (
                    f"{len(elements) - self.SNAPSHOT_MAX_ELEMENTS} elements omitted"
                )
                break
        return result

    # ── Main execution loop ───────────────────────────────────────────────────

    async def _run_task(self, task: str) -> None:
        """Core agentic loop: snapshot → LLM decision → action → repeat."""
        sid = self.get_session_id()
        history: list = []

        # Open browser session — `open` initializes the session (required before any other command)
        # If task contains a URL, open directly to it; otherwise open a blank session
        url_match = re.search(r"https?://\S+", task)
        try:
            if url_match:
                await self._run_cmd([f"-s={sid}", "open", url_match.group(0)])
            else:
                await self._run_cmd([f"-s={sid}", "open", "about:blank"])
        except RuntimeError as e:
            self._result = f"Error opening browser session: {e}"
            return

        for step in range(self.MAX_STEPS):
            # Get structured snapshot with element refs (e1, e2, ...)
            snapshot = await self._get_snapshot(sid)
            truncated = self._truncate_snapshot(snapshot)

            # Build LLM prompt with snapshot + history
            prompt = self._build_prompt(task, truncated, history)

            # Call browser LLM — SystemMessage carries browser_agent.system.md instructions;
            # HumanMessage carries situational context (task + snapshot + history)
            try:
                from langchain_core.messages import HumanMessage, SystemMessage
                llm = self.agent.llm
                system_text = self._load_system_prompt()
                messages = []
                if system_text:
                    messages.append(SystemMessage(content=system_text))
                messages.append(HumanMessage(content=prompt))
                response = await llm.ainvoke(messages)
                decision = self._parse_decision(response.content)
            except Exception as e:
                log.warning("PlaywrightCliBackend: LLM call failed at step %d: %s", step, e)
                self._result = f"LLM error at step {step}: {e}"
                return

            history.append(decision)
            log.debug(
                "PlaywrightCliBackend step %d: action=%s ref=%s",
                step,
                decision.get("action"),
                decision.get("ref", ""),
            )

            # Check completion
            if decision.get("done") or decision.get("action") == "done":
                self._result = f"Task complete.\n{decision.get('value', '')}"
                break

            # Execute action
            try:
                await self._execute_action(sid, decision)
            except RuntimeError as e:
                log.warning("PlaywrightCliBackend: action failed at step %d: %s", step, e)
                # Don't abort — let LLM adapt on next snapshot
                history[-1]["_error"] = str(e)
        else:
            self._result = "Max steps reached without completing task."

        # Clean up session
        try:
            await self._run_cmd([f"-s={sid}", "close"])
        except Exception:
            pass  # Best-effort close

    # ── Decision parsing ──────────────────────────────────────────────────────

    def _parse_decision(self, content: str) -> dict:
        """Parse LLM JSON response. Falls back to regex extraction, then done."""
        # Try direct JSON parse
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        # Try extracting JSON object from prose
        match = re.search(r"\{[^{}]*\}", content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
        # Fallback: treat entire response as final answer
        return {"action": "done", "value": content, "done": True}

    # ── Action executor ───────────────────────────────────────────────────────

    async def _execute_action(self, sid: str, decision: dict) -> None:
        """Dispatch action to playwright-cli.

        Security:
        - goto: URL scheme allowlist (http/https only) prevents file://, javascript:, etc.
        - click/fill ref: must match ^e\\d+$ pattern to prevent flag injection (--arg style).
        Raises RuntimeError on CLI failure.
        """
        action = decision.get("action", "")
        ref = decision.get("ref", "")
        value = decision.get("value", "")

        if action == "goto":
            # URL scheme validation — only allow http/https
            if not any(str(value).startswith(s) for s in _URL_ALLOWED_SCHEMES):
                log.warning(
                    "PlaywrightCliBackend: goto rejected non-http URL: %s",
                    str(value)[:100],
                )
                return
            await self._run_cmd([f"-s={sid}", "goto", value])

        elif action == "click":
            # Ref must match e\d+ to prevent flag injection
            if not ref or not _REF_PATTERN.match(str(ref)):
                log.warning("PlaywrightCliBackend: click rejected invalid ref '%s'", ref)
                return
            await self._run_cmd([f"-s={sid}", "click", ref])

        elif action == "fill":
            if not ref or not _REF_PATTERN.match(str(ref)):
                log.warning("PlaywrightCliBackend: fill rejected invalid ref '%s'", ref)
                return
            await self._run_cmd([f"-s={sid}", "fill", ref, value])

        elif action == "dblclick":
            if not ref or not _REF_PATTERN.match(str(ref)):
                log.warning("PlaywrightCliBackend: dblclick rejected invalid ref '%s'", ref)
                return
            await self._run_cmd([f"-s={sid}", "dblclick", ref])

        elif action == "type":
            await self._run_cmd([f"-s={sid}", "type", str(value)])

        elif action == "press":
            if not value:
                log.warning("PlaywrightCliBackend: press action missing value")
                return
            await self._run_cmd([f"-s={sid}", "press", str(value)])

        elif action == "select":
            if not ref or not _REF_PATTERN.match(str(ref)):
                log.warning("PlaywrightCliBackend: select rejected invalid ref '%s'", ref)
                return
            await self._run_cmd([f"-s={sid}", "select", ref, str(value)])

        elif action == "check":
            if not ref or not _REF_PATTERN.match(str(ref)):
                log.warning("PlaywrightCliBackend: check rejected invalid ref '%s'", ref)
                return
            await self._run_cmd([f"-s={sid}", "check", ref])

        elif action == "uncheck":
            if not ref or not _REF_PATTERN.match(str(ref)):
                log.warning("PlaywrightCliBackend: uncheck rejected invalid ref '%s'", ref)
                return
            await self._run_cmd([f"-s={sid}", "uncheck", ref])

        elif action == "hover":
            if not ref or not _REF_PATTERN.match(str(ref)):
                log.warning("PlaywrightCliBackend: hover rejected invalid ref '%s'", ref)
                return
            await self._run_cmd([f"-s={sid}", "hover", ref])

        elif action == "go-back":
            await self._run_cmd([f"-s={sid}", "go-back"])

        elif action == "go-forward":
            await self._run_cmd([f"-s={sid}", "go-forward"])

        elif action == "reload":
            await self._run_cmd([f"-s={sid}", "reload"])

        elif action == "snapshot":
            # Explicit snapshot request — loop will call _get_snapshot on next iteration
            pass

        elif action == "tab-new":
            if value and any(str(value).startswith(s) for s in _URL_ALLOWED_SCHEMES):
                await self._run_cmd([f"-s={sid}", "tab-new", str(value)])
            else:
                await self._run_cmd([f"-s={sid}", "tab-new"])

        elif action == "tab-close":
            await self._run_cmd([f"-s={sid}", "tab-close"])

        elif action == "screenshot":
            snap_path = os.path.join(tempfile.gettempdir(), f"pw-shot-{sid}.png")
            await self._run_cmd([f"-s={sid}", "screenshot", f"--filename={snap_path}"])
            log.info("PlaywrightCliBackend: screenshot saved to %s", snap_path)
            try:
                if os.path.exists(snap_path):
                    os.unlink(snap_path)
            except Exception:
                pass

        else:
            log.warning("PlaywrightCliBackend: unknown action '%s' — skipping", action)

    # ── Prompt builder ────────────────────────────────────────────────────────

    def _load_system_prompt(self) -> str:
        """Load browser_agent.system.md from plugin prompts directory.

        Returns empty string if file not found (fallback: human-only message).
        Loaded fresh on each call — no caching — so hot-reload and agent-profile
        overrides work without restarting Agent Zero.
        """
        path = os.path.join(_PLUGIN_ROOT, "prompts", "browser_agent.system.md")
        try:
            return open(path, encoding="utf-8").read()
        except Exception as e:
            log.warning(
                "PlaywrightCliBackend: could not load system prompt from '%s': %s", path, e
            )
            return ""

    def _build_prompt(self, task: str, snapshot: dict, history: list) -> str:
        """Build human-turn LLM message: task + current snapshot + recent action history.

        System instructions are loaded separately from browser_agent.system.md
        and passed as a SystemMessage. This method carries only situational context.

        Security note: task string comes from the parent agent after secrets masking.
        It is embedded directly in the prompt — inherent prompt injection relay risk.
        Operators should restrict task content to trusted inputs.
        """
        # Safe serialization — snapshot already truncated at dict level
        snap_json = json.dumps(snapshot, indent=2)
        # Cap total snapshot bytes to prevent LLM context overflow
        # (deeply nested structures or long attribute values can exceed cap after element truncation)
        if len(snap_json) > self.SNAPSHOT_MAX_BYTES:
            snap_json = snap_json[: self.SNAPSHOT_MAX_BYTES] + "\n... (snapshot truncated at byte limit)"
        # Only last 5 history entries to avoid prompt bloat
        hist_json = json.dumps(history[-5:], indent=2)
        return (
            f"## Current Task\n{task}\n\n"
            f"## Page Snapshot\n"
            f"(Use element refs e1, e2, ... as targets for click/fill/hover/etc.)\n"
            f"{snap_json}\n\n"
            f"## Action History (last 5 steps)\n"
            f"{hist_json}\n\n"
            "Respond with a single JSON object — no prose, no markdown fences."
        )
