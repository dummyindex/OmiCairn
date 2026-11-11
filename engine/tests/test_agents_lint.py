"""Agent-file lint: catch drift in agents/*.md.

`omicairn.workspace.init()` copies every `agents/*.md` into the workspace's
`.claude/agents/` directory, where Claude Code picks them up as
dispatchable subagents. The name in each file's frontmatter is the
string phase agents use to dispatch the subagent (e.g. "Dispatch the
`code-writer` subagent via the Agent tool").

Drift surface:

1. **Frontmatter shape.** Each file must open with a `---` fence, close
   with a `---` fence, and declare `name`, `description`, `tools` keys.
   A typo in any key makes Claude Code ignore the file silently.
2. **name ↔ filename.** The `name:` value must match the filename stem.
   A mismatch means `Dispatch the X subagent` still works (Claude Code
   keys on the frontmatter name) but the file is named something else,
   breaking grep/search and eventual renames.
3. **Uniqueness.** No two agent files may share a name.
4. **Prompt references.** Every backticked agent name mentioned in
   `prompts/*.md` must correspond to a real agents/*.md file. If
   someone renames an agent without updating the prompts, the phase
   LLM tries to dispatch a non-existent subagent.

All four checks are cheap enough to run on every `pytest` — they
replace a class of "works in the IDE, breaks on first omicairn run" drift
that is otherwise invisible until you kick off a real pipeline.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from omicairn import phases
from omicairn.constants import REMOVED_AGENT_NAMES


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO_ROOT / "agents"
PROMPTS_DIR = REPO_ROOT / "prompts"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FRONTMATTER_FENCE = "---"


def _agent_files() -> list[pathlib.Path]:
    return sorted(AGENTS_DIR.glob("*.md"))


def _prompt_files() -> list[pathlib.Path]:
    return sorted(PROMPTS_DIR.glob("*.md"))


def _split_frontmatter(text: str) -> tuple[str, str] | None:
    """Return (frontmatter_body, rest) or None if the file has no
    well-formed frontmatter block."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_FENCE:
        return None
    # Find the closing fence.
    for i in range(1, len(lines)):
        if lines[i].strip() == _FRONTMATTER_FENCE:
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return None


def _frontmatter_keys(fm_body: str) -> set[str]:
    """Return the set of top-level keys declared in the frontmatter.

    Supports both plain scalar `key: value` lines and YAML `key: >`
    folded-block continuations (as used by literature-fetcher.md). A
    top-level key is any line that starts at column 0 and contains a
    colon before any whitespace-only continuation lines.
    """
    keys: set[str] = set()
    for line in fm_body.splitlines():
        # Continuation line of a folded block — skip.
        if line.startswith(" ") or line.startswith("\t"):
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in line:
            key = line.split(":", 1)[0].strip()
            if key:
                keys.add(key)
    return keys


def _extract_name(fm_body: str) -> str | None:
    """Pull the `name:` scalar value from the frontmatter body."""
    for line in fm_body.splitlines():
        if line.startswith(" ") or line.startswith("\t"):
            continue
        if line.lstrip().startswith("name:"):
            _, _, value = line.partition(":")
            return value.strip().strip('"').strip("'") or None
    return None


def _extract_tools(fm_body: str) -> list[str] | None:
    """Pull the `tools:` scalar value from the frontmatter body and
    return the comma-separated entries (with whitespace stripped).

    Returns None if the agent has no `tools:` line at all.
    """
    for line in fm_body.splitlines():
        if line.startswith(" ") or line.startswith("\t"):
            continue
        if line.lstrip().startswith("tools:"):
            _, _, value = line.partition(":")
            return [t.strip() for t in value.split(",") if t.strip()]
    return None


# ---------------------------------------------------------------------------
# Sanity: there's at least one agent file
# ---------------------------------------------------------------------------

