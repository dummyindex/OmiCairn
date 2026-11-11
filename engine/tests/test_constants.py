"""Unit tests for omicairn.constants (string helpers + decision sets).

These are pure functions with no I/O — a single bug here shows up in
orchestrator routing and contract paths, so cover them tightly.
"""
from __future__ import annotations

import pytest

from omicairn.constants import (
    BACKTRACK_DECISIONS,
    CLI_PHASE_CHOICES,
    DECISION_TO_PHASE,
    Decision,
    CLAUDE_CODE_PHASES,
    EdgeRel,
    EventKind,
    GRAPH_UPDATE_VERDICTS,
    GraphUpdateVerdict,
    HypothesisStatus,
    VerdictKey,
    KNOWN_STAGE_PHASES,
    NON_TERMINAL_DECISIONS,
    PHASE_CHOICE_ALL,
    PIPELINE_ORDER,
    PhaseName,
    STAGE_INDEX_MAX,
    STAGE_INDEX_WIDTH,
    TERMINAL_DECISIONS,
    VERDICT_PHASES,
    domain_pack_phase_file,
    latest_stage_token,
    parse_stage_dir_name,
    stage_dir_name,
)


# ---------------------------------------------------------------------------
# stage_dir_name / parse_stage_dir_name
# ---------------------------------------------------------------------------

def test_stage_dir_name_zero_padding():
    assert stage_dir_name(0, PhaseName.SCOPE) == "000_scope"
    assert stage_dir_name(5, PhaseName.EXECUTE) == "005_execute"
    assert stage_dir_name(42, PhaseName.CROSS_CHECK) == "042_cross_check"
    assert stage_dir_name(999, PhaseName.REVIEW) == "999_review"


def test_stage_dir_name_rejects_out_of_range():
    with pytest.raises(ValueError):
        stage_dir_name(-1, PhaseName.SCOPE)
    with pytest.raises(ValueError):
        stage_dir_name(STAGE_INDEX_MAX, PhaseName.SCOPE)


def test_parse_stage_dir_name_roundtrip():
    for idx, phase in [
        (0, PhaseName.SCOPE),
        (1, PhaseName.PLAN),
        (10, PhaseName.EXECUTE),
        (100, PhaseName.CROSS_CHECK),
        (999, PhaseName.REVIEW),
    ]:
        name = stage_dir_name(idx, phase)
        parsed = parse_stage_dir_name(name)
        assert parsed == (idx, phase)


def test_parse_stage_dir_name_roundtrips_standalone_stages():
    """STANDALONE stages (ideate/free) are real stage dirs that
    stage_dir_name CREATES, so parse_stage_dir_name MUST round-trip them too
    — not just PIPELINE_ORDER phases (core-beliefs #14)."""
    for phase in (PhaseName.IDEATE, PhaseName.FREE):
        name = stage_dir_name(9, phase)
        assert parse_stage_dir_name(name) == (9, phase), (
            f"{name} must round-trip; it's a dir stage_dir_name can create"
        )


def test_known_stage_phases_covers_every_creatable_phase():
    """The round-trip INVARIANT, generalised: every phase that appears in a
    stage dir (pipeline + standalone) must be in KNOWN_STAGE_PHASES, which is
    exactly what parse_stage_dir_name validates against."""
    # All PIPELINE_ORDER phases + the standalone pair.
    for ph in (*PIPELINE_ORDER, PhaseName.FREE, PhaseName.IDEATE):
        assert ph in KNOWN_STAGE_PHASES
        assert parse_stage_dir_name(stage_dir_name(3, ph)) == (3, ph)


def test_parse_stage_dir_name_rejects_garbage():
    assert parse_stage_dir_name("") is None
    assert parse_stage_dir_name("xx_scope") is None
    assert parse_stage_dir_name("00_scope") is None  # too-short index
    assert parse_stage_dir_name("000_notaphase") is None
    assert parse_stage_dir_name("000scope") is None  # missing separator
    assert parse_stage_dir_name("knowledge") is None
    assert parse_stage_dir_name(".claude") is None


def test_parse_stage_dir_name_width_matches_constant():
    """Any renaming of STAGE_INDEX_WIDTH would break the parser —
    this test guards against it."""
    # Pad manually to the expected width.
    name = "0" * STAGE_INDEX_WIDTH + "_scope"
    assert parse_stage_dir_name(name) == (0, PhaseName.SCOPE)


# ---------------------------------------------------------------------------
# Decision sets: exhaustive and disjoint
# ---------------------------------------------------------------------------

def test_decision_sets_are_disjoint():
    """No decision may be both terminal and non-terminal."""
    assert NON_TERMINAL_DECISIONS.isdisjoint(TERMINAL_DECISIONS)


