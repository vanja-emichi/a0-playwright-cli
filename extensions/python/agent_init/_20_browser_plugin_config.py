"""Playwright CLI plugin init extension.

Playwright CLI plugin uses Agent Zero built-in Browser Model settings
(browser_model_provider, browser_model_name, etc.) directly from agent.config.
No plugin-specific config injection needed.
"""
import logging
from python.helpers.extension import Extension

log = logging.getLogger(__name__)


class PlaywrightCliPluginInit(Extension):
    def execute(self, **kwargs) -> None:
        log.debug("PlaywrightCliPluginInit: playwright_cli plugin active")
