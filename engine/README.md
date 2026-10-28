# OmiCairn engine

This directory holds the OmiCairn Python package (`omicairn/`), the agent
prompts and sub-agent specifications it renders for each phase, the domain
packs, and the test suite. See the repository
[README](../README.md) for installation and usage.

## Layout

| Path | Contents |
|------|----------|
| `omicairn/` | Orchestrator, phase contracts, cross-check gates, knowledge graph, literature modules, CLI |
| `prompts/` | One prompt per pipeline phase (scope, plan, execute, review, ideate) plus the steering planner and project scan |
| `agents/` | Sub-agent specifications used inside phase sessions (analyst, code reviewer, devil's advocate, …) |
| `rules/` | Analysis-rigor and method-depth rules injected into prompts |
| `domain_packs/` | Field-specific guidance (`scrna_seq`, `survival_prognostic`, `_generic`) |
| `templates/` | Workspace template (`CLAUDE.md`, `settings.json`) |
| `LeanScaffold/` | Optional Lean project scaffold used by the formal-verification track |
| `tests/` | Unit and end-to-end tests (no network or LLM calls; run with `pytest`) |

## Develop

```bash
pip install -e .
pip install pytest
python -m pytest tests -q
```

Tests run without a Claude CLI: phase sessions are exercised in dry-run mode
(`OMICAIRN_DRY_RUN=1`).
