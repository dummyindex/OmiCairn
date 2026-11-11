# Design Docs Index

Architectural decisions and their rationale. Each doc records a decision,
the alternatives considered, and why the chosen path won.

## Active Decisions

| ID | Title | Date | Status |
|----|-------|------|--------|
| DD-001 | [Core Beliefs](core-beliefs.md) | 2026-04-10 | Active |
| DD-002 | Multi-turn sessions over single-turn API calls | 2026-04-08 | Active |
| DD-003 | Contract-driven phase boundaries | 2026-04-08 | Active — see `engine/omicairn/contracts.py` |
| DD-004 | Cross-check semantic verification | 2026-04-10 | Active — see `engine/omicairn/cross_check.py` |
| DD-005 | [Feasibility / SCOPE-power gate](feasibility-gate.md) | 2026-06-18 | Active — see `engine/omicairn/feasibility.py` |

## How to Add a Design Doc

1. Create `docs/design-docs/DD-NNN-short-title.md`
2. Include: **Context**, **Decision**, **Alternatives considered**, **Consequences**
3. Add a row to this index
4. Reference it from docs/DESIGN.md if agents need to know about it
