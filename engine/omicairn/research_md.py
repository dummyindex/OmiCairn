"""RESEARCH.md parser and validator.

RESEARCH.md is the single human-authored file. It contains YAML
front-matter in the style of the colleague's v3 spec. We parse it,
validate required fields, and expose it as a dataclass.
"""
from __future__ import annotations

import dataclasses
import os
import pathlib
import warnings
from typing import Any

import yaml

from . import contribution as _contribution
from .compute_resources import ComputeResource, resolve as _resolve_compute
from .constants import (
    DEFAULT_MAX_BACKTRACKS,
    DEFAULT_MAX_ITERS_PER_PHASE,
    DEFAULT_MAX_PHASE_WALL_MINUTES,
    DEFAULT_MAX_TOTAL_WALL_MINUTES,
    DEFAULT_RUN_BUDGET_USD,
    DEFAULT_STAGE_ITER_MODE,
    DEFAULT_STAGE_TIME_LIMIT_MODE,
    STAGE_ITER_MODES,
    STAGE_TIME_LIMIT_MODES,
    DEPTH_CONFIG_BOOL_KEYS,
    DEPTH_CONFIG_KEYS,
    DEPRECATED_CONFIG_KEYS,
    DEPRECATED_DEPTH_KEYS,
)


def _default_feasibility_config() -> Any:
    """Safe default for ResearchMd.feasibility_config (neutral gate).

    Lazily imports feasibility (it only depends on constants, so there is
    no cycle; the lazy form keeps research_md importable even if the gate
    module is ever stripped). Used as the dataclass field's
    default_factory AND the load()-time fallback for a case whose
    RESEARCH.md / omicairn_config.yaml carries no `feasibility:` block — both
    yield neutral, exploratory-friendly defaults (applies_to=confirmatory,
    min_fraction_of_reference=0.1)."""
    from . import feasibility
    return feasibility.parse_config(None)


