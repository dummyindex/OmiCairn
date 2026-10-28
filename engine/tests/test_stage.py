"""Unit tests for omicairn.stage (list/enumerate/knowledge-journal helpers)."""
from __future__ import annotations

import json
import pathlib
import tempfile

from omicairn import stage
from omicairn.constants import (
    DONE_FLAG,
    KNOWLEDGE_DIR,
    LESSONS_FILE,
    PIPELINE_ORDER,
    PhaseName,
    SUMMARY_FILE,
)


def _fresh_workspace() -> pathlib.Path:
    return pathlib.Path(tempfile.mkdtemp())


def _make_stage(
    root: pathlib.Path, idx: int, phase: str, *, done: bool, summary: str = ""
) -> pathlib.Path:
    path = stage.create_stage_dir(root, idx, phase)
    if summary:
        (path / SUMMARY_FILE).write_text(summary)
    if done:
        (path / DONE_FLAG).write_text("1\n")
    return path


# ---------------------------------------------------------------------------
# Stage enumeration
# ---------------------------------------------------------------------------

def test_list_stages_empty_workspace():
    ws = _fresh_workspace()
    assert stage.list_stages(ws) == []
    assert stage.next_stage_index(ws) == 0


def test_list_stages_sorted_by_index_and_ignores_non_stage_dirs():
    ws = _fresh_workspace()
    # Create some non-stage directories that should be ignored.
    (ws / "knowledge").mkdir()
    (ws / "literature").mkdir()
    (ws / ".claude").mkdir()
    # And real stages, deliberately out of natural order.
    _make_stage(ws, 2, PhaseName.EXECUTE, done=True)
    _make_stage(ws, 0, PhaseName.SCOPE, done=True)
    _make_stage(ws, 1, PhaseName.PLAN, done=True)
    stages = stage.list_stages(ws)
    assert [s.index for s in stages] == [0, 1, 2]
    assert [s.phase for s in stages] == [
        PhaseName.SCOPE,
        PhaseName.PLAN,
        PhaseName.EXECUTE,
    ]
    assert all(s.done for s in stages)


def test_next_stage_index_after_existing_stages():
    ws = _fresh_workspace()
    _make_stage(ws, 0, PhaseName.SCOPE, done=True)
    _make_stage(ws, 1, PhaseName.PLAN, done=True)
    _make_stage(ws, 2, PhaseName.EXECUTE, done=False)  # in progress
    assert stage.next_stage_index(ws) == 3


def test_latest_stage_of_returns_most_recent_done():
    ws = _fresh_workspace()
    _make_stage(ws, 0, PhaseName.EXECUTE, done=True, summary="first attempt")
    _make_stage(ws, 1, PhaseName.CROSS_CHECK, done=True)
    _make_stage(ws, 2, PhaseName.EXECUTE, done=True, summary="second attempt")
    _make_stage(ws, 3, PhaseName.EXECUTE, done=False)  # incomplete
    latest = stage.latest_stage_of(ws, PhaseName.EXECUTE)
    assert latest is not None
    assert latest.index == 2
    assert "second" in latest.summary()


def test_latest_stage_of_skips_incomplete_stages():
    ws = _fresh_workspace()
    _make_stage(ws, 0, PhaseName.SCOPE, done=False)
    assert stage.latest_stage_of(ws, PhaseName.SCOPE) is None


def test_latest_stage_of_rejects_unknown_phase():
    ws = _fresh_workspace()
    try:
        stage.latest_stage_of(ws, "not_a_phase")
    except ValueError as e:
        assert "unknown phase" in str(e)
    else:
        raise AssertionError("expected ValueError for unknown phase")


def test_create_stage_dir_refuses_duplicate():
    ws = _fresh_workspace()
    stage.create_stage_dir(ws, 0, PhaseName.SCOPE)
    try:
        stage.create_stage_dir(ws, 0, PhaseName.SCOPE)
    except FileExistsError:
        pass
    else:
        raise AssertionError("expected FileExistsError for duplicate stage")


# ---------------------------------------------------------------------------
# Stage listing / latest-dir tokens
# ---------------------------------------------------------------------------

