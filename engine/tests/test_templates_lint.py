"""Templates + settings.json drift lint.

`omicairn.workspace.init()` renders two template files into every newly
initialized workspace:

1. `templates/CLAUDE.md` → `<workspace>/CLAUDE.md` — the workspace rules
   that every phase agent reads before doing anything.
2. `templates/settings.json` → `<workspace>/.claude/settings.json` — the
   permission allowlist that Claude Code loads when a developer opens
   the workspace interactively (e.g., `cd <ws> && claude`).

The renderer is a simple string-replace driven by a small `ctx` dict
defined inline in `workspace.init()`. Drift here is the usual kind:

- Add a new field to `ctx` but forget to reference it in the template
  → orphan ctx key (harmless but confusing, and usually a sign that
  the template was reverted without updating the caller).
- Add `{{new_token}}` to a template but forget to populate it in `ctx`
  → the literal `{{new_token}}` ships into the rendered workspace and
  the agent sees it as text.
- Drop a Bash subcommand from `omicairn/phases.py` `_DEFAULT_TOOLS` without
  dropping it from `settings.json` (or vice versa) → a developer using
  the workspace interactively gets a permission allowlist that does
  not match what the automated `omicairn run` actually allows.

These tests pin all three at edit time.

Scope note: CLAUDE.md also contains several `{{token}}` strings that are
*intentionally* pass-through — they document phase-prompt tokens
(`{{stage_dir}}`, `{{knowledge_summary}}`, etc.) that the orchestrator
later substitutes into the phase prompt, not values the workspace
renderer expands. The test separates those two classes via an
allowlist.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "templates"
WORKSPACE_PY = REPO_ROOT / "omicairn" / "workspace.py"
PHASES_PY = REPO_ROOT / "omicairn" / "phases.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOKEN_PATTERN = re.compile(r"\{\{([a-z_][a-z0-9_]*)\}\}")


def _template_files() -> list[pathlib.Path]:
    """Top-level template files. Skips subdirectories, which are not
    part of the workspace-rendering pipeline."""
    return sorted(p for p in TEMPLATES_DIR.iterdir() if p.is_file())


def _tokens_in(path: pathlib.Path) -> set[str]:
    return set(_TOKEN_PATTERN.findall(path.read_text()))


def _workspace_ctx_keys() -> set[str]:
    """Static scan of omicairn/workspace.py for the ctx dict literal used by
    _render_template. We intentionally parse the source text rather than
    importing and running init() so this test has zero side effects.

    We match the first dict literal inside the body of `init()` that
    assigns `ctx = { ... }`.
    """
    src = WORKSPACE_PY.read_text()
    m = re.search(r"\n    ctx\s*=\s*\{(.*?)\n    \}\n", src, re.DOTALL)
    assert m is not None, (
        "Could not locate `ctx = { ... }` dict literal in "
        "omicairn/workspace.py. Either the renderer was refactored "
        "(update this test) or the dict moved out of init()."
    )
    body = m.group(1)
    # Match `"key": ...` entries at the top level of the dict body.
    return set(re.findall(r'"([a-z_][a-z0-9_]*)"\s*:', body))


# Tokens that appear in templates/CLAUDE.md but are NOT workspace ctx
# keys — they are documentation-level references to phase-prompt tokens
# that the orchestrator substitutes later (not what workspace.init()
# expands). Any new token added to CLAUDE.md must either be wired into
# ctx OR added here with a comment explaining why it's pass-through.
_PASSTHROUGH_CLAUDE_MD_TOKENS: frozenset[str] = frozenset({
    # Orchestrator injects these when rendering the per-phase prompt.
    # CLAUDE.md mentions them for the phase agent's awareness.
    "stage_dir",
    "stage_listing",
    "knowledge_summary",
    # Per-phase latest-stage pointers (documented for phase agents).
    # These expand to {{latest_scope_dir}}, {{latest_plan_dir}}, etc. —
    # CLAUDE.md uses the literal `latest_<phase>_dir` to describe them.
    "latest_<phase>_dir",
})


# ---------------------------------------------------------------------------
# 1. Workspace ctx keys are referenced in at least one template file
# ---------------------------------------------------------------------------

def test_every_workspace_ctx_key_is_used_by_some_template():
    """Every key in workspace.init's `ctx` dict must be referenced by
    at least one `{{key}}` in a templates/ file. An orphan ctx key is
    usually drift: a template token got removed but the ctx wiring
    wasn't cleaned up."""
    ctx_keys = _workspace_ctx_keys()
    assert ctx_keys, "failed to parse any ctx keys from workspace.py"
    used: set[str] = set()
    for tpl in _template_files():
        used |= _tokens_in(tpl)
    orphans = ctx_keys - used
    assert not orphans, (
        f"workspace.init() populates ctx keys that no template "
        f"references: {sorted(orphans)}. Either wire them into a "
        f"template or drop them from the ctx dict."
    )


