"""Tests for omicairn.user_notes — append, render, and --once consumption."""
from __future__ import annotations

import pathlib

import pytest

from omicairn import user_notes
from omicairn.constants import KNOWLEDGE_DIR, PIPELINE_ORDER


def test_append_and_render_sticky(tmp_path: pathlib.Path) -> None:
    user_notes.append_note(tmp_path, "use concordance-index, not AUC", phase="plan")
    rendered = user_notes.render_notes_for_phase(tmp_path, "plan")
    assert "concordance-index" in rendered
    assert "scope=sticky" in rendered
    # Phase scoping: other phases do not see this note.
    other = user_notes.render_notes_for_phase(tmp_path, "execute")
    assert "(none)" in other
    assert "concordance-index" not in other


def test_render_any_phase_visible_everywhere(tmp_path: pathlib.Path) -> None:
    user_notes.append_note(tmp_path, "use the held-out cohort for validation", phase=None)
    for phase in PIPELINE_ORDER:
        rendered = user_notes.render_notes_for_phase(tmp_path, phase)
        assert "held-out cohort" in rendered


def test_render_none_when_empty(tmp_path: pathlib.Path) -> None:
    rendered = user_notes.render_notes_for_phase(tmp_path, "plan")
    assert "(none)" in rendered
    # The whole section (heading + preamble) is rendered even when
    # there are no notes — the prompts reference a single
    # `{{user_notes}}` token that supplies its own heading.
    assert rendered.startswith("## User notes for this phase")
    assert "treat those notes" in rendered


def test_unknown_phase_rejected(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ValueError):
        user_notes.append_note(tmp_path, "bad", phase="wrtie")


def test_once_note_consumed_on_matching_phase(tmp_path: pathlib.Path) -> None:
    user_notes.append_note(tmp_path, "please lower alpha to 0.01", phase="execute", once=True)
    # Present before consumption.
    before = user_notes.render_notes_for_phase(tmp_path, "execute")
    assert "lower alpha" in before
    # Consume after execute completes.
    moved = user_notes.consume_once_notes(tmp_path, "execute")
    assert moved == 1
    after = user_notes.render_notes_for_phase(tmp_path, "execute")
    assert "(none)" in after and "lower alpha" not in after
    consumed = (tmp_path / KNOWLEDGE_DIR / "user_notes_consumed.md").read_text()
    assert "lower alpha" in consumed
    assert "consumed" in consumed


def test_sticky_note_survives_consume(tmp_path: pathlib.Path) -> None:
    user_notes.append_note(tmp_path, "stick around", phase="execute", once=False)
    moved = user_notes.consume_once_notes(tmp_path, "execute")
    assert moved == 0
    rendered = user_notes.render_notes_for_phase(tmp_path, "execute")
    assert "stick around" in rendered


def test_once_note_with_any_phase_consumed_on_first_matching(tmp_path: pathlib.Path) -> None:
    user_notes.append_note(tmp_path, "global one-shot", phase=None, once=True)
    # First consume on plan should pick it up.
    moved = user_notes.consume_once_notes(tmp_path, "plan")
    assert moved == 1
    # Gone everywhere afterward.
    for phase in PIPELINE_ORDER:
        rendered = user_notes.render_notes_for_phase(tmp_path, phase)
        assert "(none)" in rendered
        assert "global one-shot" not in rendered


def test_status_lines_summarises(tmp_path: pathlib.Path) -> None:
    assert user_notes.status_lines(tmp_path) == []
    user_notes.append_note(tmp_path, "hello", phase="plan")
    user_notes.append_note(tmp_path, "world", phase=None, once=True)
    lines = user_notes.status_lines(tmp_path)
    assert len(lines) == 2
    assert any("plan" in l and "sticky" in l for l in lines)
    assert any("any" in l and "once" in l for l in lines)


def test_append_multiple_notes_preserves_order(tmp_path: pathlib.Path) -> None:
    user_notes.append_note(tmp_path, "first", phase="plan")
    user_notes.append_note(tmp_path, "second", phase="plan")
    rendered = user_notes.render_notes_for_phase(tmp_path, "plan")
    assert rendered.index("first") < rendered.index("second")