def test_agents_dir_is_non_empty():
    """A totally empty agents/ directory would be a rename-everything
    catastrophe. The repo ships 10+ subagents — if the count collapses,
    surface it loudly."""
    files = _agent_files()
    assert files, f"No agents/*.md found under {AGENTS_DIR}"
    # We currently ship 11 subagents. A dramatic drop is almost
    # always a checkout/move accident.
    assert len(files) >= 5, (
        f"Only {len(files)} agent file(s) found — expected at least 5. "
        f"Check if agents/ was partially moved or the glob is wrong."
    )


# ---------------------------------------------------------------------------
# Per-agent frontmatter shape
# ---------------------------------------------------------------------------

_REQUIRED_FRONTMATTER_KEYS = frozenset({"name", "description", "tools"})


@pytest.mark.parametrize(
    "agent_path",
    _agent_files(),
    ids=lambda p: p.stem,
)
def test_agent_file_has_well_formed_frontmatter(agent_path):
    """Every agents/*.md must open and close with `---` fences and
    declare name/description/tools. Missing any one of these makes
    Claude Code silently refuse to load the agent."""
    text = agent_path.read_text()
    split = _split_frontmatter(text)
    assert split is not None, (
        f"{agent_path.name}: no well-formed `---` frontmatter block. "
        f"Every agent file must start with `---` on line 1 and close "
        f"with `---` before the body."
    )
    fm_body, rest = split
    keys = _frontmatter_keys(fm_body)
    missing = _REQUIRED_FRONTMATTER_KEYS - keys
    assert not missing, (
        f"{agent_path.name}: frontmatter missing required keys "
        f"{sorted(missing)}. Every agent must declare name, description, "
        f"and tools."
    )
    # Body must not be empty — an agent with no instructions is drift.
    assert rest.strip(), (
        f"{agent_path.name}: agent has frontmatter but empty body. "
        f"Every agent must have an instructional body after the "
        f"frontmatter block."
    )


@pytest.mark.parametrize(
    "agent_path",
    _agent_files(),
    ids=lambda p: p.stem,
)
def test_agent_name_matches_filename_stem(agent_path):
    """`name:` in the frontmatter must match the filename stem. A
    mismatch makes grep-by-filename fail even though dispatch still
    works — a subtle source of drift that compounds over renames."""
    split = _split_frontmatter(agent_path.read_text())
    assert split is not None, f"{agent_path.name}: no frontmatter"
    fm_body, _ = split
    declared = _extract_name(fm_body)
    assert declared == agent_path.stem, (
        f"{agent_path.name}: frontmatter name={declared!r} but filename "
        f"stem is {agent_path.stem!r}. Rename one or the other so they "
        f"match — Claude Code keys subagent dispatch on the frontmatter "
        f"name, but humans read filenames."
    )


# ---------------------------------------------------------------------------
# Uniqueness across all agents
# ---------------------------------------------------------------------------

def test_agent_names_are_unique_across_files():
    """Two agents with the same `name:` would collide when Claude Code
    builds its subagent registry from .claude/agents/. Catch
    collisions at edit time."""
    seen: dict[str, pathlib.Path] = {}
    dupes: list[str] = []
    for path in _agent_files():
        split = _split_frontmatter(path.read_text())
        if split is None:
            continue  # reported by the frontmatter-shape test
        name = _extract_name(split[0])
        if name is None:
            continue
        if name in seen:
            dupes.append(
                f"{name!r} in both {seen[name].name} and {path.name}"
            )
        else:
            seen[name] = path
    assert not dupes, "Duplicate agent names:\n  " + "\n  ".join(dupes)


# ---------------------------------------------------------------------------
# Prompt ↔ agent name cross-reference
# ---------------------------------------------------------------------------

# Pattern: a backticked token in a prompt that looks like an agent name.
# Agent names are kebab-case lowercase — this matches `code-writer`,
# `citation-resolver`, `literature-fetcher`, etc. We deliberately do
# NOT match single-word backtick tokens (`Read`, `Write`) because
# those are overwhelmingly tool names, not agent names.
_BACKTICK_AGENT_LIKE = re.compile(r"`([a-z]+(?:-[a-z]+)+)`")


def _known_agent_names() -> set[str]:
    out: set[str] = set()
    for path in _agent_files():
        split = _split_frontmatter(path.read_text())
        if split is None:
            continue
        name = _extract_name(split[0])
        if name:
            out.add(name)
    return out