@dataclasses.dataclass
class Goal:
    """One research goal within a workspace (core belief 12). A workspace
    has one or more. Each carries its seed (pre-registered) hypotheses;
    more may be raised on the fly during EXECUTE/REVIEW and recorded in
    the knowledge graph rather than here."""
    id: str
    question: str
    hypotheses: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class ResearchMd:
    research_mode: str
    domain: str
    topic_slug: str
    research_question: str
    data_location: pathlib.Path
    data_description: str
    cohorts: list[str]
    modalities: list[str]
    # Optional absolute path to a project-wide dataset cache shared across
    # OmiCairn runs. ``None`` means "use the default" — the Workspace resolves
    # it to ``<workspaces_root_parent>/datasets/`` at init time. Phases
    # fetch on-the-way data here via the generic ``{{shared_cache_dir}}``
    # token; see docs/references/research-md-schema.md § Data fetching.
    shared_cache_override: pathlib.Path | None
    primary_metric: str
    baseline: str | None
    pre_registration: bool
    honest_validation: bool
    # Per-phase and total wall budgets, in minutes. Honoured by the
    # orchestrator: `_run_llm_stage` clamps the `claude -p` subprocess
    # timeout to ``max_phase_wall_minutes * 60`` when it is tighter
    # than the phase's own default; `_run_all_locked` breaks the outer
    # stage loop once cumulative wall time has exceeded
    # ``max_total_wall_minutes * 60``. Set 0 / negative to fall back
    # on the phase defaults.
    max_phase_wall_minutes: int
    max_total_wall_minutes: int
    allow_web: bool
    claude_model: str
    # Currently no human-approval gate exists in the orchestrator —
    # OmiCairn always proceeds without prompting. The `auto_approve` field
    # is retained as a contract token: every test fixture and example
    # RESEARCH.md sets ``auto_approve: true``, and removing it would
    # silently break those parsers. When (if) an interactive gate is
    # added — e.g. a SCOPE-then-confirm checkpoint before EXECUTE —
    # this is the field to consult. See parkinson smoke-run report
    # §3.2 ("dormant field") for the original ticket.
    auto_approve: bool
    # Per-phase LLM dollar cap (`claude --max-budget-usd`). None = UNLIMITED:
    # the orchestrator passes None to the runner, which OMITS the flag so
    # `claude -p` never self-halts on spend (owner directive 2026-06-27 —
    # unlimited run budget for ALL research runs). A case may still set a finite
    # numeric cap in its `budgets.max_phase_budget_usd`. Default lives in the
    # named constant DEFAULT_RUN_BUDGET_USD.
    max_phase_budget_usd: float | None
    # Flat-numbered-stage bounds; read from bounds: block of RESEARCH.md.
    max_iters_per_phase: int
    max_backtracks: int
    # Stratified per-phase wall ceilings (NEW, omicairn_config.yaml only).
    # When a phase name appears here, `_run_llm_stage` uses this value
    # instead of `max_phase_wall_minutes`. Phases not listed inherit
    # `max_phase_wall_minutes`. Empty dict = no overrides.
    #
    # Source: `omicairn_config.yaml`'s `budgets.max_phase_wall_minutes_by_phase`
    # block (sibling file to RESEARCH.md). The parkinson 15-case
    # suite's `asyn-propagation` case timed out on a 4h global ceiling
    # during an iterated EXECUTE; this lets that single case raise
    # EXECUTE-specific budget without inflating the others.
    max_phase_wall_minutes_by_phase: dict[str, int]
    # Multi-goal / dynamic-hypothesis model (core belief 12). A workspace
    # holds >=1 goals; each goal has >=1 seed hypotheses. When the
    # RESEARCH.md has no explicit `goals:` block, exactly one goal is
    # synthesized from `research_question` so existing single-goal cases
    # are unchanged. `allow_on_the_fly_hypotheses` gates whether EXECUTE/
    # REVIEW may register new hypotheses mid-run; `max_on_the_fly_hypotheses`
    # bounds how many. See docs/design-docs/dynamic-hypotheses.md.
    goals: list[Goal]
    allow_on_the_fly_hypotheses: bool
    max_on_the_fly_hypotheses: int
    # IDEATE source-collection targets (see _IDEATE_DEFAULTS in load()):
    # target_papers / target_blogs / target_beliefs / min_directions /
    # max_directions. Rendered into the ideate prompt via {{ideate_targets}}.
    ideate_config: dict[str, int]
    # OPTIONAL source-credibility-weighting bias for IDEATE (belief 13):
    # when true, the ideate prompt is told to weight reputable / integrity-
    # clean sources higher. Off by default — an opt-in bias, never an
    # assumption. Rendered via {{ideate_source_policy}}.
    prefer_reputable_sources: bool
    # OPTIONAL depth / rigor SOFT targets (biomedical-2026 thoroughness).
    # Overlaid from omicairn_config.yaml's `depth:` (or `rigor:`) block like
    # budgets/bounds. Keys are constants.DEPTH_CONFIG_KEYS; each value is
    # the parsed target or ``None`` when unset (= no constraint). These
    # are advisory targets surfaced to prompts/REVIEW, NEVER hard
    # contract failures. An empty/absent block leaves every value None.
    depth_config: dict[str, Any]
    # Contribution-depth knob (analysis | method | theory). Overlaid from
    # omicairn_config.yaml's `contribution_type:` (RESEARCH.md fallback);
    # default "analysis". Declares what KIND of contribution the study
    # must make and is surfaced to SCOPE/PLAN/EXECUTE via the
    # {{contribution_directive}} token (rendered by omicairn.contribution).
    # A "method" case MUST propose + beat >=2 real baselines with
    # ablations; a "theory" case MUST Lean-verify its math claims. See
    # omicairn/contribution.py + rules/method_depth.md.
    contribution_type: str
    # Research-arc knob — an OPTIONAL FREE-TEXT methodological-intent hint
    # (or None). Overlaid from omicairn_config.yaml's `research_arc:` (RESEARCH.md
    # fallback); default None (= unset, back-compat). A short free-form hint
    # for which way the study's argument flows between math and data, in the
    # brief-author's own words (NO fixed value set). Surfaced to
    # SCOPE/PLAN/EXECUTE via the {{research_arc_directive}} token (rendered by
    # omicairn.contribution.render_arc_directive) as ADVISORY guidance — it shapes
    # EMPHASIS the agent SHOULD weigh, never a rule, never the stage list
    # (PIPELINE_ORDER is fixed), never contribution_type. An unset arc forces
    # NOTHING. Validated TOLERANTLY — an empty value degrades to None, never
    # crashes (no value set to reject against). See omicairn/contribution.py.
    research_arc: str | None
    # Resolved compute-resource envelope for this case. Comes from the
    # omicairn_config.yaml `compute:` block resolved against the nearest
    # COMPUTATION_RESOURCES.md catalog (see compute_resources.py). When a
    # case declares no `compute:`, this is the DEFAULT_LOCAL CPU-only
    # fallback (historical behaviour). Rendered into EXECUTE/PLAN prompts
    # via the {{compute_resources}} token so the agent knows the real
    # hardware envelope (GPU availability, RAM, runtime ceiling).
    compute: "ComputeResource"
    raw: dict[str, Any]
    source_path: pathlib.Path
    # SCOPE feasibility / statistical-power gate config. Overlaid from
    # omicairn_config.yaml's `feasibility:` block (preferred) or RESEARCH.md,
    # like budgets/bounds/depth. OPTIONAL with a neutral
    # default: an absent/None/malformed block → feasibility.parse_config(None)
    # (applies_to=confirmatory, min_fraction_of_reference=0.1). The gate
    # routes a doomed underpowered confirmatory run to remediation BEFORE
    # PLAN locks a pre-registration; it NEVER blocks a well-powered null,
    # and exploratory work is exempt by default (belief 13). Older
    # RESEARCH.md without the field load unchanged. See omicairn.feasibility and
    # docs/design-docs/feasibility-gate.md.
    feasibility_config: "Any" = dataclasses.field(
        default_factory=lambda: _default_feasibility_config())
    # Stage time-limit MODE — how the per-phase WALL is enforced for
    # execute-class stages: "dynamic" (default) makes the wall a liveness-gated
    # soft ceiling so a PROGRESSING long GPU/FT/inference arm is never
    # truncated (only a genuine stall or the hard safety ceiling kills it);
    # "hard" keeps the wall an unconditional Slurm-style kill. Overlaid from
    # omicairn_config.yaml's `budgets.stage_time_limit_mode` (RESEARCH.md fallback),
    # like the other budget fields; an absent/unrecognised value degrades to
    # the default via constants.stage_time_limit_mode (back-compat: older briefs
    # load unchanged and get dynamic). The env OMICAIRN_STAGE_TIME_LIMIT_MODE
    # overrides this per-invocation. See constants.stage_time_limit_mode +
    # orchestrator.estimate_execute_wall_seconds.
    stage_time_limit_mode: str = DEFAULT_STAGE_TIME_LIMIT_MODE
    # Stage ITERATION mode: "dynamic" (default) demotes the per-phase iteration
    # cap (max_iters_per_phase) to a high, NON-BINDING safety ceiling — a healthy
    # phase is terminated by its real stop-condition (cross_check proceed /
    # the analysis review's done) and only a genuine zero-progress spin is halted;
    # "hard" keeps the configured/defaulted cap as an unconditional deterministic
    # terminator. Sibling axis to stage_time_limit_mode; overlaid the same way
    # from omicairn_config.yaml's `budgets.stage_iter_mode` (RESEARCH.md fallback),
    # degrade-safe, env OMICAIRN_STAGE_ITER_MODE overrides. See
    # constants.stage_iter_mode + orchestrator zero-progress guard (#133).
    stage_iter_mode: str = DEFAULT_STAGE_ITER_MODE
    # OPTIONAL declared EXECUTE workload scale — degrade-safe hints the dynamic
    # estimator uses to SEED the initial soft wall (never a hard bound; a wrong
    # value can only shift when soft-extension logging begins, never abandon an
    # arm). Overlaid from omicairn_config.yaml's `workload:` block (RESEARCH.md
    # fallback). All None when absent → estimator falls back to the resolved
    # per-phase wall + GPU multiplier. `n_models` × `n_eval_items` scales the
    # inference budget; `is_finetune` multiplies for the FT-vs-inference gap.
    workload_n_models: int | None = None
    workload_n_eval_items: int | None = None
    workload_is_finetune: bool = False

    @property
    def data_dir(self) -> pathlib.Path:
        return self.data_location

    @property
    def is_multi_goal(self) -> bool:
        return len(self.goals) > 1

    @property
    def all_seed_hypotheses(self) -> list[str]:
        out: list[str] = []
        for g in self.goals:
            out.extend(g.hypotheses)
        return out