# ---------------------------------------------------------------------------
# 2. Every {{token}} in templates is either in ctx or on the pass-through
#    allowlist — i.e. no silent literal leaks into rendered workspaces
# ---------------------------------------------------------------------------

def test_every_template_token_is_either_ctx_or_passthrough():
    """The renderer does a literal string-replace for each ctx key; any
    {{token}} not in ctx survives verbatim into the rendered output.
    That is fine for docs tokens (e.g. {{stage_dir}} in CLAUDE.md,
    which a phase agent reads for its own reference), but a brand-new
    token that wasn't wired into ctx is drift."""
    ctx_keys = _workspace_ctx_keys()
    allowed = ctx_keys | _PASSTHROUGH_CLAUDE_MD_TOKENS
    drift: list[str] = []
    for tpl in _template_files():
        # settings.json should have NO pass-through tokens — every
        # token there must be a ctx key (it's machine-read by Claude
        # Code, not docs for a human reader).
        strict_ctx_only = tpl.suffix == ".json"
        for tok in sorted(_tokens_in(tpl)):
            if strict_ctx_only:
                if tok not in ctx_keys:
                    drift.append(
                        f"{tpl.name}: {{{{{tok}}}}} is not in "
                        f"workspace.init() ctx — a literal will ship "
                        f"into the rendered {tpl.name}."
                    )
            else:
                if tok not in allowed:
                    drift.append(
                        f"{tpl.name}: {{{{{tok}}}}} is neither a ctx "
                        f"key nor on _PASSTHROUGH_CLAUDE_MD_TOKENS."
                    )
    assert not drift, "Template token drift:\n  " + "\n  ".join(drift)


# ---------------------------------------------------------------------------
# 3. settings.json parses as valid JSON with expected shape
# ---------------------------------------------------------------------------

def test_settings_json_parses_as_valid_json_after_token_sub():
    """The template uses `{{data_location}}` as a literal inside a
    JSON string value. If someone introduces a token outside a string
    context, rendering will produce invalid JSON. Substitute a plausible
    value and reparse."""
    raw = (TEMPLATES_DIR / "settings.json").read_text()
    # Fake-substitute every ctx key with a simple string so the token
    # site doesn't matter — we only care that the post-substitution
    # text is valid JSON.
    for key in _workspace_ctx_keys():
        raw = raw.replace("{{" + key + "}}", "/tmp/fake")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AssertionError(
            f"settings.json is not valid JSON after rendering: {e}"
        )
    # Expected top-level shape.
    assert "env" in parsed
    assert "permissions" in parsed
    assert "allow" in parsed["permissions"]
    assert "deny" in parsed["permissions"]
    assert isinstance(parsed["permissions"]["allow"], list)
    assert isinstance(parsed["permissions"]["deny"], list)


# ---------------------------------------------------------------------------
# 4. settings.json Bash allowlist matches phases.py _DEFAULT_TOOLS
# ---------------------------------------------------------------------------

_BASH_PATTERN = re.compile(r"^Bash\(([^:)]+):")


def _bash_subcommands(tokens: list[str]) -> set[str]:
    """Pull the Bash subcommand name out of entries like `Bash(ls:*)`."""
    out: set[str] = set()
    for t in tokens:
        m = _BASH_PATTERN.match(t)
        if m:
            out.add(m.group(1))
    return out


def _phases_default_tools_bash() -> set[str]:
    """Extract Bash(...) subcommands from omicairn/phases.py _DEFAULT_TOOLS
    by static text scan (no import side effects)."""
    src = PHASES_PY.read_text()
    m = re.search(
        r"_DEFAULT_TOOLS:\s*tuple\[str,\s*\.\.\.\]\s*=\s*\((.*?)\)\s*\n",
        src,
        re.DOTALL,
    )
    assert m is not None, (
        "Could not locate _DEFAULT_TOOLS tuple in omicairn/phases.py. "
        "Either the registry was refactored or the regex needs "
        "updating."
    )
    tokens = re.findall(r'"([^"]+)"', m.group(1))
    return _bash_subcommands(tokens)


def _settings_bash_allow() -> set[str]:
    raw = (TEMPLATES_DIR / "settings.json").read_text()
    for key in _workspace_ctx_keys():
        raw = raw.replace("{{" + key + "}}", "/tmp/fake")
    parsed = json.loads(raw)
    return _bash_subcommands(parsed["permissions"]["allow"])


