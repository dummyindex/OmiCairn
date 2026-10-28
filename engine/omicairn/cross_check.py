"""Post-execute semantic consistency checks.

The basic contract in `contracts.py` only checks file existence and
size. These checks go further — they inspect the content to catch
the kind of subtle cross-artifact disagreements that slip past
prompt-level discipline.

`check_execute(execute_stage_dir)` compares `results.json` against
`analysis.md` within a single execute stage directory and flags CI
bounds from results.json that are completely absent from analysis.md —
a strong signal that the analyst recomputed statistics instead of
copying them.

`build_execute_verdict(...)` wraps `check_execute` and returns a
`Verdict` suitable for writing to the cross_check stage dir.

Schema normalisation
--------------------
`results.json` is meant to use the canonical shape::

    {
      "primary_model": { "auc": ..., "ci_95": [lo, hi], ... },
      "baselines":     { "<name>": { "auc": ..., "ci_95": [lo, hi], ... } },
      "comparisons":   { "<name_vs_name>": { "delta": ..., "ci_95": [lo, hi] } }
    }

but real executor runs drift into legacy shapes — e.g. a top-level
`conditions` dict or `ci_lower_95`/`ci_upper_95` sibling scalars. The
`normalize_results_json()` helper rewrites those into the canonical
form in memory (never on disk) so downstream enforcement — especially
the "baselines without paired delta CI" error — fires consistently
regardless of which shape the executor happened to produce.
"""
from __future__ import annotations

import ast
import collections
import dataclasses
import json
import math
import pathlib
import re
from typing import Any

from . import parallel_arms as _parallel_arms
from . import verdict as verdict_mod
from .constants import (
    ANALYSIS_MD as _ANALYSIS_MD,
    ARM_MANIFEST_FILE as _ARM_MANIFEST_FILE,
    ARM_PROVENANCE_FRAGMENT as _ARM_PROVENANCE_FRAGMENT,
    CODE_SUBDIR as _CODE_SUBDIR,
    DATA_FETCH_LOG as _DATA_FETCH_LOG,
    ConstructValidityGate,
    DecisiveArmPowerGate,
    EffectClearsMdeGate,
    FIGURES_CONTRACT_JSON as _FIGURES_CONTRACT_JSON,
    FIGURES_SUBDIR as _FIGURES_SUBDIR,
    LEAN_VERDICT_SUFFIX as _LEAN_VERDICT_SUFFIX,
    LatestExecuteProvenanceGate,
    ParallelArmIndependenceGate,
    PREREGISTRATION_FILE as _PREREGISTRATION_FILE,
    PhaseName,
    PreregCompletenessGate,
    PreregField,
    RESOURCE_REQUEST_MD as _RESOURCE_REQUEST_MD,
    RESULTS_JSON as _RESULTS_JSON,
    ResultsField,
    ScopedExitCorroborationGate,
    parse_stage_dir_name,
)


@dataclasses.dataclass
class CrossCheckResult:
    ok: bool
    warnings: list[str]
    errors: list[str]

    def summary(self) -> str:
        lines = []
        if self.errors:
            lines.append("ERRORS: " + " | ".join(self.errors))
        if self.warnings:
            lines.append("WARNINGS: " + " | ".join(self.warnings))
        return "; ".join(lines) if lines else "ok"


# Sentinel for "path does not resolve" in results.json lookups.
_RESOLVE_MISSING = object()


# Condition names that clearly identify a baseline rather than the
# primary model. Used by normalize_results_json() to split a legacy
# `conditions.*` dict into primary_model + baselines.*.
#
# Keep this list GENERIC — field-specific baseline tokens
# (e.g. "grade_only", "stage_only", "age_only" for survival/prognostic
# work) belong in the domain pack's profile.yaml under
# `baseline_name_hints:`, merged in by
# `baseline_name_hints(domain)` below.
_BASELINE_NAME_HINTS_GENERIC: tuple[str, ...] = (
    "null",
    "random",
    "baseline",
    "adversarial",
    "single_feature",
    "single-feature",
    "naive",
    "constant",
    "majority",
    "trivial",
    "reference",
)


def baseline_name_hints(domain: str | None = None) -> tuple[str, ...]:
    """Return the effective baseline-name hint list for `domain`.

    Merges the generic defaults with any `baseline_name_hints` list
    from the resolved domain pack's `profile.yaml`. A domain pack
    that does not declare the key contributes nothing.
    """
    from . import domain_pack as dp
    extra: list[str] = []
    try:
        profile = dp.load_profile(dp.resolve_pack(domain) or "")
        raw = profile.get("baseline_name_hints") or []
        if isinstance(raw, list):
            extra = [str(x).lower() for x in raw if x]
    except Exception:
        extra = []
    return tuple(_BASELINE_NAME_HINTS_GENERIC) + tuple(extra)


def _is_baseline_name(name: str, domain: str | None = None) -> bool:
    n = name.lower()
    return any(h in n for h in baseline_name_hints(domain))


def _guess_primary_condition(conditions: dict) -> str | None:
    """Heuristically pick the primary condition from a legacy `conditions` dict."""
    if not conditions:
        return None
    for name, block in conditions.items():
        if isinstance(block, dict) and block.get("role") == "primary":
            return name
    for name in conditions:
        nl = name.lower()
        if "primary" in nl or "full_model" in nl or nl == "full":
            return name
    for name in conditions:
        if not _is_baseline_name(name):
            return name
    return None


