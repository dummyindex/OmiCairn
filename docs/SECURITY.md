# Security — OmiCairn

## Threat Model

OmiCairn is a local research tool, not a web service. The primary security
concerns are:

1. **API key exposure** — Claude API keys, literature API keys
2. **Data confidentiality** — research data may be pre-publication
3. **Agent tool abuse** — Claude Code sessions have shell access
4. **Dependency supply chain** — Python packages

## API Key Management

| Key | Storage | Exposure Risk |
|-----|---------|---------------|
| `CLAUDE_CODE_OAUTH_TOKEN` | `.env` at repo root (gitignored) | Never commit; never log. Primary credential OmiCairn uses to authenticate `claude -p` subprocesses. Rotate at <https://console.anthropic.com/> if leaked. |
| `ANTHROPIC_API_KEY` | shell env (optional fallback) | If already set in the calling shell, OmiCairn preserves it and does NOT overwrite it from `.env`. |
| Literature API keys (NCBI, S2, etc.) | `.env` (gitignored) | Never commit; never log |

**Rules:**
- `.env` is in `.gitignore` — never committed
- API keys are never passed as CLI arguments (visible in `ps`)
- API keys are never logged in phase logs or STATE.md
- If a key is accidentally committed, rotate it immediately

## Data Handling

- Research data in `research_cases/*/data/` may include patient-
  derived datasets (TCGA, etc.). These are publicly available but should
  still be handled responsibly.
- Large data files (CSV, TSV, HDF5, etc.) are gitignored to prevent
  accidental commits of bulky or sensitive data.
- Workspace outputs may contain analysis results that are pre-publication.
  Workspaces are gitignored.
- Never push workspace contents to a public repository.

## Agent Tool Restrictions

Phase sessions are launched with a Claude Code tool allowlist defined
in `omicairn/phases.py::_DEFAULT_TOOLS`:

- **Bash** is granted as the bare `Bash` tool (no per-command
  filtering). Per-command sandboxing was tried earlier and removed
  in commit 077e20a — narrower scoping caused agents to thrash on
  legitimate self-healing operations and never recover. Destructive
  edits are blocked instead by the **`deny` list in
  `templates/settings.json`** (no writes to `RESEARCH.md`,
  `CLAUDE.md`, `STATE.md`; no `rm -rf` of the workspace root) and by
  the `Write`/`Edit` tools' workspace-scoped paths.
- **Web access** (`WebFetch`, `WebSearch`) is gated by `allow_web`
  in RESEARCH.md — when `false`, both tools are stripped from the
  allowlist and the data-fetch protocol in EXECUTE skips network
  calls entirely.
- The allowlist also includes `Read`, `Write`, `Edit`, `Glob`,
  `Grep`, `TodoWrite`, and `Task` (for subagent dispatch).
- Workspace `CLAUDE.md` (rendered from `templates/CLAUDE.md.tmpl`)
  provides additional session-level rules (read-only data dirs,
  immutability of prior stage dirs, etc.).

## Dependency Management

- Dependencies are minimal: `pyyaml`, `rich`, `requests`
- Pin versions in production; use `pip install -e .` for dev
- Review dependency updates before upgrading
- No runtime download of external code or models

## What to Do If

| Scenario | Action |
|----------|--------|
| API key committed to git | Rotate key immediately; `git filter-branch` or BFG to remove from history |
| Agent writes unexpected files | Check tool allowlist; tighten Bash scoping |
| Agent accesses unexpected URLs | Review WebFetch/WebSearch logs; consider disabling `allow_web` |
| Sensitive data in workspace | Ensure workspaces stay gitignored; don't share workspace directories |