def test_render_stage_listing_includes_summary_first_line():
    ws = _fresh_workspace()
    _make_stage(
        ws, 0, PhaseName.SCOPE, done=True,
        summary="Data profiled, 2-cohort design\nsecond line ignored",
    )
    _make_stage(ws, 1, PhaseName.PLAN, done=True, summary="3 hypotheses")
    listing = stage.render_stage_listing(ws)
    assert "000_scope" in listing
    assert "001_plan" in listing
    assert "Data profiled" in listing
    assert "second line ignored" not in listing
    assert "3 hypotheses" in listing


def test_render_stage_listing_empty_workspace():
    ws = _fresh_workspace()
    listing = stage.render_stage_listing(ws)
    assert "no prior stages" in listing


def test_latest_dir_tokens_populates_every_phase():
    ws = _fresh_workspace()
    _make_stage(ws, 0, PhaseName.SCOPE, done=True)
    tokens = stage.latest_dir_tokens(ws)
    # Every phase in the pipeline gets a token.
    for phase_name in PIPELINE_ORDER:
        assert f"latest_{phase_name}_dir" in tokens
    assert tokens["latest_scope_dir"] == "000_scope"
    # Phases with no stage yet fall back to a safe placeholder.
    assert tokens["latest_plan_dir"] == "(none yet)"
    assert tokens["latest_execute_dir"] == "(none yet)"


# ---------------------------------------------------------------------------
# Knowledge journal
# ---------------------------------------------------------------------------

def test_ensure_knowledge_scaffold_creates_files():
    ws = _fresh_workspace()
    stage.ensure_knowledge_scaffold(ws)
    kdir = ws / KNOWLEDGE_DIR
    assert kdir.is_dir()
    assert (kdir / LESSONS_FILE).exists()
    assert (kdir / "journal.md").exists()
    assert (kdir / "domain_notes.md").exists()


def test_ensure_knowledge_scaffold_is_idempotent():
    ws = _fresh_workspace()
    stage.ensure_knowledge_scaffold(ws)
    # Pre-populate one of the files; a second call must not clobber it.
    (ws / KNOWLEDGE_DIR / "journal.md").write_text("# existing content\n")
    stage.ensure_knowledge_scaffold(ws)
    assert (ws / KNOWLEDGE_DIR / "journal.md").read_text() == "# existing content\n"


def test_append_and_read_lessons():
    ws = _fresh_workspace()
    stage.ensure_knowledge_scaffold(ws)
    stage.append_lesson(ws, {
        "id": "L1",
        "phase": "execute",
        "task": "seed",
        "root_cause": "a thing happened",
        "fix": "do the other thing",
    })
    stage.append_lesson(ws, {
        "id": "L2",
        "phase": "review",
        "task": "adequacy",
        "root_cause": "over-claimed",
        "fix": "hedge the claim",
    })
    lessons = stage.read_lessons(ws)
    assert len(lessons) == 2
    assert lessons[0]["id"] == "L1"
    assert lessons[1]["id"] == "L2"
    # Timestamps are auto-populated.
    assert "created" in lessons[0]


def test_read_lessons_tolerates_malformed_jsonl():
    ws = _fresh_workspace()
    stage.ensure_knowledge_scaffold(ws)
    path = stage.lessons_path(ws)
    path.write_text(
        json.dumps({"id": "L1", "phase": "execute"}) + "\n"
        "this is not json\n"
        "\n"  # blank line
        + json.dumps({"id": "L2", "phase": "plan"}) + "\n"
    )
    lessons = stage.read_lessons(ws)
    assert len(lessons) == 2
    assert lessons[0]["id"] == "L1"
    assert lessons[1]["id"] == "L2"


def test_summarize_lessons_filters_by_phase():
    ws = _fresh_workspace()
    stage.ensure_knowledge_scaffold(ws)
    stage.append_lesson(ws, {
        "id": "L1", "phase": "execute", "task": "t",
        "root_cause": "rc1", "fix": "fix1",
    })
    stage.append_lesson(ws, {
        "id": "L2", "phase": "review", "task": "t",
        "root_cause": "rc2", "fix": "fix2",
    })
    exec_only = stage.summarize_lessons(ws, for_phase="execute")
    assert "L1" in exec_only
    assert "L2" not in exec_only
    all_ = stage.summarize_lessons(ws, for_phase=None)
    assert "L1" in all_
    assert "L2" in all_


