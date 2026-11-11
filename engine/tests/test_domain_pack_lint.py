"""Domain-pack lint: catch drift in domain_packs/*/ directories.

`omicairn.domain_pack.render_guidance` gracefully returns an empty string
when a pack is missing a phase file, which is both a feature (non-generic
packs can opt out of phases they don't have anything to say about) and
a hazard (a typo in the filename silently drops the guidance). These
tests pin that surface area:

1. `_generic` is the reference pack — it MUST ship a `phase_<name>.md`
   for every LLM_PHASE. If _generic loses coverage, every RESEARCH.md
   with an unmatched domain loses guidance for that phase too.
2. Every profile.yaml must parse via the mini-YAML parser and declare
   `name:` and `matches:`. Packs with malformed profiles never match.
3. Every directory under `domain_packs/` must be a valid pack (have
   profile.yaml). Stray directories like `.DS_Store` or a half-finished
   pack break `available_packs()`.
4. Files named `phase_*.md` in a pack must correspond to real LLM
   phases — no `phase_crosscheck.md`, `phase_finalise.md`, etc.
5. Non-generic packs MAY omit phase files, but the set of omissions
   must match `_INTENTIONALLY_MISSING` exactly. Adding or removing a
   phase_<name>.md file requires updating the allowlist, which forces
   a diff-level review of intentional coverage changes.
6. Every `phase_<name>.md` that EXISTS in any pack must be non-empty
   after `.strip()` — render_guidance treats whitespace-only bodies
   as absent guidance, so a half-typed file silently drops its block.
7. Every non-generic pack's `matches:` list must be non-empty and
   contain only strings — an empty list makes resolve_pack unable to
   route to the pack via indirect match.
8. Every `lessons/*.jsonl` file in every pack must be parseable: each
   non-blank line must be a valid JSON object. The loader
   (`iter_seed_lessons`) silently skips malformed lines, so a typo
   means a lesson ships with the pack but never reaches the LLM.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from omicairn import domain_pack
from omicairn.constants import (
    DOMAIN_PACK_GENERIC,
    DOMAIN_PACK_PROFILE_FILE,
    CLAUDE_CODE_PHASES,
    domain_pack_phase_file,
)


PACKS_DIR = (
    pathlib.Path(__file__).resolve().parents[1] / "domain_packs"
)


def _pack_dirs() -> list[pathlib.Path]:
    return sorted(p for p in PACKS_DIR.iterdir() if p.is_dir())


def _pack_names() -> list[str]:
    return [p.name for p in _pack_dirs()]


# ---------------------------------------------------------------------------
# _generic: the reference pack must cover every LLM phase
# ---------------------------------------------------------------------------

# Phases that are intentionally exempt from domain-pack guidance.
# `free` (steering Slice 2) is dispatched only via SteeringPlan steps
# where the user has already supplied prompt_inline; injecting domain-
# pack guidance on top would conflict with the steered intent.
# `ideate` is the standalone brainstorm front-stage: it runs before any
# domain-specific scientific work, its prompt does not render
# {{domain_pack_guidance}}, and its job (survey + propose directions) is
# domain-agnostic by construction. So packs ship no phase_ideate.md.
_DOMAIN_PACK_EXEMPT_PHASES = frozenset({"free", "ideate"})


@pytest.mark.parametrize(
    "phase_name", sorted(CLAUDE_CODE_PHASES - _DOMAIN_PACK_EXEMPT_PHASES)
)
def test_generic_pack_ships_guidance_for_every_llm_phase(phase_name):
    """_generic is the fallback every unmatched domain uses. If it's
    missing a phase file, an entire class of runs loses guidance for
    that phase silently (render_guidance returns '')."""
    phase_file = (
        PACKS_DIR / DOMAIN_PACK_GENERIC / domain_pack_phase_file(phase_name)
    )
    assert phase_file.is_file(), (
        f"{DOMAIN_PACK_GENERIC} is missing guidance for LLM phase "
        f"{phase_name!r}. Every LLM phase must have a phase_<name>.md "
        f"in the generic fallback pack, or runs with an unmatched "
        f"domain will silently get empty guidance for this phase."
    )
    # Non-empty: render_guidance treats empty files as "no guidance".
    assert phase_file.read_text().strip(), (
        f"{phase_file} exists but is empty. render_guidance treats an "
        f"empty body as absent guidance, so either add real content or "
        f"delete the file."
    )


# ---------------------------------------------------------------------------
# Every pack directory is a valid pack
# ---------------------------------------------------------------------------

def test_every_pack_dir_has_profile_yaml():
    """A directory under domain_packs/ without profile.yaml would be
    silently ignored by available_packs(), leaving broken intent behind.
    """
    missing: list[str] = []
    for pack in _pack_dirs():
        if not (pack / DOMAIN_PACK_PROFILE_FILE).is_file():
            missing.append(pack.name)
    assert not missing, (
        f"domain_packs/ directories without {DOMAIN_PACK_PROFILE_FILE}: "
        f"{missing}. Either add a profile or remove the dir."
    )


def test_every_profile_yaml_declares_name_and_matches():
    """Every pack's profile.yaml must parse and declare at least `name`
    and `matches`. Without `matches`, resolve_pack can never route to
    this pack. Without `name`, identification is ambiguous."""
    bad: list[str] = []
    for pack in _pack_dirs():
        profile = domain_pack.load_profile(pack.name)
        if "name" not in profile:
            bad.append(f"{pack.name}: missing 'name' in profile.yaml")
            continue
        if "matches" not in profile or not isinstance(
            profile["matches"], list
        ):
            bad.append(
                f"{pack.name}: missing 'matches' list in profile.yaml"
            )
    assert not bad, "Invalid domain pack profiles:\n  " + "\n  ".join(bad)


def test_profile_name_matches_directory_name():
    """profile.yaml's `name:` field must match the directory. Mismatch
    causes resolve_pack to return the dir name while load_profile
    returns the profile — a silent inconsistency."""
    bad: list[str] = []
    for pack in _pack_dirs():
        profile = domain_pack.load_profile(pack.name)
        declared = profile.get("name")
        if declared != pack.name:
            bad.append(
                f"dir={pack.name!r} but profile.yaml says name={declared!r}"
            )
    assert not bad, (
        "Pack directory / profile name mismatches:\n  "
        + "\n  ".join(bad)
    )


# ---------------------------------------------------------------------------
# Phase files in packs must correspond to real LLM phases
# ---------------------------------------------------------------------------

def test_pack_phase_files_use_valid_phase_names():
    """A file named `phase_foo.md` inside a pack that doesn't map to an
    LLM_PHASE is drift — render_guidance will never pick it up."""
    valid_files = {domain_pack_phase_file(p) for p in CLAUDE_CODE_PHASES}
    drift: list[str] = []
    for pack in _pack_dirs():
        for path in pack.glob("phase_*.md"):
            if path.name not in valid_files:
                drift.append(f"{pack.name}/{path.name}")
    assert not drift, (
        "Pack phase files that don't correspond to an LLM phase "
        f"(valid names: {sorted(valid_files)}):\n  " + "\n  ".join(drift)
    )


# ---------------------------------------------------------------------------
# Non-generic packs: intentional omissions must be explicit
# ---------------------------------------------------------------------------

# Phases that a non-generic pack is intentionally NOT overriding.
# Adding a phase_<name>.md file to a non-generic pack requires removing
# the phase from this allowlist. Removing a phase_<name>.md file
# requires adding it here. This forces drift to show up in the diff.
_INTENTIONALLY_MISSING: dict[str, frozenset[str]] = {
    # survival_prognostic: scope.md is phase-agnostic enough that the
    # generic fallback is sufficient. The pack specializes from plan
    # onward (hypotheses, cox/lasso modeling, KM analysis, etc.).
    # review: the analysis-adequacy review is domain-agnostic; both
    # packs fall back to the _generic phase_review.md.
    "survival_prognostic": frozenset({"scope", "review"}),
    "scrna_seq": frozenset({"review"}),
}


def test_non_generic_packs_match_intentional_missing_allowlist():
    """Compare the ACTUAL set of missing phase files against the
    declared intentional omissions. Any delta means either:
    - someone added a file without updating the allowlist (good — drop
      the entry from _INTENTIONALLY_MISSING);
    - someone deleted a file without updating the allowlist (bad — the
      pack is now silently missing coverage).
    Either way we want the diff to be loud."""
    errors: list[str] = []
    # `free` is steering-only and exempt from domain-pack coverage in
    # both _generic and non-generic packs (see _DOMAIN_PACK_EXEMPT_PHASES
    # above). Subtract it before comparing against the allowlist.
    coverable_phases = CLAUDE_CODE_PHASES - _DOMAIN_PACK_EXEMPT_PHASES
    for pack in _pack_dirs():
        if pack.name == DOMAIN_PACK_GENERIC:
            continue
        actual_missing = {
            phase for phase in coverable_phases
            if not (pack / domain_pack_phase_file(phase)).is_file()
        }
        declared = _INTENTIONALLY_MISSING.get(pack.name, frozenset())
        if actual_missing != declared:
            extra = actual_missing - declared
            surprise_present = declared - actual_missing
            parts = []
            if extra:
                parts.append(
                    f"missing but NOT in allowlist: {sorted(extra)}"
                )
            if surprise_present:
                parts.append(
                    f"in allowlist but file now exists: "
                    f"{sorted(surprise_present)}"
                )
            errors.append(f"{pack.name}: " + "; ".join(parts))
    assert not errors, (
        "Non-generic pack coverage changed without updating "
        "_INTENTIONALLY_MISSING. Review the delta and update the "
        "allowlist:\n  " + "\n  ".join(errors)
    )


# ---------------------------------------------------------------------------
# Content checks: no whitespace-only phase files, no empty matches lists
# ---------------------------------------------------------------------------

def _shipped_phase_files() -> list[pathlib.Path]:
    """Every (pack, phase_file) pair that exists on disk, flattened."""
    out: list[pathlib.Path] = []
    for pack in _pack_dirs():
        out.extend(sorted(pack.glob("phase_*.md")))
    return out


@pytest.mark.parametrize(
    "phase_file",
    _shipped_phase_files(),
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_shipped_phase_files_are_non_empty(phase_file):
    """Any phase_<name>.md that exists on disk must have a non-empty
    body after `.strip()`. render_guidance intentionally returns ""
    for whitespace-only files (so half-finished guidance doesn't leak
    into prompts), which means a typo that empties the file is
    invisible until someone notices the prompt block is missing."""
    body = phase_file.read_text()
    assert body.strip(), (
        f"{phase_file.parent.name}/{phase_file.name} is "
        f"whitespace-only. render_guidance will skip it entirely. "
        f"Either add real content or delete the file."
    )


def test_every_non_generic_pack_has_non_empty_matches_list():
    """A non-generic pack with an empty `matches:` list can only be
    reached via direct domain-name match — never via indirect
    substring matching through resolve_pack. That's almost always a
    bug: an empty list looks fine in the YAML but silently narrows
    the pack's reach."""
    bad: list[str] = []
    for pack in _pack_dirs():
        if pack.name == DOMAIN_PACK_GENERIC:
            continue
        profile = domain_pack.load_profile(pack.name)
        matches = profile.get("matches") or []
        if not matches:
            bad.append(f"{pack.name}: empty matches list")
            continue
        for m in matches:
            if not isinstance(m, str) or not m.strip():
                bad.append(f"{pack.name}: non-string/empty match: {m!r}")
    assert not bad, (
        "Non-generic packs with empty/bad matches lists:\n  "
        + "\n  ".join(bad)
    )


