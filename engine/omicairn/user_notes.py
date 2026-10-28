"""User-authored free-form notes injected into phase prompts.

Users leave notes via ``omicairn note <workspace> "<message>" [--phase ...]
[--once]``. Each note becomes a block in ``knowledge/user_notes.md``.
At prompt-render time the orchestrator filters the notes by phase scope
and substitutes them into the ``{{user_notes}}`` token. After a phase
completes successfully, ``--once`` notes scoped to that phase are moved
to ``knowledge/user_notes_consumed.md`` so they don't repeat.

Block format (plain markdown; one block per note)::

    ## 2026-04-12T14:32:01 — user — phase=execute — scope=sticky
    <free-form body>

``phase=<phase>`` may be the literal ``any``. ``scope`` is either
``sticky`` (default) or ``once``. No YAML — easy for users to hand-edit
and easy for the orchestrator to re-serialize when consuming entries.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import pathlib
import re
from typing import Iterable

from .constants import (
    KNOWLEDGE_DIR,
    PhaseName,
    PIPELINE_ORDER,
    USER_NOTES_CONSUMED_FILE,
    USER_NOTES_FILE,
)

# Phases a user note may target: the linear pipeline plus the standalone
# IDEATE front-stage (so `omicairn note --phase ideate` validates).
_NOTE_TARGET_PHASES: tuple[str, ...] = (*PIPELINE_ORDER, PhaseName.IDEATE)


_ANY_PHASE: str = "any"
_SCOPE_STICKY: str = "sticky"
_SCOPE_ONCE: str = "once"

# Shared prompt boilerplate. Lives here (single source of truth) rather
# than in each of the 6 phase prompts — the per-prompt alternative
# violated the "no duplicated string constants" project rule.
_SECTION_HEADING: str = "## User notes for this phase"
_AUTHORITATIVE_PREAMBLE: str = (
    "If the block above is anything other than `(none)`, treat those notes\n"
    "as authoritative: they were written by the user between runs and\n"
    "override default choices where they conflict. If a note contradicts\n"
    "RESEARCH.md or the domain pack, prefer the note AND flag the conflict\n"
    "in your stage summary so the user can reconcile it."
)

_HEADER_RE = re.compile(
    r"^## (?P<ts>[0-9T:\-]+) — user — phase=(?P<phase>[a-z_]+) "
    r"— scope=(?P<scope>sticky|once)\s*$",
    re.MULTILINE,
)


@dataclasses.dataclass
class Note:
    timestamp: str
    phase: str            # phase name or "any"
    scope: str            # "sticky" or "once"
    body: str             # free-form markdown, no leading/trailing blanks

    def applies_to(self, phase_name: str) -> bool:
        return self.phase == _ANY_PHASE or self.phase == phase_name

    def render(self) -> str:
        return (
            f"## {self.timestamp} — user — "
            f"phase={self.phase} — scope={self.scope}\n\n"
            f"{self.body.rstrip()}\n"
        )


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def notes_path(ws_root: pathlib.Path) -> pathlib.Path:
    return ws_root / KNOWLEDGE_DIR / USER_NOTES_FILE


def consumed_path(ws_root: pathlib.Path) -> pathlib.Path:
    return ws_root / KNOWLEDGE_DIR / USER_NOTES_CONSUMED_FILE


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse(text: str) -> list[Note]:
    """Parse a user_notes.md body into a list of Note objects.

    Unparseable blocks (missing header, malformed scope) are dropped
    silently — the file is user-editable so we prefer robustness to
    strictness.
    """
    matches = list(_HEADER_RE.finditer(text))
    if not matches:
        return []
    notes: list[Note] = []
    for i, m in enumerate(matches):
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip("\n")
        notes.append(Note(
            timestamp=m.group("ts"),
            phase=m.group("phase"),
            scope=m.group("scope"),
            body=body,
        ))
    return notes


def _serialize(notes: Iterable[Note]) -> str:
    blocks = [n.render() for n in notes]
    if not blocks:
        return ""
    return "\n".join(blocks).rstrip() + "\n"


def load_notes(ws_root: pathlib.Path) -> list[Note]:
    path = notes_path(ws_root)
    if not path.is_file():
        return []
    return _parse(path.read_text())


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------

def append_note(
    ws_root: pathlib.Path,
    body: str,
    phase: str | None = None,
    once: bool = False,
) -> Note:
    """Append a new user note to ``knowledge/user_notes.md``.

    ``phase`` may be None (applies to all phases) or a known phase name.
    An unknown phase raises ValueError so a CLI typo (``--phase exectue``)
    fails loudly rather than silently creating an unapplied note.
    """
    if phase is not None and phase not in _NOTE_TARGET_PHASES:
        raise ValueError(
            f"unknown phase {phase!r}; must be one of {list(_NOTE_TARGET_PHASES)} "
            f"or None"
        )
    ts = dt.datetime.now().replace(microsecond=0).isoformat()
    note = Note(
        timestamp=ts,
        phase=phase or _ANY_PHASE,
        scope=_SCOPE_ONCE if once else _SCOPE_STICKY,
        body=body.strip(),
    )
    path = notes_path(ws_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.is_file() else ""
    new_text = existing.rstrip() + ("\n\n" if existing.strip() else "") + note.render()
    path.write_text(new_text)
    return note


def _render_notes_body(ws_root: pathlib.Path, phase_name: str) -> str:
    """Inner notes-body renderer. Returns ``"(none)"`` when empty, or a
    stack of ``### <ts> — scope=<tag>`` blocks otherwise.
    """
    notes = [n for n in load_notes(ws_root) if n.applies_to(phase_name)]
    if not notes:
        return "(none)"
    lines = []
    for n in notes:
        tag = "once" if n.scope == _SCOPE_ONCE else "sticky"
        lines.append(f"### {n.timestamp} — scope={tag}\n\n{n.body.rstrip()}\n")
    return "\n".join(lines).rstrip() + "\n"


def render_notes_for_phase(ws_root: pathlib.Path, phase_name: str) -> str:
    """Return the complete markdown section injected into ``{{user_notes}}``.

    Includes the ``## User notes for this phase`` heading, the notes
    body (``"(none)"`` when empty), and the authoritative-override
    preamble — the whole block is rendered here so phase prompts can
    reduce to a single ``{{user_notes}}`` token, eliminating the
    six-way duplicated boilerplate.
    """
    body = _render_notes_body(ws_root, phase_name)
    return (
        f"{_SECTION_HEADING}\n\n"
        f"{body.rstrip()}\n\n"
        f"{_AUTHORITATIVE_PREAMBLE}\n"
    )


def consume_once_notes(ws_root: pathlib.Path, phase_name: str) -> int:
    """Move ``--once`` notes applicable to ``phase_name`` into the
    consumed log. Returns the number of notes moved.

    Called by the orchestrator after a phase completes successfully.
    Notes with scope=sticky (default) are left untouched. Notes with
    phase=any AND scope=once are consumed on the first successful phase
    they apply to, which is deliberate — the user said "once", so they
    mean once.
    """
    notes = load_notes(ws_root)
    if not notes:
        return 0
    kept: list[Note] = []
    consumed: list[Note] = []
    for n in notes:
        if n.scope == _SCOPE_ONCE and n.applies_to(phase_name):
            consumed.append(n)
        else:
            kept.append(n)
    if not consumed:
        return 0

    # Rewrite notes file with survivors.
    path = notes_path(ws_root)
    path.write_text(_serialize(kept))

    # Append consumed entries to the consumed log with a timestamped
    # "consumed at phase=X" trailer.
    cpath = consumed_path(ws_root)
    cpath.parent.mkdir(parents=True, exist_ok=True)
    existing = cpath.read_text() if cpath.is_file() else ""
    ts = dt.datetime.now().replace(microsecond=0).isoformat()
    trailer = f"<!-- consumed {ts} by phase={phase_name} -->\n"
    blocks = [n.render() + trailer for n in consumed]
    new_text = (
        existing.rstrip()
        + ("\n\n" if existing.strip() else "")
        + "\n".join(blocks).rstrip()
        + "\n"
    )
    cpath.write_text(new_text)
    return len(consumed)


# ---------------------------------------------------------------------------
# Status rendering (used by `omicairn status`)
# ---------------------------------------------------------------------------

def status_lines(ws_root: pathlib.Path) -> list[str]:
    """One-line-per-note status summary. Empty list when no notes."""
    notes = load_notes(ws_root)
    if not notes:
        return []
    out: list[str] = []
    for n in notes:
        snippet = n.body.replace("\n", " ").strip()
        if len(snippet) > 64:
            snippet = snippet[:61] + "..."
        tag = "once" if n.scope == _SCOPE_ONCE else "sticky"
        out.append(
            f"  [{tag:<6}] {n.phase:<11} — {n.timestamp} — {snippet}"
        )
    cpath = consumed_path(ws_root)
    if cpath.is_file() and cpath.read_text().strip():
        consumed_n = len(_HEADER_RE.findall(cpath.read_text()))
        out.append(f"  (consumed: {consumed_n}, see knowledge/{USER_NOTES_CONSUMED_FILE})")
    return out