def test_summarize_lessons_respects_applies_to():
    ws = _fresh_workspace()
    stage.ensure_knowledge_scaffold(ws)
    stage.append_lesson(ws, {
        "id": "L1",
        "phase": "general",
        "applies_to": ["execute", "review"],
        "root_cause": "rc",
        "fix": "fix",
    })
    review_view = stage.summarize_lessons(ws, for_phase="review")
    assert "L1" in review_view
    scope_view = stage.summarize_lessons(ws, for_phase="scope")
    assert "L1" not in scope_view


def test_summarize_lessons_empty_returns_friendly_text():
    ws = _fresh_workspace()
    stage.ensure_knowledge_scaffold(ws)
    out = stage.summarize_lessons(ws)
    assert "no lessons" in out.lower()


# ---------------------------------------------------------------------------
# Events log
# ---------------------------------------------------------------------------

def test_append_event_writes_jsonl():
    ws = _fresh_workspace()
    stage.append_event(ws, {"kind": "test", "stage": "000_scope"})
    stage.append_event(ws, {"kind": "test", "stage": "001_plan"})
    events_file = ws / "events.jsonl"
    assert events_file.exists()
    lines = [json.loads(l) for l in events_file.read_text().splitlines() if l]
    assert len(lines) == 2
    assert lines[0]["kind"] == "test"
    assert lines[0]["stage"] == "000_scope"
    # ts auto-added.
    assert "ts" in lines[0]


def test_append_event_rejects_missing_kind():
    """The `kind` field is load-bearing for post-hoc event inspection
    tools. append_event must raise if a caller forgets it rather than
    silently writing a kindless record that breaks downstream grouping."""
    ws = _fresh_workspace()
    import pytest
    with pytest.raises(ValueError, match="kind"):
        stage.append_event(ws, {"stage": "000_scope", "phase": "scope"})
    # And no file was created.
    assert not (ws / "events.jsonl").exists()


def test_append_event_rejects_empty_kind():
    """Empty-string kind must also raise — otherwise callers could
    use `{"kind": ""}` to accidentally bypass the check."""
    ws = _fresh_workspace()
    import pytest
    with pytest.raises(ValueError, match="kind"):
        stage.append_event(ws, {"kind": "", "stage": "000_scope"})


# ---------------------------------------------------------------------------
# StageRef property / summary edge branches
#
# These branches are small but easy to regress: `.name` is used by
# event-logging callers that want the stage's canonical directory name,
# and the OSError fallback in `summary()` is what keeps prompt rendering
# from crashing on a workspace with a broken filesystem / permissions.
# ---------------------------------------------------------------------------

def test_stage_ref_name_property_roundtrips_stage_dir_name():
    """Line 69: `StageRef.name` returns `stage_dir_name(index, phase)`.
    None of the live tests construct a StageRef and read `.name` — all
    callers go through the path machinery — so the property itself
    was never exercised."""
    ws = _fresh_workspace()
    path = stage.create_stage_dir(ws, 7, PhaseName.EXECUTE)
    ref = stage.StageRef(
        index=7, phase=PhaseName.EXECUTE, path=path, rel_path=path.name
    )
    assert ref.name == "007_execute"


def test_stage_ref_summary_returns_empty_when_summary_missing():
    """Line 79: summary.md does not exist → empty string. `_make_stage`
    with no `summary=` argument leaves the file absent, so this
    exercises the `not p.exists()` early return."""
    ws = _fresh_workspace()
    path = _make_stage(ws, 0, PhaseName.SCOPE, done=True)  # no summary
    ref = stage.StageRef(
        index=0, phase=PhaseName.SCOPE, path=path, rel_path=path.name
    )
    assert ref.summary() == ""


def test_list_stages_skips_files_in_workspace_root():
    """Line 99: `if not child.is_dir(): continue`. A stray file at the
    workspace root (e.g. STATE.md, events.jsonl) must be skipped
    silently rather than crashing `parse_stage_dir_name` or being
    treated as a stage."""
    ws = _fresh_workspace()
    # Stray files — these are real artifacts OmiCairn writes at workspace root.
    (ws / "STATE.md").write_text("# state\n")
    (ws / "events.jsonl").write_text("")
    _make_stage(ws, 0, PhaseName.SCOPE, done=True)
    stages = stage.list_stages(ws)
    assert len(stages) == 1
    assert stages[0].index == 0