# ---------------------------------------------------------------------------
# Lessons lint: every *.jsonl line must be a valid JSON object
# ---------------------------------------------------------------------------

def _shipped_lessons_files() -> list[pathlib.Path]:
    """Every (pack, lessons/*.jsonl) pair that exists on disk."""
    out: list[pathlib.Path] = []
    for pack in _pack_dirs():
        lessons_dir = pack / "lessons"
        if not lessons_dir.is_dir():
            continue
        out.extend(sorted(lessons_dir.glob("*.jsonl")))
    return out


@pytest.mark.parametrize(
    "lessons_file",
    _shipped_lessons_files(),
    ids=lambda p: f"{p.parents[1].name}/{p.name}",
)
def test_shipped_lessons_jsonl_is_well_formed(lessons_file):
    """`omicairn.domain_pack.iter_seed_lessons` silently skips malformed
    JSONL lines so a bad pack still loads. At edit time we want the
    opposite: fail loud so the typo is caught before it ships. Every
    non-blank line must parse as a JSON object (dict)."""
    errors: list[str] = []
    for lineno, raw in enumerate(lessons_file.read_text().splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"line {lineno}: invalid JSON ({e})")
            continue
        if not isinstance(obj, dict):
            errors.append(
                f"line {lineno}: expected JSON object, got {type(obj).__name__}"
            )
    assert not errors, (
        f"{lessons_file.parents[1].name}/{lessons_file.name} has bad "
        f"lines:\n  " + "\n  ".join(errors)
    )


