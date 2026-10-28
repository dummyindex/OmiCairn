"""Stage-directory helpers.

A *stage* is one numbered directory under a workspace like `007_execute`.
Stages are the atomic unit of pipeline execution: each stage is produced
exactly once and never overwritten. Re-attempts create a new numbered
stage, so the workspace is an immutable append-only log of attempts.

Example workspace layout::

    workspaces/example-topic-20260410-190638/
    ├── STATE.md
    ├── events.jsonl
    ├── knowledge/
    │   ├── journal.md
    │   ├── lessons.jsonl
    │   └── domain_notes.md
    ├── 000_scope/
    │   ├── goal.md
    │   ├── data_profile.md
    │   ├── summary.md
    │   └── done.flag
    ├── 001_plan/
    ├── 002_execute/
    ├── 003_cross_check/
    │   ├── verdict.json
    │   ├── findings.md
    │   └── done.flag
    └── 004_execute/          # triggered by 003's verdict

All string formats come from `constants.py` (no literal `"scope"` etc.).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import pathlib
from typing import Any, Iterable

from .constants import (
    DONE_FLAG,
    EVENTS_LOG,
    JOURNAL_FILE,
    KNOWLEDGE_DIR,
    LESSONS_FILE,
    NON_PROGRESS_EVENT_KINDS,
    PIPELINE_ORDER,
    PRIOR_STAGES_FILE,
    STAGE_INDEX_SEP,
    STAGE_INDEX_WIDTH,
    SUMMARY_FILE,
    parse_stage_dir_name,
    stage_dir_name,
)


@dataclasses.dataclass(frozen=True)
class StageRef:
    """A resolved stage: its numeric index, phase name, and path.

    `path` is the absolute directory. `rel_path` is relative to the
    workspace root — useful for rendering into prompts without leaking
    absolute paths.
    """
    index: int
    phase: str
    path: pathlib.Path
    rel_path: str

    @property
    def name(self) -> str:
        return stage_dir_name(self.index, self.phase)

    @property
    def done(self) -> bool:
        return (self.path / DONE_FLAG).exists()

    def summary(self) -> str:
        """Return the summary.md contents or an empty string."""
        p = self.path / SUMMARY_FILE
        if not p.exists():
            return ""
        try:
            return p.read_text().strip()
        except OSError:
            return ""


# ---------------------------------------------------------------------------
# Stage enumeration
# ---------------------------------------------------------------------------

def list_stages(workspace_root: pathlib.Path) -> list[StageRef]:
    """Return all stage directories in index order.

    Ignores any top-level directory that does not match the stage-dir
    format (knowledge/, logs/, literature/, .claude/, ...).
    """
    out: list[StageRef] = []
    for child in sorted(workspace_root.iterdir()):
        if not child.is_dir():
            continue
        parsed = parse_stage_dir_name(child.name)
        if parsed is None:
            continue
        idx, phase = parsed
        out.append(
            StageRef(
                index=idx,
                phase=phase,
                path=child,
                rel_path=child.name,
            )
        )
    out.sort(key=lambda s: s.index)
    return out


def latest_stage_of(
    workspace_root: pathlib.Path, phase: str
) -> StageRef | None:
    """The most recent *done* stage of the given phase, or None."""
    if phase not in PIPELINE_ORDER:
        raise ValueError(f"unknown phase {phase!r}")
    matches = [s for s in list_stages(workspace_root) if s.phase == phase and s.done]
    if not matches:
        return None
    return matches[-1]


def next_stage_index(workspace_root: pathlib.Path) -> int:
    """The next unused stage index for this workspace (max existing + 1).

    Counts EVERY numbered stage dir on disk (``NNN_<anything>``), not just
    the pipeline-valid phases `list_stages` returns. Standalone phases
    like IDEATE and FREE are intentionally absent from PIPELINE_ORDER, so
    `parse_stage_dir_name` (and thus `list_stages`) skips their dirs — but
    their `NNN_ideate` / `NNN_free` dirs still occupy an index on disk.
    Indexing off `list_stages` alone made a second `omicairn ideate` collide
    on `000_ideate` (FileExistsError, audit H14). We therefore scan raw
    directory names for the index, independent of phase validity.
    """
    max_idx = -1
    if workspace_root.is_dir():
        for child in workspace_root.iterdir():
            if not child.is_dir():
                continue
            name = child.name
            if len(name) <= STAGE_INDEX_WIDTH:
                continue
            prefix = name[:STAGE_INDEX_WIDTH]
            if prefix.isdigit() and name[STAGE_INDEX_WIDTH] == STAGE_INDEX_SEP:
                max_idx = max(max_idx, int(prefix))
    return max_idx + 1


def create_stage_dir(
    workspace_root: pathlib.Path, index: int, phase: str
) -> pathlib.Path:
    """Create and return an empty stage directory."""
    name = stage_dir_name(index, phase)
    path = workspace_root / name
    if path.exists():
        raise FileExistsError(f"stage directory {path} already exists")
    path.mkdir(parents=True, exist_ok=False)
    return path


# ---------------------------------------------------------------------------
# Stage listing rendering (for {{stage_listing}} token)
# ---------------------------------------------------------------------------

def render_stage_listing(workspace_root: pathlib.Path) -> str:
    """Render the prior-stage table used in phase prompts.

    Format (plain text, one line per stage)::

        000_scope      scope        done   "Defined question, 2-cohort design"
        001_plan       plan         done   "4 hypotheses, primary cohort design"
        002_execute    execute      done   "Ran primary analysis; H1 failed"

    The last column is the stage's `summary.md` clipped to one line
    (agents can read the full summary by opening the file).
    """
    stages = list_stages(workspace_root)
    if not stages:
        return "(no prior stages — this is the first stage in the workspace)"

    lines: list[str] = []
    for s in stages:
        summary = s.summary().splitlines()
        one_line = summary[0].strip() if summary else ""
        status = "done" if s.done else "incomplete"
        lines.append(
            f"  {s.rel_path:<18} {s.phase:<12} {status:<10} \"{one_line[:80]}\""
        )
    return "\n".join(lines)


def latest_dir_tokens(workspace_root: pathlib.Path) -> dict[str, str]:
    """Build the `latest_<phase>_dir` token map for _render_prompt.

    Any phase with no done stage yet returns "(none yet)" so prompts
    degrade cleanly.
    """
    out: dict[str, str] = {}
    for phase in PIPELINE_ORDER:
        from .constants import latest_stage_token  # avoid circular refs
        tok = latest_stage_token(phase)
        latest = latest_stage_of(workspace_root, phase)
        out[tok] = latest.rel_path if latest else "(none yet)"
    return out


# ---------------------------------------------------------------------------
# Events log
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def append_event(workspace_root: pathlib.Path, event: dict[str, Any]) -> None:
    """Append a JSON-line event to events.jsonl.

    Every event must have a `ts` and `kind` field. `ts` is auto-filled
    if absent; `kind` is REQUIRED and raises ValueError if missing or
    empty. The kind check is load-bearing: post-hoc inspection tools
    (`omicairn status`, events timelines) key off `kind` to classify
    records, and a kindless event silently breaks them all.
    """
    if not event.get("kind"):
        raise ValueError(
            "append_event requires a non-empty 'kind' field; got "
            f"event keys={sorted(event.keys())!r}"
        )
    path = workspace_root / EVENTS_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(event)
    record.setdefault("ts", _now_iso())
    with path.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# How many bytes of events.jsonl tail to read when hunting the newest
# NON-beacon event. The beacon cadence (~75s) means even a multi-hour stage
# appends only a few hundred lines; a NON-beacon event from a still-running
# stage (e.g. a stage_done / floor-applied / gate event) is always within the
# last few KB. Bounding the read keeps the probe O(cheap) even on a finished
# run's large events.jsonl and never reads the whole file.
_EVENTS_TAIL_BYTES: int = 65_536


def newest_non_beacon_event_mtime(workspace_root: pathlib.Path) -> float | None:
    """Newest POSIX timestamp of a NON-beacon event in events.jsonl, or None.

    The liveness/stall machinery (fleet ``last_activity_age_s``) counts
    ``events.jsonl``'s file mtime as activity — but the inactivity-watchdog's
    periodic ``EXECUTE_PROGRESS`` beacon (claude_runner) re-touches that file
    every ~75s EVEN WHEN the stage's inner agent is frozen. So a wedged stage
    masks its own stall: the run keeps looking "active" (the
    beacon-masks-watchdog bug, audit 2026-06-17). This helper lets the
    activity computation key on the newest event whose ``kind`` is NOT a
    timer-emitted observability beacon (``NON_PROGRESS_EVENT_KINDS``), so a
    beacon-only tail does NOT count as progress.

    Returns the parsed UTC timestamp (epoch seconds) of the newest qualifying
    record, or None when there is no events.jsonl, it is unreadable/empty, or
    it contains ONLY beacon events. Best-effort and exception-safe: any parse
    error on a line is skipped, and a wholesale read failure returns None
    (callers fall back to the file mtime, i.e. degrade to the old behaviour
    — never crash a status sweep).
    """
    path = workspace_root / EVENTS_LOG
    try:
        size = path.stat().st_size
    except OSError:
        return None
    try:
        with path.open("rb") as f:
            if size > _EVENTS_TAIL_BYTES:
                f.seek(-_EVENTS_TAIL_BYTES, 2)
            chunk = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    newest: float | None = None
    # Walk newest→oldest; the first non-beacon line we successfully parse is
    # the freshest by append order, so we can stop early.
    for line in reversed(chunk.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            # A truncated leading line (we may have sliced mid-record on the
            # tail seek) or a malformed line — skip it.
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("kind") in NON_PROGRESS_EVENT_KINDS:
            continue
        ts_raw = rec.get("ts")
        if not isinstance(ts_raw, str) or not ts_raw:
            continue
        try:
            parsed = dt.datetime.fromisoformat(ts_raw)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        newest = parsed.timestamp()
        break
    return newest


# ---------------------------------------------------------------------------
# Knowledge journal
# ---------------------------------------------------------------------------

def knowledge_dir(workspace_root: pathlib.Path) -> pathlib.Path:
    return workspace_root / KNOWLEDGE_DIR


def ensure_knowledge_scaffold(workspace_root: pathlib.Path) -> None:
    """Create the knowledge/ directory with empty seed files if missing."""
    kdir = knowledge_dir(workspace_root)
    kdir.mkdir(parents=True, exist_ok=True)
    lessons = kdir / LESSONS_FILE
    if not lessons.exists():
        lessons.write_text("")  # empty JSONL
    journal = kdir / JOURNAL_FILE
    if not journal.exists():
        journal.write_text(
            "# Project knowledge journal\n\n"
            "This is the running narrative of the research project. "
            "Each stage appends what it learned, what surprised it, "
            "and how it decided to proceed. Agents read this to stay "
            "coherent across attempts.\n"
        )
    from .constants import DOMAIN_NOTES_FILE
    notes = kdir / DOMAIN_NOTES_FILE
    if not notes.exists():
        notes.write_text(
            "# Project-specific gotchas\n\n"
            "Append any domain-specific quirks discovered during this "
            "project: data peculiarities, API incompatibilities, "
            "library version issues, non-obvious formulas.\n"
        )


def lessons_path(workspace_root: pathlib.Path) -> pathlib.Path:
    return knowledge_dir(workspace_root) / LESSONS_FILE


def read_lessons(workspace_root: pathlib.Path) -> list[dict[str, Any]]:
    path = lessons_path(workspace_root)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def summarize_lessons(
    workspace_root: pathlib.Path, for_phase: str | None = None
) -> str:
    """Render a short summary of lessons, optionally filtered by phase.

    Used to inject `{{knowledge_summary}}` into the current phase's prompt.
    A phase filter of None includes all lessons. Lessons with
    `phase == for_phase` OR `applies_to` mentioning the phase are kept.
    """
    lessons = read_lessons(workspace_root)
    if not lessons:
        return "(no lessons recorded yet for this workspace)"

    def relevant(L: dict[str, Any]) -> bool:
        if for_phase is None:
            return True
        if L.get("phase") == for_phase:
            return True
        applies = L.get("applies_to") or []
        if isinstance(applies, list) and any(
            isinstance(a, str) and for_phase in a for a in applies
        ):
            return True
        return False

    out_lines: list[str] = []
    for L in lessons:
        if not relevant(L):
            continue
        lid = L.get("id", "L?")
        phase = L.get("phase", "?")
        task = L.get("task", "")
        root = L.get("root_cause", "")
        fix = L.get("fix", "")
        header = f"- {lid} [{phase}/{task}]" if task else f"- {lid} [{phase}]"
        body = f"{root} — FIX: {fix}" if fix else root
        if body:
            out_lines.append(f"{header}: {body}"[:300])
        else:
            out_lines.append(header)
    if not out_lines:
        return "(no lessons relevant to this phase yet)"
    return "\n".join(out_lines)


def append_lesson(
    workspace_root: pathlib.Path, lesson: dict[str, Any]
) -> None:
    """Append a lesson entry; used by the test suite and the orchestrator."""
    path = lessons_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(lesson)
    record.setdefault("created", _now_iso())
    with path.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Prior-stages helper file written by subagents
# ---------------------------------------------------------------------------

def prior_stages_path(stage_dir: pathlib.Path) -> pathlib.Path:
    return stage_dir / PRIOR_STAGES_FILE


def iter_stage_artifacts(stage_dir: pathlib.Path) -> Iterable[pathlib.Path]:
    """Yield every file under a stage directory (useful for inspect CLI)."""
    if not stage_dir.exists():
        return
    for p in sorted(stage_dir.rglob("*")):
        if p.is_file():
            yield p