def test_every_decision_appears_in_some_set():
    all_decisions = {
        Decision.PROCEED,
        Decision.RETRY_EXECUTE,
        Decision.BACK_TO_SCOPE,
        Decision.BACK_TO_PLAN,
        Decision.BACK_TO_EXECUTE,
        Decision.HALT,
        Decision.DONE,
    }
    union = NON_TERMINAL_DECISIONS | TERMINAL_DECISIONS
    assert all_decisions == union


def test_backtrack_decisions_are_subset_of_non_terminal():
    assert BACKTRACK_DECISIONS.issubset(NON_TERMINAL_DECISIONS)


def test_decision_to_phase_keys_are_routing_decisions():
    """Every routing decision (retry_*, back_to_*) must have a phase
    mapping, and every mapped phase must be a valid pipeline phase."""
    routing = {
        Decision.RETRY_EXECUTE,
        Decision.BACK_TO_SCOPE,
        Decision.BACK_TO_PLAN,
        Decision.BACK_TO_EXECUTE,
    }
    assert set(DECISION_TO_PHASE.keys()) == routing
    for phase in DECISION_TO_PHASE.values():
        assert phase in PIPELINE_ORDER


# ---------------------------------------------------------------------------
# Phase sets
# ---------------------------------------------------------------------------

def test_claude_code_phases_excludes_cross_check():
    assert PhaseName.CROSS_CHECK not in CLAUDE_CODE_PHASES
    # All PIPELINE_ORDER entries except cross_check spawn a `claude -p`
    # subprocess. Plus the off-pipeline FREE phase (steering Slice 2) and
    # the off-pipeline IDEATE phase (standalone brainstorm front-stage).
    expected = {p for p in PIPELINE_ORDER if p != PhaseName.CROSS_CHECK}
    expected.add(PhaseName.FREE)
    expected.add(PhaseName.IDEATE)
    assert CLAUDE_CODE_PHASES == expected


def test_verdict_phases_includes_cross_check_and_review():
    assert PhaseName.CROSS_CHECK in VERDICT_PHASES
    assert PhaseName.REVIEW in VERDICT_PHASES


def test_pipeline_order_has_each_phase_exactly_once():
    assert len(PIPELINE_ORDER) == len(set(PIPELINE_ORDER))
    # Analysis pipeline: the analysis-adequacy REVIEW is the last phase.
    assert PIPELINE_ORDER == (
        PhaseName.SCOPE,
        PhaseName.PLAN,
        PhaseName.EXECUTE,
        PhaseName.CROSS_CHECK,
        PhaseName.REVIEW,
    )


def test_removed_paper_phases_are_gone():
    for attr in ("WRITE", "CLAIM_AUDIT", "PEER_REVIEW", "TYPESET"):
        assert not hasattr(PhaseName, attr)
    for attr in ("RETRY_WRITE", "RETRY_REVIEW", "BACK_TO_WRITE"):
        assert not hasattr(Decision, attr)
    for legacy in ("write", "claim_audit", "peer_review", "typeset"):
        assert legacy not in KNOWN_STAGE_PHASES
        assert parse_stage_dir_name(f"004_{legacy}") is None


# ---------------------------------------------------------------------------
# CLI choices
# ---------------------------------------------------------------------------

def test_cli_phase_choices_contains_all_and_pipeline():
    assert CLI_PHASE_CHOICES[0] == PHASE_CHOICE_ALL
    assert set(CLI_PHASE_CHOICES[1:]) == set(PIPELINE_ORDER)


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def test_latest_stage_token_format():
    assert latest_stage_token(PhaseName.SCOPE) == "latest_scope_dir"
    assert latest_stage_token(PhaseName.EXECUTE) == "latest_execute_dir"
    assert latest_stage_token(PhaseName.CROSS_CHECK) == "latest_cross_check_dir"


def test_domain_pack_phase_file_format():
    assert domain_pack_phase_file(PhaseName.EXECUTE) == "phase_execute.md"
    assert domain_pack_phase_file(PhaseName.REVIEW) == "phase_review.md"


# ---------------------------------------------------------------------------
# EventKind: canonical taxonomy for the `kind` field of every
# append_event / steering-log record. The whole point is to kill the
# emitter/consumer string-coupling (a renamed/typo'd literal silently
# broke a consumer with no error). These guards ensure (a) values stay
# unique and (b) every kind actually emitted in omicairn/*.py has a constant,
# so a new kind added as a raw literal trips the suite.
# ---------------------------------------------------------------------------

def _event_kind_values() -> dict[str, str]:
    return {
        name: val
        for name, val in vars(EventKind).items()
        if not name.startswith("_") and isinstance(val, str)
    }