def _has_bare_bash(tokens: list[str]) -> bool:
    return "Bash" in tokens


def _phases_default_tools_tokens() -> list[str]:
    src = PHASES_PY.read_text()
    m = re.search(
        r"_DEFAULT_TOOLS:\s*tuple\[str,\s*\.\.\.\]\s*=\s*\((.*?)\)\s*\n",
        src,
        re.DOTALL,
    )
    assert m is not None
    return re.findall(r'"([^"]+)"', m.group(1))


def _settings_allow_tokens() -> list[str]:
    raw = (TEMPLATES_DIR / "settings.json").read_text()
    for key in _workspace_ctx_keys():
        raw = raw.replace("{{" + key + "}}", "/tmp/fake")
    return list(json.loads(raw)["permissions"]["allow"])


def test_settings_json_bash_allowlist_matches_phases_default_tools():
    """The Bash policy in templates/settings.json (which governs
    interactive `claude` use inside a workspace) must agree with
    omicairn/phases.py _DEFAULT_TOOLS (which governs automated `omicairn run`
    calls). Two valid shapes:

    1. Both use bare ``Bash`` (unrestricted, deny-list-only).
    2. Both use the same set of ``Bash(subcmd:*)`` patterns.

    Drift here produces a confusing "works in omicairn run but not when I
    open the workspace manually" failure."""
    interactive_tokens = _settings_allow_tokens()
    automated_tokens = _phases_default_tools_tokens()

    interactive_bare = _has_bare_bash(interactive_tokens)
    automated_bare = _has_bare_bash(automated_tokens)

    if interactive_bare != automated_bare:
        raise AssertionError(
            f"Bash policy mismatch: settings.json bare Bash="
            f"{interactive_bare}, phases.py _DEFAULT_TOOLS bare Bash="
            f"{automated_bare}. Both must be unrestricted (bare) or "
            f"both must use scoped Bash(subcmd:*) patterns."
        )

    if interactive_bare and automated_bare:
        return  # Unrestricted policy on both sides — nothing more to check.

    interactive = _bash_subcommands(interactive_tokens)
    automated = _bash_subcommands(automated_tokens)
    only_in_settings = interactive - automated
    only_in_phases = automated - interactive
    errors: list[str] = []
    if only_in_settings:
        errors.append(
            f"Bash commands allowed in settings.json but NOT in "
            f"phases.py _DEFAULT_TOOLS: {sorted(only_in_settings)}"
        )
    if only_in_phases:
        errors.append(
            f"Bash commands in phases.py _DEFAULT_TOOLS but NOT in "
            f"settings.json: {sorted(only_in_phases)}"
        )
    assert not errors, (
        "Bash-subcommand drift between automated and interactive "
        "tool allowlists:\n  " + "\n  ".join(errors)
    )


# ---------------------------------------------------------------------------
# 5. settings.json deny list protects the workspace-scaffold files
# ---------------------------------------------------------------------------

_EXPECTED_DENY_WRITE_FILES: frozenset[str] = frozenset({
    "RESEARCH.md",
    "CLAUDE.md",
    "STATE.md",
})


def test_settings_json_denies_writes_to_scaffold_files():
    """RESEARCH.md, CLAUDE.md, and STATE.md are workspace-scaffold
    files that phase agents must never modify. The deny list in
    settings.json is the interactive-mode guardrail for that. If any
    of the three scaffold files falls off the deny list, an
    interactively-driven agent could silently corrupt the workspace."""
    raw = (TEMPLATES_DIR / "settings.json").read_text()
    for key in _workspace_ctx_keys():
        raw = raw.replace("{{" + key + "}}", "/tmp/fake")
    deny = json.loads(raw)["permissions"]["deny"]
    missing: list[str] = []
    for scaffold in sorted(_EXPECTED_DENY_WRITE_FILES):
        needs_write = f"Write({scaffold})"
        needs_edit = f"Edit({scaffold})"
        if needs_write not in deny:
            missing.append(needs_write)
        if needs_edit not in deny:
            missing.append(needs_edit)
    assert not missing, (
        "settings.json deny list is missing scaffold-file guards: "
        + str(missing)
    )