def test_shipped_lessons_target_a_live_phase():
    """Every shipped lesson's `phase` must name a phase that still exists —
    a lesson for a removed phase (e.g. the paper-era `write`) would never
    be rendered, and its content would leak removed guidance."""
    from omicairn.constants import KNOWN_STAGE_PHASES
    bad: list[str] = []
    for lessons_file in _shipped_lessons_files():
        for lineno, raw in enumerate(lessons_file.read_text().splitlines(), 1):
            if not raw.strip():
                continue
            phase = json.loads(raw).get("phase")
            if phase is not None and phase not in KNOWN_STAGE_PHASES:
                bad.append(f"{lessons_file.parents[1].name}/{lessons_file.name}"
                           f":{lineno} phase={phase!r}")
    assert not bad, "lessons target removed/unknown phases:\n  " + "\n  ".join(bad)


def test_no_pack_ships_paper_phase_files():
    for pack in _pack_dirs():
        for legacy in ("write", "claim_audit", "peer_review", "typeset"):
            assert not (pack / f"phase_{legacy}.md").exists(), (
                f"{pack.name} still ships phase_{legacy}.md")


def test_every_shipped_lessons_file_has_at_least_one_entry():
    """A lessons/*.jsonl file that ships with a pack but contains only
    blank lines or comments is drift — it either got emptied by a bad
    merge or was created as a placeholder and forgotten."""
    empty: list[str] = []
    for lessons_file in _shipped_lessons_files():
        nonblank = [
            line for line in lessons_file.read_text().splitlines()
            if line.strip()
        ]
        if not nonblank:
            empty.append(
                f"{lessons_file.parents[1].name}/{lessons_file.name}"
            )
    assert not empty, (
        "Shipped lessons files with no non-blank lines:\n  "
        + "\n  ".join(empty)
    )