def test_stage_ref_summary_returns_empty_on_read_error(monkeypatch):
    """Lines 82-83: if `summary.md` exists but reading it raises
    OSError (e.g. permissions, racing delete, stale NFS), `.summary()`
    must degrade to `""` rather than propagating — prompt rendering
    calls `.summary()` on every stage and a single bad file must not
    blow up the whole phase."""
    ws = _fresh_workspace()
    path = _make_stage(ws, 0, PhaseName.SCOPE, done=True, summary="hello\n")
    ref = stage.StageRef(
        index=0, phase=PhaseName.SCOPE, path=path, rel_path=path.name
    )
    # Sanity: the happy path still works.
    assert ref.summary() == "hello"

    real_read_text = pathlib.Path.read_text

    def boom(self, *args, **kwargs):
        if self.name == SUMMARY_FILE:
            raise OSError("simulated permission denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", boom)
    assert ref.summary() == ""


# ---------------------------------------------------------------------------
# Lessons / summarize_lessons edge branches
# ---------------------------------------------------------------------------

def test_read_lessons_returns_empty_when_lessons_file_missing():
    """Line 266: a fresh workspace with no `knowledge/` scaffold at all
    must return `[]` rather than raising FileNotFoundError. The live
    tests all call `ensure_knowledge_scaffold` first, so the early-
    return branch was unreachable."""
    ws = _fresh_workspace()
    # No ensure_knowledge_scaffold → lessons.jsonl does not exist.
    assert stage.read_lessons(ws) == []


def test_summarize_lessons_header_only_when_body_empty():
    """Line 320: a lesson with no `root_cause` and no `fix` has an
    empty body, so `summarize_lessons` must emit the header alone
    rather than a header followed by " — FIX: ". The live tests
    always provide both root_cause and fix, so the no-body branch
    was never hit."""
    ws = _fresh_workspace()
    stage.ensure_knowledge_scaffold(ws)
    stage.append_lesson(ws, {
        "id": "L1",
        "phase": "execute",
        "task": "t",
        # No root_cause, no fix.
    })
    out = stage.summarize_lessons(ws, for_phase="execute")
    assert "L1" in out
    assert "[execute/t]" in out
    # No FIX suffix because fix is absent.
    assert "FIX:" not in out
    # No trailing ": " from an empty body.
    assert not out.rstrip().endswith(":")


# ---------------------------------------------------------------------------
# prior_stages_path / iter_stage_artifacts
# ---------------------------------------------------------------------------

def test_prior_stages_path_returns_child_of_stage_dir():
    """Line 343: simple path helper, but untested directly. Returns
    `stage_dir / PRIOR_STAGES_FILE` — pin it so a rename of the
    constant gets caught at test time."""
    from omicairn.constants import PRIOR_STAGES_FILE
    ws = _fresh_workspace()
    stage_dir = stage.create_stage_dir(ws, 0, PhaseName.SCOPE)
    p = stage.prior_stages_path(stage_dir)
    assert p == stage_dir / PRIOR_STAGES_FILE
    assert p.parent == stage_dir


def test_iter_stage_artifacts_returns_empty_when_stage_dir_missing():
    """Line 349: iterating a non-existent stage dir must yield nothing
    (early return), not raise FileNotFoundError. The inspect CLI
    depends on this graceful degradation when pointed at a stage
    name that was never created."""
    ws = _fresh_workspace()
    missing = ws / "000_scope"  # never created
    assert list(stage.iter_stage_artifacts(missing)) == []


def test_iter_stage_artifacts_yields_all_files_recursively():
    """Positive-case pin for iter_stage_artifacts: it must yield every
    file under the stage dir recursively, sorted, and skip
    directories. Not strictly a coverage gap but pairs with the
    missing-dir test above to pin the full contract."""
    ws = _fresh_workspace()
    stage_dir = stage.create_stage_dir(ws, 0, PhaseName.EXECUTE)
    (stage_dir / "summary.md").write_text("s")
    (stage_dir / "results.json").write_text("{}")
    sub = stage_dir / "code"
    sub.mkdir()
    (sub / "main.py").write_text("print(1)")
    artifacts = list(stage.iter_stage_artifacts(stage_dir))
    names = {p.name for p in artifacts}
    assert names == {"summary.md", "results.json", "main.py"}
    # All are files, never directories.
    assert all(p.is_file() for p in artifacts)