# Bash-subcommand patterns that the deny list must block regardless of
# what the allow list contains. These are belt-and-suspenders safety
# guardrails: even if someone later adds `Bash(rm:*)` to the allow
# list (which would be a separate bug), the explicit deny must still
# block destructive rm, and the curl|sh deny must still block piping
# a remote download into a shell.
_EXPECTED_DENY_BASH_PATTERNS: frozenset[str] = frozenset({
    # Destructive recursive-force rm. An interactive Claude Code
    # session in a OmiCairn workspace could otherwise be coerced into
    # wiping the workspace or its read-only data directory.
    "Bash(rm -rf:*)",
    # Classic curl-piped-to-shell remote-code-execution shape. Denying
    # the pattern blocks an interactively-driven agent from being
    # talked into running an unreviewed remote script.
    "Bash(curl:*|sh:*)",
})


def test_settings_json_denies_dangerous_bash_patterns():
    """The Bash deny list in settings.json is the belt-and-suspenders
    layer that blocks destructive or remote-code-execution shapes even
    if the allow list grows. Dropping `Bash(rm -rf:*)` or the curl|sh
    pattern from the deny list removes the only hard guard against an
    interactively-driven agent being talked into wiping the workspace
    or running an unreviewed remote script.

    Note: the patterns are listed as literal strings in the deny list
    — this test pins the exact set, so if someone rephrases a
    pattern (e.g. `Bash(rm:*)` instead of `Bash(rm -rf:*)`), update
    both the template and `_EXPECTED_DENY_BASH_PATTERNS` together."""
    raw = (TEMPLATES_DIR / "settings.json").read_text()
    for key in _workspace_ctx_keys():
        raw = raw.replace("{{" + key + "}}", "/tmp/fake")
    deny = set(json.loads(raw)["permissions"]["deny"])
    missing = sorted(_EXPECTED_DENY_BASH_PATTERNS - deny)
    assert not missing, (
        f"settings.json deny list is missing required Bash safety "
        f"pattern(s): {missing}. These are belt-and-suspenders guards "
        f"against destructive or RCE command shapes. If you deliberately "
        f"reshaped a pattern, update _EXPECTED_DENY_BASH_PATTERNS in "
        f"tests/test_templates_lint.py together with the template."
    )


# ---------------------------------------------------------------------------
# 6. CLAUDE.md references to knowledge/ and scaffold files must match
#    the canonical constants in omicairn/constants.py
# ---------------------------------------------------------------------------
#
# CLAUDE.md is what every phase agent reads before doing anything. It
# lists the read-only context files and the knowledge/ directory
# layout verbatim (not via {{tokens}}), so if someone renames a
# constant like LESSONS_FILE = "lessons.jsonl" → "seed_lessons.jsonl"
# and forgets to update CLAUDE.md, the phase agent tries to read a
# file that doesn't exist. The orchestrator sees a well-formed stage
# but the agent fails to accumulate knowledge — silent regression.
#
# We pin it by asserting every canonical knowledge/scaffold constant's
# value appears somewhere in CLAUDE.md. If a rename happens, the
# stale value disappears from the template and this test fails at
# edit time.

def test_claude_md_references_canonical_knowledge_and_scaffold_names():
    """Every canonical knowledge/ filename and scaffold filename
    declared in omicairn/constants.py must appear as a literal string in
    templates/CLAUDE.md. A constant rename without a template update
    is drift — the phase agent follows the stale CLAUDE.md reference,
    hits a missing file, and accumulates no knowledge."""
    from omicairn.constants import (
        DOMAIN_NOTES_FILE,
        EVENTS_LOG,
        JOURNAL_FILE,
        KNOWLEDGE_DIR,
        LESSONS_FILE,
    )
    claude_md = (TEMPLATES_DIR / "CLAUDE.md").read_text()

    # Canonical names that CLAUDE.md refers to by literal string.
    # EVENTS_LOG is excluded — it's workspace-root-level append-only
    # log for the orchestrator, not something phase agents read.
    canonical = {
        "KNOWLEDGE_DIR": KNOWLEDGE_DIR,
        "LESSONS_FILE": LESSONS_FILE,
        "JOURNAL_FILE": JOURNAL_FILE,
        "DOMAIN_NOTES_FILE": DOMAIN_NOTES_FILE,
    }
    missing: list[str] = []
    for name, val in canonical.items():
        if val not in claude_md:
            missing.append(f"{name}={val!r}")
    assert not missing, (
        f"templates/CLAUDE.md no longer mentions the following canonical "
        f"constants: {missing}. A rename in omicairn/constants.py was not "
        f"reflected in the template. Either update CLAUDE.md to the new "
        f"value or revert the constant rename.\n"
        f"(Note: {EVENTS_LOG!r} is intentionally not checked; it's the "
        f"orchestrator event log at workspace root, not a phase-agent "
        f"read target.)"
    )