# These backticked kebab-case strings appear in prompts but are NOT
# subagent names — they're filenames, shell hints, or other tokens that
# happen to match the same shape. Keep this allowlist small and reviewed.
_KEBAB_NON_AGENT_ALLOWLIST: frozenset[str] = frozenset({
    # literature source APIs / filenames in prompts
    "europe-pmc",
    "semantic-scholar",
    # adequacy dimensions mentioned in review.md
    "anti-fabrication",
    "honest-validation",
    # conda env name referenced in execute.md (not a subagent)
    "omicairn",
    # shell command in execute.md's GPU-host setup step (not a subagent)
    "nvidia-smi",
})


def test_every_kebab_backtick_in_prompts_is_a_real_agent():
    """Every backticked kebab-case string in prompts/*.md that is not
    on the allowlist must resolve to a shipped agent file. A mismatch
    means a phase prompt is dispatching a non-existent subagent —
    invisible until the first real `omicairn run`."""
    known_agents = _known_agent_names()
    drift: list[str] = []
    for prompt_path in _prompt_files():
        text = prompt_path.read_text()
        for token in set(_BACKTICK_AGENT_LIKE.findall(text)):
            if token in known_agents:
                continue
            if token in _KEBAB_NON_AGENT_ALLOWLIST:
                continue
            drift.append(f"{prompt_path.name}: `{token}`")
    assert not drift, (
        "Prompt references a backticked kebab-case token that is "
        "neither a shipped agent nor on the allowlist. Either create "
        "the agent file, fix the prompt, or add the token to "
        "_KEBAB_NON_AGENT_ALLOWLIST with a comment:\n  "
        + "\n  ".join(drift)
    )


# ---------------------------------------------------------------------------
# Per-agent tools-list validation: every entry must be a known Claude Code
# tool name (with optional Bash(pattern:*) constraint)
# ---------------------------------------------------------------------------
#
# The `tools:` field in an agent's frontmatter is the permission allowlist
# Claude Code applies when the subagent is dispatched. A typo like `Writee`
# silently drops the tool from the allowlist — the subagent can't use it
# and fails the task it was dispatched for, but only at first real
# dispatch. This is latent failure territory.
#
# We pin the set of valid tool names to the ones the project actually
# uses today. If a new tool needs to be added, the allowlist must be
# updated — which forces a deliberate diff rather than a silent typo.

# Valid Claude Code tool names used anywhere in this project's
# agents/*.md frontmatter. If a new tool is legitimately added to any
# agent, include it here.
_KNOWN_AGENT_TOOL_NAMES: frozenset[str] = frozenset({
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash",
    "WebFetch",
    "WebSearch",
})


_BASH_WITH_PATTERN = re.compile(r"^Bash\(([^)]+)\)$")


def _tool_base_name(entry: str) -> str:
    """Strip the Bash(...) pattern wrapper from a tools-list entry and
    return the underlying tool name. `Bash(python3:*)` → `Bash`."""
    m = _BASH_WITH_PATTERN.match(entry)
    if m:
        return "Bash"
    return entry


@pytest.mark.parametrize(
    "agent_path",
    _agent_files(),
    ids=lambda p: p.stem,
)
def test_agent_tools_list_contains_only_known_tool_names(agent_path):
    """Every entry in an agent's frontmatter `tools:` list must
    resolve to a known Claude Code tool name. Catches typos like
    `Writee`, `Gloib`, or `Web-Fetch` at edit time — otherwise the
    subagent ships with a broken allowlist and fails only on first
    dispatch."""
    split = _split_frontmatter(agent_path.read_text())
    assert split is not None, f"{agent_path.name}: no frontmatter block"
    fm_body, _ = split
    tools = _extract_tools(fm_body)
    assert tools is not None, (
        f"{agent_path.name}: frontmatter has no `tools:` line. "
        f"Every agent must declare its tool allowlist."
    )
    assert tools, (
        f"{agent_path.name}: `tools:` line is present but empty. "
        f"An agent with no tools cannot do anything — either list "
        f"real tools or remove the agent."
    )

    unknown: list[str] = []
    for entry in tools:
        base = _tool_base_name(entry)
        if base not in _KNOWN_AGENT_TOOL_NAMES:
            unknown.append(entry)
    assert not unknown, (
        f"{agent_path.name}: `tools:` list contains unknown tool "
        f"name(s) {unknown}. Valid Claude Code tools (as used by "
        f"this project): {sorted(_KNOWN_AGENT_TOOL_NAMES)}. Either "
        f"fix the typo or, if this is a legitimate new tool, add "
        f"it to _KNOWN_AGENT_TOOL_NAMES in "
        f"tests/test_agents_lint.py with a comment explaining why."
    )