# ---------------------------------------------------------------------------
# newest_non_beacon_event_mtime — beacon-masks-watchdog (audit 2026-06-17)
# ---------------------------------------------------------------------------
#
# The stall machinery (fleet last_activity_age_s) must key on the newest
# NON-beacon event, never on the timer-driven EXECUTE_PROGRESS beacon, or a
# frozen stage's own beacon keeps the run looking "active" forever.


def _fresh_ws_for_events() -> pathlib.Path:
    return pathlib.Path(tempfile.mkdtemp())


def test_newest_non_beacon_event_mtime_none_when_no_log():
    """No events.jsonl → None (the helper contributes no signal)."""
    ws = _fresh_ws_for_events()
    assert stage.newest_non_beacon_event_mtime(ws) is None


def test_newest_non_beacon_event_mtime_none_for_beacon_only_tail():
    """A tail of ONLY EXECUTE_PROGRESS beacons must yield None — a wedged
    stage's own periodic beacon must contribute NO progress signal, so it
    cannot mask its stall."""
    from omicairn.constants import EventKind

    ws = _fresh_ws_for_events()
    for _ in range(5):
        stage.append_event(ws, {"kind": EventKind.EXECUTE_PROGRESS,
                                 "stage": "047_review"})
    assert stage.newest_non_beacon_event_mtime(ws) is None


def test_newest_non_beacon_event_mtime_none_for_soft_extend_only_tail():
    """A tail of ONLY DYNAMIC-mode wall beacons (EXECUTE_WALL_SOFT_EXTENDED /
    EXECUTE_HARD_CEILING_KILL) must yield None — they are timer-emitted by the
    in-process watchdog poll loop, NOT proof of real stage work. A past-soft-
    wall stage keeps appending soft-extend beacons for the whole inactivity
    grace window even when no liveness advanced this poll; if they counted as
    progress they would mask the stall in the fleet view (the beacon-masks-
    watchdog class, audit 2026-06-17; DYNAMIC-mode regression, 2026-07-01)."""
    from omicairn.constants import EventKind

    ws = _fresh_ws_for_events()
    for _ in range(5):
        stage.append_event(ws, {"kind": EventKind.EXECUTE_WALL_SOFT_EXTENDED,
                                 "stage": "046_execute"})
    stage.append_event(ws, {"kind": EventKind.EXECUTE_HARD_CEILING_KILL,
                             "stage": "046_execute"})
    assert stage.newest_non_beacon_event_mtime(ws) is None


def test_newest_non_beacon_event_mtime_picks_real_event_over_later_beacon():
    """A real progress event (e.g. stage_done) sets the timestamp even when
    LATER beacons follow it — the helper walks newest→oldest and skips the
    beacons, returning the real event's ts (the freshest NON-beacon record)."""
    import datetime as dt

    from omicairn.constants import EventKind

    ws = _fresh_ws_for_events()
    real_ts = "2026-06-17T12:00:00+00:00"
    # A real event, then several beacons AFTER it.
    stage.append_event(ws, {"kind": EventKind.STAGE_DONE,
                            "stage": "046_execute", "ts": real_ts})
    for _ in range(4):
        stage.append_event(ws, {"kind": EventKind.EXECUTE_PROGRESS,
                                 "stage": "047_review"})
    got = stage.newest_non_beacon_event_mtime(ws)
    assert got is not None
    expected = dt.datetime.fromisoformat(real_ts).timestamp()
    assert abs(got - expected) < 1.0


def test_newest_non_beacon_event_mtime_skips_unparseable_lines():
    """Truncated/garbage lines (a tail seek can slice mid-record) and a
    record with a bad ts are skipped; the newest VALID non-beacon ts wins.
    Best-effort and never crashes."""
    from omicairn.constants import EVENTS_LOG, EventKind

    ws = _fresh_ws_for_events()
    good_ts = "2026-06-17T09:30:00+00:00"
    stage.append_event(ws, {"kind": EventKind.STAGE_DONE,
                            "stage": "010_execute", "ts": good_ts})
    # Append junk + a bad-ts record by hand (after the good one).
    with (ws / EVENTS_LOG).open("a") as f:
        f.write("}{ not json at all\n")
        f.write('{"kind":"stage_done","ts":"not-a-timestamp"}\n')
    import datetime as dt

    got = stage.newest_non_beacon_event_mtime(ws)
    assert got is not None
    assert abs(got - dt.datetime.fromisoformat(good_ts).timestamp()) < 1.0
