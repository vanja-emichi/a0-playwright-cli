### browser_agent:

Subordinate agent that controls a real browser via **Playwright CLI**.
Provides structured DOM snapshots with stable element references (`e1`, `e2`, ...).

**When to use:**
- Navigate, click, fill forms, extract content, take screenshots
- Multi-step web workflows (login → navigate → interact → extract)
- Any task requiring live browser interaction

**How to instruct:**
- Be specific and task-based — describe the full goal in one message
- Include credentials, URLs, and any required values inline
- Use `reset: true` to spawn a fresh browser session
- Use `reset: false` to continue an existing session (start message with "Considering open pages...")
- Do NOT use the phrase "wait for instructions" — end tasks definitively

**Capabilities:**
- Navigation: `goto`, `go-back`, `go-forward`, `reload`
- Interaction: `click`, `dblclick`, `fill`, `type`, `press`, `select`, `check`, `uncheck`, `hover`
- Tabs: `tab-new`, `tab-close`
- Capture: `snapshot` (DOM), `screenshot`

**Downloads:** saved to `/a0/tmp/downloads` by default.

**Pass secrets** using the secret alias format — they are masked and substituted automatically.

usage:
```json
{
  "thoughts": ["I need to log in to..."],
  "headline": "Opening new browser session for login",
  "tool_name": "browser_agent",
  "tool_args": {
    "message": "Open https://example.com, log in with email user@example.com and password <provided_password>, then extract the account balance shown on the dashboard. End task.",
    "reset": "true"
  }
}
```

```json
{
  "thoughts": ["Continuing from previous page..."],
  "headline": "Continuing existing browser session",
  "tool_name": "browser_agent",
  "tool_args": {
    "message": "Considering open pages, click the Submit button and confirm the result. End task.",
    "reset": "false"
  }
}
```