def test_graph_update_verdict_values_canonical():
    """F1: the graph_updates result_refs `verdict` field values are the
    byte-identical literals the prompt emits + knowledge_graph consumes."""
    assert GraphUpdateVerdict.SUPPORTS == "supports"
    assert GraphUpdateVerdict.REFUTES == "refutes"
    assert GraphUpdateVerdict.INCONCLUSIVE == "inconclusive"
    assert GRAPH_UPDATE_VERDICTS == {
        GraphUpdateVerdict.SUPPORTS,
        GraphUpdateVerdict.REFUTES,
        GraphUpdateVerdict.INCONCLUSIVE,
    }


def test_graph_update_verdict_supports_refutes_match_edge_rel():
    """SUPPORTS/REFUTES spell the same as the EdgeRel they map to (so the
    in-memory routing is identity-clean), but INCONCLUSIVE is verdict-only —
    it has no EdgeRel and advances HypothesisStatus only."""
    assert GraphUpdateVerdict.SUPPORTS == EdgeRel.SUPPORTS
    assert GraphUpdateVerdict.REFUTES == EdgeRel.REFUTES
    assert GraphUpdateVerdict.INCONCLUSIVE == HypothesisStatus.INCONCLUSIVE


def test_verdict_key_values_canonical():
    """F2: the verdict.json schema KEY names are byte-identical to the
    historical literals (verdict.json on-disk payloads stay unchanged)."""
    assert VerdictKey.DECISION == "decision"
    assert VerdictKey.VERDICT == "verdict"
    assert VerdictKey.REASON == "reason"
    assert VerdictKey.EVIDENCE == "evidence"
    assert VerdictKey.PROPOSED_FIX == "proposed_fix"
    assert VerdictKey.TARGET_FILE_HINTS == "target_file_hints"


def test_verdict_key_matches_verdict_dataclass_fields():
    """The Verdict dataclass field names (the canonical WRITE path, via
    dataclasses.asdict) must equal the VerdictKey constants — otherwise a
    read keyed on VerdictKey would miss what the write emitted."""
    import dataclasses

    from omicairn.verdict import Verdict

    field_names = {f.name for f in dataclasses.fields(Verdict)}
    assert field_names == {
        VerdictKey.DECISION,
        VerdictKey.REASON,
        VerdictKey.EVIDENCE,
        VerdictKey.PROPOSED_FIX,
        VerdictKey.TARGET_FILE_HINTS,
    }


def test_event_kind_values_are_unique():
    """No two EventKind attributes may share a value — a collision would
    make two distinct event types indistinguishable to consumers."""
    vals = list(_event_kind_values().values())
    assert len(vals) == len(set(vals)), (
        "duplicate EventKind values: "
        f"{sorted(v for v in set(vals) if vals.count(v) > 1)}"
    )


def test_event_kind_values_are_lower_snake():
    """Values are wire-format strings consumers/tools match on; keep them
    lower_snake (the historical convention) so the byte stream is stable."""
    import re
    for name, val in _event_kind_values().items():
        assert re.fullmatch(r"[a-z][a-z0-9_]*", val), (
            f"EventKind.{name} = {val!r} is not lower_snake"
        )


def test_every_emitted_kind_has_an_event_kind_constant():
    """Scan omicairn/*.py for every `kind` emitted through append_event (the
    events.jsonl stream) and steering's write_plan / append_log (the
    steering_log.jsonl stream); each MUST resolve to an EventKind
    attribute. This is the drift guard: add a new emit site with a raw
    literal and this test fails until you give it a constant.

    Deliberately scoped to the event/steering-log taxonomy. Other JSONL
    stores (human-feedback records, knowledge-graph ops, lab-knowledge
    entries, the routing_suggestion.json `route` schema) are SEPARATE
    validated taxonomies and are excluded by construction below.
    """
    import pathlib
    import re

    omicairn_dir = pathlib.Path(__file__).resolve().parent.parent / "omicairn"
    known = set(_event_kind_values().values())

    # Emitter call patterns that write to an event/steering-log stream.
    EMIT_CALL = re.compile(
        r"(append_event|append_log)\(",
    )
    KIND_LIT = re.compile(r'"kind":\s*"([a-z_]+)"')

    missing: list[str] = []
    for py in sorted(omicairn_dir.glob("*.py")):
        src = py.read_text()
        for m in EMIT_CALL.finditer(src):
            # Look at the dict literal that follows the call (bounded window;
            # event dicts are small).
            window = src[m.start(): m.start() + 1500]
            km = KIND_LIT.search(window)
            if km and km.group(1) not in known:
                missing.append(f"{py.name}: {km.group(1)}")
        # steering.write_plan / consume_plan write the kind inline (not via a
        # call named append_*), so also sweep the steering module wholesale.
        if py.name == "steering.py":
            for km in KIND_LIT.finditer(src):
                if km.group(1) not in known:
                    missing.append(f"{py.name}: {km.group(1)}")

    assert not missing, (
        "event/steering-log kinds emitted as raw literals with no "
        f"EventKind constant: {sorted(set(missing))}"
    )


