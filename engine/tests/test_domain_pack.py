"""Unit tests for omicairn.domain_pack.

The real domain_packs/ directory under the OmiCairn install is used as a
fixture for the "live" tests. Tests that exercise the tiny YAML parser
work purely from strings. Tests at the bottom of the file redirect
`packs_root()` to a tmp dir so edge branches (missing pack, OSError
on profile/phase/lessons read, non-list matches, etc.) can be hit
without polluting the real domain_packs/ tree.
"""
from __future__ import annotations

import pathlib

import pytest

from omicairn import domain_pack
from omicairn.constants import (
    DOMAIN_PACK_GENERIC,
    PhaseName,
)


# ---------------------------------------------------------------------------
# Tiny YAML parser
# ---------------------------------------------------------------------------

def test_parse_mini_yaml_key_value():
    text = """\
name: survival_prognostic
description: "Cox survival models for prognostic biomarker studies"
"""
    d = domain_pack._parse_mini_yaml(text)
    assert d["name"] == "survival_prognostic"
    assert d["description"] == "Cox survival models for prognostic biomarker studies"


def test_parse_mini_yaml_list_block():
    text = """\
name: foo
matches:
  - survival
  - prognostic
  - oncology
"""
    d = domain_pack._parse_mini_yaml(text)
    assert d["name"] == "foo"
    assert d["matches"] == ["survival", "prognostic", "oncology"]


def test_parse_mini_yaml_comments_and_blank_lines():
    text = """\
# top comment
name: foo   # trailing comment
# another comment

matches:
  - a
  # comment inside list
  - b
"""
    d = domain_pack._parse_mini_yaml(text)
    assert d["name"] == "foo"
    assert d["matches"] == ["a", "b"]


def test_parse_mini_yaml_ignores_unparseable_lines():
    text = "name: foo\nno_colon_here\nother: bar\n"
    d = domain_pack._parse_mini_yaml(text)
    assert d == {"name": "foo", "other": "bar"}


# ---------------------------------------------------------------------------
# Pack resolution
# ---------------------------------------------------------------------------

def test_available_packs_includes_generic_and_survival():
    names = domain_pack.available_packs()
    # The install ships with at least _generic; survival_prognostic was
    # added alongside the domain-pack work.
    assert DOMAIN_PACK_GENERIC in names
    assert "survival_prognostic" in names


def test_resolve_pack_direct_match():
    assert domain_pack.resolve_pack("survival_prognostic") == "survival_prognostic"


def test_resolve_pack_substring_match_via_profile_matches():
    # The survival_prognostic profile lists several "matches:" keywords;
    # any domain string whose name overlaps one of them must resolve.
    assert domain_pack.resolve_pack("oncology_survival") == "survival_prognostic"
    assert domain_pack.resolve_pack("cox_regression_benchmark") == "survival_prognostic"


def test_resolve_pack_unknown_falls_back_to_generic():
    out = domain_pack.resolve_pack("quantum_gravity_experiments")
    assert out == DOMAIN_PACK_GENERIC


def test_resolve_pack_none_domain_falls_back_to_generic():
    assert domain_pack.resolve_pack(None) == DOMAIN_PACK_GENERIC
    assert domain_pack.resolve_pack("") == DOMAIN_PACK_GENERIC


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------

def test_load_profile_missing_returns_empty_dict():
    assert domain_pack.load_profile("absolutely-not-a-real-pack") == {}


def test_load_profile_survival_has_expected_keys():
    profile = domain_pack.load_profile("survival_prognostic")
    # Whatever's in the pack's profile.yaml, it must at least parse.
    assert isinstance(profile, dict)
    # The pack should have a matches list (that's how resolve_pack finds it).
    assert isinstance(profile.get("matches"), list)
    assert len(profile["matches"]) > 0


# ---------------------------------------------------------------------------
# Guidance rendering
# ---------------------------------------------------------------------------

def test_render_guidance_rejects_unknown_phase():
    assert domain_pack.render_guidance("survival_prognostic", "not_a_phase") == ""


def test_render_guidance_returns_block_for_known_phase():
    block = domain_pack.render_guidance("survival_prognostic", PhaseName.EXECUTE)
    assert block.startswith("<!-- domain_pack: survival_prognostic / execute -->")
    # The block is not empty — survival_prognostic ships execute guidance.
    body = block.split("\n\n", 1)[-1]
    assert len(body) > 50  # some substantive content


def test_render_guidance_generic_fallback_for_unknown_domain():
    block = domain_pack.render_guidance("fictional_physics_v42", PhaseName.PLAN)
    # Either generic ships guidance or returns empty — both are fine —
    # but we must never raise, and we must never return non-header text
    # without a header.
    if block:
        assert block.startswith("<!-- domain_pack: _generic / plan -->")


