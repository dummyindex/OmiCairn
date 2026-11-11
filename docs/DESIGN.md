# Design Philosophy — OmiCairn

## Guiding Metaphor

OmiCairn is a **research assistant that you supervise**, not a pipeline you
run. Each phase session is a capable researcher working in a private
office (the workspace). Your job as the developer is to make sure the
office is well-equipped (tools, data, prompts) and the quality bar is
enforced (contracts, cross-checks, analysis review).

## Design Principles

### 1. Thin Orchestrator, Fat Sessions

The orchestrator does as little as possible: init workspace, launch
Claude, verify outputs, advance state. All intelligence — literature
search, statistical analysis, analysis review, self-critique — lives
inside the Claude Code sessions. This keeps the Python codebase small,
testable, and free of research-domain logic.

### 2. Contracts as Interfaces

Phases communicate through files, not function calls. The contract
(required output files per phase) is the API. This makes phases
independently testable and replaceable. A phase can be re-run in
isolation without replaying the entire pipeline.

### 3. Progressive Verification

- **Layer 1**: Contract check — do the files exist and have content?
- **Layer 2**: Cross-check — are the artifacts internally consistent?
- **Layer 3**: Analysis review — does the REVIEW phase judge that the
  evidence actually answers the pre-registered question (nulls diagnosed,
  coverage and baselines adequate, negative results recorded)?

Each layer catches different failure modes. Layer 1 is cheap and fast;
Layer 3 is expensive and thorough.

### 4. Prompt Engineering as System Design

Prompt templates (`prompts/*.md`) and agent specs (`agents/*.md`) are
first-class system components, not afterthoughts. Changes to prompts
are code changes — they should be reviewed, tested via smoke tests,
and committed with rationale.

### 5. Fail Loud, Retry Smart

When a phase fails, the system captures the failure reason (contract
violation, cross-check error, timeout) and injects it into the retry
prompt. The agent gets specific, actionable feedback about what went
wrong, not a generic "try again."

### 6. No Hallucination Tolerance

Every statistical result must come from code executed on real data.
The cross-check verifier compares `results.json` numbers against
claims in `analysis.md`. Discrepancies block phase completion.

## Coding Conventions

### No duplicated string constants

When a string constant appears more than once in the codebase, hoist it
into a shared class, enum, dataclass, or module-level constant instead
of re-typing the literal at each call site.

**Why this matters.** Python has no compile-time protection against
string typos. Duplicated string literals are silent drift bombs — if
someone renames `"execute"` to `"run"` in one place but forgets another,
the system breaks at runtime with an error that's hard to trace. When
the same string appears in multiple places, every future rename becomes
an invitation to introduce a bug.

**Status (2026-04-12).** This rule has been enforced for phase names.
The canonical phase identifiers live on `omicairn.constants.PhaseName` and
the ordered tuple `PIPELINE_ORDER` is derived from `phases.ORDER`. CLI
`choices` (`omicairn/cli.py`), state dict keys (`omicairn/state.py`), and
orchestrator routing (`omicairn/constants.DECISION_TO_PHASE`) all read from
that single source — adding or renaming a phase only requires editing
`phases.py` + `constants.py`. The `test_constants.py` suite pins the
coupling.

The same pattern applies to `Token` (prompt placeholders), `Decision`
(verdict strings), and `RunStatus` (STATE.md `status:` values). When
adding a new repeated string literal, hoist first; grep for existing
constants second.

**How to apply this rule.**

1. Before introducing a new string literal, grep for it first. If it
   already exists, import or reference the existing constant.
2. When writing new code that will reference the same string **twice**,
   declare it once at module top (or in `constants.py` / an enum /
   a dataclass) and import from there.
3. For *related* constants (phase names, tool allowlist entries,
   contract file names, agent spec filenames, RESEARCH.md field keys),
   prefer a single source-of-truth module over tuples and dicts
   scattered across files.
4. When refactoring anything in the neighborhood of a duplicated
   literal, hoist it — treat the duplication as tech debt even if the
   immediate change doesn't require the fix.
5. This rule applies to **every** kind of string: file paths, tool
   names, CLI choices, JSON/YAML keys, phase names, agent names,
   prompt filenames. Anywhere a typo would silently break a pipeline.

**Good** (single source of truth):

```python
# omicairn/phases.py — canonical definition
ORDER: tuple[Phase, ...] = (
    SCOPE, PLAN, EXECUTE, CROSS_CHECK, REVIEW,
)

# omicairn/constants.py — re-expose as plain strings
PIPELINE_ORDER: Final[tuple[str, ...]] = tuple(p.name for p in phases.ORDER)
CLI_PHASE_CHOICES: Final[tuple[str, ...]] = (PHASE_CHOICE_ALL, *PIPELINE_ORDER)

# omicairn/cli.py — derive from canonical
from .constants import CLI_PHASE_CHOICES, PHASE_CHOICE_ALL
p_run.add_argument("--phase", default=PHASE_CHOICE_ALL, choices=CLI_PHASE_CHOICES)
```

**Bad** (drift risk):

```python
# omicairn/cli.py — hardcoded, will drift from phases.py
p_run.add_argument("--phase", default="all",
                   choices=["all", "scope", "plan", "execute", "review"])
```

## Anti-Patterns to Avoid

- **Hand-writing outputs**: If you're writing analysis results by hand,
  you've left the developer role. Fix OmiCairn instead.
- **Hardcoded domain knowledge**: Gene names, method lists, disease
  terms in Python code break OmiCairn for new domains.
- **Monolithic prompts**: A 5000-word prompt overwhelms context. Keep
  prompts focused; delegate detail to agent specs.
- **Skipping smoke tests**: Full runs are expensive. Always validate
  on smoke-tiny first.
- **Fixing outputs instead of systems**: A hand-edited result is a
  one-time fix. A better prompt is a permanent fix.