def _has_sibling_scalar_ci(obj: Any) -> bool:
    if isinstance(obj, dict):
        if "ci_lower_95" in obj and "ci_upper_95" in obj:
            return True
        return any(_has_sibling_scalar_ci(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_sibling_scalar_ci(v) for v in obj)
    return False


def _normalize_ci(block: Any) -> Any:
    if not isinstance(block, dict):
        return block
    out = dict(block)
    if "ci_95" not in out and "ci_lower_95" in out and "ci_upper_95" in out:
        try:
            out["ci_95"] = [float(out["ci_lower_95"]), float(out["ci_upper_95"])]
        except (TypeError, ValueError):
            pass
    return out


def normalize_results_json(results: Any) -> Any:
    """Return a canonical-shape copy of `results`.

    Rewrites:
      - legacy `conditions.*` dict → `primary_model` + `baselines.*`
      - sibling-scalar `ci_lower_95`/`ci_upper_95` → `ci_95: [lo, hi]`
      - `comparisons.*` entries with sibling-scalar CIs → `ci_95`

    Does not touch disk. Leaves unrecognised structures alone.
    """
    if not isinstance(results, dict):
        return results
    normalized = dict(results)

    if (
        ResultsField.CONDITIONS in normalized
        and ResultsField.PRIMARY_MODEL not in normalized
        and isinstance(normalized[ResultsField.CONDITIONS], dict)
    ):
        conditions = normalized.pop(ResultsField.CONDITIONS)
        primary_key = _guess_primary_condition(conditions)
        baselines: dict[str, Any] = {}
        for name, block in conditions.items():
            if not isinstance(block, dict):
                continue
            normalised_block = _normalize_ci(block)
            if name == primary_key:
                normalized[ResultsField.PRIMARY_MODEL] = normalised_block
            else:
                baselines[name] = normalised_block
        if baselines:
            normalized[ResultsField.BASELINES] = baselines

    if isinstance(normalized.get(ResultsField.PRIMARY_MODEL), dict):
        normalized[ResultsField.PRIMARY_MODEL] = _normalize_ci(
            normalized[ResultsField.PRIMARY_MODEL]
        )
    if isinstance(normalized.get(ResultsField.BASELINES), dict):
        normalized[ResultsField.BASELINES] = {
            k: _normalize_ci(v) for k, v in normalized[ResultsField.BASELINES].items()
        }
    if isinstance(normalized.get(ResultsField.COMPARISONS), dict):
        normalized[ResultsField.COMPARISONS] = {
            k: _normalize_ci(v) for k, v in normalized[ResultsField.COMPARISONS].items()
        }

    return normalized


# Generic CI-walk skip patterns. A domain pack may declare additional
# patterns under `ci_skip_paths:` in its profile.yaml (substring match,
# case-insensitive, e.g. "multivariate_cox.variables" for
# survival_prognostic).
_CI_SKIP_GENERIC_SUBSTRING: tuple[str, ...] = ("univariate",)
_CI_SKIP_GENERIC_REGEX: tuple[str, ...] = (r"h2[._\[]",)


def ci_skip_patterns(domain: str | None = None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return (substring_patterns, regex_patterns) for `domain`.

    Generic defaults plus any `ci_skip_paths:` list declared by the
    resolved domain pack's `profile.yaml`. Pack entries are treated
    as case-insensitive substrings.
    """
    extras = tuple(sorted(_profile_str_list(domain, "ci_skip_paths")))
    return (_CI_SKIP_GENERIC_SUBSTRING + extras, _CI_SKIP_GENERIC_REGEX)


def _walk_ci95(
    obj: Any, path: str = "", domain: str | None = None
) -> list[tuple[str, float, float]]:
    """Yield (path, lower, upper) for every canonical `ci_95: [lo, hi]` field.

    Call `normalize_results_json()` first if the input may contain
    legacy sibling-scalar CIs.

    Skips:
      - CIs containing NaN/Inf (these mark a deliberately-skipped
        analysis whose placeholder values must never appear in text).
      - Paths matching the generic or domain-pack skip patterns
        returned by `ci_skip_patterns(domain)` (exploratory tables
        that are summarised rather than listed verbatim).
    """
    substring_pats, regex_pats = ci_skip_patterns(domain)
    out: list[tuple[str, float, float]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if k == "ci_95" and isinstance(v, list) and len(v) == 2:
                try:
                    lo = float(v[0])
                    hi = float(v[1])
                except (TypeError, ValueError):
                    continue
                if math.isnan(lo) or math.isnan(hi) or math.isinf(lo) or math.isinf(hi):
                    continue
                p_lower = path.lower()
                if any(s in p_lower for s in substring_pats):
                    continue
                if any(re.search(pat, p_lower) for pat in regex_pats):
                    continue
                out.append((path or k, lo, hi))
            else:
                out.extend(_walk_ci95(v, p, domain=domain))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_walk_ci95(v, f"{path}[{i}]", domain=domain))
    return out


# ---------------------------------------------------------------------------
# Numeric sanity helpers — catch classes of fabrication that pass the
# "CI appears verbatim in analysis.md" check but are nonetheless
# impossible values (reversed CIs, point estimates outside their own CI,
# out-of-range bounded metrics, impossible p-values). These fire on
# canonical-shape results.json blocks (primary_model, baselines.*,
# comparisons.*) — run `normalize_results_json()` first.
# ---------------------------------------------------------------------------

# Metric-bounds classes. Case-insensitive exact-match on the metric
# field name. Generic defaults cover universal metrics; field-specific
# names (brier_score, bleu/rouge, ndcg/map, r_squared, chi2_reduced, …)
# go in the domain pack's profile.yaml under
# `metric_bounds_{0_1, neg1_1, nonneg}:`, merged by the helpers below.
_METRIC_BOUNDS_0_1_GENERIC: frozenset[str] = frozenset({
    "auc", "auroc", "auprc", "aupr", "ap", "average_precision",
    "accuracy", "acc", "precision", "recall", "f1", "f1_score",
    "sensitivity", "specificity", "concordance_index", "c_index",
    "cindex", "harrell_c", "mcc",
})

_METRIC_BOUNDS_NEG1_1_GENERIC: frozenset[str] = frozenset({
    "pearson", "spearman", "kendall", "pearson_r", "spearman_r",
    "kendall_tau", "correlation", "corr", "rho",
})

_METRIC_BOUNDS_NONNEG_GENERIC: frozenset[str] = frozenset({
    "rmse", "mae", "mse", "logloss", "log_loss", "cross_entropy",
    "xent", "brier", "brier_score",
})


def _profile_str_list(domain: str | None, key: str) -> frozenset[str]:
    """Read `profile.yaml::<key>` (a list of strings) from the resolved
    pack as a lowercased frozenset. Silent on any failure."""
    from . import domain_pack as dp
    try:
        profile = dp.load_profile(dp.resolve_pack(domain) or "")
    except Exception:
        return frozenset()
    raw = profile.get(key) or []
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(x).lower() for x in raw if x)


def metric_bounds_0_1(domain: str | None = None) -> frozenset[str]:
    return _METRIC_BOUNDS_0_1_GENERIC | _profile_str_list(domain, "metric_bounds_0_1")


def metric_bounds_neg1_1(domain: str | None = None) -> frozenset[str]:
    return _METRIC_BOUNDS_NEG1_1_GENERIC | _profile_str_list(domain, "metric_bounds_neg1_1")


def metric_bounds_nonneg(domain: str | None = None) -> frozenset[str]:
    return _METRIC_BOUNDS_NONNEG_GENERIC | _profile_str_list(domain, "metric_bounds_nonneg")


# W2/W5 are biomedical-style conventions and false-fire on other fields
# (CS/ML benchmarks, theory, math, Bayesian-only work, exploratory
# descriptive studies). Each domain pack opts in per patch via
# profile.yaml::enforce_execute_checks. W7 and W8 are
# always-on: W7 silent-skips when the plan has no stratum marker, W8's
# renderer / viz-data separation is a universal hygiene pattern.

_EXECUTE_CHECKS_KEY: str = "enforce_execute_checks"
# The `disable_execute_checks:` profile key — an OPT-OUT list for the
# default-ON execute checks (currently only w32). A domain pack that lists a
# default-on id here suppresses it; every other domain gets it automatically.
_DISABLE_EXECUTE_CHECKS_KEY: str = "disable_execute_checks"
_W_CHECK_IDS_EXECUTE: frozenset[str] = frozenset({"w2", "w5", "w32"})
# Execute checks that are ON BY DEFAULT (opt-OUT via `disable_execute_checks`),
# in contrast to w2/w5 which are opt-IN via `enforce_execute_checks`. W32
# (parallel-arm contract) is default-on because its manifest-level invariants
# are BLOCKING and it is a silent no-op for every run that does not fan out —
# so a domain pack never needs to remember to enable it, but may opt out.
_W_CHECK_IDS_EXECUTE_DEFAULT_ON: frozenset[str] = frozenset({"w32"})


def enabled_execute_checks(domain: str | None = None) -> frozenset[str]:
    """W-check IDs enabled on execute for `domain`.

    Two classes: the opt-IN checks (w2/w5 — enabled only when the domain
    pack lists them under ``enforce_execute_checks``) UNION the default-ON
    checks (w32 — enabled unless the pack lists it under
    ``disable_execute_checks``)."""
    opt_in = _profile_str_list(domain, _EXECUTE_CHECKS_KEY) & _W_CHECK_IDS_EXECUTE
    disabled = _profile_str_list(domain, _DISABLE_EXECUTE_CHECKS_KEY)
    default_on = _W_CHECK_IDS_EXECUTE_DEFAULT_ON - disabled
    return opt_in | default_on


# Field-name prefixes that identify p-value scalars in any block.
_P_VALUE_PREFIXES: tuple[str, ...] = ("p_value", "p_directional", "pvalue")

# Field-name prefixes that identify NON-metric scalars commonly found
# alongside a point-estimate + ci_95 in primary_model / baselines.* /
# comparisons.*. These must NOT be picked as the "point estimate" the
# CI bounds:
#   `n_*`         — sample/event/feature counts (n_events, n_samples,
#                    n_selected_genes, n_features, n_clusters, ...)
#   `lambda_*`    — regularisation hyperparameter values
#   `alpha_*`     — significance level / regularisation values
#   `cv_*`        — CV fold counts / metric names
#   `seed_*`      — RNG seeds
#   `bootstrap_*` — resample counts
#
# Real bug observed in ddr-blca-20260430 run: agent wrote
# primary_model = {"n": 347, "n_events": 231, "concordance_index":
# 0.518, "ci_95": [0.479, 0.556], ...}. With insertion-order dict
# walks, `n_events` was found before `concordance_index`, picked as
# the point estimate (231), and flagged as outside the c-index CI.
_NON_METRIC_FIELD_PREFIXES: tuple[str, ...] = (
    "n_", "lambda_", "alpha_", "cv_", "seed_", "bootstrap_",
)

# The canonical confidence-interval key, and the suffix that marks a
# metric-specific CI sibling. A block may carry the canonical `ci_95`
# (which pairs with the block's single point estimate) AND additional
# per-metric CIs keyed `<metric>_ci_95` (e.g. `probe_auroc` paired with
# `probe_auroc_ci_95`). Both flavours are load-bearing and must contain
# the value they bound. Defined as single module-level constants so the
# `_ci_95` literal is never scattered raw through the checker (GP7).
_CI95_KEY: str = "ci_95"
_CI95_SUFFIX: str = "_" + _CI95_KEY

# Field-name suffixes/exact matches that identify the point estimate
# metric scalar when walking a block. A "scalar point estimate" is any
# float-valued field that is NOT ci_95 (a list), NOT a p-value, NOT a
# hash/checksum, and NOT one of the known metadata keys.
_METADATA_FIELD_NAMES: frozenset[str] = frozenset({
    "ci_95", "n", "n_resamples", "n_bootstrap", "n_permutations",
    "bootstrap_indices", "permutation_seed", "seed", "method", "role",
    "preregistration_sha256", "locked_model_sha256", "model_sha256",
    "sha256", "hash",
})


def _is_point_estimate_field(name: str) -> bool:
    """True if `name` could plausibly be the point-estimate scalar of a
    primary_model/baseline/comparison block. Excludes CI lists, p-values,
    hashes, count-style metadata (n_*), hyperparameters (lambda_*,
    alpha_*, cv_*, seed_*, bootstrap_*), and the known metadata keys —
    everything left is assumed to be a metric value."""
    lower = name.lower()
    if lower in _METADATA_FIELD_NAMES:
        return False
    if any(lower.startswith(p) for p in _P_VALUE_PREFIXES):
        return False
    if any(lower.startswith(p) for p in _NON_METRIC_FIELD_PREFIXES):
        return False
    if lower.endswith("_sha256") or lower.endswith("_hash"):
        return False
    return True


def _extract_point_estimate(block: dict) -> tuple[str, float] | None:
    """Return (field_name, value) for the first plausible point-estimate
    scalar in `block`. Returns None if none found.

    Preference order:
      1. "delta" (canonical for comparisons.* blocks)
      2. Any non-metadata float scalar (canonical for primary_model /
         baselines.* blocks: the metric value sits alongside ci_95).
    """
    if not isinstance(block, dict):
        return None
    if "delta" in block:
        try:
            return ("delta", float(block["delta"]))
        except (TypeError, ValueError):
            return None
    for key, value in block.items():
        if not _is_point_estimate_field(key):
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                return (key, float(value))
            except (TypeError, ValueError):
                continue
    return None


def _iter_load_bearing_blocks(results: Any):
    """Yield (path, block_dict) for every canonical block whose numeric
    correctness is load-bearing: `primary_model`, every `baselines.*`,
    every `comparisons.*`, plus nested hypothesis sub-blocks under
    ``primary_model.hypotheses.H<n>`` and stratum-trained sub-blocks
    under ``primary_model.stratum_trained_<name>``.

    Tier 2.4 fix: nested hypothesis blocks were previously not walked,
    so a CI-contains-point violation under
    ``primary_model.hypotheses.H1.cluster_composition_concordance`` (the
    real morphogen-organoid run anomaly) was not caught.
    """
    if not isinstance(results, dict):
        return
    pm = results.get(ResultsField.PRIMARY_MODEL)
    if isinstance(pm, dict):
        yield (ResultsField.PRIMARY_MODEL, pm)
        # Hypothesis sub-blocks. Each may carry its own point + ci_95.
        hyps = pm.get("hypotheses")
        if isinstance(hyps, dict):
            for hid, hblock in hyps.items():
                if isinstance(hblock, dict) and not _is_explicit_skip_block(hblock):
                    yield (f"{ResultsField.PRIMARY_MODEL}.hypotheses.{hid}", hblock)
        # Stratum-trained sub-blocks (W7).
        for key, sblock in pm.items():
            if isinstance(key, str) and key.startswith("stratum_trained_"):
                if isinstance(sblock, dict) and not _is_explicit_skip_block(sblock):
                    yield (f"{ResultsField.PRIMARY_MODEL}.{key}", sblock)
    for name, block in (results.get(ResultsField.BASELINES) or {}).items():
        if isinstance(block, dict):
            yield (f"{ResultsField.BASELINES}.{name}", block)
    for name, block in (results.get(ResultsField.COMPARISONS) or {}).items():
        if isinstance(block, dict):
            yield (f"{ResultsField.COMPARISONS}.{name}", block)


def _is_explicit_skip_block(block: dict) -> bool:
    """True if `block` looks like a deliberately-skipped sentinel — i.e.
    its canonical point-estimate key is present but set to None. Such a
    block is the executor's way of saying "this comparison was
    intentionally not computed"; NaN CI bounds paired with None estimates
    are honest placeholders, not fabricated numbers, and the analyst is
    free to omit them from analysis.md.
    """
    if not isinstance(block, dict):
        return False
    if "delta" in block and block["delta"] is None:
        return True
    # primary_model / baselines.* case: any non-metadata scalar explicitly
    # set to None marks the whole block as skipped.
    for key, value in block.items():
        if not _is_point_estimate_field(key):
            continue
        if value is None:
            return True
    return False


def _check_ci_order_and_nan(
    results: Any,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Flag reversed CIs (lo > hi) and NaN/Inf CI bounds in load-bearing
    blocks. Silent NaN skipping is fine for exploratory sub-tables (and
    _walk_ci95 handles those), but NaN inside `primary_model.ci_95` or
    `comparisons.*.ci_95` is an error — a failed computation or a
    placeholder the executor forgot to replace.

    Exception: a block whose canonical point-estimate key is explicitly
    None (e.g. `{"delta": None, "ci_95": [NaN, NaN]}`) is recognised as
    a deliberately-skipped sentinel and passes through untouched.
    """
    for path, block in _iter_load_bearing_blocks(results):
        ci = block.get("ci_95")
        if not (isinstance(ci, list) and len(ci) == 2):
            continue
        if _is_explicit_skip_block(block):
            continue
        try:
            lo = float(ci[0])
            hi = float(ci[1])
        except (TypeError, ValueError):
            errors.append(
                f"{path}.ci_95={ci!r} is not a pair of floats"
            )
            continue
        if math.isnan(lo) or math.isnan(hi) or math.isinf(lo) or math.isinf(hi):
            errors.append(
                f"{path}.ci_95=[{ci[0]}, {ci[1]}] contains NaN/Inf — "
                "load-bearing blocks cannot carry placeholder CIs. "
                "Either compute the CI honestly or drop the block."
            )
            continue
        if lo > hi:
            errors.append(
                f"{path}.ci_95=[{lo}, {hi}] has lower > upper — "
                "reversed or fabricated CI. A real bootstrap / "
                "permutation CI always has ci[0] ≤ ci[1]."
            )


def _check_point_estimate_in_ci(
    results: Any,
    errors: list[str],
) -> None:
    """For every load-bearing block, every (point estimate, CI) pairing
    MUST be self-consistent: the point estimate lies inside its own CI.
    A CI that does not contain the value it bounds is either a
    fabrication or a serious computational bug, which invalidates the
    block. Two pairing flavours are checked: the canonical
    `_extract_point_estimate()` scalar against `ci_95`, AND every
    metric-suffixed `<metric>` / `<metric>_ci_95` pair — so a block with
    a valid canonical metric alongside a second metric whose own suffixed
    CI excludes its value (the fair-removal-vs-equalization `probe_auroc`
    / `probe_auroc_ci_95` breach that reached a shipped headline) is no
    longer silently passed.
    """
    for path, block in _iter_load_bearing_blocks(results):
        # (point-name, ci-key) pairs: suffixed metrics first, then the
        # canonical pair (skipped if a suffixed pair already covered that
        # point, so one breach is never double-reported).
        pairs: list[tuple[str, str]] = []
        covered: set[str] = set()
        for key in block:
            if not (isinstance(key, str) and key.endswith(_CI95_SUFFIX)):
                continue
            metric = key[: -len(_CI95_SUFFIX)]
            ci = block.get(key)
            if metric in block and isinstance(ci, list) and len(ci) == 2:
                pairs.append((metric, key))
                covered.add(metric)
        pe = _extract_point_estimate(block)
        if pe is not None and pe[0] not in covered:
            pairs.append((pe[0], _CI95_KEY))
        for name, ci_key in pairs:
            _check_one_point_in_ci(path, name, ci_key, block, errors)


def _check_one_point_in_ci(
    path: str, name: str, ci_key: str, block: dict, errors: list[str],
) -> None:
    """Append an error if `block[name]` lies outside the CI at
    `block[ci_key]`. Tolerance, reversed-CI normalisation, NaN/Inf
    handling and message shape match the legacy canonical-only check
    exactly; this just parameterises the CI key a point is paired with."""
    ci = block.get(ci_key)
    if not (isinstance(ci, list) and len(ci) == 2):
        return
    try:
        lo = float(ci[0])
        hi = float(ci[1])
    except (TypeError, ValueError):
        return
    if math.isnan(lo) or math.isnan(hi):
        return
    # Normalise reversed CIs so containment still works as a secondary
    # signal even if the order check also fires.
    if lo > hi:
        lo, hi = hi, lo
    raw = block.get(name)
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return
    value = float(raw)
    if math.isnan(value) or math.isinf(value):
        errors.append(
            f"{path}.{name}={value!r} is NaN/Inf — load-bearing "
            "metrics cannot be placeholders."
        )
        return
    # Tiny rounding tolerance — analysts sometimes round CI bounds to
    # 3 dp while keeping the point at full precision, putting it ~5e-4
    # outside the rounded CI without any dishonesty.
    tol = 5e-4
    if value < lo - tol or value > hi + tol:
        errors.append(
            f"{path}.{name}={value:.4f} lies outside its own "
            f"ci_95=[{lo:.4f}, {hi:.4f}] — a CI must contain the "
            "value it bounds. Either the point estimate or the "
            "CI is fabricated."
        )


def _check_bounded_metric_ranges(
    results: Any,
    errors: list[str],
    domain: str | None = None,
) -> None:
    """Walk load-bearing blocks and verify every metric value whose name
    matches a known bounded-metric class (AUC/accuracy/etc in [0,1];
    correlations in [-1,1]; losses ≥ 0). `domain` is forwarded to the
    `metric_bounds_*()` helpers so the resolved pack can extend the
    per-class allowlists with field-specific metrics.
    """
    bounds_0_1 = metric_bounds_0_1(domain)
    bounds_neg1_1 = metric_bounds_neg1_1(domain)
    bounds_nonneg = metric_bounds_nonneg(domain)
    for path, block in _iter_load_bearing_blocks(results):
        for key, value in block.items():
            if not _is_point_estimate_field(key):
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            v = float(value)
            if math.isnan(v) or math.isinf(v):
                continue
            lower_key = key.lower()
            if lower_key in bounds_0_1:
                if v < 0.0 or v > 1.0:
                    errors.append(
                        f"{path}.{key}={v} is outside [0, 1] — "
                        f"{key} is a bounded metric. Likely fabricated "
                        "or a unit mismatch (fraction vs percent)."
                    )
            elif lower_key in bounds_neg1_1:
                if v < -1.0 or v > 1.0:
                    errors.append(
                        f"{path}.{key}={v} is outside [-1, 1] — "
                        f"{key} is a bounded correlation metric."
                    )
            elif lower_key in bounds_nonneg:
                if v < 0.0:
                    errors.append(
                        f"{path}.{key}={v} is negative — "
                        f"{key} is a non-negative loss metric."
                    )


def _check_p_value_validity(
    results: Any,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Walk load-bearing blocks and validate any field that looks like a
    p-value: 0 ≤ p ≤ 1 is a hard rule. Values of exactly 1.0 or 0.0 are
    warnings — real bootstraps / permutation tests cannot produce exact
    endpoints and the prompts explicitly require reporting resolution-
    floor p-values as `p<1/B`, never `p=1/B`.
    """
    for path, block in _iter_load_bearing_blocks(results):
        for key, value in block.items():
            if not any(key.lower().startswith(p) for p in _P_VALUE_PREFIXES):
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            v = float(value)
            if math.isnan(v) or math.isinf(v):
                errors.append(
                    f"{path}.{key}={value!r} is NaN/Inf — p-values "
                    "cannot be placeholders in load-bearing blocks."
                )
                continue
            if v < 0.0 or v > 1.0:
                errors.append(
                    f"{path}.{key}={v} is outside [0, 1] — p-values "
                    "are probabilities and cannot fall outside this range."
                )
                continue
            if v == 0.0 or v == 1.0:
                warnings.append(
                    f"{path}.{key}={v} — exact endpoint p-values are "
                    "almost never real. If this is the bootstrap / "
                    "permutation resolution floor, report it as "
                    "`p<1/B` in analysis.md, never `p=1/B`."
                )


# ---------------------------------------------------------------------------
# Prompt-patch W2/W5 contract enforcement.
#
# W2 (pre-hoc power analysis) and W5 (H1 annotation on the
# primary-vs-baseline panel) turn prompt-level demands into deterministic,
# opt-in checks. Both silent-skip when the expected input artifact is
# absent (no plan sibling, no figure script). This keeps minimal test
# fixtures passing; only present-but-violating artifacts are flagged.
# ---------------------------------------------------------------------------

# W2: Pre-hoc power analysis markers. Any of these tokens inside the
# plan stage's hypotheses.md or experiment_plan.md counts as an
# adequately-declared power section.
_W2_POWER_MARKERS: tuple[str, ...] = (
    "## power",
    "### power",
    "minimum detectable",
    "80% power",
    "α=0.05",
    "alpha=0.05",
    "power analysis",
    " mde ",
)

# Pattern matching a numeric MDE / minimum detectable effect (Tier 2.5
# refinement of W2). A power section that has the heading and the right
# vocabulary but no number is rubber-stamping; W2 should require a
# concrete value.
_W2_NUMERIC_MDE_PATTERN: re.Pattern = re.compile(
    r"(?ix)\b(?:mde|minimum\s+detectable\s+effect)\b[^.\n]{0,80}?"
    r"(?:=|:|is|of|≈|~|approximately)\s*[+\-]?\d+(?:\.\d+)?"
)

# W5: The H1-decision annotation string must include the token `H1`
# plus one of these decision words inside an ax.text / ax.annotate call.
_W5_H1_DECISION_WORDS: tuple[str, ...] = (
    "supported", "not supported", "partial", "rejected", "falsified",
)

# W7: Markers that indicate the plan has committed to a subgroup /
# stratum-trained confirmation analysis. If any of these appear in the
# plan artifacts AND the execute results.json has a primary_model but
# no `stratum_trained_*` entry (or an entry without `skipped=true +
# reason`), W7 fires an error. Silent-skip when no marker is present.
_W7_STRATUM_MARKERS: tuple[str, ...] = (
    "stratum-trained",
    "stratum_trained",
    "stratum trained",
    "subgroup-confirmation",
    "subgroup confirmation",
    "stratified confirmation",
    # Tier 2 expansion: leave-one-line-out and held-out-line phrasings
    # are common in scRNA-seq and clinical-cohort runs (the morphogen
    # run uses "held-out line" verbatim). Without these markers W7
    # silent-skipped on a plan that did commit to per-line stratum fits.
    "held-out line",
    "held out line",
    "hold-one-out",
    "hold one out",
    "leave-one-line-out",
    "leave one line out",
    "leave-one-out per line",
)
_W7_PRIMARY_STRATUM_PREFIX: str = "stratum_trained_"

# W13 — load-bearing-arm gate. The preregistration names the single
# hypothesis/comparison whose completion the headline depends on, by its
# `comparisons.delta_*` key, in this field. When present (named), the
# arm's key(s) MUST resolve in results.json post-EXECUTE or W13 ERRORs —
# the headline cannot ship incomplete. When the field is absent (older
# pre-2026-06 cases), W13 is a silent no-op (backward compatible).
_W13_LOAD_BEARING_ARM_FIELD: str = PreregField.LOAD_BEARING_ARM
# The container in results.json that holds the named arm. The field
# names a `comparisons.delta_*` key, so a bare key resolves under here.
# Aliases the canonical schema name so the literal is never re-typed.
_W13_COMPARISONS_KEY: str = ResultsField.COMPARISONS

# W14 — shared-protocol baseline awareness (ADVISORY / warn-only). The
# preregistration MAY declare a `baselines` block recording whether a
# head-to-head against a named competing/published method on the same
# protocol is feasible (see prompts/plan.md § Shared-protocol baseline).
# Unlike W13 (a hard gate), a missing baseline is sometimes
# legitimately N/A (first-of-kind, pure null/analysis), so W14 never
# ERRORs — it only WARNS when the plan declares no feasible baseline AND
# offers no rationale, or names a feasible baseline but never wires a
# competing-method comparison arm. The METHOD-case >=2-baseline rule stays
# hard-enforced in rules/method_depth.md + REVIEW; W14 extends AWARENESS to
# analysis cases. Absent `baselines` block -> silent no-op (backward compat).
# NOTE: this names the PREREGISTRATION.json baselines block (a PLAN-time field
# read from a DIFFERENT file), NOT the results.json `baselines` schema field —
# it merely shares the spelling, so it is deliberately a standalone literal and
# is NOT aliased to ResultsField.BASELINES.
_W14_BASELINES_FIELD: str = "baselines"
_W14_FEASIBLE_KEY: str = "shared_protocol_feasible"
_W14_CANDIDATES_KEY: str = "candidates"
_W14_RATIONALE_KEY: str = "no_baseline_rationale"

# W15 — salvage-provenance guard (always-on ERROR gate). Companion to W13:
# where W13 verifies the load-bearing arm is COMPLETE, W15 verifies its
# numbers are PROVENANCE-CLEAN — re-scorable from a clean re-runnable
# computation, not recovered via salvage (host-scratch, manual, or any
# non-re-runnable path). EXECUTE tags each `comparisons.delta_*` entry with
# an optional `provenance` field; a value in `_W15_SALVAGE_VALUES` marks a
# number that cannot be re-derived from on-disk code+seed. When the
# preregistration names a `load_bearing_arm` whose comparison entry is
# salvage-tagged, W15 ERRORs — the headline cannot rest on salvage (CVPR
# report systemic #7 / A2: "don't let the strongest numbers rest on the
# weakest provenance"). REVIEW then routes `retry_execute` (re-run the arm
# cleanly) or a `scoped` re-headline (re-headline on a clean arm). Backward compat:
# a comparison with NO `provenance` field is treated as clean (older
# results pre-date the tag), and no `load_bearing_arm` → silent no-op.
_W15_PROVENANCE_KEY: str = "provenance"
# Values that mark a number as NOT re-scorable from a clean re-runnable
# path. Matched case-insensitively against the comparison's provenance tag.
_W15_SALVAGE_VALUES: frozenset[str] = frozenset({
    "salvage", "salvaged", "host_scratch", "host-scratch", "manual",
})
# The canonical clean value (documented in the schema); any non-salvage
# value — including absence of the field — is treated as clean.
_W15_CLEAN_VALUE: str = "fresh"

# W16 — degenerate-metric headline guard (load-bearing-gated; WARN by
# default, ERROR when the degenerate metric is the SOLE evidence for the
# headline arm). Companion to W13/W15. W13 verifies the load-bearing arm
# is COMPLETE; W15 verifies it is PROVENANCE-CLEAN; W16 verifies it is
# NON-DEGENERATE — i.e. its headline metric is not pinned exactly on a
# non-informative boundary WITH a collapsed (zero-width) CI.
#
# Motivation (d3, 2026-06): a run's self-described load-bearing
# experiment rested on `auc=0.500, ci=[0.5, 0.5]`, presented as a
# mechanistic demonstration. But an AUC pinned at 0.5 with a point CI is
# the statistical signature of a CONSTANT / degenerate predictor: it is
# consistent with BOTH the claimed mechanism AND a mundane null (the
# score is simply uninformative). A green checkmark on a number that
# cannot discriminate the claim from a null is exactly the failure mode
# W16 catches — it demands a disambiguating companion measurement.
#
# Detection predicate (per load-bearing block): a metric value whose
# field-name resolves to a bounded-metric CLASS sits within
# `_W16_BOUNDARY_EPSILON` of that class's non-informative boundary
# (`_W16_DEGENERATE_BOUNDARIES`) AND its `ci_95` has collapsed to ~zero
# width (`hi - lo <= _W16_CI_WIDTH_EPSILON`). 0_1-bounded metrics
# (AUC/AUROC/accuracy/c-index/...) have their null at 0.5; correlation
# metrics (pearson/spearman/...) have their null at 0.0.
#
# Load-bearing gating: W16 fires ONLY when a `load_bearing_arm` is named
# in preregistration.json (same field/normaliser W13 uses), and only on
# the blocks that back the headline — the named `comparisons.delta_*`
# arm(s) AND `primary_model` (the headline rests on the primary analysis).
# An incidental/control number (e.g. a planted-null sanity control that
# CORRECTLY reads 0.5) must NOT trip the guard: any block whose `role`
# field is in `_W16_CONTROL_ROLES` is exempt (a degenerate value there is
# the EXPECTED, correct result).
#
# Severity: ERROR when the degenerate metric is the SOLE point-estimate
# in its block (no disambiguating companion metric to separate "claimed
# mechanism" from "uninformative predictor"); WARN when a non-degenerate
# companion point-estimate IS present in the same block (the arm carries
# a separating measurement, so the guard is advisory). Backward compat:
# no `load_bearing_arm` declared, no plan sibling, or no degenerate
# metric in a load-bearing block → silent no-op.
_W16_DEGENERATE_BOUNDARIES: dict[str, float] = {
    # class label -> the non-informative (null) boundary value.
    "bounded_0_1": 0.5,   # AUC/AUROC/accuracy/c-index: chance = 0.5
    "bounded_neg1_1": 0.0,  # pearson/spearman/...: no association = 0.0
}
# How close a value must sit to a boundary to count as "pinned" there.
_W16_BOUNDARY_EPSILON: float = 1e-6
# Max CI width that counts as "collapsed" (a point CI: lo == hi).
_W16_CI_WIDTH_EPSILON: float = 1e-9
# `role` values marking a block as a control whose degenerate reading is
# the EXPECTED, correct result (a planted-null SHOULD read 0.5 / 0.0).
# Matched case-insensitively against the block's `role` field.
_W16_CONTROL_ROLES: frozenset[str] = frozenset({
    "null_control", "null-control", "negative_control", "negative-control",
    "planted_null", "planted-null", "sanity", "sanity_control",
    "sanity-control", "control",
})

# W17 — discriminating-arm resolution gate (load-bearing sibling of W13;
# the key rigor lever from the 2026-06-13 portfolio assessment). Where
# W13 verifies the HEADLINE arm (the experiment that demonstrates the
# effect EXISTS) is complete, W17 verifies the DISCRIMINATING arm — the
# single experiment that would ADJUDICATE the run's central
# mechanistic/causal question — is complete BEFORE the run may frame
# that question as resolved.
#
# Motivation (portfolio assessment, 2026-06-13): all six finalized wave
# runs left their own decisive experiment un-run (d1's perceiver-layer
# steering, d3's soft-gate counterfactual, d7's Winoground, fair-removal's
# race arm + calibrate-then-pin), wrote the run, and labelled the hole
# "future work" / "RESOURCE_REQUEST" / "gated" — while still framing the
# central question as answered. OmiCairn already NAMES this experiment (it is
# the "future work that would settle it" sentence). W17 surfaces it at
# PLAN as a prereg field and gates progression on it.
#
# The arm is named in `preregistration.json.discriminating_arm` by its
# `comparisons.delta_*` key (a string or list — SAME normaliser W13 uses;
# this is NOT a parallel mechanism). Post-EXECUTE, W17 checks whether each
# named key RESOLVED in results.json["comparisons"] (reusing W13's
# presence test).
#
# Resolution framing is read STRUCTURALLY, not by parsing prose:
# results.json may carry a canonical `central_question` block whose
# `discriminating_arm_resolution` field records how the run frames the
# central question — `"resolved"` (claims the question is answered) or
# `"scoped"` (the honest narrowed-scope path: the claim is narrowed to what
# the completed arms establish). This mirrors how W15 reads a structural
# `provenance` tag rather than prose.
#
# Severity (the key rigor lever — graceful by design):
#   * ERROR — discriminating arm UNRUN *and* framing is `"resolved"`
#     (default when the block/field is absent — see below): the run
#     over-claims, asserting the central question is answered while the
#     deciding experiment did not run. progression to REVIEW is blocked.
#   * WARN — discriminating arm UNRUN but framing is `"scoped"` (the
#     honest path): advisory reminder that the central question stays
#     open; never blocks progression.
#   * WARN — no `discriminating_arm` field declared at all: PLAN should
#     name one (it is the experiment the review would otherwise
#     list as future work); degrade gracefully, never crash, never block.
# When the discriminating arm IS present in results.json → silent pass
# (the deciding experiment ran), regardless of framing.
#
# Backward compatibility: no plan sibling, no `discriminating_arm` field,
# or an empty/blank value → the field-absent path (WARN-advisory that
# PLAN should register one); an OLDER results.json with no
# `central_question` block defaults framing to `"resolved"` (the
# conservative reading — an un-narrowed run claims its question).
_W17_DISCRIMINATING_ARM_FIELD: str = PreregField.DISCRIMINATING_ARM
# Canonical results.json block + field carrying the resolution framing.
_W17_CENTRAL_QUESTION_KEY: str = "central_question"
_W17_RESOLUTION_KEY: str = "discriminating_arm_resolution"
# The two legal framing values. `resolved` = the run asserts the
# central question is answered (over-claim if the arm is unrun);
# `scoped` = the claim is narrowed to the completed arms (honest path).
_W17_RESOLVED_FRAMING: str = "resolved"
_W17_SCOPED_FRAMING: str = "scoped"
# When no `central_question` block is present, default to the
# conservative reading: an un-narrowed run is treated as claiming
# resolution (so an unrun arm ERRORs rather than slipping through).
_W17_DEFAULT_FRAMING: str = _W17_RESOLVED_FRAMING

# W18 — proof-substance / theorem-depth enforcement gate (the enforcement
# layer over omicairn.lean_verify.substance_warnings). M3b detection (the
# trivial-pattern + elementary-theorem heuristics) ALREADY runs and is
# STORED on every LeanVerdict.substance_warnings (persisted to
# `<execute_stage>/lean/<Claim>.verdict.json` via LeanVerdict.to_dict).
# But until W18 NOTHING enforceable consumed it: the only consumer was the
# REVIEW prompt anchor (review.md "proof theater" rule),
# which fires ONLY if the review agent reads the warning AND obeys — a run
# launched before the anchor merged (fair-erasure-durability, whose
# XOR-table H4 headline shipped) used the pre-anchor prompt and never
# gated. W18 closes that asymmetry: it is a STRUCTURAL cross-check gate the
# orchestrator routes on (a sibling of W13/W15/W16/W17), so the elementary/
# trivial detection is ENFORCEABLE, not advisory-only.
#
# W18 does NOT re-detect — it READS the stored substance_warnings and
# COMPOSES with W13/W17's load-bearing machinery + the contribution_type
# theory signal. It fires ONLY for a THEORY-contribution run (read
# structurally from preregistration.json, mirroring W13/W17). For a
# theory run, a load-bearing Lean theorem carrying a NON-EMPTY
# substance_warning is:
#   * ERROR — when that theorem is the run's HEADLINE/load-bearing
#     theorem AND it is framed as a first-class `theorem` contribution
#     (the XOR-table-ships-ungated failure mode): a run may not ship an
#     elementary/trivial theorem AS its theorem contribution.
#   * WARN — when the theorem is SUPPORTING (non-headline), OR when the
#     headline theorem has been HONESTLY DEMOTED to a labeled
#     conjecture / formalized observation (the honest path
#     rules/method_depth.md prescribes — see review.md).
# Backward compatible: non-theory run, no plan sibling, no
# `contribution_type` field / non-theory value, or no lean verdict carries
# a substance_warning → silent no-op.

# Canonical preregistration.json field naming the contribution type
# (analysis | method | theory). omicairn.contribution.validate() owns the
# vocabulary; W18 gates on the literal `theory`. Mirrors how the plan emits
# load_bearing_arm / discriminating_arm as structured declarations.
_W18_CONTRIBUTION_TYPE_FIELD: str = "contribution_type"
_W18_THEORY_VALUE: str = "theory"
# Canonical preregistration.json field naming the run's HEADLINE /
# load-bearing Lean theorem(s) by theorem name — the theory analogue of
# W13's load_bearing_arm. A single name or a list. When named, W18 uses it
# to tell headline-vs-supporting (ERROR vs WARN). When ABSENT, W18 falls
# back to the per-verdict `claim` field the math-formalization skill writes
# (it sets `claim` to the headline theorem name) — so an older theory run
# with no declared field still has its headline gated.
_W18_LOAD_BEARING_THEOREM_FIELD: str = "load_bearing_theorem"
# Canonical lean-verdict field marking how the run FRAMES the theorem.
# Default (absent) = first-class `theorem` (conservative — an ungated
# elementary headline ERRORs rather than slipping through, mirroring W17's
# conservative resolved-framing default). The HONEST demotion path sets it
# to one of `_W18_DEMOTED_ROLES`, which downgrades W18 ERROR→WARN: a run
# that labels the elementary result a conjecture / formalized observation
# (rules/method_depth.md M3b) is no longer over-claiming.
_W18_CONTRIBUTION_ROLE_FIELD: str = "contribution_role"
_W18_FIRST_CLASS_ROLE: str = "theorem"
_W18_DEMOTED_ROLES: frozenset[str] = frozenset({
    "conjecture",
    "labeled_conjecture",
    "formalized_observation",
    "empirical_observation",
    "observation",
})
# The lean artifacts subdirectory + verdict-file suffix (canonical;
# LEAN_VERDICT_SUFFIX is shared with lean_verify / constants).
_W18_LEAN_SUBDIR: str = "lean"
# Verdict-JSON keys W18 reads (all written by LeanVerdict.to_dict /
# the math-formalization skill — no prose parsing).
_W18_SUBSTANCE_KEY: str = "substance_warnings"
_W18_THEOREMS_KEY: str = "theorems_checked"
_W18_CLAIM_KEY: str = "claim"

# W20 — decision-rule-HONORED gate (a W17-sibling; the one honesty class
# W13–W18 don't touch). W9 verifies every hypothesis has a `decision_rule`
# (free PROSE) PRESENT, but NEVER that the rule was HONORED. Real casualty:
# wm-cross-model-transfer's prereg H3 rule ("measured tau <= 1-rho within
# CI") was VIOLATED on the CCA arm, yet cross_check PASSED and the write-up
# framed H3 "PROVEN" by silently reframing to a different quantity. W9
# verifies gate STRUCTURE; W20 verifies "does results.json honor the
# prereg's own quantitative decision rule" — mechanically, never by parsing
# the free-prose rule (Golden Principle 8: structured JSON, never parsed
# prose).
#
# Schema (the machine-checkable TWIN of the prose `decision_rule`): PLAN
# emits an OPTIONAL `decision_rule_structured` object per hypothesis in
# preregistration.json, alongside (not replacing) the prose `decision_rule`:
#
#   "decision_rule_structured": {
#       "metric": "<dotted key path into results.json>",  # e.g. "h2_spearman.r_s"
#       "op": ">=|>|<=|<|==",
#       "threshold": <number>,
#       "ci_excludes": <number|null>   # optional: the metric's ci_95 sibling
#                                       # must EXCLUDE this value (typically 0)
#   }
#
# The "framed resolved/supported" signal is ALSO structural (GP8, never
# prose): an OPTIONAL per-hypothesis `status` in
# results.json["hypotheses"][<id>]["status"] (or a top-level
# results.json["hypothesis_status"][<id>]) in {supported|refuted|
# inconclusive}. `supported` = the run asserts the hypothesis resolved in
# its favour (the over-claim if the structured rule is violated).
#
# Severity:
#   * silent PASS — structured rule present, well-formed, metric resolvable,
#     and HONORED (op-vs-threshold holds, + ci_excludes if set);
#   * ERROR — structured rule present + well-formed + metric resolvable +
#     VIOLATED + the hypothesis is framed `supported` (structured status):
#     the run asserts a hypothesis resolved in its favour while its own
#     pre-registered quantitative rule is mathematically violated → progression
#     blocked (the wm-cross-model-transfer H3 failure mode);
#   * WARN — structured rule VIOLATED but NO structured `supported` status
#     (framing unknown / honestly null): a violated prereg rule is worth
#     surfacing even when framing is ambiguous, but it does not BLOCK — an
#     honest "rule not met, hypothesis not supported" is fine;
#   * WARN — malformed structured rule, unparseable JSON, or metric-key not
#     found in results.json (a missing key is NOT a violation — conservative;
#     when in doubt, WARN not ERROR).
#
# Backward compatibility (NON-NEGOTIABLE — 23 finalized + 2 live runs
# predate this field): the `decision_rule_structured` field is OPTIONAL.
# When ABSENT (every existing/in-flight run), W20 SILENTLY PASSES. It NEVER
# errors on a run that lacks the new field — the W17 declare-then-verify
# pattern exactly. Degrades silently on: no plan sibling, no preregistration,
# no hypotheses, no structured rule on a hypothesis, no results.json.
_W20_HYPOTHESES_FIELD: str = PreregField.HYPOTHESES
_W20_HYPOTHESIS_ID_KEY: str = "id"
# The OPTIONAL machine-checkable twin of the prose `decision_rule`.
_W20_STRUCTURED_RULE_KEY: str = PreregField.DECISION_RULE_STRUCTURED
_W20_PROSE_RULE_KEY: str = "decision_rule"
# Fields inside a decision_rule_structured object.
_W20_METRIC_KEY: str = "metric"
_W20_OP_KEY: str = "op"
_W20_THRESHOLD_KEY: str = "threshold"
_W20_CI_EXCLUDES_KEY: str = "ci_excludes"
# The canonical ci_95 sibling key W20 reads when a structured rule sets
# ci_excludes (shared shape with every other results.json metric block).
_W20_CI95_KEY: str = "ci_95"
# The legal comparison operators — a frozenset constant, NO raw operator
# literals scattered through the checker (GP7). Each maps to its evaluator
# in `_W20_OP_EVALUATORS`.
_W20_LEGAL_OPS: frozenset[str] = frozenset({">=", ">", "<=", "<", "=="})
# op -> (lhs, rhs) -> bool. `==` uses math.isclose so a float round-trip
# (e.g. 0.30000000000000004 == 0.3) does not spuriously read as violated.
_W20_OP_EVALUATORS: dict[str, "collections.abc.Callable[[float, float], bool]"] = {
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12),
}
# Structured per-hypothesis verdict carried in results.json. W20 reads the
# `supported` value as the conservative ERROR trigger (a violated rule on a
# hypothesis NOT framed supported stays a WARN — honest null). Looked up
# under results.json["hypotheses"][i]["status"] (list keyed by id) OR
# results.json["hypothesis_status"][<id>] (dict). Absent → framing unknown
# → WARN, never ERROR (back-compat: no existing run carries it).
_W20_STATUS_KEY: str = "status"
_W20_HYP_STATUS_BLOCK: str = "hypothesis_status"
_W20_STATUS_SUPPORTED: str = "supported"

# W7 negation tokens. If a plan-sentence contains a stratum marker AND
# any of these tokens, the sentence is treated as an explicit NON-
# commitment ("no stratum-trained confirmation is required", "stratum-
# trained sensitivity not applicable", ...). W7 fires only when at least
# one sentence carries a marker *without* a negation token — that is,
# the plan actually commits to a stratum-trained fit. Lowercase matching.
_W7_NEGATION_TOKENS: tuple[str, ...] = (
    "no stratum",
    "not required",
    "not applicable",
    "not needed",
    "without stratum",
    "without a stratum",
    "n/a",
    " not a ",
    "skip ",
    "skipped",
    "deferred",
    "out of scope",
    "does not apply",
)

# W8: Figure-render scripts must load their panel data from a separate
# `figures/data/<stem>.{json,csv,npz,tsv,parquet}` file so aesthetic
# tweaks (fonts, colors, layout) can be iterated without re-running
# the heavy analysis pipeline. Every figN_*.py render script must have
# a matching data file with the same stem, and must NOT import the
# workspace's data/model modules (data_loader, models) — those indicate
# the script is recomputing results inside the renderer, which defeats
# the separation.
_W8_VIZ_DATA_SUBDIR: str = "data"
_W8_VIZ_DATA_EXTS: tuple[str, ...] = (
    ".json", ".csv", ".tsv", ".npz", ".parquet",
)
_W8_FORBIDDEN_RENDER_IMPORTS: frozenset[str] = frozenset({
    "data_loader", "models",
})


def _ast_imported_module_roots(path: pathlib.Path) -> list[str]:
    """Parse `path` and return the set of top-level module names imported
    (e.g. `from data_loader import X` → `data_loader`,
    `import models.primary` → `models`). Returns [] if the file is
    missing or unparseable.
    """
    try:
        source = path.read_text()
    except OSError:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                out.append(node.module.split(".")[0])
    return out


def _ast_called_name(call: ast.Call) -> str | None:
    """Return the method/function name being invoked by `call`.
    For `ax.plot(...)` returns `"plot"`; for `sns.heatmap(...)` returns
    `"heatmap"`; for bare `plot(...)` returns `"plot"`. Returns None for
    exotic call targets (computed attributes, subscripts, etc.)."""
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _fig_script_call_string_args(
    path: pathlib.Path,
) -> list[tuple[str, str]]:
    """Parse `path` and yield (call_name, string_literal) for every Call
    whose arguments contain a Constant string. Used by W5 to find
    `ax.text("H1: SUPPORTED", ...)`-style annotations.
    """
    try:
        source = path.read_text()
    except OSError:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _ast_called_name(node) or ""
        all_args: list[ast.expr] = list(node.args)
        all_args.extend(kw.value for kw in node.keywords)
        for a in all_args:
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                out.append((name, a.value))
    return out


def _find_latest_plan_sibling(
    execute_stage_dir: pathlib.Path,
) -> pathlib.Path | None:
    """Return the highest-indexed sibling `*_plan` directory that
    precedes `execute_stage_dir`, or None if none exists.

    We intentionally require the plan index to be strictly less than the
    execute index so that a retry_execute stage walks back to the plan
    it was run against rather than a later re-plan.
    """
    workspace_root = execute_stage_dir.parent
    if not workspace_root.exists():
        return None
    exec_parsed = parse_stage_dir_name(execute_stage_dir.name)
    exec_idx = exec_parsed[0] if exec_parsed else 10**9
    best: tuple[int, pathlib.Path] | None = None
    try:
        children = list(workspace_root.iterdir())
    except OSError:
        return None
    for child in children:
        if not child.is_dir():
            continue
        parsed = parse_stage_dir_name(child.name)
        if parsed is None:
            continue
        idx, phase = parsed
        if phase != PhaseName.PLAN:
            continue
        if idx >= exec_idx:
            continue
        if best is None or idx > best[0]:
            best = (idx, child)
    return best[1] if best else None


def _read_preregistration_field(
    plan_dir: pathlib.Path, field: str
) -> Any:
    """Read one field from `<plan_dir>/preregistration.json`.

    Returns the raw value or `_RESOLVE_MISSING` if the file or field
    is absent. Used by W2/W7/W10 to consult structured plan
    declarations (parser audit Step C, since 2026-05) instead of
    parsing prose. Falls through to legacy prose parsers when the
    field is missing — bridges old ledgers / pre-Step-C plans.
    """
    pre_path = plan_dir / _PREREGISTRATION_FILE
    if not pre_path.exists():
        return _RESOLVE_MISSING
    try:
        data = json.loads(pre_path.read_text())
    except (OSError, json.JSONDecodeError):
        return _RESOLVE_MISSING
    if not isinstance(data, dict) or field not in data:
        return _RESOLVE_MISSING
    return data.get(field)


def _check_w2_power_section_present(
    execute_stage_dir: pathlib.Path,
    errors: list[str],
) -> None:
    """W2: the most recent preceding plan stage must declare a pre-hoc
    power / sample-size analysis with a CONCRETE numeric MDE.

    Canonical (Step C, since 2026-05): plan emits
    `preregistration.json.mde` (number, optional `power_target`).
    When set, W2 reads it directly — no prose parsing. When unset,
    falls back to the legacy `## Power` + `_W2_NUMERIC_MDE_PATTERN`
    prose parser (warn-only via the demoted-W-check pathway).

    Silent-skip if no plan sibling exists.
    """
    plan_dir = _find_latest_plan_sibling(execute_stage_dir)
    if plan_dir is None:
        return

    # Canonical: structured field.
    mde = _read_preregistration_field(plan_dir, "mde")
    if mde is not _RESOLVE_MISSING:
        if mde is None:
            errors.append(
                f"{plan_dir.name}/preregistration.json `mde` is null — "
                "W2 requires a numeric minimum detectable effect "
                "(set `mde: <number>` to declare it; set `mde_rationale: "
                "\"none required because ...\"` if intentionally absent)."
            )
        elif not isinstance(mde, (int, float)) or isinstance(mde, bool):
            errors.append(
                f"{plan_dir.name}/preregistration.json `mde={mde!r}` is "
                "not numeric — W2 requires a number."
            )
        return

    # Legacy: prose parser fallback (warn-only via demoted W-check).
    candidates = [
        plan_dir / "hypotheses.md",
        plan_dir / "experiment_plan.md",
    ]
    text_parts: list[str] = []
    raw_parts: list[str] = []
    for c in candidates:
        if c.exists():
            try:
                raw = c.read_text()
                raw_parts.append(raw)
                text_parts.append(raw.lower())
            except OSError:
                continue
    if not text_parts:
        return
    text = "\n".join(text_parts)
    raw_text = "\n".join(raw_parts)
    if not any(marker in text for marker in _W2_POWER_MARKERS):
        errors.append(
            f"{plan_dir.name} has no power / sample-size section in "
            "hypotheses.md / experiment_plan.md AND no "
            "preregistration.json `mde` field — W2 (legacy prose "
            "fallback). Switch to the structured field: set "
            "`mde: <number>` in preregistration.json."
        )
        return
    if not _W2_NUMERIC_MDE_PATTERN.search(raw_text):
        errors.append(
            f"{plan_dir.name} power section has no concrete numeric "
            "MDE in prose AND no preregistration.json `mde` field — "
            "W2 (legacy prose fallback). Switch to the structured "
            "field: set `mde: <number>` in preregistration.json."
        )


def _check_w5_h1_annotation_on_primary_panel(
    execute_stage_dir: pathlib.Path,
    errors: list[str],
) -> None:
    """W5: at least one `code/figures/fig*.py` script (other than the
    fig1 conceptual schematic) must contain an ax.text or ax.annotate
    call whose string literal carries the H1 hypothesis decision
    ("H1: SUPPORTED", "H1: NOT SUPPORTED", "H1 partial", ...).

    The annotation's location inside the figure stack is layout-specific
    (for a simple study it is often fig2 = primary-vs-baseline; for a
    multi-panel run with upstream descriptive figures it may be fig5
    or later validation figure). W5 only enforces that a literal
    H1-decision annotation exists somewhere in the figure source — the
    experiment's preregistration decides which panel carries it.

    Silent skip if no non-fig1 figure script exists.
    """
    fig_code = execute_stage_dir / _CODE_SUBDIR / _FIGURES_SUBDIR
    if not fig_code.exists():
        return
    scripts = [
        f for f in sorted(fig_code.glob("fig*.py"))
        if not f.name.startswith("fig1")
    ]
    if not scripts:
        return
    annotation_calls = {"text", "annotate"}
    for f in scripts:
        hits = _fig_script_call_string_args(f)
        for name, sval in hits:
            if name not in annotation_calls:
                continue
            lower = sval.lower()
            if "h1" not in lower:
                continue
            if any(word in lower for word in _W5_H1_DECISION_WORDS):
                return  # at least one figure carries the H1 decision
    errors.append(
        "no figure script carries an H1-decision text annotation — W5 "
        "requires an explicit `ax.text(...)` or `ax.annotate(...)` "
        'string like "H1: SUPPORTED (Δ=0.15, 95% CI 0.05–0.25)" on '
        "whichever figure shows the primary-vs-baseline comparison. "
        "Prompt-level discipline is not enough; the literal must live "
        f"in one of the figure sources ({', '.join(f.name for f in scripts)})."
    )


def _w7_plan_commits_to_stratum(plan_text: str) -> bool:
    """True if the plan commits to a stratum-trained confirmation fit.

    A bare substring match on `_W7_STRATUM_MARKERS` is too loose: a plan
    that says "No stratum-trained confirmation is required" hits the
    same match as one that says "we will fit a stratum-trained model on
    the high-stage subgroup". We resolve the ambiguity by splitting the
    plan into sentence-like chunks and requiring ≥1 chunk to contain a
    marker *without* a `_W7_NEGATION_TOKENS` negation. If every marker
    occurrence is inside a negation chunk, the plan is saying "we will
    NOT do this" — W7 should silent-skip.

    Input is already lowercased by the caller.
    """
    # Split on common sentence/line boundaries. Simpler than a real
    # tokenizer and sufficient for markdown plan prose.
    chunks = re.split(r"[.;!?\n]+", plan_text)
    for chunk in chunks:
        if not any(marker in chunk for marker in _W7_STRATUM_MARKERS):
            continue
        if any(neg in chunk for neg in _W7_NEGATION_TOKENS):
            continue
        return True
    return False


def _check_w7_stratified_sensitivity(
    execute_stage_dir: pathlib.Path,
    results: dict,
    errors: list[str],
) -> None:
    """W7: if the plan commits to a stratum-trained confirmation model,
    the execute phase must emit `primary_model.stratum_trained_<name>`
    with either a complete schema (`c_index`/primary_metric, `ci_95`,
    `n`, `events`) OR an honest `skipped: true` + `reason`. A bare
    subgroup mirror (within-stratum evaluation of the pan-cohort model)
    does NOT satisfy W7 — the whole point is to fit on the stratum.

    Silent-skip when no plan sibling is found, when the plan has no
    stratum-confirmation marker, or when results.json has no
    primary_model.
    """
    plan_dir = _find_latest_plan_sibling(execute_stage_dir)
    if plan_dir is None:
        return

    # Canonical (Step C, since 2026-05): structured boolean field
    # `requires_stratum_confirmation` in preregistration.json. When
    # set, W7 reads it directly. When unset, falls back to the
    # legacy prose parser `_w7_plan_commits_to_stratum` over plan
    # markdown.
    requires = _read_preregistration_field(
        plan_dir, "requires_stratum_confirmation"
    )
    if requires is not _RESOLVE_MISSING:
        if not bool(requires):
            return
    else:
        text_parts: list[str] = []
        for name in ("hypotheses.md", "experiment_plan.md"):
            p = plan_dir / name
            if p.exists():
                try:
                    text_parts.append(p.read_text().lower())
                except OSError:
                    continue
        if not text_parts:
            return
        plan_text = "\n".join(text_parts)
        if not _w7_plan_commits_to_stratum(plan_text):
            return

    primary = results.get(ResultsField.PRIMARY_MODEL) if isinstance(results, dict) else None
    if not isinstance(primary, dict):
        return

    stratum_entries = {
        k: v for k, v in primary.items()
        if isinstance(k, str) and k.startswith(_W7_PRIMARY_STRATUM_PREFIX)
    }
    if not stratum_entries:
        errors.append(
            f"{plan_dir.name} commits to a stratum-trained confirmation "
            "model but results.json has no "
            "`primary_model.stratum_trained_<name>` entry — W7 requires "
            "a fit-on-stratum sensitivity model with its own locked "
            "coefficients and held-out evaluation. Emit at least one "
            "`stratum_trained_<name>` block (or skipped:true with reason "
            "if discovery events are below the domain-pack threshold)."
        )
        return

    for name, entry in stratum_entries.items():
        if not isinstance(entry, dict):
            errors.append(
                f"primary_model.{name} must be an object (got "
                f"{type(entry).__name__}) — W7"
            )
            continue
        if entry.get("skipped") is True:
            if not entry.get("reason"):
                errors.append(
                    f"primary_model.{name}.skipped=true but no `reason` "
                    "field — W7 requires an honest string reason when "
                    "the stratum-trained fit is skipped."
                )
            continue
        # Full-schema branch.
        missing = [k for k in ("ci_95", "n", "events") if k not in entry]
        has_metric = any(
            k in entry for k in ("c_index", "auc", "concordance_index",
                                 "rmse", "accuracy")
        )
        if not has_metric:
            missing.append("<primary_metric>")
        if missing:
            errors.append(
                f"primary_model.{name} missing "
                f"{', '.join(missing)} — W7 requires "
                "c_index/primary_metric + ci_95 + n + events on every "
                "stratum-trained entry (or skipped:true with reason)."
            )


def _check_w8_viz_data_contract(
    execute_stage_dir: pathlib.Path,
    errors: list[str],
) -> None:
    """W8: every `code/figures/figN_*.py` render script must (a) have a
    matching `figures/data/<stem>.{json,csv,npz,tsv,parquet}` file so
    aesthetic updates can be made without re-running the analysis
    pipeline, and (b) NOT import `data_loader` or `models` (those
    indicate the renderer is recomputing results instead of reading
    pre-computed viz data).

    Silent-skip when `code/figures/` does not exist (minimal fixtures).
    """
    fig_code = execute_stage_dir / _CODE_SUBDIR / _FIGURES_SUBDIR
    if not fig_code.exists():
        return
    # By convention fig1 may be a study-design schematic: drawn with
    # matplotlib primitives, it carries no data and has no viz-data file.
    # W8 applies to results figures (fig2+) only. Match `fig1_*.py`
    # exactly — `fig10_*.py`, `fig11_*.py` etc. are results figures.
    fig1_pat = re.compile(r"^fig1[_\-].*\.py$")
    scripts = [
        s for s in sorted(fig_code.glob("fig*.py"))
        if not fig1_pat.match(s.name)
    ]
    if not scripts:
        return
    viz_data_dir = execute_stage_dir / _FIGURES_SUBDIR / _W8_VIZ_DATA_SUBDIR
    for script in scripts:
        stem = script.stem
        # (a) Data file with the matching stem must exist.
        data_present = False
        if viz_data_dir.exists():
            for ext in _W8_VIZ_DATA_EXTS:
                if (viz_data_dir / f"{stem}{ext}").exists():
                    data_present = True
                    break
        if not data_present:
            errors.append(
                f"{script.name} has no matching viz-data file at "
                f"figures/{_W8_VIZ_DATA_SUBDIR}/{stem}."
                f"{{json,csv,npz,tsv,parquet}} — W8 requires each render "
                "script to load panel data from a separate minimal file "
                "so fonts/colors/layout can be tweaked without re-running "
                "the analysis pipeline. Emit the viz data from main.py "
                f"into figures/{_W8_VIZ_DATA_SUBDIR}/{stem}.json."
            )
            continue
        # (b) Render script must not import heavy workspace modules.
        imports = _ast_imported_module_roots(script)
        forbidden = sorted(
            set(imports) & _W8_FORBIDDEN_RENDER_IMPORTS
        )
        if forbidden:
            errors.append(
                f"{script.name} imports {forbidden} — W8 forbids render "
                "scripts from importing data_loader or models. Renderers "
                f"must read only from figures/{_W8_VIZ_DATA_SUBDIR}/ "
                "(plus matplotlib/pandas/numpy/json). Move the "
                "computation into main.py and persist the minimal viz "
                f"data to figures/{_W8_VIZ_DATA_SUBDIR}/{script.stem}.json."
            )


def _w13_named_arm_keys(raw: Any) -> list[str]:
    """Normalise the preregistration `load_bearing_arm` value to a list
    of named `comparisons.delta_*` keys.

    Accepts a single string ("delta_steered_vs_unsteered") or a list of
    strings. Each entry may be the bare comparisons key or the fully
    dotted form ("comparisons.delta_*"); the dotted prefix is stripped
    so the leaf key is matched against results.json["comparisons"].
    Returns [] for an empty/blank/wrong-typed value (treated as "not
    named" → W13 no-ops).
    """
    if isinstance(raw, str):
        candidates = [raw]
    elif isinstance(raw, list):
        candidates = [c for c in raw if isinstance(c, str)]
    else:
        return []
    keys: list[str] = []
    prefix = f"{_W13_COMPARISONS_KEY}."
    for c in candidates:
        c = c.strip()
        if not c:
            continue
        # Tolerate the fully-dotted "comparisons.delta_*" form.
        if c.startswith(prefix):
            c = c[len(prefix):]
        keys.append(c)
    return keys


def _check_w13_load_bearing_arm_complete(
    execute_stage_dir: pathlib.Path,
    results: dict,
    errors: list[str],
) -> None:
    """W13 — load-bearing-arm gate (always-on, ERROR-on-failure).

    The preregistration may name the single hypothesis/comparison whose
    completion the headline depends on, by its `comparisons.delta_*`
    key, in `preregistration.json.load_bearing_arm` (a string or list of
    strings). Post-EXECUTE, W13 verifies every named key is PRESENT in
    `results.json["comparisons"]`. If any named arm is absent, W13
    ERRORs — the headline cannot ship incomplete (contrast: figure-gap
    checks W8/figures are warn-only). REVIEW then routes the run to
    a `scoped` re-headline (re-headline on a completed arm) or `retry_execute`.

    Backward compatibility: when `preregistration.json` is missing, the
    `load_bearing_arm` field is absent, or its value is empty/blank,
    W13 is a SILENT NO-OP — older workspaces and minimal fixtures are
    never broken. The gate fires ONLY when an arm IS named AND its key
    IS absent — never on absence-of-field.
    """
    plan_dir = _find_latest_plan_sibling(execute_stage_dir)
    if plan_dir is None:
        return
    raw = _read_preregistration_field(plan_dir, _W13_LOAD_BEARING_ARM_FIELD)
    if raw is _RESOLVE_MISSING:
        return  # Older case: no load_bearing_arm declared → no-op.
    named = _w13_named_arm_keys(raw)
    if not named:
        return  # Empty/blank/wrong-typed declaration → no-op.

    comparisons = results.get(_W13_COMPARISONS_KEY) if isinstance(results, dict) else None
    present = comparisons if isinstance(comparisons, dict) else {}
    missing = [k for k in named if k not in present]
    if missing:
        joined = ", ".join(f"comparisons.{k}" for k in missing)
        errors.append(
            f"{plan_dir.name}/preregistration.json names load_bearing_arm "
            f"{named!r} but {joined} is absent from {_RESULTS_JSON}["
            f"\"{_W13_COMPARISONS_KEY}\"] — W13 (load-bearing-arm gate) "
            "blocks progression to REVIEW: the headline depends on this arm and it did not "
            "complete. Either finish the arm and re-emit results.json, or "
            "re-headline the results on a completed arm (record the "
            "narrowed `scoped` framing in results.json). The central results table must not ship with a "
            "hole in the cell the contribution lives in."
        )


def _check_w14_shared_protocol_baseline(
    execute_stage_dir: pathlib.Path,
    results: dict,
    warnings: list[str],
) -> None:
    """W14 — shared-protocol baseline awareness (ADVISORY / warn-only).

    Reads the optional `baselines` block from preregistration.json (see
    prompts/plan.md § Shared-protocol baseline). WARNS (never ERRORs) when:

      * the plan declares `shared_protocol_feasible: false` but offers no
        `no_baseline_rationale` — an unexplained "no baseline applies" is
        the exact gap the CVPR comparison flagged (d1 declined a head-to-
        head it never justified); OR
      * the plan declares `shared_protocol_feasible: true` and names
        `candidates`, yet no competing-method comparison arm
        (`comparisons.delta_*`) reached results.json — a feasible baseline
        that was identified at PLAN time but never actually run.

    A missing baseline is sometimes legitimately N/A (first-of-kind, pure
    null / analysis), so this is advisory only: it lowers the REVIEW
    positioning/novelty judgement (review.md), it never blocks progression.

    Backward compatibility: when preregistration.json is missing, the
    `baselines` block is absent, or it is well-formed and complete, W14 is
    SILENT. Older workspaces and minimal fixtures are never affected.
    """
    plan_dir = _find_latest_plan_sibling(execute_stage_dir)
    if plan_dir is None:
        return
    raw = _read_preregistration_field(plan_dir, _W14_BASELINES_FIELD)
    if raw is _RESOLVE_MISSING or not isinstance(raw, dict):
        return  # No baselines block declared (or malformed) → no-op.
    feasible = raw.get(_W14_FEASIBLE_KEY)
    if feasible is False:
        rationale = raw.get(_W14_RATIONALE_KEY)
        rationale = rationale.strip() if isinstance(rationale, str) else ""
        if not rationale:
            warnings.append(
                f"W14: {plan_dir.name}/preregistration.json declares "
                f"baselines.{_W14_FEASIBLE_KEY}=false but no "
                f"`{_W14_RATIONALE_KEY}` — state one concrete reason no "
                "shared-protocol baseline applies (first-of-kind, or a pure "
                "null/analysis whose comparator is the claim itself). "
                "Comparison-driven evaluation penalises an unjustified declined "
                "head-to-head. ADVISORY: REVIEW weighs this in its "
                "positioning judgement; this never blocks progression."
            )
        return
    if feasible is True:
        candidates = raw.get(_W14_CANDIDATES_KEY)
        named = [c for c in candidates if isinstance(c, str) and c.strip()] \
            if isinstance(candidates, list) else []
        if not named:
            return  # Feasible declared but no candidate named yet → PLAN-only signal.
        comparisons = results.get(_W13_COMPARISONS_KEY) if isinstance(results, dict) else None
        ran_comparison = isinstance(comparisons, dict) and bool(comparisons)
        if not ran_comparison:
            warnings.append(
                f"W14: {plan_dir.name}/preregistration.json names feasible "
                f"shared-protocol baseline(s) {named!r} but {_RESULTS_JSON} "
                f"has no `{_W13_COMPARISONS_KEY}.*` arm comparing against a "
                "competing method — a baseline identified at PLAN time was "
                "not run. Add the head-to-head, or justify dropping it. "
                "ADVISORY: REVIEW weighs this in its positioning judgement; this "
                "never blocks progression."
            )


def _w15_provenance_is_salvage(entry: Any) -> bool:
    """Return True iff a `comparisons.delta_*` entry carries a `provenance`
    tag whose value marks it as un-re-scorable salvage.

    A non-dict entry, a missing `provenance` field, or any value that is
    not in `_W15_SALVAGE_VALUES` (including the canonical `"fresh"`) is
    treated as CLEAN → returns False. Matching is case-insensitive and
    whitespace-tolerant so `"Salvaged"` / `" host-scratch "` still trip.
    """
    if not isinstance(entry, dict):
        return False
    tag = entry.get(_W15_PROVENANCE_KEY)
    if not isinstance(tag, str):
        return False
    return tag.strip().lower() in _W15_SALVAGE_VALUES


def _check_w15_load_bearing_arm_provenance(
    execute_stage_dir: pathlib.Path,
    results: dict,
    errors: list[str],
) -> None:
    """W15 — salvage-provenance guard (always-on, ERROR-on-failure).

    Companion to W13. W13 verifies the load-bearing arm is COMPLETE; W15
    verifies its numbers are PROVENANCE-CLEAN. The preregistration names
    the headline arm by its `comparisons.delta_*` key in
    `preregistration.json.load_bearing_arm` (string or list — same field
    and normaliser W13 uses). EXECUTE may tag each comparison entry with a
    `provenance` field (`"fresh"` | `"salvaged"` | host-scratch | manual).
    If any NAMED load-bearing arm's entry is salvage-tagged, W15 ERRORs —
    the headline cannot rest on a number recovered via a non-re-runnable
    path while finished arms are clean (CVPR report systemic #7 / A2). The
    salvaged number may still appear in the BODY as a robustness/supporting
    result; it just may not be the headline. REVIEW then routes
    `retry_execute` (re-run the arm cleanly so it becomes re-scorable) or
    a `scoped` re-headline (re-headline on a clean arm).

    Backward compatibility — three independent no-op paths:
      * no plan sibling / no `load_bearing_arm` field / empty value → no-op
        (mirrors W13: never fires on absence-of-field);
      * the named arm is ABSENT from results.json → silent (that is W13's
        job to flag as INCOMPLETE; W15 only judges provenance of arms that
        DID land);
      * the named arm is present with NO `provenance` field, or any
        non-salvage value → clean → no-op (older results predate the tag).
    """
    plan_dir = _find_latest_plan_sibling(execute_stage_dir)
    if plan_dir is None:
        return
    raw = _read_preregistration_field(plan_dir, _W13_LOAD_BEARING_ARM_FIELD)
    if raw is _RESOLVE_MISSING:
        return  # Older case: no load_bearing_arm declared → no-op.
    named = _w13_named_arm_keys(raw)
    if not named:
        return  # Empty/blank/wrong-typed declaration → no-op.

    comparisons = results.get(_W13_COMPARISONS_KEY) if isinstance(results, dict) else None
    present = comparisons if isinstance(comparisons, dict) else {}
    salvaged = [
        k for k in named
        if k in present and _w15_provenance_is_salvage(present[k])
    ]
    if salvaged:
        joined = ", ".join(f"comparisons.{k}" for k in salvaged)
        errors.append(
            f"{plan_dir.name}/preregistration.json names load_bearing_arm "
            f"{named!r} but {joined} carries provenance="
            f"\"salvage\" in {_RESULTS_JSON} — W15 (salvage-provenance "
            "guard) blocks progression to REVIEW: the headline cannot rest on a number "
            "recovered via a non-re-runnable salvage path (host-scratch / "
            "manual). Either re-run the arm cleanly so it is re-scorable "
            f"from on-disk code+seed and re-tag provenance=\"{_W15_CLEAN_VALUE}\", "
            "or re-headline the results on a clean arm (record the narrowed "
            "`scoped` framing in results.json). A salvaged number may stay in the body as a "
            "robustness result; it may not be the headline."
        )


def _execute_sibling_stages(
    execute_stage_dir: pathlib.Path,
) -> list[tuple[int, pathlib.Path]]:
    """Return every EXECUTE-phase sibling of `execute_stage_dir` (including
    itself) as (index, path) pairs, ascending by stage index.

    Reuses the canonical `parse_stage_dir_name` round-trip — never a fresh
    regex — so it tracks STAGE_INDEX_WIDTH / KNOWN_STAGE_PHASES automatically
    (mirrors `_find_latest_plan_sibling`). Returns [] on an unreadable
    workspace root.
    """
    workspace_root = execute_stage_dir.parent
    if not workspace_root.exists():
        return []
    try:
        children = list(workspace_root.iterdir())
    except OSError:
        return []
    out: list[tuple[int, pathlib.Path]] = []
    for child in children:
        if not child.is_dir():
            continue
        parsed = parse_stage_dir_name(child.name)
        if parsed is None:
            continue
        idx, phase = parsed
        if phase != PhaseName.EXECUTE:
            continue
        out.append((idx, child))
    out.sort(key=lambda pair: pair[0])
    return out


def _execute_stage_has_arm(stage_dir: pathlib.Path, key: str) -> bool:
    """True iff `<stage_dir>/results.json` carries `comparisons.<key>`.

    Reuses W13's presence semantics (a comparisons sub-key that exists),
    reading the stage's OWN results.json off disk. A missing/unparseable
    results.json, a non-dict `comparisons`, or an absent key → False.
    """
    results_path = stage_dir / _RESULTS_JSON
    if not results_path.exists():
        return False
    try:
        raw = json.loads(results_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    results = normalize_results_json(raw)
    comparisons = results.get(_W13_COMPARISONS_KEY) if isinstance(results, dict) else None
    return isinstance(comparisons, dict) and key in comparisons


def _check_w15_latest_execute_provenance(
    execute_stage_dir: pathlib.Path,
    results: dict,
    warnings: list[str],
) -> None:
    """W15L — latest-execute-stage provenance gate (ADVISORY / warn-only).

    Sibling of W15 (which guards salvage provenance WITHIN one stage). W15L
    guards the CROSS-STAGE provenance fracture on a multi-execute run: a
    named load_bearing_arm that is ABSENT from the LATEST execute stage's
    results.json but PRESENT in an EARLIER execute sibling's. That is the
    exact d5 failure (2026-06): the headline (n=48 set-level CKA=0.839) lived
    only in an early execute stage (026) while the latest execute stage (032)
    dropped the arm — the number traced to results.json, but a NON-LATEST
    one. The run still reported it as the headline, a provenance fracture
    W13/W15 (which read only the latest stage) could not see.

    `execute_stage_dir` is the LATEST execute stage (the one `check_execute`
    verifies). W15L WARNs (never ERRORs, never blocks progression) when:
      * a load_bearing_arm is named (same field/normaliser W13/W15 use), AND
      * a named arm is ABSENT from `results` (the latest stage), AND
      * that same arm IS present in an EARLIER execute sibling's results.json.

    It names the fractured arm + the stale stage so REVIEW can either re-run
    the arm in the latest stage (so the headline traces to current
    provenance) or re-headline on an arm the latest stage carries.

    Shipped WARN-FIRST (mirroring how W21 landed): it appends to `warnings`
    only and takes no errors sink, so it can never flip ok / route back while
    the cross-stage signal is observed in the wild.

    Backward-compat — several independent no-op paths:
      * no plan sibling / no load_bearing_arm field / empty value → no-op
        (mirrors W13/W15: never fires on absence-of-field);
      * a single-execute run (no earlier execute sibling) → no-op;
      * the named arm IS present in the latest stage → no-op (that is the
        common, healthy case; W13 already handles a uniformly-absent arm).
    """
    plan_dir = _find_latest_plan_sibling(execute_stage_dir)
    if plan_dir is None:
        return
    raw = _read_preregistration_field(plan_dir, _W13_LOAD_BEARING_ARM_FIELD)
    if raw is _RESOLVE_MISSING:
        return  # No load_bearing_arm declared → no-op.
    named = _w13_named_arm_keys(raw)
    if not named:
        return  # Empty/blank/wrong-typed declaration → no-op.

    # Arms ABSENT from the latest stage (the one under verification).
    latest_comparisons = results.get(_W13_COMPARISONS_KEY) if isinstance(results, dict) else None
    latest_present = latest_comparisons if isinstance(latest_comparisons, dict) else {}
    absent_in_latest = [k for k in named if k not in latest_present]
    if not absent_in_latest:
        return  # Every named arm landed in the latest stage → healthy → no-op.

    # Earlier execute siblings, newest→oldest (the freshest stale stage that
    # still carries the arm is the most informative to name).
    siblings = _execute_sibling_stages(execute_stage_dir)
    exec_parsed = parse_stage_dir_name(execute_stage_dir.name)
    latest_idx = exec_parsed[0] if exec_parsed else 10**9
    earlier = [(idx, p) for idx, p in siblings if idx < latest_idx]
    if not earlier:
        return  # Single-execute run → no fracture possible → no-op.

    fractured: list[tuple[str, str]] = []  # (arm_key, stale_stage_name)
    for key in absent_in_latest:
        for idx, stage in sorted(earlier, key=lambda pair: pair[0], reverse=True):
            if _execute_stage_has_arm(stage, key):
                fractured.append((key, stage.name))
                break

    if fractured:
        joined = ", ".join(
            f"comparisons.{k} (last seen in {stale})" for k, stale in fractured
        )
        warnings.append(
            f"{LatestExecuteProvenanceGate.GATE_ID}: {plan_dir.name}/"
            f"{_PREREGISTRATION_FILE} names load_bearing_arm {named!r} but "
            f"{joined} is ABSENT from the latest execute stage "
            f"({execute_stage_dir.name})/{_RESULTS_JSON} while present in an "
            "earlier execute stage — the headline's provenance has fractured "
            "to a stale stage (the d5 set-level-CKA failure mode). ADVISORY "
            "only: this never blocks progression — but REVIEW should either re-run "
            "the arm in the latest execute stage so the headline traces to "
            "current provenance, or re-headline on an arm the latest stage "
            "carries. A headline must not rest on a number the latest "
            "results.json dropped."
        )


def _w16_block_is_control(block: Any) -> bool:
    """True iff `block` carries a `role` marking it a control whose
    degenerate (null-boundary) reading is the EXPECTED correct result —
    a planted-null / negative / sanity control. Matched case-insensitively
    against `_W16_CONTROL_ROLES`. A non-dict block, a missing `role`, or
    any non-control role → False (the block is treated as substantive)."""
    if not isinstance(block, dict):
        return False
    role = block.get("role")
    if not isinstance(role, str):
        return False
    return role.strip().lower() in _W16_CONTROL_ROLES


def _w16_boundary_for_metric(
    name: str, domain: str | None
) -> float | None:
    """Return the non-informative boundary value for a metric field
    `name`, or None if `name` does not resolve to a bounded-metric class
    that HAS a null boundary.

    Reuses the SAME `metric_bounds_*` classification W's bounded-range
    check uses: a 0_1-bounded metric (AUC/AUROC/accuracy/c-index/...)
    nulls at 0.5; a correlation metric (pearson/spearman/...) nulls at
    0.0. Non-negative losses (RMSE/...) have no informative-boundary
    notion here and return None.
    """
    if not _is_point_estimate_field(name):
        return None
    lower = name.lower()
    if lower in metric_bounds_0_1(domain):
        return _W16_DEGENERATE_BOUNDARIES["bounded_0_1"]
    if lower in metric_bounds_neg1_1(domain):
        return _W16_DEGENERATE_BOUNDARIES["bounded_neg1_1"]
    return None


def _w16_degenerate_metric(
    block: dict, domain: str | None
) -> tuple[str, float, float, float] | None:
    """If `block` carries a metric pinned on its class's non-informative
    boundary WITH a collapsed (zero-width) CI, return
    (field_name, value, ci_lo, ci_hi); else None.

    Predicate, both clauses required:
      1. a point-estimate field whose name resolves to a bounded-metric
         class sits within `_W16_BOUNDARY_EPSILON` of that class's null
         boundary (0.5 for 0_1 metrics, 0.0 for correlations); AND
      2. the block's `ci_95` is a real [lo, hi] pair whose width is
         <= `_W16_CI_WIDTH_EPSILON` (lo == hi to numeric tolerance).

    A collapsed point CI on a boundary value is the statistical signature
    of a constant/degenerate predictor. The CI must be present and
    well-formed — a missing/NaN CI is handled by W's other CI checks, not
    here (W16 fires only on the specific "pinned + collapsed" signature).
    """
    if not isinstance(block, dict):
        return None
    ci = block.get("ci_95")
    if not (isinstance(ci, list) and len(ci) == 2):
        return None
    try:
        lo = float(ci[0])
        hi = float(ci[1])
    except (TypeError, ValueError):
        return None
    if math.isnan(lo) or math.isnan(hi) or math.isinf(lo) or math.isinf(hi):
        return None
    if lo > hi:
        lo, hi = hi, lo
    if (hi - lo) > _W16_CI_WIDTH_EPSILON:
        return None  # CI has real width → not collapsed → not degenerate.
    for key, value in block.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        boundary = _w16_boundary_for_metric(key, domain)
        if boundary is None:
            continue
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            continue
        if abs(v - boundary) <= _W16_BOUNDARY_EPSILON:
            return (key, v, lo, hi)
    return None


def _w16_has_disambiguating_companion(
    block: dict, degenerate_field: str, domain: str | None
) -> bool:
    """True iff `block` carries at least one NON-degenerate point-estimate
    scalar OTHER than `degenerate_field` — a companion measurement that
    could separate "claimed mechanism" from "uninformative predictor".

    A companion is any further bounded-metric point estimate in the same
    block that is NOT itself pinned on a null boundary, OR any other
    substantive point-estimate scalar (e.g. a `delta`, an effect size).
    The presence of such a companion demotes W16 from ERROR to WARN: the
    arm still carries a separating signal, so the guard is advisory.
    """
    for key, value in block.items():
        if key == degenerate_field:
            continue
        if not _is_point_estimate_field(key):
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            continue
        boundary = _w16_boundary_for_metric(key, domain)
        if boundary is None:
            # A non-bounded substantive scalar (delta / effect size) is a
            # companion measurement on its own.
            return True
        if abs(v - boundary) > _W16_BOUNDARY_EPSILON:
            # A second bounded metric that is NOT pinned at its null.
            return True
    return False


def _w16_load_bearing_blocks(
    results: Any, named: list[str]
) -> list[tuple[str, dict]]:
    """Return the (path, block) pairs that BACK the headline for W16:
    the named `comparisons.delta_*` arm(s) plus `primary_model` (the
    headline rests on the primary analysis whenever a load-bearing arm is
    declared). Control-role blocks are filtered out here so a planted
    null never reaches the degeneracy predicate.
    """
    if not isinstance(results, dict):
        return []
    out: list[tuple[str, dict]] = []
    pm = results.get(ResultsField.PRIMARY_MODEL)
    if isinstance(pm, dict) and not _w16_block_is_control(pm):
        out.append((ResultsField.PRIMARY_MODEL, pm))
    comparisons = results.get(_W13_COMPARISONS_KEY)
    present = comparisons if isinstance(comparisons, dict) else {}
    for key in named:
        block = present.get(key)
        if isinstance(block, dict) and not _w16_block_is_control(block):
            out.append((f"{_W13_COMPARISONS_KEY}.{key}", block))
    return out


def _check_w16_degenerate_metric_headline(
    execute_stage_dir: pathlib.Path,
    results: dict,
    errors: list[str],
    warnings: list[str],
    domain: str | None = None,
) -> None:
    """W16 — degenerate-metric headline guard (load-bearing-gated).

    Fires when a metric BACKING the headline is pinned exactly on a
    non-informative boundary (AUC/accuracy/c-index at 0.5; correlation at
    0.0) WITH a collapsed (zero-width) confidence interval — the
    statistical signature of a constant/degenerate predictor. Such a
    number is consistent with BOTH the claimed mechanism AND a mundane
    null, so it cannot discriminate the claim; the fix is a disambiguating
    companion measurement (d3, 2026-06).

    Load-bearing gating (reuses W13's machinery): only fires when a
    `load_bearing_arm` is named in preregistration.json, and only on the
    blocks that back the headline — the named `comparisons.delta_*` arm(s)
    and `primary_model`. A planted-null control (block `role` in
    `_W16_CONTROL_ROLES`) is exempt: a control that reads 0.5 is correct.

    Severity:
      * ERROR when the degenerate metric is the SOLE point-estimate in its
        block (no companion to separate mechanism from uninformative
        predictor) — like W13/W15, the headline cannot ship on it.
      * WARN when a non-degenerate companion point-estimate is present in
        the same block (the arm carries a separating measurement) — like
        W14, advisory.

    Backward compatibility — silent no-op when: no plan sibling; no
    `load_bearing_arm` field / empty value; or no degenerate metric in any
    load-bearing block.
    """
    plan_dir = _find_latest_plan_sibling(execute_stage_dir)
    if plan_dir is None:
        return
    raw = _read_preregistration_field(plan_dir, _W13_LOAD_BEARING_ARM_FIELD)
    if raw is _RESOLVE_MISSING:
        return  # Older case: no load_bearing_arm declared → no-op.
    named = _w13_named_arm_keys(raw)
    if not named:
        return  # Empty/blank/wrong-typed declaration → no-op.

    for path, block in _w16_load_bearing_blocks(results, named):
        hit = _w16_degenerate_metric(block, domain)
        if hit is None:
            continue
        field, value, lo, hi = hit
        has_companion = _w16_has_disambiguating_companion(block, field, domain)
        sink = warnings if has_companion else errors
        tag = "W16 (advisory)" if has_companion else "W16"
        prescription = (
            "a metric pinned at the null boundary with a collapsed CI is "
            "consistent with BOTH the claimed mechanism AND a degenerate/"
            "uninformative predictor; report a companion measurement that "
            "separates them (e.g. a non-degenerate paired Δ vs. a planted-"
            "null control, a per-input score distribution, or the metric on "
            "a positive control where the mechanism MUST move it)."
        )
        if has_companion:
            advisory = (
                " A non-degenerate companion measurement IS present in this "
                "block, so W16 is ADVISORY: REVIEW must confirm the headline "
                "leans on the companion, not the degenerate cell, and never "
                "presents the pinned value as a positive demonstration."
            )
        else:
            advisory = (
                " This degenerate value is the SOLE evidence in the headline "
                "arm — there is no companion to separate the claim from a "
                "null. W16 blocks progression to REVIEW: add the disambiguating measurement "
                "and re-emit results.json, or re-headline on an arm that "
                "discriminates the claim by recording the narrowed (`scoped`) framing in results.json."
            )
        sink.append(
            f"{tag}: {plan_dir.name}/preregistration.json names "
            f"load_bearing_arm {named!r}; the backing block "
            f"{path}.{field}={value:g} with ci_95=[{lo:g}, {hi:g}] is "
            f"DEGENERATE — {prescription}{advisory}"
        )


def _w17_resolution_framing(results: Any) -> str:
    """Read the run's central-question framing STRUCTURALLY from
    results.json — never by parsing prose.

    Returns the canonical
    `results.json["central_question"]["discriminating_arm_resolution"]`
    value, lower-cased and whitespace-trimmed, when it is the legal
    `"scoped"` value; otherwise returns `_W17_DEFAULT_FRAMING`
    (`"resolved"`). The conservative default means an absent/malformed/
    un-narrowed block reads as a run that CLAIMS its central question —
    so an unrun discriminating arm ERRORs rather than slipping through.
    """
    if not isinstance(results, dict):
        return _W17_DEFAULT_FRAMING
    block = results.get(_W17_CENTRAL_QUESTION_KEY)
    # `central_question` may be a single dict (common) or a LIST of questions
    # (a multi-question run). For a list, the run is `scoped` iff ANY question
    # is framed scoped (a single resolved question among scoped ones still
    # leaves a scoped exit to corroborate / cap); otherwise resolved.
    candidates = (
        [block]
        if isinstance(block, dict)
        else [b for b in block if isinstance(b, dict)]
        if isinstance(block, list)
        else []
    )
    for b in candidates:
        raw = b.get(_W17_RESOLUTION_KEY)
        if isinstance(raw, str) and raw.strip().lower() == _W17_SCOPED_FRAMING:
            return _W17_SCOPED_FRAMING
    return _W17_DEFAULT_FRAMING


def _w17_central_question_blocks(results: Any) -> list[dict]:
    """Return the central_question block(s) as a list of dicts.

    `central_question` may be a single dict (the common case) or a LIST of
    central-question dicts (a multi-question run). Non-dict entries are
    dropped. Returns [] when the block is absent or malformed — the caller
    treats that as "no structured corroboration / a single question".
    """
    if not isinstance(results, dict):
        return []
    block = results.get(_W17_CENTRAL_QUESTION_KEY)
    if isinstance(block, dict):
        return [block]
    if isinstance(block, list):
        return [b for b in block if isinstance(b, dict)]
    return []


def _w17_scoped_is_corroborated(results: Any) -> bool:
    """True iff a `scoped` exit carries a STRUCTURED, canonical-enum
    `blocked_reason` (NOT bare self-assertion / free text).

    Reads `central_question.blocked_reason` (single-dict or, for a list of
    questions, ANY scoped-framed entry) and accepts it only when its value is
    one of `ScopedExitCorroborationGate.BLOCKED_REASONS`. Absent / free-text /
    wrong-typed → uncorroborated (False). Fail-safe: a malformed block reads
    as uncorroborated (the conservative direction — an unjustified scoped exit
    should escalate, not slip).
    """
    key = ScopedExitCorroborationGate.BLOCKED_REASON_KEY
    enum = ScopedExitCorroborationGate.BLOCKED_REASONS
    for block in _w17_central_question_blocks(results):
        reason = block.get(key)
        if isinstance(reason, str) and reason.strip().lower() in enum:
            return True
    return False


def _w17_scoped_exit_count(results: Any) -> int:
    """How many `scoped` exits this run has accumulated across its central
    question(s) — read STRUCTURALLY from results.json (no STATE.md schema
    change).

    Resolution order:
      1. An explicit integer `central_question.scoped_exit_count` (the run's
         own cross-stage counter), when present and >= 1, wins.
      2. Otherwise, when `central_question` is a LIST, count the entries framed
         `scoped` (each is one scoped central question).
      3. Otherwise (a single scoped dict, no explicit counter) → 1.

    Always returns >= 1 once this is reached (the caller only consults it in
    the scoped branch, i.e. there is at least one scoped exit).
    """
    blocks = _w17_central_question_blocks(results)
    count_key = ScopedExitCorroborationGate.SCOPED_EXIT_COUNT_KEY
    for block in blocks:
        raw = block.get(count_key)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int) and raw >= 1:
            return raw
    if isinstance(results, dict) and isinstance(
        results.get(_W17_CENTRAL_QUESTION_KEY), list
    ):
        scoped = 0
        for block in blocks:
            raw_framing = block.get(_W17_RESOLUTION_KEY)
            if isinstance(raw_framing, str) and raw_framing.strip().lower() == _W17_SCOPED_FRAMING:
                scoped += 1
        if scoped >= 1:
            return scoped
    return 1


def _check_w17_discriminating_arm_resolution(
    execute_stage_dir: pathlib.Path,
    results: dict,
    errors: list[str],
    warnings: list[str],
) -> None:
    """W17 — discriminating-arm resolution gate (the key rigor lever).

    Sibling of W13. W13 verifies the HEADLINE arm (the effect EXISTS) is
    complete; W17 verifies the DISCRIMINATING arm — the single experiment
    that would ADJUDICATE the run's central mechanistic/causal question
    — is complete before the run may frame that question as RESOLVED.

    The arm is named in `preregistration.json.discriminating_arm` by its
    `comparisons.delta_*` key (string or list — reuses `_w13_named_arm_keys`
    and `_W13_COMPARISONS_KEY`, NOT a parallel mechanism). The resolution
    framing is read structurally via `_w17_resolution_framing` (no prose
    parsing). Severity:

      * silent PASS — the discriminating arm RESOLVED in results.json
        (the deciding experiment ran), regardless of framing;
      * ERROR — arm UNRUN and framing is `"resolved"` (the over-claim the
        assessment found in all six wave runs): the central question is
        asserted answered while the deciding experiment did not run →
        progression blocked. REVIEW routes `retry_execute` (run the arm, which
        PLAN scheduled first on the scarce resource) or a `scoped`
        re-headline (re-headline on what the completed arms establish);
      * WARN — arm UNRUN but framing is `"scoped"` (the honest narrowed-scope
        path): advisory note that the central question stays open. P1
        hardening (2026-06-19, ScopedExitCorroborationGate): a `scoped` exit
        is no longer FREE — it ESCALATES to a stronger advisory when it
        carries no structured `central_question.blocked_reason` (a canonical
        enum, NOT bare self-assertion) OR when the run has accumulated more
        than `MAX_SCOPED_EXITS` scoped framings (serial scoping = dodging).
        A single, corroborated scope under the cap stays the benign WARN.
        Still WARN-only either way — never blocks progression;
      * WARN — no `discriminating_arm` declared at all: PLAN should name
        the experiment the review would otherwise list as "future work". Degrades
        gracefully — never crashes, never blocks.

    Backward compatibility: no plan sibling / no field / empty value →
    the no-field WARN path; older results.json with no `central_question`
    block defaults framing to `"resolved"` (conservative).
    """
    plan_dir = _find_latest_plan_sibling(execute_stage_dir)
    if plan_dir is None:
        return
    raw = _read_preregistration_field(plan_dir, _W17_DISCRIMINATING_ARM_FIELD)
    if raw is _RESOLVE_MISSING:
        warnings.append(
            f"W17: {plan_dir.name}/preregistration.json declares no "
            f"`{_W17_DISCRIMINATING_ARM_FIELD}` — PLAN should name the single "
            "experiment that would ADJUDICATE the run's central question "
            "(the one the review would otherwise list as 'future work that "
            "would settle it'), by its comparisons.delta_* key, so it can be "
            "scheduled FIRST on the scarce resource and gated. ADVISORY: "
            "this never blocks progression — but a run that poses a central "
            "question and never registers the experiment that closes it is "
            "the recurring rigor blocker the portfolio assessment found."
        )
        return
    named = _w13_named_arm_keys(raw)
    if not named:
        warnings.append(
            f"W17: {plan_dir.name}/preregistration.json `"
            f"{_W17_DISCRIMINATING_ARM_FIELD}` is empty/blank — name the "
            "comparisons.delta_* key of the experiment that adjudicates the "
            "central question. ADVISORY: never blocks progression."
        )
        return

    comparisons = results.get(_W13_COMPARISONS_KEY) if isinstance(results, dict) else None
    present = comparisons if isinstance(comparisons, dict) else {}
    missing = [k for k in named if k not in present]
    if not missing:
        return  # The deciding experiment ran → silent pass.

    framing = _w17_resolution_framing(results)
    joined = ", ".join(f"comparisons.{k}" for k in missing)
    if framing == _W17_SCOPED_FRAMING:
        # P1 hardening: a `scoped` exit is not a FREE downgrade. It escalates
        # the benign WARN when (1) it carries no STRUCTURED, canonical-enum
        # `blocked_reason` (uncorroborated self-assertion), or (2) the run has
        # accumulated more than MAX_SCOPED_EXITS scoped framings (serial
        # scoping = dodging, not honest narrowing). A single, corroborated
        # scope under the cap stays the benign base WARN. WARN-only throughout.
        corroborated = _w17_scoped_is_corroborated(results)
        scoped_count = _w17_scoped_exit_count(results)
        over_cap = scoped_count > ScopedExitCorroborationGate.MAX_SCOPED_EXITS
        if not corroborated or over_cap:
            enum_list = ", ".join(ScopedExitCorroborationGate.BLOCKED_REASONS)
            if over_cap:
                escalation = (
                    f"this run has accumulated {scoped_count} `scoped` exits "
                    f"(> MAX_SCOPED_EXITS={ScopedExitCorroborationGate.MAX_SCOPED_EXITS}) — "
                    "a run that narrows every decisive arm to `scoped` is not "
                    "honestly scoping, it is DODGING the deciding experiments. "
                )
            else:
                escalation = (
                    f"the {_W17_SCOPED_FRAMING} resolution carries no STRUCTURED "
                    f"{_W17_CENTRAL_QUESTION_KEY}.{ScopedExitCorroborationGate.BLOCKED_REASON_KEY} "
                    f"(a canonical enum: {enum_list}) — it is a bare self-assertion. "
                )
            warnings.append(
                f"{ScopedExitCorroborationGate.GATE_ID} (escalated advisory): "
                f"{plan_dir.name}/{_PREREGISTRATION_FILE} names "
                f"{_W17_DISCRIMINATING_ARM_FIELD} {named!r} but {joined} did not "
                f"run, and {_RESULTS_JSON}[\"{_W17_CENTRAL_QUESTION_KEY}\"]["
                f"\"{_W17_RESOLUTION_KEY}\"]=\"{_W17_SCOPED_FRAMING}\" — but "
                f"{escalation}The central question is NOT resolved and the "
                "headline MUST NOT imply it is. A `scoped` exit must be "
                "corroborated by a structured "
                f"{ScopedExitCorroborationGate.BLOCKED_REASON_KEY} (one of: "
                f"{enum_list}) and a run may not serially scope past "
                f"{ScopedExitCorroborationGate.MAX_SCOPED_EXITS} decisive arms. "
                "ADVISORY only (never blocks progression): either run the discriminating "
                "arm, or record a structured blocked_reason and re-headline "
                "strictly on what the completed arms establish."
            )
            return
        warnings.append(
            f"W17 (advisory): {plan_dir.name}/preregistration.json names "
            f"{_W17_DISCRIMINATING_ARM_FIELD} {named!r} but {joined} did not "
            f"run, and {_RESULTS_JSON}[\"{_W17_CENTRAL_QUESTION_KEY}\"]["
            f"\"{_W17_RESOLUTION_KEY}\"]=\"{_W17_SCOPED_FRAMING}\" — the run "
            "correctly narrows its claim to the completed arms (the honest "
            "path) with a structured blocked_reason, under the scoped-exit cap. "
            "ADVISORY only: the central question remains OPEN; keep "
            "the unrun arm as named future work, do not frame it as resolved."
        )
        return
    errors.append(
        f"{plan_dir.name}/preregistration.json names "
        f"{_W17_DISCRIMINATING_ARM_FIELD} {named!r} but {joined} is absent "
        f"from {_RESULTS_JSON}[\"{_W13_COMPARISONS_KEY}\"] while the central "
        f"question is framed as RESOLVED ({_RESULTS_JSON}[\""
        f"{_W17_CENTRAL_QUESTION_KEY}\"][\"{_W17_RESOLUTION_KEY}\"] is "
        f"\"{_W17_RESOLVED_FRAMING}\" or absent) — W17 (discriminating-arm "
        "resolution gate) blocks progression to REVIEW: the run asserts its central "
        "question is answered while the experiment that would ADJUDICATE it "
        "did not run. A rigorous study runs the deciding experiment; it does not "
        "defer it to future work and claim resolution. Either run the "
        "discriminating arm and re-emit results.json (PLAN scheduled it first "
        "on the scarce resource), or set "
        f"{_RESULTS_JSON}[\"{_W17_CENTRAL_QUESTION_KEY}\"][\""
        f"{_W17_RESOLUTION_KEY}\"]=\"{_W17_SCOPED_FRAMING}\" in a "
        "retry_execute — re-headline the results on what the completed arms "
        "establish, with the open question kept as honest future work."
    )


def _w23_constant_input_signature(
    block: Any,
) -> tuple[str, Any, int] | None:
    """Detect signature (1): a per-unit panel whose decisive numerator field
    is CONSTANT across all comparison units (zero variance).

    `block` is searched for a panel list under any `DecisiveArmPowerGate.
    PANEL_FIELDS` key (or, if `block` is itself a list, treated as the panel).
    For each `DecisiveArmPowerGate.POWER_INPUT_FIELDS` numerator name, if EVERY
    unit in the panel carries that field AND they all share ONE value (over
    >= MIN_UNITS units), returns (field_name, value, n_units). Else None.

    This is the regime defect verbatim: the coverage numerator `c == 1` across
    all 10 models, so the leave-one-model-out predictor had zero power even
    though the ratio `c_over_d` varied (because the denominator varied). The
    check keys on the INPUT (numerator), never the ratio — a varying ratio over
    a constant numerator is exactly the trap.
    """
    panels: list[list] = []
    if isinstance(block, list):
        panels.append(block)
    elif isinstance(block, dict):
        for pf in DecisiveArmPowerGate.PANEL_FIELDS:
            cand = block.get(pf)
            if isinstance(cand, list):
                panels.append(cand)
    for panel in panels:
        units = [u for u in panel if isinstance(u, dict)]
        if len(units) < DecisiveArmPowerGate.MIN_UNITS:
            continue
        for field in DecisiveArmPowerGate.POWER_INPUT_FIELDS:
            values = [u[field] for u in units if field in u]
            # Require the field present on EVERY unit (a partial panel is
            # ambiguous → skip, conservative).
            if len(values) != len(units):
                continue
            # Only numeric numerators (a constant string label is not a
            # power signal). bool excluded (1/0 flags are not numerators).
            if not all(
                isinstance(v, (int, float)) and not isinstance(v, bool)
                for v in values
            ):
                continue
            distinct = {float(v) for v in values}
            if len(distinct) == 1:
                return (field, values[0], len(units))
    return None


def _w23_zero_power_signature(block: Any) -> str | None:
    """Detect signature (2): an explicitly-reported power field reads 0.

    Searches `block` (an arm block or the run-level results) and any nested
    `DecisiveArmPowerGate.POWER_BLOCK_FIELDS` / `REALISED_BALANCE_FIELD`
    sub-blocks for:
      * any `POWER_FIELDS` scalar == 0 (e.g. power_to_detect_perfect_boundary);
      * a `power_curve` list whose EVERY entry has power == 0 (>= 1 entry);
      * an `MDE_AT_POWER_FIELD` explicitly null while a power_curve exists
        (no effect size reaches the power target → zero power).

    Returns a short human description of the first hit, else None. Conservative:
    a power field that is ABSENT (not 0) is never a hit; only an explicit 0 /
    explicit-null-with-curve fires.
    """
    if not isinstance(block, dict):
        return None
    # The candidate scopes to inspect: the block itself plus its nested
    # power-analysis / realised-balance sub-blocks.
    scopes: list[dict] = [block]
    for bf in DecisiveArmPowerGate.POWER_BLOCK_FIELDS:
        sub = block.get(bf)
        if isinstance(sub, dict):
            scopes.append(sub)
            rb = sub.get(DecisiveArmPowerGate.REALISED_BALANCE_FIELD)
            if isinstance(rb, dict):
                scopes.append(rb)
    rb_top = block.get(DecisiveArmPowerGate.REALISED_BALANCE_FIELD)
    if isinstance(rb_top, dict):
        scopes.append(rb_top)

    for scope in scopes:
        for field in DecisiveArmPowerGate.POWER_FIELDS:
            if field not in scope:
                continue
            v = scope[field]
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)) and float(v) == 0.0:
                return f"{field}={v}"
        curve = scope.get(DecisiveArmPowerGate.POWER_CURVE_FIELD)
        if isinstance(curve, list) and curve:
            powers = [
                pt.get(DecisiveArmPowerGate.POWER_CURVE_POWER_KEY)
                for pt in curve
                if isinstance(pt, dict)
            ]
            powers = [
                p for p in powers
                if isinstance(p, (int, float)) and not isinstance(p, bool)
            ]
            if powers and all(float(p) == 0.0 for p in powers):
                # An MDE-at-target of null corroborates "no effect size reaches
                # the target", but the all-zero curve alone is sufficient.
                return (
                    f"{DecisiveArmPowerGate.POWER_CURVE_FIELD} power == 0 at "
                    f"all {len(powers)} effect sizes"
                )
            if DecisiveArmPowerGate.MDE_AT_POWER_FIELD in scope and (
                scope.get(DecisiveArmPowerGate.MDE_AT_POWER_FIELD) is None
            ):
                return (
                    f"{DecisiveArmPowerGate.MDE_AT_POWER_FIELD}=null "
                    "(no effect size reaches the power target)"
                )
    return None


def _w23_full_range_ci_signature(
    block: Any, domain: str | None
) -> str | None:
    """Detect signature (3): the arm's bootstrap `ci_95` spans the ENTIRE
    admissible range of a bounded-metric class — no discrimination possible.

    Fires ONLY when a bounded-metric point estimate is present in the block
    (so the admissible range is known) AND the paired `ci_95` runs lo == the
    class minimum and hi == the class maximum (0..1 for a 0_1 metric, -1..1 for
    a correlation). A merely WIDE CI is not a hit — it must touch BOTH extremes.
    Conservative: no bounded metric / no ci_95 / a CI that stops short of either
    extreme → None (a small-n honest null with a wide-but-not-full CI is exempt).
    """
    if not isinstance(block, dict):
        return None
    ci = block.get("ci_95")
    if not (isinstance(ci, list) and len(ci) == 2):
        return None
    try:
        lo = float(ci[0])
        hi = float(ci[1])
    except (TypeError, ValueError):
        return None
    if math.isnan(lo) or math.isnan(hi) or math.isinf(lo) or math.isinf(hi):
        return None
    if lo > hi:
        lo, hi = hi, lo
    eps = _W16_BOUNDARY_EPSILON
    for key, value in block.items():
        if not _is_point_estimate_field(key):
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        lower = key.lower()
        if lower in metric_bounds_0_1(domain):
            if abs(lo - 0.0) <= eps and abs(hi - 1.0) <= eps:
                return f"{key} ci_95=[{lo}, {hi}] spans the full [0, 1] range"
        elif lower in metric_bounds_neg1_1(domain):
            if abs(lo - (-1.0)) <= eps and abs(hi - 1.0) <= eps:
                return f"{key} ci_95=[{lo}, {hi}] spans the full [-1, 1] range"
    return None


def _check_w23_decisive_arm_power(
    execute_stage_dir: pathlib.Path,
    results: dict,
    warnings: list[str],
    domain: str | None = None,
) -> None:
    """W23 — decisive-arm zero-power gate (ADVISORY / warn-only sibling of W17).

    W17 verifies the pre-registered DISCRIMINATING arm RAN before the central
    question is framed RESOLVED; W23 verifies that arm — when it DID run — had
    statistical POWER to discriminate. The motivating defect (posttrain-regime-
    predicts-erasure, 2026-06): the plan pre-registered a leave-one-model-out
    coverage predictor whose numerator `c` collapsed to a CONSTANT (1 across all
    10 models), so the decisive test had power == 0 at every effect size and
    could not fire — yet nothing flagged it and the run had to be re-framed by
    hand as a measurement-design null.

    The named arm (reusing W17's `discriminating_arm` field + the SAME
    `_w13_named_arm_keys` / `_W13_COMPARISONS_KEY` machinery — NOT a parallel
    mechanism) plus the run-level `power_analysis` block are checked for any ONE
    of three conservative degenerate-predictor signatures:
      (1) CONSTANT-INPUT — a panel whose decisive numerator is constant across
          comparison units (zero variance);
      (2) ZERO-POWER — an explicit power field == 0 / an all-zero power_curve /
          an MDE-at-power-target of null;
      (3) FULL-RANGE-CI — the arm's ci_95 spans an entire bounded-metric range.

    WARN-only (shipped WARN-first like W21/W15L): appends to `warnings` only,
    takes no errors sink, never flips ok / routes back.

    Fail-safe no-op (false-positive guard): no plan sibling / no discriminating_arm
    declared / empty value → no-op (W17 already WARNs about an absent declaration;
    W23 is about a present-but-powerless arm); the named arm absent from
    results.json → no-op (W17's job); the arm varied / has nonzero power → no-op.
    A legitimate honest-null where the arm FIRED with power and returned a null
    effect must NOT trip it. The signature draws the line "the test had no power
    to detect an effect" vs "the test ran with power and found none".
    """
    plan_dir = _find_latest_plan_sibling(execute_stage_dir)
    if plan_dir is None:
        return
    raw = _read_preregistration_field(plan_dir, _W17_DISCRIMINATING_ARM_FIELD)
    if raw is _RESOLVE_MISSING:
        return  # No discriminating_arm declared → W17's absence-WARN territory.
    named = _w13_named_arm_keys(raw)
    if not named:
        return  # Empty/blank/wrong-typed declaration → no-op.

    comparisons = results.get(_W13_COMPARISONS_KEY) if isinstance(results, dict) else None
    present = comparisons if isinstance(comparisons, dict) else {}
    # The run-level power-analysis block backs every arm's power statement.
    # Resolve it via the canonical alias list (POWER_BLOCK_FIELDS = the same
    # constant _w23_zero_power_signature iterates), not a bare literal, so the
    # block name stays single-sourced — a results dict using the `"power"`
    # alias instead of `"power_analysis"` is recognised identically.
    run_power = None
    if isinstance(results, dict):
        for _bf in DecisiveArmPowerGate.POWER_BLOCK_FIELDS:
            if _bf in results:
                run_power = results.get(_bf)
                break

    for key in named:
        arm = present.get(key)
        if not isinstance(arm, dict):
            continue  # Arm absent / non-dict → W17's job, not a power signal.
        hit: str | None = None
        signature: str | None = None
        # (1) constant decisive INPUT across a per-unit panel — checked on the
        # arm block AND the run-level results (the regime panel lives top-level).
        const = _w23_constant_input_signature(arm)
        if const is None and isinstance(results, dict):
            const = _w23_constant_input_signature(results)
        if const is not None:
            field, value, n_units = const
            signature = "constant decisive input"
            hit = (
                f"its decisive input `{field}` is CONSTANT (== {value}) across "
                f"all {n_units} comparison units (zero variance)"
            )
        # (2) explicit zero power on the arm or the run-level power_analysis.
        if hit is None:
            zp = _w23_zero_power_signature(arm)
            if zp is None and isinstance(run_power, dict):
                # Re-wrap under the canonical block field (the first
                # POWER_BLOCK_FIELDS alias) so _w23_zero_power_signature finds
                # it as a nested power sub-block — single-sourced, no literal.
                zp = _w23_zero_power_signature(
                    {DecisiveArmPowerGate.POWER_BLOCK_FIELDS[0]: run_power}
                )
            if zp is not None:
                signature = "zero reported power"
                hit = f"its reported power is zero ({zp})"
        # (3) full-range bootstrap CI on a bounded metric.
        if hit is None:
            fr = _w23_full_range_ci_signature(arm, domain)
            if fr is not None:
                signature = "full-range CI"
                hit = f"its bootstrap CI is non-discriminating ({fr})"
        if hit is None:
            continue  # This arm varied / had power → no-op (the healthy case).

        warnings.append(
            f"{DecisiveArmPowerGate.GATE_ID}: {plan_dir.name}/"
            f"{_PREREGISTRATION_FILE} names {_W17_DISCRIMINATING_ARM_FIELD} "
            f"'{key}' but in {execute_stage_dir.name}/{_RESULTS_JSON} {hit} — "
            f"the pre-registered DECISIVE test could not fire ({signature}): it "
            "had statistical power == 0 to detect an effect at any size, so it "
            "cannot discriminate the discriminating hypothesis from the null "
            "(the posttrain-regime coverage-numerator defect). ADVISORY only: "
            "this never blocks progression — but the headline must be framed as a "
            "MEASUREMENT-DESIGN negative (the decisive arm was structurally "
            "degenerate), NOT as a supported or honest-null scientific result. "
            "REVIEW should either re-design the decisive arm so its input varies "
            "/ has power, or re-headline on a powered arm and report the "
            "degenerate arm as a measurement-design finding."
        )
        return  # One WARN per run is enough; do not spam per-arm.


def _w25_arm_effect(arm: Any) -> float | None:
    """Return the |point-estimate| of a comparisons.* arm block, or None.

    Reuses `_extract_point_estimate` (the SAME resolver W7/W16/the point-in-CI
    gate use — NOT a parallel mechanism) so the headline effect is read
    identically everywhere. Returns the absolute value (a sub-MDE check is
    sign-agnostic: a -0.0001 effect is as undetectable as +0.0001).
    """
    if not isinstance(arm, dict):
        return None
    pe = _extract_point_estimate(arm)
    if pe is None:
        return None
    _field, value = pe
    if math.isnan(value) or math.isinf(value):
        return None
    return abs(value)


def _check_w25_effect_clears_mde(
    execute_stage_dir: pathlib.Path,
    results: dict,
    warnings: list[str],
) -> None:
    """W25 — effect-clears-own-MDE gate (ADVISORY / warn-only sibling of W2/W17).

    W2 verifies a numeric MDE is DECLARED in preregistration.json.mde; W25
    verifies the run's load-bearing/headline effect actually CLEARS that MDE.
    The motivating defect (audit proposal P4, 2026-06-18): runs shipped with
    headline effects far below their own declared MDE, framed as positive
    findings — sensitive-memory-retrieval (Δ=+0.0043 vs MDE 0.03 → 7× sub-MDE)
    and fair-intersectional-subspace (+0.0013 vs MDE 0.02 → 15× sub-MDE).

    Two tiers, both keyed on the headline/load-bearing arm only (like W17/W23):

      TIER 1 (sharp) — a `load_bearing_arm` is declared (W13's field + the SAME
        `_w13_named_arm_keys` normaliser): read that arm's |effect| from
        results.json["comparisons"]; WARN if |effect| < mde for ANY named arm.

      TIER 2 (advisory fallback) — no load_bearing_arm is declared but a numeric
        MDE is, and there exist `comparisons.delta_primary_vs_*` primary-contrast
        arms: WARN (advisory) iff EVERY such arm is sub-MDE (the largest |effect|
        among them does not clear the MDE). A single primary contrast clearing
        the MDE → no-op (a powered contrast exists).

    WARN-only (shipped WARN-first like W21/W15L/W23): appends to `warnings` only,
    takes no errors sink, never flips ok / routes back.

    Fail-safe NO-OP (false-positive guard): no plan sibling / no numeric MDE
    declared (W2's job) / no load_bearing_arm AND no primary-contrast arm / no
    resolvable point estimate / the effect clears the MDE. An honest null whose
    arm cleared power and returned a null effect is correctly framed already;
    because W25 only ever WARNs (never blocks) and Tier 2's message is advisory,
    the rare honest-null overlap costs at most one advisory note, never a block.
    """
    plan_dir = _find_latest_plan_sibling(execute_stage_dir)
    if plan_dir is None:
        return
    mde_raw = _read_preregistration_field(plan_dir, EffectClearsMdeGate.MDE_FIELD)
    if mde_raw is _RESOLVE_MISSING or mde_raw is None:
        return  # No declared MDE → W2's territory (declaration gate), not W25.
    if isinstance(mde_raw, bool) or not isinstance(mde_raw, (int, float)):
        return  # Non-numeric MDE → W2 flags the declaration; W25 stays silent.
    mde = abs(float(mde_raw))
    if math.isnan(mde) or math.isinf(mde) or mde <= 0.0:
        return  # Degenerate MDE → nothing to clear.

    comparisons = results.get(_W13_COMPARISONS_KEY) if isinstance(results, dict) else None
    present = comparisons if isinstance(comparisons, dict) else {}

    # TIER 1 — a load_bearing_arm is declared: read its own effect (sharp).
    raw = _read_preregistration_field(plan_dir, _W13_LOAD_BEARING_ARM_FIELD)
    if raw is not _RESOLVE_MISSING:
        named = _w13_named_arm_keys(raw)
        for key in named:
            effect = _w25_arm_effect(present.get(key))
            if effect is None:
                continue  # Arm absent / non-numeric → W13's job, no effect to test.
            if effect < mde:
                warnings.append(
                    f"{EffectClearsMdeGate.GATE_ID}: {plan_dir.name}/"
                    f"{_PREREGISTRATION_FILE} declares mde={mde_raw} but the "
                    f"load_bearing_arm 'comparisons.{key}' effect "
                    f"|{effect:.6g}| is BELOW it ({effect:.6g} < {mde:.6g}) in "
                    f"{execute_stage_dir.name}/{_RESULTS_JSON} — the headline "
                    "leads with an effect that does NOT clear its own "
                    "pre-registered minimum detectable effect. It must be framed "
                    "as BELOW the pre-registered detectability threshold "
                    "(formally undetectable), NOT as a positive finding. "
                    "ADVISORY only: this never blocks progression — but a headline "
                    "below its own MDE is the sensitive-memory / intersectional-"
                    "subspace defect (P4). Either re-headline on a powered arm "
                    "or state the sub-MDE caveat in the reported results."
                )
                return  # One WARN per run is enough; do not spam per-arm.
        # A load_bearing_arm was declared and (every named arm cleared the MDE
        # OR was unresolvable). Either way Tier 1 is authoritative — do NOT
        # fall through to the broad Tier-2 fallback.
        if named:
            return

    # TIER 2 — no load_bearing_arm declared: fall back to the primary-contrast
    # family. WARN (advisory) only if EVERY `delta_primary_vs_*` arm is sub-MDE.
    contrast_effects: list[tuple[str, float]] = []
    for key, block in present.items():
        if not (isinstance(key, str) and key.startswith(EffectClearsMdeGate.PRIMARY_CONTRAST_PREFIX)):
            continue
        effect = _w25_arm_effect(block)
        if effect is None:
            continue
        contrast_effects.append((key, effect))
    if not contrast_effects:
        return  # No resolvable primary-contrast arm → nothing to test (no-op).
    max_key, max_effect = max(contrast_effects, key=lambda kv: kv[1])
    if max_effect >= mde:
        return  # A primary contrast clears the MDE → powered contrast exists, no-op.
    warnings.append(
        f"{EffectClearsMdeGate.GATE_ID}: {plan_dir.name}/{_PREREGISTRATION_FILE} "
        f"declares mde={mde_raw} but NO `{_W13_LOAD_BEARING_ARM_FIELD}` is named, "
        f"and EVERY primary-contrast arm (comparisons."
        f"{EffectClearsMdeGate.PRIMARY_CONTRAST_PREFIX}*) is sub-MDE in "
        f"{execute_stage_dir.name}/{_RESULTS_JSON} — the largest, "
        f"'comparisons.{max_key}', is |{max_effect:.6g}| < {mde:.6g}. No primary "
        "contrast cleared its own pre-registered detectability threshold, so the "
        "headline necessarily leads with a formally-undetectable effect (the "
        "sensitive-memory / intersectional-subspace defect, P4). ADVISORY only: "
        "verify the sub-MDE effect is framed in the reported results as BELOW the "
        "pre-registered MDE (formally undetectable), NOT as a positive result; "
        "and name the load_bearing_arm in preregistration.json so W25 can gate it "
        "sharply (cf. W21)."
    )


# W32 — within-EXECUTE parallel-arm independence gate. The pre-dispatch CLI gate
# (python -m omicairn.parallel_arms validate) is prompt-driven (soft); this is the
# HARD BACKSTOP that re-validates the same contract off the ON-DISK manifest +
# materialized arm dirs and BLOCKS progression on a genuine violation. It also folds in
# Proposal C's ADVISORY recorded-evidence re-check (validate_recorded, reads
# arm_provenance.jsonl) as WARN-only findings. Silent no-op when neither
# arm_manifest.json nor arm_provenance.jsonl is present (the critical
# backward-compat case: every run that does not fan out sees nothing).
def _check_parallel_arm_contract(
    execute_stage_dir: pathlib.Path,
    errors: list[str],
    warnings: list[str],
) -> None:
    """W32 — parallel-arm contract re-validation (BLOCKING manifest invariants
    + ADVISORY recorded-evidence).

    (a) When ``arm_manifest.json`` is present: re-run
        ``parallel_arms.validate_manifest`` against the on-disk manifest and
        append every HARD-invariant violation (overlapping/nested/escaping
        output_dir, duplicate/missing/non-int seed, concurrent-GPU over
        gpu_slots, reserved-host routing, empty manifest, dup arm_id,
        max_parallel over cap) to ``errors`` (a real contract violation must
        not reach REVIEW).
    (b) ADVISORY: call ``parallel_arms.validate_recorded`` (reads
        ``arm_provenance.jsonl`` + inspects materialized arm dirs) and append
        its findings to ``warnings`` only (WARN-first like W21/W23/W25).

    Silent no-op when NEITHER file is present.
    """
    manifest_path = execute_stage_dir / _ARM_MANIFEST_FILE
    # Per-arm provenance fragments live in each arm's own dir
    # (arms/<arm_id>/arm_provenance.json), never a single shared append-log —
    # detect the presence of ANY fragment for the no-op decision.
    has_fragment = bool(
        list(execute_stage_dir.glob(f"*/*/{_ARM_PROVENANCE_FRAGMENT}"))
        or list(execute_stage_dir.glob(f"*/{_ARM_PROVENANCE_FRAGMENT}"))
    ) if execute_stage_dir.exists() else False
    if not manifest_path.exists() and not has_fragment:
        return  # backward-compatible no-op — the critical case

    gate = ParallelArmIndependenceGate.GATE_ID
    if manifest_path.exists():
        verdict = _parallel_arms.validate_manifest(
            manifest_path, stage_dir=execute_stage_dir
        )
        for e in verdict.errors:
            # Malformed-input errors from the module already carry a "W32:"
            # prefix (from _fail); the per-invariant errors are plain strings.
            # Ensure the gate id is present exactly once, then annotate that
            # this is the on-disk re-validation (not the pre-dispatch CLI gate).
            msg = e if e.startswith(gate) else f"{gate}: {e}"
            errors.append(
                f"{msg} (re-validated on-disk in {execute_stage_dir.name}/"
                f"{_ARM_MANIFEST_FILE})"
            )
        for w in verdict.warnings:
            warnings.append(f"{gate}: {w}")

    # ADVISORY recorded-evidence re-check (Proposal C graft). WARN-only.
    recorded = _parallel_arms.validate_recorded(execute_stage_dir)
    for w in recorded.warnings:
        warnings.append(
            f"{gate} (recorded-evidence advisory): {w} "
            f"[{execute_stage_dir.name}/arms/*/{_ARM_PROVENANCE_FRAGMENT}]"
        )


def _w20_resolve_metric_path(results: Any, path: str) -> Any:
    """Follow a dotted `metric` path into the results.json tree.

    `"h2_spearman.r_s"` → results["h2_spearman"]["r_s"]. Returns
    `_RESOLVE_MISSING` when any segment is absent or a non-dict is
    traversed (a missing key is NOT a violation — the caller WARNs, never
    ERRORs, on a missing metric). No prose parsing; pure structured lookup.
    """
    if not isinstance(path, str) or not path.strip():
        return _RESOLVE_MISSING
    node: Any = results
    for segment in path.split("."):
        if not isinstance(node, dict) or segment not in node:
            return _RESOLVE_MISSING
        node = node[segment]
    return node


def _w20_coerce_number(value: Any) -> float | None:
    """Coerce a JSON scalar to float for op comparison, or None if it is
    not a real finite number (bool is rejected — `True` is not a metric)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        if math.isfinite(f):
            return f
        return None
    return None


def _w20_ci_excludes(results: Any, metric_path: str, target: float) -> bool | None:
    """True iff the metric's `ci_95` sibling EXCLUDES `target`.

    Reads the `ci_95` list that lives ALONGSIDE the metric scalar — i.e. the
    parent dict of the dotted path carries a `ci_95: [lo, hi]`. `excludes`
    means `target < lo or target > hi`. Returns None when no usable two-
    element numeric ci_95 sibling exists (the caller then WARNs that the
    ci_excludes clause could not be evaluated — conservative, never ERRORs
    on an unresolvable CI). No prose parsing.
    """
    segments = metric_path.split(".")
    parent_path = ".".join(segments[:-1])
    parent = results if not parent_path else _w20_resolve_metric_path(results, parent_path)
    if not isinstance(parent, dict):
        return None
    ci = parent.get(_W20_CI95_KEY)
    if not isinstance(ci, (list, tuple)) or len(ci) != 2:
        return None
    lo = _w20_coerce_number(ci[0])
    hi = _w20_coerce_number(ci[1])
    if lo is None or hi is None:
        return None
    if lo > hi:
        lo, hi = hi, lo
    return target < lo or target > hi


def _read_preregistration(plan_dir: pathlib.Path) -> Any:
    """Read the WHOLE `<plan_dir>/preregistration.json` as a dict.

    Returns the parsed dict or `_RESOLVE_MISSING` if the file is absent /
    unreadable / not a JSON object. The per-field reader
    (`_read_preregistration_field`) can only fetch one named key; W24 needs the
    full object to walk `hypotheses[i].construct_validity` and the top-level
    declaration in one pass."""
    pre_path = plan_dir / _PREREGISTRATION_FILE
    if not pre_path.exists():
        return _RESOLVE_MISSING
    try:
        data = json.loads(pre_path.read_text())
    except (OSError, json.JSONDecodeError):
        return _RESOLVE_MISSING
    if not isinstance(data, dict):
        return _RESOLVE_MISSING
    return data


def _w24_iter_construct_validity_decls(prereg: Any):
    """Yield every `construct_validity` object declared in the preregistration.

    Two canonical homes (both OPTIONAL, mirroring W20's structured-rule twin):
      * top-level `preregistration.json.construct_validity`; AND
      * per-hypothesis `hypotheses[i].construct_validity`.
    Yields each object that is a dict (malformed / absent → simply not yielded).
    Pure structured read — no prose parsing (GP8).
    """
    if not isinstance(prereg, dict):
        return
    top = prereg.get(ConstructValidityGate.CONSTRUCT_VALIDITY_KEY)
    if isinstance(top, dict):
        yield top
    hyps = prereg.get(_W20_HYPOTHESES_FIELD)
    if isinstance(hyps, list):
        for entry in hyps:
            if not isinstance(entry, dict):
                continue
            cv = entry.get(ConstructValidityGate.CONSTRUCT_VALIDITY_KEY)
            if isinstance(cv, dict):
                yield cv


def _w24_decl_threshold(cv: dict) -> float | None:
    """Resolve the numeric validity threshold from a construct_validity object.

    Tries `ConstructValidityGate.THRESHOLD_KEYS` in order (min_rho canonical,
    then min_stat / threshold aliases). Returns the first finite numeric value,
    or None when none present / non-numeric (the gate then no-ops: a malformed
    declaration is not a violation)."""
    for key in ConstructValidityGate.THRESHOLD_KEYS:
        if key in cv:
            value = _w20_coerce_number(cv.get(key))
            if value is not None:
                return value
    return None


def _w24_is_load_bearing(cv: dict) -> bool:
    """A declared construct_validity requirement is LOAD-BEARING by default.

    `load_bearing` absent → True (the axis the headline rests on). An explicit
    boolean `False` (or the string 'false') opts the axis out → the gate
    no-ops. Any other value falls back to the load-bearing default (True):
    we only DEMOTE on an unambiguous opt-out."""
    if ConstructValidityGate.LOAD_BEARING_KEY not in cv:
        return True
    flag = cv.get(ConstructValidityGate.LOAD_BEARING_KEY)
    if flag is False:
        return False
    if isinstance(flag, str) and flag.strip().lower() == "false":
        return False
    return True


def _w24_ci_spans_null(results: Any, metric_path: str) -> bool:
    """True iff the validity metric resolves under a bounded-correlation class
    AND its `ci_95` sibling SPANS the correlation null (0.0 ∈ [lo, hi]).

    The CI lives alongside the metric scalar (same convention as W20/W23/W16):
    the parent dict of the dotted path carries `ci_95: [lo, hi]`. Only fires
    when the LEAF metric name is a recognised correlation metric
    (`metric_bounds_neg1_1` — pearson/spearman/rho/...), so a 0_1-bounded
    validity stat (e.g. an agreement proportion) is NOT subjected to the
    correlation-null test. Conservative: no correlation leaf / no usable
    two-element numeric ci_95 sibling → False (the point-vs-threshold test is
    then authoritative)."""
    if not isinstance(metric_path, str) or not metric_path.strip():
        return False
    leaf = metric_path.split(".")[-1].lower()
    corr_names = metric_bounds_neg1_1(None)
    # Exact match (`rho`, `spearman_r`, `pearson`, ...) OR a compound leaf one of
    # whose `_`-separated tokens is a recognised correlation metric — so a
    # real-world `spearman_rho` / `kendall_tau_b` resolves as a correlation
    # without enumerating every compound. Conservative: a token must EXACTLY
    # equal a correlation name (no fuzzy substring), so `correlation_count`
    # would NOT (token `count` is not a correlation), but `spearman_rho` does
    # (token `spearman` is).
    if leaf not in corr_names and not any(tok in corr_names for tok in leaf.split("_")):
        return False
    segments = metric_path.split(".")
    parent_path = ".".join(segments[:-1])
    parent = results if not parent_path else _w20_resolve_metric_path(results, parent_path)
    if not isinstance(parent, dict):
        return False
    ci = parent.get(ConstructValidityGate.CI95_KEY)
    if not isinstance(ci, (list, tuple)) or len(ci) != 2:
        return False
    lo = _w20_coerce_number(ci[0])
    hi = _w20_coerce_number(ci[1])
    if lo is None or hi is None:
        return False
    if lo > hi:
        lo, hi = hi, lo
    null = ConstructValidityGate.CORRELATION_NULL
    return lo <= null <= hi


def _check_w24_construct_validity(
    execute_stage_dir: pathlib.Path,
    results: dict,
    warnings: list[str],
) -> None:
    """W24 — construct-validity gate (ADVISORY / warn-only sibling of W20/W23/W25).

    When a headline rests on a LEARNED / SURROGATE axis (an LLM-judge stand-in
    for a human construct, a learned predictor used in place of a gold label),
    its scientific meaning is only as good as a pre-registered construct-validity
    check — agreement with a human/gold anchor. The motivating defect
    (sensitive-memory-retrieval, 2026-06): the appropriateness axis the whole
    frontier is built on was validated against human ConfAIde ratings at
    Spearman rho=0.276, 95% CI [-0.119, 0.594] — BELOW the pre-registered
    rho>=0.6 AND a CI spanning the null (0.0) — i.e. the validity check FAILED,
    yet the construct was left LOAD-BEARING and the run still got verdict
    `proceed`. No gate flagged that the headline rests on an UNVALIDATED
    construct. W20 checks a hypothesis DECISION rule was honored; W23 checks the
    decisive arm had POWER; neither checks a load-bearing construct's VALIDITY.

    Schema (OPTIONAL, mirrors W20's `decision_rule_structured` twin — PLAN emits
    a machine-checkable validity declaration alongside the prose H4/validity-gate
    rule; NO prose parsing, GP8). A `construct_validity` object, on a prereg
    hypothesis (`hypotheses[i].construct_validity`) and/or top-level prereg:

        "construct_validity": {
            "metric": "h4_judge_human_agreement.spearman_rho",  # dotted path
            "min_rho": 0.6,        # threshold the validity stat must CLEAR
                                   # (aliases: min_stat / threshold)
            "load_bearing": true   # OPTIONAL; default true. false → no-op.
        }

    FIRE (escalate a WARN) iff ALL hold for some declared object:
      (1) it declares a `metric` dotted path + a numeric threshold;
      (2) the axis is LOAD-BEARING (`load_bearing` absent/true);
      (3) the MEASURED stat at `metric` FAILS — point estimate strictly below
          the threshold, OR (for a bounded-correlation metric) its `ci_95`
          sibling SPANS the null (0.0).

    WARN-only (shipped WARN-first like W21/W15L/W23/W25): appends to `warnings`
    only, takes no errors sink, never flips ok / routes back.

    Fail-safe NO-OP (false-positive guard): no plan sibling / no
    `construct_validity` object (every existing / in-flight run → silent pass,
    back-compatible) / a malformed object (no metric or no numeric threshold) /
    an explicit `load_bearing: false` / an unresolvable measured stat / the
    validity stat PASSES (point >= threshold AND, for a correlation, its CI
    excludes 0.0). A genuine PASSING validation must NOT trip it.
    """
    plan_dir = _find_latest_plan_sibling(execute_stage_dir)
    if plan_dir is None:
        return
    prereg = _read_preregistration(plan_dir)
    if prereg is _RESOLVE_MISSING or not isinstance(prereg, dict):
        return  # No preregistration.json / unreadable → no-op (W2/W21 territory).

    for cv in _w24_iter_construct_validity_decls(prereg):
        metric_path = cv.get(ConstructValidityGate.METRIC_KEY)
        if not isinstance(metric_path, str) or not metric_path.strip():
            continue  # Malformed declaration (no metric) → not a violation.
        threshold = _w24_decl_threshold(cv)
        if threshold is None:
            continue  # No numeric threshold declared → nothing to clear.
        if not _w24_is_load_bearing(cv):
            continue  # Explicitly not load-bearing → not the headline → no-op.

        measured = _w20_resolve_metric_path(results, metric_path)
        point = _w20_coerce_number(measured) if measured is not _RESOLVE_MISSING else None
        ci_null = _w24_ci_spans_null(results, metric_path)

        if point is None and not ci_null:
            continue  # No resolvable measured stat → cannot test (no-op).

        point_fails = point is not None and point < threshold
        if not (point_fails or ci_null):
            continue  # Validity PASSES (point clears AND CI excludes null) → no-op.

        # Build a precise failure description (point and/or CI-spans-null).
        reasons: list[str] = []
        if point is not None:
            reasons.append(
                f"measured {metric_path}={point:.6g} "
                + ("(BELOW" if point_fails else "(at/above")
                + f" the pre-registered threshold {threshold:.6g})"
            )
        if ci_null:
            reasons.append(
                f"its 95% CI SPANS the null ({ConstructValidityGate.CORRELATION_NULL:g}) "
                "— statistically indistinguishable from no agreement with the "
                "human/gold anchor"
            )
        detail = "; ".join(reasons)
        warnings.append(
            f"{ConstructValidityGate.GATE_ID}: {plan_dir.name}/"
            f"{_PREREGISTRATION_FILE} declares a construct-validity requirement "
            f"(metric '{metric_path}', threshold {threshold:.6g}) for a "
            f"LOAD-BEARING learned/surrogate axis, but in "
            f"{execute_stage_dir.name}/{_RESULTS_JSON} that validity check "
            f"FAILED: {detail}. The headline rests on a learned construct whose "
            "pre-registered validity check FAILED — the result must be framed as "
            "PRELIMINARY / PENDING-VALIDATION, NOT as an established finding (the "
            "sensitive-memory appropriateness-axis defect, P3). ADVISORY only: "
            "this never blocks progression — but either re-validate the construct to "
            "clear its threshold, or state the validity-fail caveat in the "
            "headline and demote the construct-dependent frontier to a "
            "sensitivity analysis (the pre-registered fallback)."
        )
        return  # One WARN per run is enough; do not spam per-declaration.


def _w20_hypothesis_is_supported(results: Any, hid: str) -> bool:
    """STRUCTURAL read of whether the run frames hypothesis `hid` as
    SUPPORTED (resolved in its favour) — never by parsing prose (GP8).

    Looks up a structured per-hypothesis verdict in either canonical place:
      * results.json["hypotheses"] — a list of {id, status, ...} entries
        (the same id space the prereg uses); OR
      * results.json["hypothesis_status"] — a dict {id: status}.
    Returns True only when the resolved status equals `supported`. Absent /
    malformed / any other status → False (framing UNKNOWN → the caller WARNs
    rather than ERRORs). Back-compat: no existing run carries either block,
    so this returns False for the whole finalized corpus → W20 never ERRORs
    on them.
    """
    if not isinstance(results, dict):
        return False
    block = results.get(_W20_HYP_STATUS_BLOCK)
    if isinstance(block, dict):
        status = block.get(hid)
        if isinstance(status, str) and status.strip().lower() == _W20_STATUS_SUPPORTED:
            return True
    hyps = results.get(_W20_HYPOTHESES_FIELD)
    if isinstance(hyps, list):
        for entry in hyps:
            if not isinstance(entry, dict):
                continue
            if str(entry.get(_W20_HYPOTHESIS_ID_KEY)) != str(hid):
                continue
            status = entry.get(_W20_STATUS_KEY)
            if isinstance(status, str) and status.strip().lower() == _W20_STATUS_SUPPORTED:
                return True
    return False


def _check_w20_decision_rule_honored(
    execute_stage_dir: pathlib.Path,
    results: dict,
    errors: list[str],
    warnings: list[str],
) -> None:
    """W20 — decision-rule-HONORED gate (a W17-sibling; the one honesty
    class W13–W18 don't touch).

    W9 verifies every hypothesis has a `decision_rule` (free PROSE) PRESENT;
    it NEVER checks the rule was HONORED. W20 closes that gap MECHANICALLY,
    never by parsing the prose rule (Golden Principle 8). PLAN emits an
    OPTIONAL machine-checkable twin per hypothesis —
    `decision_rule_structured: {metric, op, threshold, ci_excludes?}` —
    and W20 reads the named `metric` (a dotted key path) out of results.json,
    evaluates `op` vs `threshold` (+ the `ci_excludes` clause if set), and:

      * silent PASS — rule present, well-formed, metric resolvable, HONORED;
      * ERROR — rule present + well-formed + metric resolvable + VIOLATED
        AND the hypothesis is framed SUPPORTED via the STRUCTURAL
        per-hypothesis status (results.json["hypotheses"][i]["status"]=="
        supported" or results.json["hypothesis_status"][id]=="supported").
        This is the wm-cross-model-transfer H3 mode: a hypothesis asserted
        resolved in its favour while its own pre-registered quantitative
        rule is mathematically violated → progression blocked;
      * WARN — rule VIOLATED but NO structured supported status (framing
        unknown / honest null): a violated prereg rule is surfaced but does
        NOT block (an honest "rule not met → not supported" is fine);
      * WARN — malformed structured rule, metric-key not found, or an
        unresolvable ci_95 for a ci_excludes clause (a missing key is NOT a
        violation — conservative; when in doubt, WARN not ERROR).

    "Framed resolved" is read STRUCTURALLY (a per-hypothesis `status`
    verdict), NEVER by scraping prose — mirroring W17's
    `_w17_resolution_framing`. The choice: results.json carries no
    per-hypothesis verdict today, so without a structural signal we cannot
    prove a hypothesis was framed supported; rather than infer framing from
    prose (brittle, GP8-violating), W20 ERRORs ONLY when the new structured
    `status` says supported, and otherwise WARNs on a violation. This keeps
    the ERROR conservative (it fires exactly on the declared-supported-yet-
    violated over-claim) while still surfacing every violated rule.

    Backward compatibility (NON-NEGOTIABLE — predates the field): the
    `decision_rule_structured` field is OPTIONAL. ABSENT (every existing /
    in-flight run) → W20 silently passes. Degrades silently on no plan
    sibling, no preregistration, unparseable JSON, no hypotheses list, a
    hypothesis with no structured rule, or no results.json. It NEVER errors
    on a run that lacks the new field — the W17 declare-then-verify
    pattern exactly.
    """
    if not isinstance(results, dict):
        return
    plan_dir = _find_latest_plan_sibling(execute_stage_dir)
    if plan_dir is None:
        return
    raw_hyps = _read_preregistration_field(plan_dir, _W20_HYPOTHESES_FIELD)
    if raw_hyps is _RESOLVE_MISSING or not isinstance(raw_hyps, list):
        return  # No hypotheses block → nothing to honor-check (back-compat).

    for h in raw_hyps:
        if not isinstance(h, dict):
            continue
        rule = h.get(_W20_STRUCTURED_RULE_KEY)
        if rule is None:
            continue  # No structured twin → silent pass (back-compat).
        hid = str(h.get(_W20_HYPOTHESIS_ID_KEY) or "<unnamed>")
        if not isinstance(rule, dict):
            warnings.append(
                f"W20: hypothesis {hid} `{_W20_STRUCTURED_RULE_KEY}` in "
                f"{plan_dir.name}/{_PREREGISTRATION_FILE} is not an object — "
                "cannot honor-check. ADVISORY: emit "
                "{metric, op, threshold, ci_excludes?}."
            )
            continue
        metric_path = rule.get(_W20_METRIC_KEY)
        op = rule.get(_W20_OP_KEY)
        threshold = _w20_coerce_number(rule.get(_W20_THRESHOLD_KEY))
        if (
            not isinstance(metric_path, str)
            or not metric_path.strip()
            or not isinstance(op, str)
            or op not in _W20_LEGAL_OPS
            or threshold is None
        ):
            warnings.append(
                f"W20: hypothesis {hid} `{_W20_STRUCTURED_RULE_KEY}` is "
                f"malformed (need `{_W20_METRIC_KEY}` str, `{_W20_OP_KEY}` in "
                f"{sorted(_W20_LEGAL_OPS)}, numeric `{_W20_THRESHOLD_KEY}`) — "
                "cannot honor-check. ADVISORY: never blocks progression."
            )
            continue

        observed_raw = _w20_resolve_metric_path(results, metric_path)
        if observed_raw is _RESOLVE_MISSING:
            warnings.append(
                f"W20: hypothesis {hid} structured rule names metric "
                f"`{metric_path}` not found in {_RESULTS_JSON} — cannot "
                "honor-check (a missing key is not a violation). ADVISORY: "
                "verify the metric path matches the results schema."
            )
            continue
        observed = _w20_coerce_number(observed_raw)
        if observed is None:
            warnings.append(
                f"W20: hypothesis {hid} metric `{metric_path}` in "
                f"{_RESULTS_JSON} is not a finite number ({observed_raw!r}) — "
                "cannot honor-check. ADVISORY: never blocks progression."
            )
            continue

        op_holds = _W20_OP_EVALUATORS[op](observed, threshold)
        # ci_excludes is OPTIONAL. When set, it ANDs with the op clause: the
        # rule is honored only if BOTH the op holds AND the ci excludes the
        # named value. An unresolvable ci is a WARN (cannot prove violation).
        ci_target = rule.get(_W20_CI_EXCLUDES_KEY)
        ci_clause_ok = True
        ci_note = ""
        if ci_target is not None:
            ci_target_f = _w20_coerce_number(ci_target)
            if ci_target_f is None:
                warnings.append(
                    f"W20: hypothesis {hid} `{_W20_CI_EXCLUDES_KEY}`="
                    f"{ci_target!r} is not numeric — ignoring the CI clause, "
                    "checked op-vs-threshold only. ADVISORY."
                )
            else:
                excludes = _w20_ci_excludes(results, metric_path, ci_target_f)
                if excludes is None:
                    warnings.append(
                        f"W20: hypothesis {hid} structured rule sets "
                        f"`{_W20_CI_EXCLUDES_KEY}`={ci_target_f} but no usable "
                        f"`{_W20_CI95_KEY}` sibling of `{metric_path}` exists "
                        f"in {_RESULTS_JSON} — checked op-vs-threshold only "
                        "(cannot prove the CI clause). ADVISORY."
                    )
                else:
                    ci_clause_ok = excludes
                    ci_note = (
                        f"; {_W20_CI95_KEY} of `{metric_path}` "
                        f"{'excludes' if excludes else 'INCLUDES'} "
                        f"{ci_target_f}"
                    )

        if op_holds and ci_clause_ok:
            continue  # The structured rule is HONORED → silent pass.

        clause = f"`{metric_path}` ({observed}) {op} {threshold}"
        verdict = (
            "op holds but CI clause violated"
            if op_holds
            else f"observed {observed} does NOT satisfy {op} {threshold}"
        )
        if _w20_hypothesis_is_supported(results, hid):
            errors.append(
                f"W20: hypothesis {hid} is framed SUPPORTED "
                f"({_RESULTS_JSON} per-hypothesis status=\""
                f"{_W20_STATUS_SUPPORTED}\") but its pre-registered structured "
                f"decision rule {clause}{ci_note} is VIOLATED ({verdict}) — "
                "W20 (decision-rule-honored gate) blocks progression to REVIEW: the run "
                "asserts the hypothesis resolved in its favour while its own "
                f"{plan_dir.name}/{_PREREGISTRATION_FILE} quantitative rule "
                "is not met. Either re-run/re-measure so the rule is honored, "
                "or set the hypothesis status to refuted/inconclusive and "
                "reframe the claim honestly (do NOT silently reframe to a "
                "different quantity — the wm-cross-model-transfer H3 mode)."
            )
        else:
            warnings.append(
                f"W20: hypothesis {hid} pre-registered structured decision "
                f"rule {clause}{ci_note} is VIOLATED ({verdict}). The "
                f"hypothesis is NOT framed supported in {_RESULTS_JSON} "
                "(honest null is fine), so this is ADVISORY only — but verify "
                "the results do not claim it resolved. Set the per-hypothesis "
                f"status to \"{_W20_STATUS_SUPPORTED}\" only if the rule holds."
            )


def _w21_decision_rule_structured_present(prereg: Any) -> bool:
    """True iff the prereg declares a `decision_rule_structured` on ≥1
    hypothesis.

    Unlike load_bearing_arm / discriminating_arm / key_prior_art (top-level
    prereg fields), the W20 structured decision rule lives PER HYPOTHESIS
    inside `hypotheses[]` (see plan.md schema + `_check_w20_*`). W21 counts
    the field present when ANY hypothesis carries a non-null
    `decision_rule_structured` — mirroring exactly where W20 reads it.
    """
    if not isinstance(prereg, dict):
        return False
    hyps = prereg.get(PreregField.HYPOTHESES)
    if not isinstance(hyps, list):
        return False
    return any(
        isinstance(h, dict) and h.get(PreregField.DECISION_RULE_STRUCTURED) is not None
        for h in hyps
    )


def _w21_missing_prereg_fields(prereg: Any) -> list[str]:
    """The subset of `PreregField.ALL` the plan does NOT declare, in gate
    order (W13, W17, W20).

    Presence test per field:
      * load_bearing_arm / discriminating_arm / key_prior_art — a top-level
        key whose value is present and non-empty (an empty string / empty
        list reads as "not declared", same as the per-gate `_w13_named_arm_keys`
        empty-value WARN paths);
      * decision_rule_structured — present on ≥1 hypothesis (per-hypothesis,
        via `_w21_decision_rule_structured_present`).
    """
    if not isinstance(prereg, dict):
        return list(PreregField.ALL)
    missing: list[str] = []
    for field in PreregField.ALL:
        if field == PreregField.DECISION_RULE_STRUCTURED:
            if not _w21_decision_rule_structured_present(prereg):
                missing.append(field)
            continue
        value = prereg.get(field)
        # Absent, None, or empty (str/list/dict) → not declared.
        if value is None or (
            isinstance(value, (str, list, dict, tuple)) and len(value) == 0
        ):
            missing.append(field)
    return missing


def _check_w21_prereg_completeness(
    execute_stage_dir: pathlib.Path,
    warnings: list[str],
) -> None:
    """W21 — prereg-completeness gate (ADVISORY surfacing layer, WARN-only).

    W13/W17/W20 are each individually back-compatible: each SILENTLY
    no-ops when its pre-registration field is absent. The PLAN phase
    under-emits these fields, so the suite passes VACUOUSLY on most runs —
    the structural root cause of runs shipping their decisive experiment as
    "future work" (audit finding S1; corpus coverage was load_bearing_arm
    17/38, discriminating_arm 14/38, key_prior_art 12/38,
    decision_rule_structured 5/38). W21 surfaces that vacuous pass WITHOUT
    changing any of W13/W17/W20 or their silent-no-op behaviour.

    CONFIRMATORY-GATED, the SAME way W13/W17/W20 gate themselves: a run is
    confirmatory iff it emitted a `<plan>/preregistration.json` (the exact
    signal every one of those gates keys on — they early-return when
    `_find_latest_plan_sibling` finds no plan or the prereg file is absent).
    Exploratory / descriptive runs emit no preregistration.json, so W21 never
    fires on them.

    Severity: WARN only (advisory — the W17 back-compat philosophy). It
    names WHICH of the four `PreregField.ALL` declarations are missing and is
    a no-op (silent PASS) when all four are present. It NEVER ERRORs and never
    blocks the pipeline.
    """
    plan_dir = _find_latest_plan_sibling(execute_stage_dir)
    if plan_dir is None:
        return  # No plan sibling → not a confirmatory run we can audit.
    pre_path = plan_dir / _PREREGISTRATION_FILE
    if not pre_path.exists():
        return  # No preregistration.json → exploratory/descriptive → no fire.
    try:
        prereg = json.loads(pre_path.read_text())
    except (OSError, json.JSONDecodeError):
        return  # Unparseable prereg is W1/W-parser territory, not W21.
    if not isinstance(prereg, dict):
        return

    missing = _w21_missing_prereg_fields(prereg)
    if not missing:
        return  # All four confirmatory declarations present → silent PASS.

    joined = ", ".join(missing)
    warnings.append(
        f"{PreregCompletenessGate.GATE_ID}: {plan_dir.name}/"
        f"{_PREREGISTRATION_FILE} is missing {len(missing)} of the "
        f"{len(PreregField.ALL)} confirmatory pre-registration declaration(s): "
        f"{joined}. These are the fields W13 (load_bearing_arm), W17 "
        "(discriminating_arm) and W20 (decision_rule_structured) each "
        "SILENTLY pass on when absent — so an incomplete prereg passes the "
        "confirmatory gate suite VACUOUSLY (audit S1: the recurring cause of a "
        "run deferring its decisive experiment as 'future work'). ADVISORY "
        "only: this never blocks — but PLAN should declare all of them so the "
        "deciding experiment is scheduled and gated, not deferred."
    )


def _w18_is_theory_contribution(plan_dir: pathlib.Path) -> bool:
    """True iff the plan declares `contribution_type: theory` structurally.

    Reads `preregistration.json.contribution_type` (the canonical plan
    declaration — same file/normaliser W13/W17 use via
    `_read_preregistration_field`). Case-insensitive, whitespace-tolerant.
    Absent / non-theory value → False → W18 no-ops (backward compatible).
    No prose parsing.
    """
    raw = _read_preregistration_field(plan_dir, _W18_CONTRIBUTION_TYPE_FIELD)
    if not isinstance(raw, str):
        return False
    return raw.strip().lower() == _W18_THEORY_VALUE


def _w18_named_headline_theorems(plan_dir: pathlib.Path) -> set[str]:
    """The HEADLINE/load-bearing Lean theorem name(s) the plan declares.

    Reads `preregistration.json.load_bearing_theorem` (the theory analogue
    of W13's load_bearing_arm). Accepts a single string or a list of
    strings; returns the set of non-blank names. Empty set when the field
    is absent/blank → W18 falls back to the per-verdict `claim` field to
    tell headline-vs-supporting (see `_w18_verdict_is_headline`).
    """
    raw = _read_preregistration_field(plan_dir, _W18_LOAD_BEARING_THEOREM_FIELD)
    if isinstance(raw, str):
        candidates = [raw]
    elif isinstance(raw, list):
        candidates = [c for c in raw if isinstance(c, str)]
    else:
        return set()
    return {c.strip() for c in candidates if c.strip()}


def _w18_read_lean_verdicts(
    execute_stage_dir: pathlib.Path,
) -> list[tuple[str, dict]]:
    """Load every `<stage>/lean/*<LEAN_VERDICT_SUFFIX>` as structured JSON.

    Canonical reader for the lean artifacts cross_check consumes — returns
    `(filename, parsed_dict)` pairs, skipping unreadable / non-dict / non-
    verdict files. No regex, no prose parsing: the verdict JSON is written
    by `LeanVerdict.to_dict` (+ the math-formalization skill's `claim`/
    `contribution_role` extras). Silent empty list when the lean subdir is
    absent (a non-formalized run).
    """
    lean_dir = execute_stage_dir / _W18_LEAN_SUBDIR
    if not lean_dir.is_dir():
        return []
    out: list[tuple[str, dict]] = []
    try:
        children = sorted(lean_dir.iterdir())
    except OSError:
        return []
    for child in children:
        if not child.is_file() or not child.name.endswith(_LEAN_VERDICT_SUFFIX):
            continue
        try:
            data = json.loads(child.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out.append((child.name, data))
    return out


def _w18_verdict_substance_warnings(verdict: dict) -> list[str]:
    """The non-empty substance_warning strings stored on a verdict, or [].

    Tolerant of the schema variants seen on disk: the canonical
    `LeanVerdict.to_dict` always emits `substance_warnings: [...]`, but an
    older agent-written verdict may omit the key entirely (treated as no
    warnings). A non-list value is ignored.
    """
    raw = verdict.get(_W18_SUBSTANCE_KEY)
    if not isinstance(raw, list):
        return []
    return [w for w in raw if isinstance(w, str) and w.strip()]


def _w18_verdict_is_headline(verdict: dict, named: set[str]) -> bool:
    """True iff this verdict backs the run's HEADLINE theorem.

    Two structural signals (no prose parsing):
      * if PLAN declared `load_bearing_theorem` name(s) (`named`), the
        verdict is headline iff any named theorem is in its
        `theorems_checked` OR equals its `claim` field; ELSE
      * if PLAN named none, fall back to the math-formalization skill's
        per-verdict `claim` field — the verdict is headline iff its `claim`
        names one of the theorems it checked (the skill sets `claim` to the
        headline theorem name). When `claim` is absent too, conservatively
        treat the verdict as headline (so an ungated elementary theorem
        ERRORs rather than slipping through — mirrors W17's conservative
        default).
    """
    checked = verdict.get(_W18_THEOREMS_KEY)
    checked_set = {t for t in checked if isinstance(t, str)} \
        if isinstance(checked, list) else set()
    claim = verdict.get(_W18_CLAIM_KEY)
    claim = claim.strip() if isinstance(claim, str) else ""
    if named:
        if named & checked_set:
            return True
        if claim and claim in named:
            return True
        return False
    # No PLAN declaration — use the per-verdict claim field.
    if claim:
        # Headline iff the claimed theorem is one this verdict checked
        # (a verdict whose claim names a theorem it did NOT prove is a
        # supporting-file edge case — treat as non-headline).
        return (not checked_set) or (claim in checked_set)
    # Neither declared nor claimed → conservative: treat as headline.
    return True


def _w18_verdict_is_demoted(verdict: dict) -> bool:
    """True iff the run HONESTLY DEMOTES this theorem to a labeled
    conjecture / formalized observation (the honest path).

    Reads the canonical `contribution_role` field. Absent / first-class
    `theorem` → NOT demoted (conservative default → ERROR-eligible). A
    value in `_W18_DEMOTED_ROLES` → demoted → W18 downgrades ERROR→WARN.
    Case-insensitive, whitespace-tolerant. No prose parsing.
    """
    raw = verdict.get(_W18_CONTRIBUTION_ROLE_FIELD)
    if not isinstance(raw, str):
        return False
    return raw.strip().lower() in _W18_DEMOTED_ROLES


def _w18_layer_label(warnings: list[str]) -> str:
    """A short human label for which detection layer(s) fired, read from
    the stored warning text (the canonical M3b markers the substance
    heuristics emit). Purely cosmetic for the finding message — the
    severity decision never depends on this string."""
    joined = " ".join(warnings).lower()
    elementary = "elementary-theorem" in joined
    trivial = "assume-the-" in joined or "monotonicity" in joined
    rearrangement = "linear-rearrangement" in joined
    parts = []
    if elementary:
        parts.append("elementary-theorem")
    if trivial:
        parts.append("trivial-pattern")
    if rearrangement:
        parts.append("linear-rearrangement")
    if parts:
        return " + ".join(parts)
    return "proof-substance"


def _check_w18_proof_substance_enforced(
    execute_stage_dir: pathlib.Path,
    errors: list[str],
    warnings: list[str],
) -> None:
    """W18 — proof-substance / theorem-depth enforcement gate.

    The ENFORCEMENT layer over `omicairn.lean_verify.substance_warnings` (the
    M3b trivial-pattern + elementary-theorem detection). W18 does NOT
    re-detect; it READS the stored `substance_warnings` from each
    `<execute_stage>/lean/*.verdict.json` (written by `LeanVerdict.to_dict`)
    and makes that detection ENFORCEABLE — a structural cross-check finding
    the orchestrator routes on, not advisory verdict data only a voluntary
    REVIEW prompt-anchor read.

    Theory-gated (mirrors W13/W17): fires ONLY when PLAN declares
    `contribution_type: theory` in preregistration.json. For a theory
    run, for each lean verdict carrying a non-empty substance_warning:
      * ERROR — the flagged theorem is the run's HEADLINE/load-bearing
        theorem (via PLAN's `load_bearing_theorem` or the per-verdict
        `claim` fallback) AND it is framed as a first-class `theorem`
        contribution (not demoted). The XOR-table-ships-ungated failure:
        a run may not ship an elementary/trivial theorem AS its theorem
        contribution. REVIEW routes a `scoped` re-headline (re-headline / demote to
        a labeled conjecture or formalized observation) or `retry_execute`
        (aim the headline at a SUBSTANTIVE theorem).
      * WARN — the theorem is SUPPORTING (non-headline), OR the headline
        theorem has been HONESTLY DEMOTED (`contribution_role` in
        `_W18_DEMOTED_ROLES`): the honest path rules/method_depth.md
        prescribes; advisory only.

    Backward compatible — silent no-op when: no plan sibling; not a theory
    run; no lean subdir / no verdict; or no verdict carries a
    substance_warning. Reuses W13/W17's plan-sibling + preregistration
    readers and the SAME canonical constants — no parallel mechanism, no
    re-detection, no regex over proof prose.
    """
    plan_dir = _find_latest_plan_sibling(execute_stage_dir)
    if plan_dir is None:
        return
    if not _w18_is_theory_contribution(plan_dir):
        return  # Non-theory run → W18 no-op.
    verdicts = _w18_read_lean_verdicts(execute_stage_dir)
    if not verdicts:
        return  # No lean artifacts → nothing to enforce.
    named = _w18_named_headline_theorems(plan_dir)
    for fname, verdict in verdicts:
        subs = _w18_verdict_substance_warnings(verdict)
        if not subs:
            continue  # Clean / substantive theorem (d5/d7 style) → pass.
        is_headline = _w18_verdict_is_headline(verdict, named)
        demoted = _w18_verdict_is_demoted(verdict)
        layer = _w18_layer_label(subs)
        if is_headline and not demoted:
            errors.append(
                f"{fname}: a load-bearing Lean theorem carries a "
                f"{layer} proof-substance warning "
                f"(omicairn.lean_verify.substance_warnings) yet is framed as a "
                f"first-class `theorem` contribution — W18 (proof-substance "
                "enforcement gate) blocks progression to REVIEW: a THEORY-contribution run "
                "may not ship an elementary/trivial theorem AS its headline "
                "theorem (the XOR-table-ships-ungated failure mode). The "
                "stored M3b hint: "
                f"\"{subs[0][:200]}\". Either re-aim the headline at a "
                "SUBSTANTIVE theorem that derives a non-obvious consequence "
                "(a constructed witness whose truth is not visible from the "
                "statement, a bound that constrains the claim) via REVIEW "
                "retry_execute, or HONESTLY DEMOTE it to a labeled "
                f"conjecture / formalized observation (set "
                f"`{_W18_CONTRIBUTION_ROLE_FIELD}` to one of "
                f"{sorted(_W18_DEMOTED_ROLES)} on the verdict) by recording the narrowed "
                "(`scoped`) framing in results.json. This gate is the enforceable companion to the "
                "REVIEW proof-theater anchor — it no longer relies on "
                "the review agent voluntarily reading the warning."
            )
        else:
            why = (
                "honestly demoted to a labeled conjecture / formalized "
                "observation" if demoted else "a SUPPORTING (non-headline) "
                "theorem"
            )
            warnings.append(
                f"W18 (advisory): {fname} carries a {layer} proof-substance "
                f"warning but is {why} — ADVISORY only: the headline does "
                "not rest on it. REVIEW should confirm the run does not "
                "present this theorem as a first-class deep result. The "
                f"stored M3b hint: \"{subs[0][:200]}\"."
            )


def _check_figures_contract(
    execute_stage_dir: pathlib.Path,
    warnings: list[str],
) -> None:
    """Surface PLAN's `figures_contract.json` vs produced artifacts as
    informational warnings — **not** errors.

    Rationale: the figure contract is a *reference*, not a hard gate.
    A missing figure might be a legitimate scientific limitation
    (method failed, data absent, infeasible within budget) that the
    run should disclose honestly. Conversely it might be a
    producible-but-skipped figure that genuinely warrants a retry.
    That judgement belongs to the REVIEW phase, which has the
    analysis in hand and can tell whether analysis.md's Limitations section
    covers each gap.

    This check therefore only reports. It walks every figure whose
    ``required`` field is missing or true, emits one warning line per
    missing ``output_paths`` / ``results_tables`` entry, and never
    appends to ``errors`` — so cross_check's verdict is not flipped
    to ``retry_execute`` by a contract gap alone. REVIEW reads these
    warnings (and the contract itself) and decides whether the gap
    is disclosed, fixable, or both.

    Silent-skip when the contract file is missing (older plans or
    minimal fixtures). Malformed JSON is a warning so a typo in the
    plan doesn't mask more fundamental execute errors.
    """
    plan_dir = _find_latest_plan_sibling(execute_stage_dir)
    if plan_dir is None:
        return
    contract_path = plan_dir / _FIGURES_CONTRACT_JSON
    if not contract_path.exists():
        return
    try:
        contract = json.loads(contract_path.read_text())
    except json.JSONDecodeError as e:
        warnings.append(
            f"{_FIGURES_CONTRACT_JSON} does not parse: {e} — "
            "figure-coverage check skipped. Fix the JSON in PLAN."
        )
        return
    if not isinstance(contract, dict):
        return
    figures = contract.get("figures")
    if not isinstance(figures, list):
        return
    for entry in figures:
        if not isinstance(entry, dict):
            continue
        if not entry.get("required", True):
            continue
        fig_id = entry.get("id", "?")
        for rel in entry.get("output_paths", []) or []:
            if not isinstance(rel, str):
                continue
            target = execute_stage_dir / rel
            if not target.exists():
                warnings.append(
                    f"figures_contract gap: {fig_id} planned "
                    f"output '{rel}' not present under the execute "
                    "stage. REVIEW must judge whether the run "
                    "honestly discloses this or whether it is a "
                    "producible-but-skipped figure worth a retry."
                )
        for rel in entry.get("results_tables", []) or []:
            if not isinstance(rel, str):
                continue
            target = execute_stage_dir / rel
            if not target.exists():
                warnings.append(
                    f"figures_contract gap: {fig_id} planned "
                    f"results table '{rel}' not present. REVIEW "
                    "must judge whether this gap is disclosed or "
                    "worth a retry."
                )


def _check_env_lockfile_coverage(
    execute_stage_dir: pathlib.Path,
    warnings: list[str],
) -> None:
    """Tier 1.3 — verify ``code/environment.lock.json`` covers every
    Python module imported by ``code/*.py``.

    A missing lockfile is a WARNING (older runs may not have one yet).
    A lockfile that is missing a clearly-imported module is also a
    WARNING — methods sections often cite library versions verbatim
    so a missing entry is awkward but not a fabrication.
    Promote to ERROR once the lockfile becomes a hard contract.

    Silent-skip when ``code/`` does not exist.
    """
    code_dir = execute_stage_dir / _CODE_SUBDIR
    if not code_dir.exists():
        return
    lock_path = code_dir / "environment.lock.json"
    if not lock_path.exists():
        warnings.append(
            "code/environment.lock.json missing — EXECUTE should emit "
            "an environment snapshot via omicairn.env_capture so Methods "
            "can cite the actual environment."
        )
        return
    try:
        from . import env_capture
    except ImportError:
        return
    lock = env_capture.load_lockfile(lock_path)
    if lock is None:
        warnings.append(
            "code/environment.lock.json is unparseable — re-run env_capture."
        )
        return
    declared = set(lock.imported_modules)
    discovered = env_capture.imports_from_code_dir(code_dir)
    missing_in_record = discovered - declared
    if missing_in_record:
        warnings.append(
            "code/environment.lock.json imported_modules misses "
            f"{sorted(missing_in_record)} — re-run env_capture so the "
            "lockfile reflects the actual import set."
        )
    _, missing_in_pkgs = env_capture.imports_present_in_lockfile(
        discovered, lock,
    )
    if missing_in_pkgs:
        warnings.append(
            "code/environment.lock.json packages missing distributions "
            f"for imports {sorted(missing_in_pkgs)} — Methods cannot "
            "cite versions for these. Either install them in the env "
            "before EXECUTE re-runs or document the gap in Limitations."
        )


def _check_preregistration_drift(
    execute_stage_dir: pathlib.Path,
    results: dict,
    errors: list[str],
) -> None:
    """Tier 1.5 — verify ``results.json.preregistration_sha256`` matches
    a sha256 of the latest preceding ``preregistration.json``.

    The agent might silently edit the preregistration between EXECUTE
    iterations (e.g., to relax a decision rule that just failed).
    Recomputing the hash and asserting equality is the moral
    equivalent of git-signing the preregistration.

    Silent-skip when:
    - no plan sibling exists (minimal fixtures)
    - no ``preregistration.json`` in the plan dir
    - no ``preregistration_sha256`` field in results.json
    """
    declared = None
    if isinstance(results, dict):
        declared = results.get("preregistration_sha256")
    if not isinstance(declared, str) or not declared:
        return
    plan_dir = _find_latest_plan_sibling(execute_stage_dir)
    if plan_dir is None:
        return
    prereg_path = plan_dir / _PREREGISTRATION_FILE
    if not prereg_path.exists():
        return
    try:
        body = prereg_path.read_bytes()
    except OSError:
        return
    import hashlib
    actual = hashlib.sha256(body).hexdigest()
    if actual.lower() != declared.lower():
        errors.append(
            f"preregistration_sha256 in {_RESULTS_JSON} ({declared[:12]}…) "
            f"does not match recomputed sha256 of "
            f"{plan_dir.name}/preregistration.json ({actual[:12]}…). "
            "The pre-registration was edited after lockdown, OR the "
            "results.json hash is stale. Either way the chain of "
            "evidence is broken — HALT and audit the diff."
        )


def _check_w9_hypothesis_dag_and_fallbacks(
    execute_stage_dir: pathlib.Path,
    errors: list[str],
    warnings: list[str],
) -> None:
    """W9 — every hypothesis declared in plan/preregistration.json must:

    - have a `decision_rule` (string)
    - have `depends_on` (list)
    - if any `dataset:<slug>` dep is blocked according to the workspace
      ``asset_probe.json``, MUST declare a non-trivial
      ``fallback_if_blocked`` (or `DEFER_TO_LIMITATIONS`).

    Silent-skip when (a) no plan sibling exists, (b) plan has no
    ``hypotheses`` list, or (c) ``asset_probe.json`` is missing/unparseable.
    A malformed ``hypotheses`` block (entry without ``id``) is a WARNING
    (so a typo in PLAN doesn't mask all later W-checks), not an ERROR.
    """
    plan_dir = _find_latest_plan_sibling(execute_stage_dir)
    if plan_dir is None:
        return
    prereg_path = plan_dir / _PREREGISTRATION_FILE
    if not prereg_path.exists():
        return
    try:
        prereg = json.loads(prereg_path.read_text())
    except json.JSONDecodeError as e:
        warnings.append(
            f"{plan_dir.name}/preregistration.json does not parse: {e} — "
            "W9 hypothesis-DAG audit skipped."
        )
        return
    if not isinstance(prereg, dict):
        return
    hypotheses = prereg.get("hypotheses")
    if not isinstance(hypotheses, list) or not hypotheses:
        return

    # Build the blocked-asset set from asset_probe.json (workspace-level).
    workspace_root = execute_stage_dir.parent
    probe_path = workspace_root / "asset_probe.json"
    blocked_slugs: set[str] = set()
    if probe_path.exists():
        try:
            probe = json.loads(probe_path.read_text())
            from .asset_probe import ProbeStatus
            for entry in probe.get("assets", []) or []:
                if entry.get("status") in ProbeStatus.BLOCKED:
                    slug = (entry.get("accession") or "").strip()
                    if slug:
                        blocked_slugs.add(slug.lower())
        except json.JSONDecodeError:
            pass

    for h in hypotheses:
        if not isinstance(h, dict):
            warnings.append(
                "W9: malformed hypothesis entry in preregistration.json "
                "(not an object) — fix in PLAN."
            )
            continue
        hid = h.get("id") or "<unnamed>"
        if not h.get("decision_rule"):
            errors.append(
                f"W9: hypothesis {hid} is missing `decision_rule` in "
                f"{plan_dir.name}/preregistration.json — every hypothesis "
                "needs a falsifiable rule. See prompts/plan.md § 5."
            )
        deps = h.get("depends_on")
        if not isinstance(deps, list):
            errors.append(
                f"W9: hypothesis {hid} is missing `depends_on` (list of "
                "`<kind>:<slug>` strings) — without it the orchestrator "
                "cannot detect data-blockage cascades. See prompts/plan.md § 5."
            )
            continue
        # Find blocked dependencies.
        blocked_dep_slugs = []
        for dep in deps:
            if not isinstance(dep, str) or ":" not in dep:
                continue
            kind, _, slug = dep.partition(":")
            if kind.strip().lower() != "dataset":
                continue
            slug = slug.strip().lower()
            if slug and slug in blocked_slugs:
                blocked_dep_slugs.append(dep)
        if blocked_dep_slugs:
            fallback = (h.get("fallback_if_blocked") or "").strip()
            if not fallback:
                errors.append(
                    f"W9: hypothesis {hid} has blocked dataset "
                    f"dependencies {blocked_dep_slugs} but no "
                    "`fallback_if_blocked` — declare either a surrogate "
                    "sub-hypothesis or `DEFER_TO_LIMITATIONS`. See "
                    "prompts/plan.md § Hypothesis dependency DAG."
                )
            elif fallback.upper() == "DEFER_TO_LIMITATIONS":
                # Acceptable but worth surfacing so REVIEW notes it.
                warnings.append(
                    f"W9: hypothesis {hid} deferred to Limitations; "
                    "REVIEW must verify that negative_results.md (or the "
                    "analysis Limitations) names this hypothesis explicitly."
                )


def _check_w10_family_correction_declared(
    execute_stage_dir: pathlib.Path,
    errors: list[str],
    warnings: list[str],
) -> None:
    """W10 — when the plan declares ≥2 hypotheses, ``family_correction``
    must be declared on the preregistration.

    Legal values: bonferroni | bh_fdr | none_with_justification |
    per_hypothesis. A value of `none_with_justification` requires a
    non-empty `family_correction_rationale` field (warning if absent).

    Silent-skip when the plan has 0–1 hypotheses (no family to correct).
    """
    plan_dir = _find_latest_plan_sibling(execute_stage_dir)
    if plan_dir is None:
        return
    prereg_path = plan_dir / _PREREGISTRATION_FILE
    if not prereg_path.exists():
        return
    try:
        prereg = json.loads(prereg_path.read_text())
    except json.JSONDecodeError:
        return
    if not isinstance(prereg, dict):
        return
    hypotheses = prereg.get("hypotheses") or []
    if not isinstance(hypotheses, list) or len(hypotheses) < 2:
        return
    fam = (prereg.get("family_correction") or "").strip().lower()
    if not fam:
        errors.append(
            "W10: plan declares ≥2 hypotheses but no "
            "`family_correction` field in preregistration.json. Declare "
            "one of: bonferroni | bh_fdr | none_with_justification | "
            "per_hypothesis. The experimentwise error rate is otherwise "
            "uncontrolled."
        )
        return
    legal = {"bonferroni", "bh_fdr", "none_with_justification", "per_hypothesis"}
    if fam not in legal:
        errors.append(
            f"W10: family_correction = {fam!r} not in {sorted(legal)}. "
            "Use the canonical labels."
        )
        return
    if fam == "none_with_justification":
        rationale = (prereg.get("family_correction_rationale") or "").strip()
        if not rationale:
            warnings.append(
                "W10: family_correction='none_with_justification' but "
                "`family_correction_rationale` is empty — add a one-sentence "
                "rationale (e.g. 'hypotheses are sequentially dependent') "
                "or pick a different correction."
            )


def _check_data_fetch(
    execute_stage_dir: pathlib.Path,
    warnings: list[str],
) -> None:
    """Informational scan of ``data_fetch_log.md``.

    The log is written by EXECUTE whenever it pulls inputs from public
    URLs into the shared dataset cache. This check never fails the
    execute verdict — a failed fetch is a legitimate scientific
    limitation that the run should disclose, not a code-level bug.
    We only surface warnings so REVIEW can cross-walk the log against
    the run's Limitations section.

    Silent-skip when the log is missing (most experiments that rely
    solely on pre-staged local data will not write one). When present,
    flag any line that names an HTTP/URL error, a non-zero
    ``exception=`` token, or the literal word ``FAILED`` (case
    sensitive) so REVIEW can verify the gap is disclosed.
    """
    log_path = execute_stage_dir / _DATA_FETCH_LOG
    if not log_path.exists():
        return
    try:
        text = log_path.read_text()
    except OSError as e:
        warnings.append(
            f"{_DATA_FETCH_LOG} could not be read: {e} — fetch audit skipped."
        )
        return
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lowered = stripped.lower()
        is_failure = (
            "FAILED" in stripped
            or "exception=" in lowered
            or "http_status=4" in lowered
            or "http_status=5" in lowered
        )
        if is_failure:
            warnings.append(
                f"{_DATA_FETCH_LOG}:{lineno} records a failed fetch — "
                "REVIEW must verify analysis.md § Limitations names the "
                "affected input and downstream figure(s)."
            )


def _number_present_in_text(value: float, text: str) -> bool:
    """True if `value` appears in `text` at any reasonable precision.

    Handles two presentation patterns:

    1. ``analysis.md`` rounds to fewer decimals than the canonical 3dp
       (e.g. ``[0.59, 0.71]``) — covered by the literal-substring
       candidate set below at 2dp/3dp.
    2. ``analysis.md`` keeps more decimals than 3dp (e.g.
       ``[-0.1177, +0.0282]`` for a value whose 3dp rounding is
       ``-0.118``) — covered by the numeric scan: we extract every
       decimal number from the text and accept if any of them, when
       rounded to 3dp, equals ``value`` rounded to 3dp.

    Prose and markdown tables routinely use Unicode typographic minus
    (U+2212 "−") and en-dash (U+2013 "–") instead of ASCII hyphen-minus
    for negative numbers — both Pandoc and many scientific writers
    substitute automatically. We normalise *both* sides to an ASCII
    hyphen before matching so a legitimate analysis.md entry like
    ``Δ = −0.060 (95 % CI −0.060 to +0.956)`` is not treated as
    "missing" the value ``-0.060``.
    """
    norm_text = _normalise_minus_signs(text)
    # Path A: literal-substring candidates at 2dp / 3dp.
    candidates = {
        f"{value:.3f}",
        f"{value:.2f}",
        f"{value:+.3f}",
        f"{value:+.2f}",
    }
    for base in list(candidates):
        stripped = base.rstrip("0").rstrip(".") if "." in base else base
        if stripped:
            candidates.add(stripped)
    for cand in candidates:
        pattern = re.escape(_normalise_minus_signs(cand))
        if re.search(pattern, norm_text):
            return True
    # Path B: numeric scan — accept any analysis-side number whose 3dp
    # rounding matches `value`'s 3dp rounding. Handles the case where
    # analysis.md kept more precision than the cross-check expects
    # (e.g. -0.1177 in text vs the 3dp round -0.118 the check is
    # looking for).
    target_3dp = round(value, 3)
    for m in _NUMBER_SCAN_RE.finditer(norm_text):
        try:
            num = float(m.group(0))
        except ValueError:
            continue
        if round(num, 3) == target_3dp:
            return True
    return False


# Match decimal numbers with optional sign and at least one digit on
# each side of the decimal point. Avoids matching JSON numbers that
# are part of larger structures like dates.
_NUMBER_SCAN_RE: re.Pattern[str] = re.compile(r"[+-]?\d+\.\d+")


# Unicode characters that commonly stand in for an ASCII hyphen-minus
# in scientific prose. Pandoc's default "smart" formatting and many
# editors/authors (Word, Zotero, etc.) substitute these automatically.
# We collapse them all to `-` before any number-presence match.
_MINUS_LIKE_CHARS: str = (
    "−"  # MINUS SIGN (U+2212)
    "–"  # EN DASH (U+2013)
    "—"  # EM DASH (U+2014)
    "‐"  # HYPHEN (U+2010)
    "‑"  # NON-BREAKING HYPHEN (U+2011)
    "\uFE63"  # SMALL HYPHEN-MINUS (U+FE63)
    "\uFF0D"  # FULLWIDTH HYPHEN-MINUS (U+FF0D)
)
_MINUS_NORMALISE_TABLE: dict[int, str] = {
    ord(c): "-" for c in _MINUS_LIKE_CHARS
}


def _normalise_minus_signs(text: str) -> str:
    """Collapse Unicode minus-like characters to ASCII `-`.

    This is a display-layer normaliser only — do NOT apply it to
    numerical parsing targets (YAML keys, JSON, code). It is safe for
    narrative markdown / analysis prose.
    """
    return text.translate(_MINUS_NORMALISE_TABLE)


# Subsampling-leak guard --------------------------------------------------
#
# Subsampling/downsampling the data is permitted ONLY for smoke/dev/quick-
# iteration testing. The FINAL analysis a run reports — and ESPECIALLY
# any method benchmark — must run on the FULL dataset; a subsample biases
# method comparisons. This guard is deliberately SOFT (warning-only, never
# an error): it scans the execute stage's code/, results.json, and
# analysis.md for explicit subsampling signals and surfaces a WARNING for
# REVIEW to scrutinise. It NEVER fabricates a full-N — it only warns on
# explicit signals, and it suppresses the warning when the same artifacts
# carry an explicit final-data / disclosure marker.

# Function-call style subsampling that lands in the analysis pipeline.
# Matched against code/*.py source text. `random_state`/seed args are
# fine to appear nearby; we key off the call name itself.
_SUBSAMPLE_CODE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsc\.pp\.subsample\b"),          # scanpy
    re.compile(r"\bpp\.subsample\b"),              # scanpy aliased import
    re.compile(r"\.subsample\s*\("),               # generic .subsample(
    re.compile(r"\bsubsample\s*\("),               # bare subsample(
    re.compile(r"\bdownsample\s*\("),              # downsample(
    re.compile(r"\.sample\s*\(\s*n\s*="),          # pandas df.sample(n=...)
    re.compile(r"\.sample\s*\(\s*frac\s*="),       # pandas df.sample(frac=...)
)
# Prose signals in results.json / analysis.md. Lower-cased before match.
_SUBSAMPLE_PROSE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"subsampl"),       # subsample / subsampled / subsampling
    re.compile(r"downsampl"),      # downsample / downsampled / downsampling
    re.compile(r"subset to \d"),   # "subset to 5000 cells"
    re.compile(r"randomly select(?:ed)? \d"),  # "randomly selected 10000"
)
# Markers that say "this is a deliberately-disclosed limitation / smoke
# run / full-data final" — when present we trust the agent followed the
# rule and DON'T also nag. Keeps the guard from firing on a run that
# already discloses the subsample prominently.
_SUBSAMPLE_OK_MARKERS: tuple[str, ...] = (
    "full dataset",
    "full data",
    "no subsampling",
    "smoke test",
    "smoke run",
    "dev iteration",
    "quick iteration",
    "resource_request",
)


def _check_subsampling(
    execute_stage_dir: pathlib.Path,
    warnings: list[str],
) -> None:
    """SOFT (warning-only) detector for subsampling that may have leaked
    into the FINAL/reported analysis.

    Scans ``code/*.py`` for subsampling call patterns and
    ``results.json`` / ``analysis.md`` prose for subsample/downsample
    language. On any hit, appends ONE warning telling REVIEW to verify
    the final/benchmark numbers ran on the full dataset (and that any
    subsample is prominently disclosed). Never errors; never fabricates
    a full-N. Suppressed when the stage already carries an explicit
    full-data / smoke / RESOURCE_REQUEST disclosure marker, since the
    agent then followed the rule and a warning would be noise.
    """
    hits: list[str] = []

    code_dir = execute_stage_dir / _CODE_SUBDIR
    if code_dir.exists():
        for py in sorted(code_dir.rglob("*.py")):
            try:
                src = py.read_text()
            except OSError:
                continue
            for pat in _SUBSAMPLE_CODE_PATTERNS:
                if pat.search(src):
                    rel = py.relative_to(execute_stage_dir)
                    hits.append(f"code `{rel}` calls `{pat.pattern}`")
                    break  # one hit per file is enough

    disclosure_present = False
    for fname in (_RESULTS_JSON, _ANALYSIS_MD):
        path = execute_stage_dir / fname
        if not path.exists():
            continue
        try:
            text = path.read_text()
        except OSError:
            continue
        lowered = text.lower()
        if any(marker in lowered for marker in _SUBSAMPLE_OK_MARKERS):
            disclosure_present = True
        for pat in _SUBSAMPLE_PROSE_PATTERNS:
            if pat.search(lowered):
                hits.append(f"{fname} mentions subsampling/downsampling")
                break

    if not hits or disclosure_present:
        return

    warnings.append(
        "possible subsampling in the EXECUTE stage ("
        + "; ".join(sorted(set(hits)))
        + "). Subsampling is valid ONLY for smoke/dev/quick-iteration "
        "runs — the FINAL reported analysis, and ESPECIALLY any method "
        "BENCHMARK, must run on the FULL dataset (subsampling biases "
        "method comparisons). REVIEW must verify the headline/benchmark "
        "numbers were computed on full data, or that any subsample is "
        f"prominently disclosed as a limitation (and a {_RESOURCE_REQUEST_MD} "
        "was filed instead of silently shrinking the data to fit the "
        "wall budget)."
    )


def check_execute(
    execute_stage_dir: pathlib.Path,
    domain: str | None = None,
) -> CrossCheckResult:
    """Verify `results.json` CI bounds appear in `analysis.md` inside a
    single execute stage directory.

    Missing CIs from the paired `comparisons.*` block are ERRORS (they
    are the primary quantitative claims of the run). Missing CIs
    from `primary_model` or `baselines.*` blocks are WARNINGS (they
    may be legitimately elided for brevity).

    An ERROR is also raised when a baselines block is present but no
    `comparisons.*` entry carries a paired delta CI — a plain
    point-estimate difference is not sufficient.

    `domain` is forwarded to `_walk_ci95` so a domain pack can add
    additional skip patterns (e.g. survival_prognostic suppresses
    exploratory `multivariate_cox.variables` tables).
    """
    warnings: list[str] = []
    errors: list[str] = []
    results_path = execute_stage_dir / _RESULTS_JSON
    analysis_path = execute_stage_dir / _ANALYSIS_MD

    if not results_path.exists() or not analysis_path.exists():
        # Missing artifacts are a contract-layer concern, not a
        # semantic one. Return trivially ok so cross_check does not
        # double-report the same failure.
        return CrossCheckResult(ok=True, warnings=[], errors=[])

    try:
        raw_results = json.loads(results_path.read_text())
    except json.JSONDecodeError as e:
        return CrossCheckResult(
            ok=False,
            warnings=[],
            errors=[f"{_RESULTS_JSON} does not parse: {e}"],
        )
    # Normalise Unicode typographic minus (U+2212), en-dash, etc. to
    # ASCII hyphen once up-front. Every downstream matcher (literal
    # number presence, `[lo, hi]` CI regex, "near-but-not-equal"
    # heuristic) assumes ASCII hyphen-minus for negative values;
    # without this, a perfectly valid analysis.md that uses `−0.060`
    # for typographic reasons reads as "CI missing".
    analysis_text = _normalise_minus_signs(analysis_path.read_text())

    results = normalize_results_json(raw_results)

    # Semantic numeric checks on canonical-shape load-bearing blocks.
    # These fire before any CI-presence walk so a fabricated/impossible
    # value surfaces as its own error rather than a cryptic "CI not
    # present in analysis.md" warning downstream.
    _check_ci_order_and_nan(results, errors, warnings)
    _check_point_estimate_in_ci(results, errors)
    _check_bounded_metric_ranges(results, errors, domain=domain)
    _check_p_value_validity(results, errors, warnings)

    # Quality-grade checks (W2/W5/W7/W8/W9/W10) are demoted to warnings
    # since 2026-05: each is a quality concern the analysis REVIEW
    # judges. Failing cross_check on top of that is double-jeopardy and
    # triggers expensive retries for things REVIEW would catch anyway. Real
    # honesty checks (CI order, point-in-CI, p-value validity,
    # bounded metric ranges, preregistration sha256 drift) stay as
    # ERRORs — they are fabrication signals, not quality grades.
    # See cross_check classification doc in
    # development notes (not distributed)
    enabled = enabled_execute_checks(domain)
    if "w2" in enabled:
        _check_w2_power_section_present(execute_stage_dir, warnings)
    if "w5" in enabled:
        _check_w5_h1_annotation_on_primary_panel(execute_stage_dir, warnings)
    _check_w7_stratified_sensitivity(execute_stage_dir, results, warnings)
    _check_w8_viz_data_contract(execute_stage_dir, warnings)
    # W13 is an always-on ERROR gate (NOT demoted): the headline arm
    # named in preregistration.json must complete before REVIEW. No-op
    # when no load_bearing_arm is declared (backward compatible).
    _check_w13_load_bearing_arm_complete(execute_stage_dir, results, errors)
    # W15 is an always-on ERROR gate (companion to W13): the load-bearing
    # arm's numbers must be PROVENANCE-CLEAN (re-scorable), not salvage-
    # tagged. No-op when no load_bearing_arm is declared, when the named
    # arm is absent (W13's job), or when no provenance tag is present
    # (older results — treated as clean). Backward compatible.
    _check_w15_load_bearing_arm_provenance(execute_stage_dir, results, errors)
    # W15L is the latest-execute-stage provenance gate (advisory WARN-only
    # sibling of W15, 2026-06-17): on a MULTI-execute run it flags a named
    # load_bearing_arm that the LATEST execute stage dropped but an EARLIER
    # execute stage still carries — the d5 set-level-CKA fracture W13/W15 (which
    # read only the latest stage) miss. WARN-ONLY (appends to `warnings` only —
    # no errors sink, no Decision) so it never blocks / routes back; shipped
    # WARN-first like W21. No-op on single-execute runs, no load_bearing_arm, or
    # when the arm is present in the latest stage.
    _check_w15_latest_execute_provenance(execute_stage_dir, results, warnings)
    # W14 is advisory (warn-only): a feasible-but-declined shared-protocol
    # baseline is weighed by REVIEW's positioning judgement but never blocks.
    # No-op when no `baselines` block is declared (backward compat).
    _check_w14_shared_protocol_baseline(execute_stage_dir, results, warnings)
    # W16 is load-bearing-gated: ERROR when a degenerate (null-boundary +
    # collapsed-CI) metric is the SOLE evidence for the named headline arm,
    # WARN when a disambiguating companion is present. No-op when no
    # load_bearing_arm is declared or no degenerate metric backs the
    # headline (backward compatible). Receives both sinks so it can route
    # severity per the SOLE-vs-companion split.
    _check_w16_degenerate_metric_headline(
        execute_stage_dir, results, errors, warnings, domain=domain
    )
    # W17 is the key rigor lever (portfolio assessment, 2026-06-13):
    # sibling of W13. W13 gates on the HEADLINE arm (the effect exists);
    # W17 gates on the DISCRIMINATING arm (the experiment that adjudicates
    # the central question). ERROR when that arm is unrun AND the run
    # frames the central question as resolved; WARN when narrowed (scoped)
    # or when PLAN registered no discriminating_arm. Receives both sinks so
    # it can route severity per the resolved-vs-scoped split. Backward
    # compatible: no field → advisory WARN, never a crash, never a block.
    _check_w17_discriminating_arm_resolution(
        execute_stage_dir, results, errors, warnings
    )
    # W18 is the enforcement layer over omicairn.lean_verify.substance_warnings
    # (the M3b trivial-pattern + elementary-theorem detection that ALREADY
    # runs and is STORED on each lean verdict). It makes that detection
    # ENFORCEABLE instead of advisory-only: theory-gated, it ERRORs when a
    # HEADLINE/load-bearing Lean theorem carries a substance_warning while
    # framed as a first-class `theorem` (the XOR-table-ships-ungated mode),
    # and WARNs when supporting or honestly demoted to a conjecture/
    # formalized observation. Receives both sinks for the headline-vs-
    # supporting severity split. No re-detection; reuses W13/W17's plan-
    # sibling + preregistration machinery. Backward compatible: non-theory
    # run / no lean verdict / no warning → silent no-op.
    _check_w18_proof_substance_enforced(
        execute_stage_dir, errors, warnings
    )
    # W20 is the decision-rule-HONORED gate (a W17-sibling, 2026-06-15): the
    # one honesty class W13–W18 don't touch. W9 verifies each hypothesis's
    # prose `decision_rule` is PRESENT; W20 verifies the OPTIONAL structured
    # twin `decision_rule_structured` was HONORED — it mechanically reads the
    # named metric from results.json and evaluates op-vs-threshold (+ a
    # ci_excludes clause if set). ERRORs only when the rule is present +
    # well-formed + metric resolvable + VIOLATED AND the hypothesis is framed
    # SUPPORTED via the structural per-hypothesis status (the wm-cross-model-
    # transfer H3 over-claim mode); WARNs on a violation with unknown framing
    # or on any unresolvable input. Receives both sinks for the split.
    # Backward compatible: no `decision_rule_structured` field (every
    # existing / in-flight run) → silent pass, never errors, never crashes.
    _check_w20_decision_rule_honored(
        execute_stage_dir, results, errors, warnings
    )
    # W21 is the prereg-COMPLETENESS gate (advisory layer over W13/W17/W20,
    # 2026-06-17): each of those four SILENTLY no-ops when its field is absent,
    # so an under-declared confirmatory plan passes the suite VACUOUSLY (audit
    # S1 — discriminating-arm-as-future-work). W21 fires for a confirmatory run
    # (preregistration.json present — the signal the four key on) declaring
    # fewer than all four PreregField.ALL fields, naming the missing ones.
    # WARN-ONLY (appends to `warnings` only — no errors sink, no Decision) so it
    # never blocks / routes back; composes with W20 (PRESENCE vs HONORED).
    _check_w21_prereg_completeness(execute_stage_dir, warnings)
    # W23 is the decisive-arm zero-power gate (advisory WARN-only sibling of W17,
    # 2026-06-17): W17 verifies the pre-registered DISCRIMINATING arm RAN; W23
    # verifies that arm — when it ran — had statistical POWER to discriminate
    # (its decisive input was not structurally degenerate). Fires on the named
    # discriminating_arm + the run-level power_analysis showing any of: a
    # constant decisive input across the comparison panel (the posttrain-regime
    # coverage-numerator c==1 defect), an explicit zero power / all-zero
    # power_curve / null MDE-at-target, or a full-range bootstrap CI. WARN-ONLY
    # (appends to `warnings` only — no errors sink, no Decision) so it never
    # blocks progression to REVIEW / routes back; shipped WARN-first like W21/W15L. No-op when no
    # discriminating_arm is declared, the arm is absent (W17's job), or the arm
    # varied / had power. A fired-but-null arm (power present, effect null) must
    # NOT trip it.
    _check_w23_decisive_arm_power(execute_stage_dir, results, warnings, domain=domain)
    # W25 is the effect-clears-own-MDE gate (advisory WARN-only sibling of
    # W2/W17, 2026-06-19, audit proposal P4): W2 verifies a numeric MDE is
    # DECLARED in preregistration.json.mde; W25 verifies the load-bearing/
    # headline effect actually CLEARS it. Tier 1 (sharp) — a declared
    # load_bearing_arm whose own |effect| < mde WARNs; Tier 2 (advisory
    # fallback) — when no load_bearing_arm is declared but a numeric MDE is,
    # WARNs iff EVERY `comparisons.delta_primary_vs_*` arm is sub-MDE (the
    # sensitive-memory +0.0043-vs-0.03 / intersectional +0.0013-vs-0.02 defects
    # — neither declared a load_bearing_arm). WARN-ONLY (appends to `warnings`
    # only — no errors sink, no Decision) so it never blocks progression / routes
    # back; shipped WARN-first like W21/W15L/W23. No-op when no numeric MDE is
    # declared (W2's job), no resolvable headline/primary-contrast effect, or
    # the effect clears the MDE.
    _check_w25_effect_clears_mde(execute_stage_dir, results, warnings)
    # W24 is the construct-validity gate (advisory WARN-only sibling of
    # W20/W23/W25, 2026-06-19, audit proposal P3): when a headline rests on a
    # LEARNED / SURROGATE axis (an LLM-judge stand-in, a learned predictor used
    # in place of a gold label), PLAN may emit an OPTIONAL structured
    # `construct_validity: {metric, min_rho, load_bearing}` declaration (the
    # machine-checkable twin of the prose H4/validity-gate decision_rule). W24
    # mechanically reads the named validity metric from results.json and WARNs
    # when the axis is load-bearing AND its measured validity stat FAILS — point
    # below the threshold OR (for a correlation) its CI spans the null (the
    # sensitive-memory appropriateness-axis rho=0.276 vs rho>=0.6 / CI spanning 0
    # defect, left load-bearing yet verdict `proceed`). WARN-ONLY (appends to
    # `warnings` only — no errors sink, no Decision) so it never blocks /
    # routes back; shipped WARN-first like W21/W15L/W23/W25. Back-compatible:
    # no `construct_validity` object (every existing / in-flight run) → silent
    # pass. No-op when not load-bearing, the stat is unresolvable, or it PASSES.
    _check_w24_construct_validity(execute_stage_dir, results, warnings)
    # W32 is the within-EXECUTE parallel-arm independence gate (2026-07-01):
    # the HARD BACKSTOP to the prompt-driven pre-dispatch CLI gate. When an
    # EXECUTE stage fanned out into independent arms it writes an arm_manifest.json;
    # W32 re-validates it on-disk and BLOCKS progression (errors) on a genuine contract
    # violation — overlapping/nested/escaping arm output dirs, duplicate/missing/
    # non-int seeds, concurrent-GPU arms over gpu_slots, or reserved-host routing —
    # so a skipped pre-check cannot ship a contract-violating fan-out. It also folds
    # in an ADVISORY (WARN-only) recorded-evidence re-check off arm_provenance.jsonl
    # (materialized-dir overlap, empty arm dir, seed echo, overlapping GPU launch
    # windows). Gated on `w32 in enabled_execute_checks(domain)` (default-ON;
    # opt-out via a domain pack's `disable_execute_checks`). Silent no-op when
    # neither arm_manifest.json nor arm_provenance.jsonl is present — every run
    # that does not fan out is byte-for-byte unchanged (backward compatible).
    if ParallelArmIndependenceGate.GATE_ID.lower() in enabled:
        _check_parallel_arm_contract(execute_stage_dir, errors, warnings)
    _check_w9_hypothesis_dag_and_fallbacks(
        execute_stage_dir, warnings, warnings  # both lists collapse to warnings
    )
    _check_w10_family_correction_declared(
        execute_stage_dir, warnings, warnings  # both lists collapse to warnings
    )
    _check_figures_contract(execute_stage_dir, warnings)
    _check_data_fetch(execute_stage_dir, warnings)
    _check_subsampling(execute_stage_dir, warnings)
    _check_env_lockfile_coverage(execute_stage_dir, warnings)
    _check_preregistration_drift(execute_stage_dir, results, errors)

    if isinstance(raw_results, dict):
        if (
            ResultsField.CONDITIONS in raw_results
            and ResultsField.PRIMARY_MODEL not in raw_results
        ):
            warnings.append(
                f"{_RESULTS_JSON} uses legacy `conditions.*` shape; canonical "
                "shape is `primary_model` + `baselines.*` + `comparisons.*`. "
                "Normalised in memory for schema check, but please emit the "
                "canonical shape on the next run."
            )
        if _has_sibling_scalar_ci(raw_results):
            warnings.append(
                f"{_RESULTS_JSON} uses sibling-scalar `ci_lower_95`/`ci_upper_95`; "
                "canonical shape is `ci_95: [lo, hi]` arrays. Normalised in "
                "memory for schema check."
            )

    if isinstance(results, dict) and results.get(ResultsField.BASELINES):
        comparisons = results.get(ResultsField.COMPARISONS) or {}
        has_paired_ci = False
        if isinstance(comparisons, dict):
            for _, block in comparisons.items():
                if not isinstance(block, dict):
                    continue
                ci = block.get("ci_95")
                if isinstance(ci, list) and len(ci) == 2:
                    has_paired_ci = True
                    break
        if not has_paired_ci:
            baseline_names = sorted(results[ResultsField.BASELINES].keys())
            errors.append(
                f"{_RESULTS_JSON} has baselines "
                f"({baseline_names}) but no `comparisons.*` entry with a "
                "paired delta CI (`ci_95: [lo, hi]`). A plain arithmetic "
                "difference of point estimates is not sufficient — compute "
                "a paired bootstrap delta CI using the SAME bootstrap "
                "indices for primary and baseline, then percentile the "
                "delta distribution."
            )

    bounds = _walk_ci95(results, domain=domain)
    if not bounds:
        return CrossCheckResult(ok=not errors, warnings=warnings, errors=errors)

    for path, lo, hi in bounds:
        lo_present = _number_present_in_text(lo, analysis_text)
        hi_present = _number_present_in_text(hi, analysis_text)
        if lo_present and hi_present:
            continue
        msg = f"{path} ci_95=[{lo:.3f}, {hi:.3f}] not fully present in {_ANALYSIS_MD}"
        if path.startswith("comparisons."):
            errors.append(msg)
        else:
            warnings.append(msg)

    ci_pattern = re.compile(
        r"\[\s*([+-]?\d+\.\d+)\s*,\s*([+-]?\d+\.\d+)\s*\]"
    )
    analysis_cis = [
        (float(m.group(1)), float(m.group(2)))
        for m in ci_pattern.finditer(analysis_text)
    ]
    results_ci_set_3dp = {(round(lo, 3), round(hi, 3)) for _, lo, hi in bounds}
    results_ci_set = {(round(lo, 2), round(hi, 2)) for _, lo, hi in bounds}
    results_ci_set_1dp = {(round(lo, 1), round(hi, 1)) for _, lo, hi in bounds}

    def _is_round(x: float) -> bool:
        scaled = round(x * 20)
        return abs(x * 20 - scaled) < 1e-6

    for a_lo, a_hi in analysis_cis:
        rounded_3 = (round(a_lo, 3), round(a_hi, 3))
        rounded_2 = (round(a_lo, 2), round(a_hi, 2))
        rounded_1 = (round(a_lo, 1), round(a_hi, 1))
        if rounded_3 in results_ci_set_3dp:
            continue
        if rounded_2 in results_ci_set:
            continue
        if _is_round(a_lo) and _is_round(a_hi):
            continue
        if rounded_1 not in results_ci_set_1dp:
            continue
        substring_match = False
        analysis_lo_str = f"{a_lo:.3f}"
        analysis_hi_str = f"{a_hi:.3f}"
        for _, b_lo, b_hi in bounds:
            if (_number_present_in_text(b_lo, analysis_lo_str)
                    and _number_present_in_text(b_hi, analysis_hi_str)):
                substring_match = True
                break
        if substring_match:
            continue
        nearest = min(
            bounds,
            key=lambda b: abs(b[1] - a_lo) + abs(b[2] - a_hi),
        )
        warnings.append(
            f"{_ANALYSIS_MD} has CI [{a_lo:.3f}, {a_hi:.3f}] which is near but "
            f"NOT equal to {_RESULTS_JSON} {nearest[0]} "
            f"ci_95=[{nearest[1]:.3f}, {nearest[2]:.3f}] "
            "— analyst may have recomputed instead of copying"
        )

    ok = not errors
    return CrossCheckResult(ok=ok, warnings=warnings, errors=errors)


# ---------------------------------------------------------------------------
# Verdict builder for the cross_check stage
# ---------------------------------------------------------------------------

def build_execute_verdict(
    execute_stage_dir: pathlib.Path,
    domain: str | None = None,
) -> tuple[verdict_mod.Verdict, CrossCheckResult]:
    """Run `check_execute` and translate the result into a Verdict.

    Mapping:
      - errors present → retry_execute verdict with the error list
      - warnings only → proceed verdict with warnings folded into reason
      - clean → proceed verdict

    SEVERITY CONTRACT (#123a/b): the ERROR sink is the SOLE trigger for a
    `retry_execute` route from Python. Advisory / WARN W-gates (W14/W15L/
    W21/W23/W24/W25 etc.) append ONLY to ``result.warnings`` and so
    ALWAYS route `proceed` here — never an execute re-run. Every retry_execute
    this function emits is therefore an ERROR-level fabrication/honesty block
    (W13/W15/W16 ERROR sink + CI-order / point-in-CI / p-value / bounded-range
    / prereg-sha256 checks), which is why the orchestrator HALTS on a capped
    cross_check retry_execute (see
    ``orchestrator._capped_blocking_cross_check_retry``).
    """
    result = check_execute(execute_stage_dir, domain=domain)
    if result.errors:
        v = verdict_mod.retry(
            phase=PhaseName.EXECUTE,
            reason="execute cross_check flagged errors",
            evidence=list(result.errors),
            fix=(
                "Rewrite the execute stage to emit canonical "
                "`primary_model`/`baselines`/`comparisons` schema "
                "with paired delta CIs, and ensure every paired CI "
                "appears verbatim in analysis.md."
            ),
        )
        return v, result
    if result.warnings:
        v = verdict_mod.proceed(
            reason=(
                "execute cross_check passed with "
                f"{len(result.warnings)} warning(s)"
            ),
        )
        v.evidence = list(result.warnings)
        return v, result
    return verdict_mod.proceed(reason="execute cross_check clean"), result