def _parse_goals(d: dict, research_question: str) -> list[Goal]:
    """Parse the optional `goals:` block; synthesize a single goal from
    `research_question` when absent (back-compat with single-goal cases).

    Each goal entry may set `id` (default g1, g2, ...), `question`
    (defaults to the top-level research_question for the first goal), and
    `hypotheses` (a list of strings; default []).
    """
    raw_goals = d.get("goals")
    if not raw_goals:
        return [Goal(id="g1", question=research_question, hypotheses=[])]
    if not isinstance(raw_goals, list):
        raise ValueError(
            f"RESEARCH.md `goals:` must be a list; got {type(raw_goals).__name__}"
        )
    goals: list[Goal] = []
    for i, entry in enumerate(raw_goals):
        if not isinstance(entry, dict):
            raise ValueError(
                f"RESEARCH.md `goals[{i}]` must be a mapping; "
                f"got {type(entry).__name__}"
            )
        gid = str(entry.get("id") or f"g{i + 1}")
        question = entry.get("question")
        if not question:
            if i == 0:
                question = research_question
            else:
                raise ValueError(
                    f"RESEARCH.md `goals[{i}]` (id={gid!r}) is missing a "
                    f"`question:` (only the first goal may fall back to "
                    f"the top-level research_question)"
                )
        hyps = entry.get("hypotheses") or []
        if not isinstance(hyps, list):
            raise ValueError(
                f"RESEARCH.md `goals[{i}].hypotheses` must be a list; "
                f"got {type(hyps).__name__}"
            )
        goals.append(Goal(
            id=gid, question=str(question),
            hypotheses=[str(h) for h in hyps],
        ))
    return goals


