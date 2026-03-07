"""Playwright CLI plugin initializer.

Called by Agent Zero when the user clicks 'Initialize' on the plugin page.
Installs playwright-cli npm package and Chromium browser binaries.
Also writes ~/.playwright/cli.config.json pointing to the discovered binary.

Can be run standalone: python initialize.py
"""
import glob
import json
import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger(__name__)


def _run(cmd: list, timeout: int = 300, cwd: str = None) -> tuple[int, str, str]:
    """Run a subprocess command. Returns (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or os.path.expanduser("~"),
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s: {' '.join(cmd)}"
    except Exception as e:
        return -1, "", str(e)


def _find_chromium() -> str | None:
    """Search for a Chromium executable in known locations. Returns path or None."""
    home = os.path.expanduser("~")
    search_patterns = [
        # playwright-cli / standard ms-playwright cache
        os.path.join(home, ".cache", "ms-playwright", "chromium-*", "chrome-linux64", "chrome"),
        os.path.join(home, ".cache", "ms-playwright", "chromium-*", "chrome-linux", "chrome"),
        # Agent Zero's own playwright cache
        "/a0/tmp/playwright/**/chrome",
        "/a0/tmp/playwright/**/headless_shell",
    ]
    for pattern in search_patterns:
        matches = sorted(glob.glob(pattern, recursive=True), reverse=True)
        for match in matches:
            if os.path.isfile(match) and os.access(match, os.X_OK):
                return match
    return None


def _write_cli_config(chromium_path: str) -> None:
    """Write ~/.playwright/cli.config.json with executablePath for playwright-cli."""
    home = os.path.expanduser("~")
    config_dir = os.path.join(home, ".playwright")
    config_path = os.path.join(config_dir, "cli.config.json")
    os.makedirs(config_dir, exist_ok=True)
    config = {"browsers": {"chromium": {"executablePath": chromium_path}}}
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    log.info("playwright-cli config written: %s -> %s", config_path, chromium_path)
    print(f"  Config written: {config_path}")
    print(f"  Chromium path:  {chromium_path}")


def initialize(plugin_dir: str = None) -> bool:
    """Install playwright-cli and Chromium. Returns True on success."""
    print("\n=== Playwright CLI Plugin Initialization ===")
    success = True

    # ── Step 1: Install playwright-cli npm package ────────────────────────────
    cli_path = shutil.which("playwright-cli")
    if cli_path:
        rc, out, _ = _run(["playwright-cli", "--version"])
        version = out.strip() if rc == 0 else "unknown"
        print(f"✅ playwright-cli already installed: {cli_path} ({version})")
    else:
        print("📦 Installing playwright-cli via npm...")
        rc, out, err = _run(["npm", "install", "-g", "@playwright/cli@latest"], timeout=180)
        if rc == 0:
            cli_path = shutil.which("playwright-cli")
            print(f"✅ playwright-cli installed: {cli_path or 'check PATH'}")
        else:
            print(f"❌ npm install failed:\n{err[-500:]}")
            log.error("npm install failed: %s", err[-500:])
            success = False

    # ── Step 2: Install Chromium if not present ───────────────────────────────
    chromium_path = _find_chromium()
    if chromium_path:
        print(f"✅ Chromium already installed: {chromium_path}")
    elif cli_path or shutil.which("playwright-cli"):
        print("🌐 Installing Chromium via playwright-cli install...")
        rc, out, err = _run(["playwright-cli", "install"], timeout=600)
        if rc == 0:
            chromium_path = _find_chromium()
            if chromium_path:
                print(f"✅ Chromium installed: {chromium_path}")
            else:
                print("⚠️  playwright-cli install ran but Chromium binary not found in expected paths")
                log.warning("Chromium not found after install")
        else:
            print(f"❌ playwright-cli install failed:\n{err[-500:]}")
            log.error("playwright-cli install failed: %s", err[-500:])
            success = False
    else:
        print("⚠️  Skipping Chromium install — playwright-cli not available")
        success = False

    # ── Step 3: Write ~/.playwright/cli.config.json ───────────────────────────
    if chromium_path:
        try:
            _write_cli_config(chromium_path)
            print("✅ playwright-cli config updated")
        except Exception as e:
            print(f"⚠️  Could not write cli.config.json: {e}")
            log.warning("Failed to write cli.config.json: %s", e)
    else:
        print("⚠️  Skipping config write — Chromium path unknown")

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    if success:
        print("✅ Initialization complete — Playwright CLI plugin is ready.")
    else:
        print("⚠️  Initialization completed with warnings. Check output above.")
        print("   Manual install: npm install -g @playwright/cli@latest && playwright-cli install")
    print()
    return success


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(0 if initialize() else 1)