# ---------------------------------------------------------------------------
# Agents vs phase contracts: every `<phase>/<file>.(md|json)` reference in
# an agent file must resolve against phases.BY_NAME[<phase>] outputs
# ---------------------------------------------------------------------------
#
# agents/*.md files hand-code cross-phase file references like
# `execute/results.json`, `plan/experiment_plan.md`, or
# `review/review_notes.md`. These are symbolic "phase <name> has a file
# called <file>" references the subagent uses to read or write
# artifacts. The orchestrator renders these into real paths at dispatch
# time based on the stage directory layout.
#
# Drift surface: a rename on either side silently desyncs the agent
# from the phase contract.
#
#   - EXECUTE.required_outputs renames `results.json` to `output.json`
#     but analyst.md still reads `execute/results.json`. The agent
#     imports fine, ships fine, and fails at first real dispatch when
#     the Read tool comes back empty-handed.
#
#   - code-writer.md starts producing a new optional artifact
#     `execute/locked_model.json` but nobody adds it to
#     EXECUTE.optional_outputs. The file leaks into the stage dir and
#     is invisible to prior_stages listings and to any downstream
#     contract-check.
#
#   - devils-advocate.md writes `review/counter_critique.md` but
#     REVIEW.optional_outputs doesn't declare it — the orchestrator's
#     prior-stage enumeration skips it, and it is invisible downstream
#     unless something grep-hunts for it by name.
#
# Forward-direction check only: every `<phase>/<file.(md|json)>`
# reference in agents/*.md must appear in
# `phases.BY_NAME[phase].required_outputs` ∪ `optional_outputs`. The
# reverse direction (every contract file must be mentioned by some
# agent) is deliberately NOT asserted — not every contract file is
# consumed by a subagent; the phase prompt may consume it directly.
#
# We restrict the extension set to `.md` and `.json` to match the
# existing prompt-lint reverse check in tests/test_prompt_lint.py. Log
# files like `execute/run.log` are ephemeral debug artifacts, not
# contract files, and deliberately fall outside both lints.

# Matches backtick-enclosed `<phase>/<rel_path>.(md|json)` tokens
# appearing anywhere in an agent file. The backtick anchor is
# important: it excludes shell-command fragments like
# `python3 execute/code/main.py` where the phase name is preceded by
# other text inside the backticks — those are invocation lines, not
# file references.
_PHASE_FILE_REF_RE = re.compile(
    r"`("
    r"(?:scope|plan|execute|review)"         # canonical phase name
    r"/"
    r"[A-Za-z0-9_./-]+?"                     # non-greedy relative path
    r"(?:\.md|\.json)"                       # contract file extension
    r")`"
)


# Placeholder / glob tokens that indicate a reference is a template
# pattern rather than a concrete filename (e.g. `execute/figures/figN_<slug>.png`).
# We skip these: they're not drift-prone because they were never
# concrete filenames in the first place.
_PLACEHOLDER_CHARS: tuple[str, ...] = ("<", ">", "*", "{", "}")


