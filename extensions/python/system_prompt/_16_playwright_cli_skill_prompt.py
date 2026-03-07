"""Playwright CLI skill auto-injection extension.

Runs at slot _16_ (before _20_browser_plugin_config).
When browser_backend is set to 'playwright_cli' in plugin config, injects the
official playwright-cli SKILL.md content into the agent system prompt so the
agent automatically knows all playwright-cli commands without needing to manually
load the skill.
"""
import logging
import os
from helpers.extension import Extension
from agent import Agent, LoopData
from helpers import plugins

log = logging.getLogger(__name__)

_PLUGIN_NAME = "playwright_cli"
_PLAYWRIGHT_CLI_BACKEND = "playwright_cli"


class PlaywrightCliSkillPrompt(Extension):

    async def execute(
        self,
        system_prompt: list[str] = [],
        loop_data: LoopData = LoopData(),
        **kwargs,
    ):
        # Read plugin config to check selected backend
        cfg = plugins.get_plugin_config(_PLUGIN_NAME, self.agent)
        if not cfg:
            return

        backend = cfg.get("browser_backend", "playwright_cli")
        if backend != _PLAYWRIGHT_CLI_BACKEND:
            return

        # Derive SKILL.md path relative to this extension file:
        # _16_...py → system_prompt → python → extensions → browser → plugins → usr
        _p = os.path.abspath(__file__)
        for _ in range(6):
            _p = os.path.dirname(_p)
        skill_md_path = os.path.join(_p, "plugins", "playwright_cli", "skills", "playwright-cli", "SKILL.md")

        if not os.path.isfile(skill_md_path):
            log.warning(
                "PlaywrightCliSkillPrompt: SKILL.md not found at '%s' — skipping injection",
                skill_md_path,
            )
            return

        try:
            skill_content = open(skill_md_path, encoding="utf-8").read()
        except Exception as e:
            log.warning("PlaywrightCliSkillPrompt: failed to read SKILL.md (%s)", e)
            return

        system_prompt.append(
            "\n\n# Browser Automation Skill (playwright-cli)\n"
            "The playwright_cli backend is active. "
            "Use the following skill reference for all browser automation commands:\n\n"
            + skill_content
        )
        log.debug("PlaywrightCliSkillPrompt: playwright-cli SKILL.md injected into system prompt")