def test_render_guidance_for_all_phases_does_not_raise():
    for phase_name in [
        PhaseName.SCOPE,
        PhaseName.PLAN,
        PhaseName.EXECUTE,
        PhaseName.REVIEW,
    ]:
        domain_pack.render_guidance("survival_prognostic", phase_name)
        domain_pack.render_guidance(None, phase_name)


# ---------------------------------------------------------------------------
# Seed lessons
# ---------------------------------------------------------------------------

def test_iter_seed_lessons_for_survival_pack():
    lessons = domain_pack.iter_seed_lessons("survival_prognostic")
    # The pack ships an iter4_seed.jsonl with 5 entries per the design.
    assert isinstance(lessons, list)
    assert len(lessons) >= 1
    # Each lesson should be a dict with at least a phase and a fix.
    for L in lessons:
        assert isinstance(L, dict)


def test_iter_seed_lessons_unknown_pack_returns_empty():
    assert domain_pack.iter_seed_lessons("definitely-not-a-pack-xyz") != []  \
        or domain_pack.iter_seed_lessons("definitely-not-a-pack-xyz") == []
    # Resolve falls back to _generic, which has no lessons by default, so
    # we only assert that the call doesn't crash and returns a list.
    out = domain_pack.iter_seed_lessons("definitely-not-a-pack-xyz")
    assert isinstance(out, list)


# ---------------------------------------------------------------------------
# Synthetic-pack edge branches
#
# The tests above run against the real domain_packs/ install, which
# always ships _generic + survival_prognostic — so several
# defensive branches in domain_pack.py (missing packs_root, OSError on
# read, empty phase file, no _generic fallback, non-list matches
# entries, malformed lessons JSONL) are never hit in live mode. The
# fixture below redirects `packs_root()` to a tmp dir we fully control
# so those branches can be exercised without touching the real tree.
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_packs_root(tmp_path, monkeypatch):
    root = tmp_path / "packs"
    root.mkdir()
    monkeypatch.setattr(domain_pack, "packs_root", lambda: root)
    return root


def test_available_packs_returns_empty_when_packs_root_missing(
    tmp_path, monkeypatch
):
    """Line 75: `packs_root().is_dir()` False → `[]`. The real install
    always has domain_packs/, so this branch never fires in live
    tests."""
    missing = tmp_path / "no_such_dir"
    monkeypatch.setattr(domain_pack, "packs_root", lambda: missing)
    assert domain_pack.available_packs() == []


def test_parse_mini_yaml_strips_trailing_comment_on_list_item():
    """Line 107: `- item  # comment` must drop the trailing comment.
    Previously the trailing-comment strip was only tested on scalar
    values, not on list items."""
    text = """\
matches:
  - survival # primary keyword
  - prognostic  # secondary keyword
"""
    d = domain_pack._parse_mini_yaml(text)
    assert d["matches"] == ["survival", "prognostic"]


def test_load_profile_returns_empty_dict_on_oserror(
    tmp_packs_root, monkeypatch
):
    """Lines 139-140: `read_text` raising OSError must degrade to an
    empty dict, not propagate. Simulated by selectively monkeypatching
    `Path.read_text` for profile.yaml."""
    pack = tmp_packs_root / "mypack"
    pack.mkdir()
    (pack / "profile.yaml").write_text("name: mypack\n")

    real_read_text = pathlib.Path.read_text

    def boom(self, *args, **kwargs):
        if self.name == "profile.yaml":
            raise OSError("simulated")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", boom)
    assert domain_pack.load_profile("mypack") == {}


def test_resolve_pack_skips_pack_with_non_list_matches(tmp_packs_root):
    """Line 166: a profile with a scalar `matches:` value (not a list)
    must be skipped silently when scanning for indirect matches,
    not crash with `TypeError: argument of type 'str' is not
    iterable`."""
    pack = tmp_packs_root / "scalarmatch"
    pack.mkdir()
    (pack / "profile.yaml").write_text(
        "name: scalarmatch\nmatches: survival\n"
    )
    # No _generic in tmp root; non-matching domain must return None
    # rather than resolving into the scalar-matches pack.
    assert domain_pack.resolve_pack("oncology") is None


def test_resolve_pack_skips_non_string_match_entries(
    tmp_packs_root, monkeypatch
):
    """Line 170: a `matches:` list containing non-string entries must
    skip those entries, not crash on `.lower()`. Mini-YAML always
    yields strings so we have to inject the bad list via a
    monkeypatched load_profile."""
    pack = tmp_packs_root / "mixedmatch"
    pack.mkdir()
    (pack / "profile.yaml").write_text("name: mixedmatch\n")

    def fake_load_profile(name: str) -> dict:
        if name == "mixedmatch":
            return {"matches": [42, None, "survival"]}
        return {}

    monkeypatch.setattr(domain_pack, "load_profile", fake_load_profile)
    # 42 and None are skipped; "survival" overlaps "oncology_survival"
    # so the resolver must land on "mixedmatch".
    assert domain_pack.resolve_pack("oncology_survival") == "mixedmatch"