def _warn_deprecated_keys(cfg: dict, d: dict) -> None:
    """Old cases may still carry keys this release no longer reads
    (e.g. ``target_venue``, ``citation_gate``). They are ignored with a
    warning rather than failing the load."""
    for source, block in (("omicairn_config.yaml", cfg), ("RESEARCH.md", d)):
        if not isinstance(block, dict):
            continue
        found = [k for k in DEPRECATED_CONFIG_KEYS if k in block]
        for section in ("depth", "rigor"):
            sub = block.get(section)
            if isinstance(sub, dict):
                found += [f"{section}.{k}" for k in DEPRECATED_DEPTH_KEYS
                          if k in sub]
        if found:
            warnings.warn(
                f"{source}: ignoring deprecated key(s) {', '.join(found)} "
                "(no longer used by OmiCairn)",
                FutureWarning,
                stacklevel=3,
            )


def _get(d: dict, keys: list[str], default: Any = None, required: bool = False) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            if required:
                raise ValueError(f"RESEARCH.md missing required field: {'.'.join(keys)}")
            return default
        cur = cur[k]
    # Treat explicit YAML null as "missing" for required fields. Without
    # this, a RESEARCH.md with `location: ~` (bare tilde is YAML null) or
    # `location: null` would pass the presence check and then be
    # str()-coerced to the literal "None" downstream — producing a cryptic
    # "file not found" much later rather than a clean "missing required
    # field" error right at load time.
    if cur is None and required:
        raise ValueError(
            f"RESEARCH.md required field is null: {'.'.join(keys)} "
            f"(set an explicit value, don't leave it blank or `~`)"
        )
    return cur