@pytest.mark.parametrize(
    "lessons_file",
    _shipped_lessons_files(),
    ids=lambda p: f"{p.parents[1].name}/{p.name}",
)
def test_shipped_lessons_phase_field_is_a_real_phase(lessons_file):
    """Every lesson's `phase` field (when present) must resolve to a
    real LLM phase. `summarize_lessons` filters lessons by phase
    when building the `{{knowledge_summary}}` token for each phase's
    prompt — a lesson tagged `phase: "executee"` (typo) or
    `phase: "old_phase_name"` (post-rename) is silently unreachable.
    The lesson ships with the pack but never reaches any prompt."""
    bad: list[str] = []
    for lineno, raw in enumerate(lessons_file.read_text().splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            # Reported by the well-formed-JSONL test; skip here to
            # keep error messages targeted at one failure mode.
            continue
        if not isinstance(obj, dict):
            continue
        phase = obj.get("phase")
        if phase is None:
            continue  # phase is optional; a lesson may apply universally
        if phase not in CLAUDE_CODE_PHASES:
            bad.append(
                f"line {lineno}: phase={phase!r} not in CLAUDE_CODE_PHASES "
                f"({sorted(CLAUDE_CODE_PHASES)})"
            )
    assert not bad, (
        f"{lessons_file.parents[1].name}/{lessons_file.name} has "
        f"lessons whose `phase` field is not a real LLM phase — they "
        f"will never be surfaced by summarize_lessons:\n  "
        + "\n  ".join(bad)
    )


# ---------------------------------------------------------------------------
# profile.yaml key consumer drift
# ---------------------------------------------------------------------------
#
# profile.yaml is consumed in exactly two places in the runtime:
#
#   - `omicairn.domain_pack` reads the meta fields (`name`, `description`,
#     `matches`) — these are always consumed no matter what the pack
#     is for.
#   - `omicairn.cross_check` reads domain-specific tuning lists via
#     `profile.get("<key>")` — currently `baseline_name_hints` and
#     `ci_skip_paths`, but the set is open.
#
# Every non-meta key in a shipped profile.yaml MUST appear as a string
# literal somewhere in `omicairn/cross_check.py` or `omicairn/domain_pack.py`.
# Two drift shapes this catches, both currently invisible:
#
#   1. Typo in a profile.yaml key. `baseline_name_hint` (missing `s`)
#      or `ci_skip_path` (missing `s`) parses fine and the loader hands
#      the dict back unchanged. cross_check then calls
#      `profile.get("baseline_name_hints")` — which returns None — and
#      falls back to the generic defaults. The shipping pack silently
#      loses its baseline classification, and no existing test fires.
#
#   2. Renaming a key on the Python side without updating every
#      profile.yaml. The stale profile key is dead code: the YAML still
#      parses, the loader still hands it back, but nothing reads it.
#      Same silent failure mode as a typo.
#
# We deliberately do NOT check the reverse direction (every `.get(...)`
# in the Python code has at least one profile declaring the key),
# because it is legitimate for domain-specific keys to be defined on
# the cross_check side without any current pack using them — that's
# the "open set" shape. The forward direction is the one with drift.

# Meta fields that are always consumed by domain_pack loader code, not
# by cross_check. These don't need to appear in cross_check.py.
_PROFILE_META_KEYS: frozenset[str] = frozenset({
    "name",
    "description",
    "matches",
})

# Python modules that are allowed to be profile-key consumers. Keeping
# this list narrow means a new consumer forces a deliberate diff.
_PROFILE_CONSUMER_MODULES: tuple[str, ...] = (
    "omicairn/cross_check.py",
    "omicairn/domain_pack.py",
)


def test_every_profile_yaml_key_is_consumed_by_python():
    """Every non-meta key in any shipped profile.yaml must appear as
    a string literal in at least one of the Python modules listed in
    `_PROFILE_CONSUMER_MODULES`. A key that ships with no consumer is
    dead config — it was either typo'd or left behind after a rename —
    and the whole point of shipping it is silently lost."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    consumer_src = ""
    for rel in _PROFILE_CONSUMER_MODULES:
        consumer_src += (repo_root / rel).read_text() + "\n"

    errors: list[str] = []
    for pack in _pack_dirs():
        profile = domain_pack.load_profile(pack.name)
        for key in sorted(profile.keys()):
            if key in _PROFILE_META_KEYS:
                continue
            # Require the key to appear as a bare string literal
            # anywhere in the consumer source. We accept either
            # `"key"` or `'key'` quoting.
            if f'"{key}"' not in consumer_src and f"'{key}'" not in consumer_src:
                errors.append(
                    f"{pack.name}/profile.yaml declares {key!r} but "
                    f"no consumer in {list(_PROFILE_CONSUMER_MODULES)} "
                    f"references it as a string literal"
                )
    assert not errors, (
        "Domain-pack profile.yaml keys that no Python consumer reads "
        "(silent dead config — likely a typo or post-rename drift). "
        "Either (a) fix the key in profile.yaml to match what the "
        "consumer reads, (b) add a consumer that reads the new key, "
        "or (c) remove the key from profile.yaml:\n  "
        + "\n  ".join(errors)
    )