def _agent_phase_file_refs() -> dict[pathlib.Path, set[str]]:
    """Map each agents/*.md file to the set of backticked
    `<phase>/<rel_path>.(md|json)` references it contains. Skips
    placeholder patterns like `<slug>`."""
    out: dict[pathlib.Path, set[str]] = {}
    for agent_path in _agent_files():
        text = agent_path.read_text()
        refs: set[str] = set()
        for match in _PHASE_FILE_REF_RE.findall(text):
            if any(c in match for c in _PLACEHOLDER_CHARS):
                continue
            refs.add(match)
        out[agent_path] = refs
    return out


def test_every_agent_phase_file_reference_resolves_to_a_phase_output():
    """Every backticked `<phase>/<file>.(md|json)` reference in an
    agent file must correspond to a real entry in that phase's
    `required_outputs` or `optional_outputs` tuple.

    Drift this catches:

    - EXECUTE.required_outputs renames `results.json` → `output.json`
      but the 5 agent files that read `execute/results.json` still
      reference the old name. Static tests all pass; the first
      subagent dispatch fails with "file not found".

    - A new optional artifact is added to an agent (e.g. a sub-agent
      starts producing `review/citation_verification.md`) without a
      matching entry in `REVIEW.optional_outputs`. The file exists
      on disk but the orchestrator never enumerates it, so later
      stages' prior_stages listings silently skip it.

    Deliberate non-goals: the reverse direction (every phase contract
    file must be referenced by at least one agent) is NOT asserted.
    Many contract files — like `done.flag`, `summary.md`,
    `verdict.json` — are consumed or written by the orchestrator, not
    by subagents, and have no business appearing in agent docs.

    Sanity guard: at least one reference must be found across the
    whole `agents/` tree. Zero refs almost certainly means the regex
    has stopped matching after a refactor (e.g. agents adopting a new
    `{{stage_dir}}/<file>` convention), and empty-vs-empty comparison
    would pass vacuously."""
    refs_by_file = _agent_phase_file_refs()
    total_refs = sum(len(r) for r in refs_by_file.values())
    assert total_refs > 0, (
        "Sanity guard: zero `<phase>/<file>` references found across "
        f"{len(refs_by_file)} agent files. The regex "
        "_PHASE_FILE_REF_RE has likely stopped matching — the agent "
        "docs probably adopted a new convention. Update this lint "
        "before trusting its (empty) result."
    )
    errors: list[str] = []
    for agent_path, refs in sorted(refs_by_file.items()):
        for ref in sorted(refs):
            phase_name, _, rel_path = ref.partition("/")
            phase = phases.BY_NAME.get(phase_name)
            if phase is None:
                # Belt-and-suspenders: the regex already limits the
                # phase name to the five LLM phases, so this branch
                # only triggers if someone adds cross_check to the
                # regex without updating BY_NAME.
                errors.append(
                    f"{agent_path.name}: `{ref}` names phase "
                    f"{phase_name!r} which is not in phases.BY_NAME"
                )
                continue
            contract = set(phase.required_outputs) | set(phase.optional_outputs)
            if rel_path not in contract:
                errors.append(
                    f"{agent_path.name}: `{ref}` — {rel_path!r} not in "
                    f"{phase_name} required_outputs or optional_outputs "
                    f"(contract: {sorted(contract)})"
                )
    assert not errors, (
        "Hardcoded phase-file references in agents/*.md do not match "
        "the phase contract in omicairn/phases.py:\n  "
        + "\n  ".join(errors)
        + "\n\nEither rename the reference in the agent file to match "
        "the current contract, update omicairn/phases.py required_outputs/"
        "optional_outputs to reflect a legitimate new file, or (for "
        "sub-agent ephemera) add the file to the phase's "
        "optional_outputs tuple."
    )


def test_removed_paper_agents_are_not_shipped():
    shipped = {p.stem for p in AGENTS_DIR.glob("*.md")}
    assert not shipped & set(REMOVED_AGENT_NAMES)


def test_devils_advocate_is_dispatched_by_analysis_review():
    """devils-advocate was re-targeted from the manuscript pipeline to the
    analysis REVIEW; the review prompt must still dispatch it."""
    assert (AGENTS_DIR / "devils-advocate.md").exists()
    assert "`devils-advocate`" in (PROMPTS_DIR / "review.md").read_text()