def test_event_kind_covers_core_lifecycle_kinds():
    """A representative spot-check that the well-known lifecycle kinds are
    present with their exact historical wire values (byte-identical)."""
    assert EventKind.WORKSPACE_INIT == "workspace_init"
    assert EventKind.STAGE_DONE == "stage_done"
    assert EventKind.STAGE_TIMEOUT == "stage_timeout"
    assert EventKind.STAGE_CONTRACT_FAIL == "stage_contract_fail"
    assert EventKind.REAPED_ORPHAN == "reaped_orphan"
    assert EventKind.STEERING_PLAN_WRITE == "steering_plan_write"
    assert EventKind.STEERING_VERIFIER_WARNING == "steering_verifier_warning"


# ---------------------------------------------------------------------------
# W32 — within-EXECUTE parallel-arm fan-out constants
# ---------------------------------------------------------------------------

def test_parallel_arm_gate_id_is_w32():
    from omicairn.constants import ParallelArmIndependenceGate
    assert ParallelArmIndependenceGate.GATE_ID == "W32"


def _all_gate_id_classes():
    """Every constants class carrying a `GATE_ID` Final[str] attribute."""
    import inspect
    from omicairn import constants as c
    out = {}
    for name, obj in vars(c).items():
        if inspect.isclass(obj) and isinstance(getattr(obj, "GATE_ID", None), str):
            out[name] = obj.GATE_ID
    return out


def test_parallel_arm_gate_id_is_unique_across_gate_classes():
    """W32 must not collide with any other Gate class GATE_ID (W26–W31 are all
    already taken; W32 is the next unused id)."""
    ids = list(_all_gate_id_classes().values())
    assert len(ids) == len(set(ids)), (
        f"duplicate GATE_ID across gate classes: "
        f"{sorted(i for i in ids if ids.count(i) > 1)}"
    )
    assert "W32" in ids


def test_arm_manifest_key_fields_present():
    from omicairn.constants import ArmManifestKey as K
    for field in ("VERSION", "ARMS", "MAX_PARALLEL", "GPU_SLOTS",
                  "ARM_ID", "SEED", "OUTPUT_DIR", "GPU", "HOST"):
        assert isinstance(getattr(K, field), str), field


def test_parallel_arm_budgets():
    from omicairn.constants import DEFAULT_GPU_SLOTS, MAX_IN_STAGE_PARALLEL
    assert DEFAULT_GPU_SLOTS == 1
    assert MAX_IN_STAGE_PARALLEL == 8


def test_parallel_arm_filenames():
    from omicairn.constants import (
        ARM_MANIFEST_FILE, ARM_PROVENANCE_FRAGMENT, ARM_PROVENANCE_LOG,
        PARALLEL_ARMS_STAMP_FILE,
    )
    assert ARM_MANIFEST_FILE == "arm_manifest.json"
    assert PARALLEL_ARMS_STAMP_FILE == "parallel_arms.json"
    # The per-arm race-free fragment (one writer per file, in its own dir).
    assert ARM_PROVENANCE_FRAGMENT == "arm_provenance.json"
    # The legacy shared-log name is retained only as a reserved output name.
    assert ARM_PROVENANCE_LOG.endswith(".jsonl")


def test_gpu_routed_hosts_defined():
    from omicairn.constants import GPU_ROUTED_HOSTS
    assert isinstance(GPU_ROUTED_HOSTS, tuple)
    assert "gpu-host-1" in GPU_ROUTED_HOSTS


def test_reserved_stage_output_names_reuse_existing_constants():
    """The reserved-file tuple must REUSE the canonical filename constants
    (no re-literaling) — assert identity of the shared members, INCLUDING the
    concurrency-critical shared append-logs and the W32 control files."""
    from omicairn.constants import (
        RESERVED_STAGE_OUTPUT_NAMES, RESULTS_JSON, ANALYSIS_MD, DONE_FLAG,
        CODE_SUBDIR, FIGURES_SUBDIR, PROVENANCE_LOG, EVENTS_LOG,
        ARM_MANIFEST_FILE, PARALLEL_ARMS_STAMP_FILE, ARM_PROVENANCE_LOG,
    )
    for const in (RESULTS_JSON, ANALYSIS_MD, DONE_FLAG, CODE_SUBDIR, FIGURES_SUBDIR,
                  PROVENANCE_LOG, EVENTS_LOG, ARM_MANIFEST_FILE,
                  PARALLEL_ARMS_STAMP_FILE, ARM_PROVENANCE_LOG):
        assert const in RESERVED_STAGE_OUTPUT_NAMES