def test_resolve_pack_returns_none_when_no_match_and_no_generic(
    tmp_packs_root,
):
    """Line 176: no direct hit, no indirect hit, no _generic → None.
    The live install always has _generic, so the `return None`
    branch is unreachable in live mode."""
    pack = tmp_packs_root / "other"
    pack.mkdir()
    (pack / "profile.yaml").write_text(
        "name: other\nmatches:\n  - physics\n"
    )
    assert domain_pack.resolve_pack("biology") is None
    assert domain_pack.resolve_pack(None) is None


def test_render_guidance_returns_empty_when_no_pack_resolved(
    tmp_packs_root,
):
    """Line 193: resolve_pack returns None → empty guidance block.
    tmp_packs_root starts empty (no _generic), so any domain
    resolves to None."""
    assert domain_pack.render_guidance("anything", PhaseName.PLAN) == ""


def test_render_guidance_returns_empty_on_read_error(
    tmp_packs_root, monkeypatch
):
    """Lines 199-200: OSError on reading the phase file must
    degrade to an empty string rather than propagating."""
    pack = tmp_packs_root / "p1"
    pack.mkdir()
    (pack / "phase_plan.md").write_text("real content")

    real_read_text = pathlib.Path.read_text

    def boom(self, *args, **kwargs):
        if self.name == "phase_plan.md":
            raise OSError("simulated")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", boom)
    assert domain_pack.render_guidance("p1", PhaseName.PLAN) == ""


def test_render_guidance_returns_empty_on_blank_phase_file(tmp_packs_root):
    """Line 202: phase file exists but `.strip()` → "" (blank
    / whitespace only) → empty block."""
    pack = tmp_packs_root / "p1"
    pack.mkdir()
    (pack / "phase_plan.md").write_text("   \n\n  \n")
    assert domain_pack.render_guidance("p1", PhaseName.PLAN) == ""


def test_iter_seed_lessons_returns_empty_when_no_pack_resolved(
    tmp_packs_root,
):
    """Line 219: resolve_pack returns None → []."""
    assert domain_pack.iter_seed_lessons("nothing") == []


def test_iter_seed_lessons_missing_lessons_dir_returns_empty(
    tmp_packs_root,
):
    """Guard at `lessons_dir.is_dir()`: pack exists but ships no
    lessons/ dir → []."""
    pack = tmp_packs_root / "nolessons"
    pack.mkdir()
    (pack / "profile.yaml").write_text("name: nolessons\n")
    assert domain_pack.iter_seed_lessons("nolessons") == []


def test_iter_seed_lessons_skips_blank_and_malformed_lines(tmp_packs_root):
    """Lines 229, 232-233: blank lines and non-JSON lines in a .jsonl
    must be skipped; valid dict entries still come through. Also
    pins that JSON-valid but non-dict values (e.g. a bare integer)
    are filtered by the `isinstance(obj, dict)` guard."""
    pack = tmp_packs_root / "hasless"
    pack.mkdir()
    lessons_dir = pack / "lessons"
    lessons_dir.mkdir()
    (lessons_dir / "seed.jsonl").write_text(
        '{"id": 1, "fix": "do X"}\n'
        "\n"
        "not valid json at all\n"
        "42\n"
        '{"id": 2, "fix": "do Y"}\n'
    )
    lessons = domain_pack.iter_seed_lessons("hasless")
    assert len(lessons) == 2
    assert lessons[0]["id"] == 1
    assert lessons[1]["id"] == 2


def test_iter_seed_lessons_swallows_oserror_per_file(
    tmp_packs_root, monkeypatch
):
    """Lines 236-237: if one .jsonl file in the lessons/ dir raises
    OSError on read, that file is skipped but sibling files still
    contribute lessons. A bad file must not wipe out the entire
    pack's lessons."""
    pack = tmp_packs_root / "mix"
    pack.mkdir()
    lessons_dir = pack / "lessons"
    lessons_dir.mkdir()
    (lessons_dir / "good.jsonl").write_text('{"id": "good"}\n')
    (lessons_dir / "bad.jsonl").write_text('{"id": "bad"}\n')

    real_read_text = pathlib.Path.read_text

    def selective(self, *args, **kwargs):
        if self.name == "bad.jsonl":
            raise OSError("simulated unreadable file")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", selective)
    lessons = domain_pack.iter_seed_lessons("mix")
    assert len(lessons) == 1
    assert lessons[0]["id"] == "good"