def load(path: str | pathlib.Path) -> ResearchMd:
    path = pathlib.Path(path).resolve()
    text = path.read_text()

    # RESEARCH.md may be pure YAML or Markdown-with-YAML-frontmatter.
    if text.lstrip().startswith("---"):
        # strip frontmatter markers
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[1]

    d = yaml.safe_load(text) or {}
    if not isinstance(d, dict):
        raise ValueError(f"RESEARCH.md {path} did not parse as a mapping")

    data_loc = _get(d, ["data", "location"], required=True)
    # allow env var interpolation
    data_loc = os.path.expandvars(os.path.expanduser(str(data_loc)))
    data_loc_path = pathlib.Path(data_loc).resolve()

    # Optional shared dataset cache. If provided, expand env vars and
    # resolve to an absolute path. If absent, Workspace will default it to
    # ``<workspaces_root_parent>/datasets/`` at init time so that every
    # run under the same workspaces root shares a cache.
    raw_cache = _get(d, ["data", "shared_cache"], default=None)
    if raw_cache is None:
        shared_cache_override: pathlib.Path | None = None
    else:
        expanded = os.path.expandvars(os.path.expanduser(str(raw_cache)))
        shared_cache_override = pathlib.Path(expanded).resolve()

    # omicairn_config.yaml overlay (NEW). When a sibling omicairn_config.yaml
    # exists next to RESEARCH.md, its values OVERRIDE the matching
    # RESEARCH.md fields. This separates the semantic brief (markdown,
    # what the agent reads as instructions) from the orchestration
    # config (yaml, what the orchestrator consumes). RESEARCH.md
    # `budgets:` / `bounds:` / `auto_approve` / `data.shared_cache`
    # still work for backward compat — they become the defaults.
    #
    # The new `max_phase_wall_minutes_by_phase` block has no RESEARCH.md
    # counterpart; it lives only in omicairn_config.yaml.
    cfg_path = path.parent / "omicairn_config.yaml"
    cfg: dict[str, Any] = {}
    if cfg_path.exists():
        cfg_text = cfg_path.read_text()
        cfg_loaded = yaml.safe_load(cfg_text) or {}
        if not isinstance(cfg_loaded, dict):
            raise ValueError(
                f"omicairn_config.yaml {cfg_path} did not parse as a mapping"
            )
        cfg = cfg_loaded
    # Layered override: cfg values win when present, else fall back
    # to the RESEARCH.md `d` block. Use a small helper.
    def _ov(cfg_keys: list[str], rmd_keys: list[str], default: Any) -> Any:
        if cfg:
            v = _get(cfg, cfg_keys, default=None)
            if v is not None:
                return v
        return _get(d, rmd_keys, default=default)

    _warn_deprecated_keys(cfg, d)

    # Shared cache: omicairn_config.yaml's `shared_cache:` is preferred.
    cfg_cache = None
    if cfg:
        cfg_cache_raw = _get(cfg, ["shared_cache"], default=None)
        if cfg_cache_raw is not None:
            cfg_cache_expanded = os.path.expandvars(
                os.path.expanduser(str(cfg_cache_raw))
            )
            cfg_cache = pathlib.Path(cfg_cache_expanded).resolve()
    final_shared_cache = cfg_cache if cfg_cache is not None else shared_cache_override

    # Per-phase budget dict from omicairn_config.yaml (no RESEARCH.md fallback).
    raw_by_phase = (
        _get(cfg, ["budgets", "max_phase_wall_minutes_by_phase"], default=None)
        if cfg else None
    )
    if raw_by_phase is None:
        max_phase_wall_minutes_by_phase: dict[str, int] = {}
    elif isinstance(raw_by_phase, dict):
        max_phase_wall_minutes_by_phase = {
            str(k): int(v) for k, v in raw_by_phase.items()
        }
    else:
        raise ValueError(
            f"omicairn_config.yaml {cfg_path} field "
            f"budgets.max_phase_wall_minutes_by_phase must be a mapping; "
            f"got {type(raw_by_phase).__name__}"
        )

    # Stage time-limit MODE (budgets.stage_time_limit_mode). Overlaid like the
    # other budget fields. Normalised case-insensitively; an absent/None/
    # unrecognised value degrades to DEFAULT_STAGE_TIME_LIMIT_MODE (dynamic) so
    # older briefs load unchanged. The final env override + per-run resolution
    # still runs through constants.stage_time_limit_mode at dispatch time.
    _raw_mode = _ov(
        ["budgets", "stage_time_limit_mode"],
        ["budgets", "stage_time_limit_mode"],
        DEFAULT_STAGE_TIME_LIMIT_MODE,
    )
    _mode_str = str(_raw_mode).strip().lower() if _raw_mode is not None else ""
    stage_time_limit_mode = (
        _mode_str if _mode_str in STAGE_TIME_LIMIT_MODES
        else DEFAULT_STAGE_TIME_LIMIT_MODE
    )

    # Stage ITERATION mode (budgets.stage_iter_mode). Sibling of the time-limit
    # mode above; overlaid + normalised the same way (case-insensitive, absent/
    # unrecognised → DEFAULT_STAGE_ITER_MODE so older briefs load unchanged and
    # get dynamic). Final env override + per-run resolution runs through
    # constants.stage_iter_mode at the bounds-check seam.
    _raw_iter_mode = _ov(
        ["budgets", "stage_iter_mode"],
        ["budgets", "stage_iter_mode"],
        DEFAULT_STAGE_ITER_MODE,
    )
    _iter_mode_str = (
        str(_raw_iter_mode).strip().lower() if _raw_iter_mode is not None else ""
    )
    stage_iter_mode = (
        _iter_mode_str if _iter_mode_str in STAGE_ITER_MODES
        else DEFAULT_STAGE_ITER_MODE
    )

    # OPTIONAL declared EXECUTE workload scale (`workload:` block). All fields
    # optional + degrade-safe: a non-int / negative / unparseable value → None
    # (estimator falls back to the resolved wall). Never raises.
    def _opt_pos_int(cfg_keys: list[str], rmd_keys: list[str]) -> int | None:
        raw = _ov(cfg_keys, rmd_keys, None)
        if raw is None:
            return None
        try:
            v = int(raw)
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None

    workload_n_models = _opt_pos_int(
        ["workload", "n_models"], ["workload", "n_models"])
    workload_n_eval_items = _opt_pos_int(
        ["workload", "n_eval_items"], ["workload", "n_eval_items"])
    workload_is_finetune = bool(
        _ov(["workload", "is_finetune"], ["workload", "is_finetune"], False))

    # IDEATE source-collection targets (hyperparameters for the brainstorm
    # stage). Configurable per case via an `ideate:` block in
    # omicairn_config.yaml (preferred) or RESEARCH.md. Defaults give a
    # balanced survey. `target_beliefs` covers stated positions from
    # researchers collectively (professor + nobel + philosopher + industry).
    _IDEATE_DEFAULTS = {
        "target_papers": 8,
        "target_blogs": 3,
        "target_beliefs": 4,
        "min_directions": 3,
        "max_directions": 6,
    }
    ideate_config: dict[str, int] = {}
    for _k, _default in _IDEATE_DEFAULTS.items():
        _v = _ov(["ideate", _k], ["ideate", _k], _default)
        try:
            ideate_config[_k] = max(0, int(_v))
        except (TypeError, ValueError):
            raise ValueError(
                f"ideate.{_k} must be an integer; got {_v!r}"
            )
    if ideate_config["min_directions"] > ideate_config["max_directions"]:
        raise ValueError(
            f"ideate.min_directions ({ideate_config['min_directions']}) > "
            f"ideate.max_directions ({ideate_config['max_directions']})"
        )

    # Depth / rigor SOFT targets (biomedical-2026 thoroughness). Read
    # from a `depth:` block (or its `rigor:` alias) in omicairn_config.yaml
    # (preferred) or RESEARCH.md. Every key defaults to None = "no
    # constraint", so omitting the block is a no-op. These are advisory
    # — surfaced to prompts/REVIEW, NEVER hard contract failures.
    depth_config: dict[str, Any] = {k: None for k in DEPTH_CONFIG_KEYS}
    for _section in ("depth", "rigor"):
        for _k in DEPTH_CONFIG_KEYS:
            _v = _ov([_section, _k], [_section, _k], None)
            if _v is None:
                continue
            if _k in DEPTH_CONFIG_BOOL_KEYS:
                depth_config[_k] = bool(_v)
            else:
                try:
                    depth_config[_k] = int(_v)
                except (TypeError, ValueError):
                    raise ValueError(
                        f"{_section}.{_k} must be an integer; got {_v!r}"
                    )

    # Compute-resource block. omicairn_config.yaml's `compute:` is preferred;
    # RESEARCH.md `compute:` is the fallback (back-compat with the overlay
    # philosophy). Resolved against the nearest COMPUTATION_RESOURCES.md
    # catalog relative to the case directory (path.parent). A case with no
    # `compute:` block resolves to the CPU-only DEFAULT_LOCAL fallback.
    compute_block = None
    if cfg:
        compute_block = _get(cfg, ["compute"], default=None)
    if compute_block is None:
        compute_block = _get(d, ["compute"], default=None)
    compute = _resolve_compute(compute_block, path.parent)

    # Contribution-depth knob. omicairn_config.yaml's `contribution_type:` is
    # preferred; RESEARCH.md `contribution_type:` is the fallback (same
    # overlay philosophy as compute/depth). Default "analysis" (back-compat
    # — no existing case declares it). An unknown value fails LOUDLY in
    # contribution.validate (a typo'd knob is a config bug).
    contribution_type = _contribution.validate(
        _ov(["contribution_type"], ["contribution_type"], None)
    )

    # Research-arc knob. omicairn_config.yaml's `research_arc:` is preferred;
    # RESEARCH.md `research_arc:` is the fallback (same overlay philosophy).
    # Default unset (None). An OPTIONAL FREE-TEXT hint — normalised TOLERANTLY
    # in contribution.validate_arc (empty degrades to None; no fixed value set
    # to reject against), never raises.
    research_arc = _contribution.validate_arc(
        _ov(["research_arc"], ["research_arc"], None)
    )

    research_question = _get(d, ["research_question"], required=True)
    goals = _parse_goals(d, research_question)

    # SCOPE feasibility / statistical-power gate config. The `feasibility:`
    # mapping is overlaid from omicairn_config.yaml (preferred) or RESEARCH.md,
    # then parsed by feasibility.parse_config (tolerant: an absent/None/
    # malformed block → neutral, exploratory-friendly defaults).
    from . import feasibility
    raw_feas = _ov(["feasibility"], ["feasibility"], None)
    feasibility_config = feasibility.parse_config(
        raw_feas if isinstance(raw_feas, dict) else None)

    return ResearchMd(
        research_mode=_get(d, ["research_mode"], default="METHOD"),
        domain=_get(d, ["domain"], default="general"),
        topic_slug=_get(d, ["topic_slug"], required=True),
        research_question=research_question,
        data_location=data_loc_path,
        data_description=_get(d, ["data", "description"], default=""),
        cohorts=_get(d, ["data", "cohorts"], default=[]) or [],
        modalities=_get(d, ["data", "modalities"], default=[]) or [],
        shared_cache_override=final_shared_cache,
        primary_metric=_get(d, ["success_criteria", "primary_metric"], default="unspecified"),
        baseline=_get(d, ["success_criteria", "baseline"], default=None),
        pre_registration=_get(d, ["success_criteria", "pre_registration"], default="optional") == "required",
        honest_validation=_get(d, ["success_criteria", "honest_validation"], default="optional") == "required",
        max_phase_wall_minutes=int(_ov(
            ["budgets", "max_phase_wall_minutes"],
            ["budgets", "max_phase_wall_minutes"],
            DEFAULT_MAX_PHASE_WALL_MINUTES)),
        max_total_wall_minutes=int(_ov(
            ["budgets", "max_total_wall_minutes"],
            ["budgets", "max_total_wall_minutes"],
            DEFAULT_MAX_TOTAL_WALL_MINUTES)),
        allow_web=bool(_ov(
            ["budgets", "allow_web"],
            ["budgets", "allow_web"], True)),
        claude_model=_ov(
            ["budgets", "claude_model"],
            ["budgets", "claude_model"], "claude-opus-4-8"),
        auto_approve=bool(_ov(
            ["auto_approve"], ["auto_approve"], True)),
        # Default DEFAULT_RUN_BUDGET_USD (None = unlimited per owner 2026-06-27;
        # runner omits --max-budget-usd → no per-phase spend halt). A case that
        # configures a number gets that float cap instead.
        max_phase_budget_usd=(
            float(_budget_v)
            if (_budget_v := _ov(
                ["budgets", "max_phase_budget_usd"],
                ["budgets", "max_phase_budget_usd"],
                DEFAULT_RUN_BUDGET_USD)) is not None
            else None),
        max_iters_per_phase=int(_ov(
            ["bounds", "max_iters_per_phase"],
            ["bounds", "max_iters_per_phase"],
            DEFAULT_MAX_ITERS_PER_PHASE)),
        max_backtracks=int(_ov(
            ["bounds", "max_backtracks"],
            ["bounds", "max_backtracks"],
            DEFAULT_MAX_BACKTRACKS)),
        max_phase_wall_minutes_by_phase=max_phase_wall_minutes_by_phase,
        goals=goals,
        allow_on_the_fly_hypotheses=bool(_ov(
            ["allow_on_the_fly_hypotheses"],
            ["allow_on_the_fly_hypotheses"], True)),
        max_on_the_fly_hypotheses=int(_ov(
            ["max_on_the_fly_hypotheses"],
            ["max_on_the_fly_hypotheses"], 5)),
        ideate_config=ideate_config,
        prefer_reputable_sources=bool(_ov(
            ["prefer_reputable_sources"],
            ["prefer_reputable_sources"], False)),
        depth_config=depth_config,
        contribution_type=contribution_type,
        research_arc=research_arc,
        compute=compute,
        raw=d,
        source_path=path,
        feasibility_config=feasibility_config,
        stage_time_limit_mode=stage_time_limit_mode,
        stage_iter_mode=stage_iter_mode,
        workload_n_models=workload_n_models,
        workload_n_eval_items=workload_n_eval_items,
        workload_is_finetune=workload_is_finetune,
    )