_EXPECTED_SCAFFOLD_FILES_IN_CLAUDE_MD: frozenset[str] = frozenset({
    "RESEARCH.md",
    "CLAUDE.md",
    "STATE.md",
})


def test_claude_md_lists_all_scaffold_context_files():
    """CLAUDE.md's read-only-context section must list every scaffold
    file the orchestrator creates in a workspace. If someone removes
    `STATE.md` from the list but the orchestrator still writes it, the
    phase agent doesn't know STATE.md exists and may fail to stay
    consistent with prior-run state."""
    claude_md = (TEMPLATES_DIR / "CLAUDE.md").read_text()
    missing = sorted(
        f for f in _EXPECTED_SCAFFOLD_FILES_IN_CLAUDE_MD
        if f not in claude_md
    )
    assert not missing, (
        f"templates/CLAUDE.md no longer lists the following scaffold "
        f"files agents should read: {missing}. Either restore the "
        f"reference or, if the file is intentionally removed from the "
        f"workspace contract, update _EXPECTED_SCAFFOLD_FILES_IN_CLAUDE_MD."
    )


# ---------------------------------------------------------------------------
# 7. CLAUDE.md must carry the workspace-level safety guardrails
# ---------------------------------------------------------------------------
#
# The prompt-lint (test_llm_prompt_has_anti_fabrication_guard) pins each
# LLM prompt against anti-fabrication drift, but CLAUDE.md is a SEPARATE
# context layer that every phase agent reads BEFORE the phase prompt.
# It's the workspace-rules file — the "absolute rules" that apply no
# matter which prompt is running. If someone edits CLAUDE.md and drops
# "never fabricate" or "never simulate data", every phase agent in
# every workspace silently loses that reminder at the workspace level.
# The prompt-level reminder still holds, but we want belt-and-
# suspenders coverage for the single highest-severity rule in the
# pipeline.
#
# We pin a small set of keyword roots rather than exact sentences so
# the rule wording can be rephrased without breaking the test — it's
# the presence of the concept that matters.

_CLAUDE_MD_REQUIRED_GUARDRAILS: tuple[tuple[str, str], ...] = (
    # (keyword root to substring-search for, human-readable name of the rule)
    ("fabricat", "anti-fabrication rule (never fabricate citations/numbers/figures)"),
    ("simulat", "anti-simulation rule (never simulate data)"),
    ("done.flag", "done.flag end-of-stage contract marker"),
    ("LAST", "write done.flag LAST (orchestrator polls for done.flag as finish signal)"),
    ("data_location", "data-location read-only guard"),
    ("immutable", "prior-stage immutability rule"),
)


def test_claude_md_has_all_workspace_safety_guardrails():
    """CLAUDE.md is the workspace-rules file every phase agent reads
    before doing anything. These keyword roots encode the workspace's
    highest-severity safety rules — they must all survive any edit
    to the template. If one disappears, every phase agent in every
    workspace silently loses that rule at the workspace level even
    though the prompt-level reminder still holds.

    We check keyword roots (e.g. 'fabricat' matches fabricate /
    fabricating / fabrication) so rule wording can be rephrased
    without breaking the test — what matters is the presence of the
    concept."""
    claude_md = (TEMPLATES_DIR / "CLAUDE.md").read_text()
    missing: list[str] = []
    for keyword, rule_name in _CLAUDE_MD_REQUIRED_GUARDRAILS:
        if keyword not in claude_md:
            missing.append(f"{keyword!r} ({rule_name})")
    assert not missing, (
        "templates/CLAUDE.md is missing workspace-level safety "
        "guardrails:\n  " + "\n  ".join(missing) + "\n\n"
        "These keyword roots are the single highest-severity rules "
        "in the pipeline — the prompt-level guardrail alone is not "
        "enough because CLAUDE.md is a separate context layer that "
        "every phase agent reads before the phase prompt. Restore "
        "the rule or, if the rule wording was deliberately changed, "
        "update _CLAUDE_MD_REQUIRED_GUARDRAILS with the new keyword "
        "and a comment explaining the rephrasing."
    )


def test_claude_md_forbids_manuscript_drafting_and_has_no_venue():
    text = (TEMPLATES_DIR / "CLAUDE.md").read_text()
    assert "Do not draft manuscripts" in text
    assert "negative_results.md" in text
    assert "venue" not in text.lower().replace("venue-formatted", "")
    assert not (TEMPLATES_DIR / "venues").exists()
    assert not (TEMPLATES_DIR / "reviewer_perspectives.yaml").exists()

