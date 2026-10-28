"""Small-batch throughput → ETA probe math (harness-level, domain-agnostic).

The single most expensive avoidable failure mode in a OmiCairn EXECUTE stage is
committing to a long generation / training / bootstrap run that cannot finish
within the phase **wall budget** (`budgets.max_phase_wall_minutes`, a
Slurm-style ``--time`` limit — a job that exceeds it is killed). This bit a
real run (the "d5" strand): nobody measured the per-item cost before launching
a ~7 h full-cohort generation against a 720-min wall, so the stage timed out
having produced nothing finishable.

The fix is the classic **observe-then-extrapolate** pattern (the same one
``tqdm`` uses to show an ETA: rate = elapsed_items / elapsed_seconds, then
``ETA = remaining / rate``) — but applied *before* committing to the full run
and *checked against the wall*, the way an HPC user must estimate ``sbatch
--time`` before submission:

  1. run a SMALL timed batch (e.g. 10–20 items, or a few training steps),
  2. compute seconds-per-unit from that batch,
  3. extrapolate to the full N units,
  4. compare the projected wall against the phase wall budget (with a safety
     margin for fixed setup + the inevitable tail-slowdown),
  5. recommend a course of action: PROCEED / SHARD / SUBSAMPLE / REQUEST.

This module is the *math* behind that practice so it can be unit-tested and
reused, rather than re-derived (often wrongly) in prose each time. It holds
**no** domain knowledge and **no** I/O: callers pass in measured numbers and
get back a structured recommendation. The prose protocol an EXECUTE agent
follows lives in ``prompts/execute.md`` (step 1c) and ``agents/
compute-scheduling.md``; this module is what those can point an agent at to
get the arithmetic right.

Design choices, justified:

- **EMA vs simple mean.** ``tqdm`` smooths its rate with an exponential
  moving average. We deliberately use the *simple* mean of the probe batch:
  a pre-run probe is a single short measurement, not a streaming series, so
  there is nothing to smooth — and a simple mean is auditable (the agent can
  reproduce ``elapsed / n`` by hand). Smoothing belongs in the *live*
  progress line during the run, not in the up-front go/no-go estimate.

- **Safety margin (``MARGIN_FRACTION``), not a point estimate.** A small
  warmup batch is optimistic: it misses one-time setup (model load, JIT,
  tokenizer warmup), it misses the tail-slowdown of long generations (KV
  cache growth, GC, thermal), and s/item has variance. We therefore project
  the full run at ``per_unit * N * (1 + MARGIN_FRACTION) + fixed_overhead``
  and compare *that* against the wall. Underestimating gets the run killed
  at the wall (the expensive failure); overestimating just nudges toward
  sharding (cheap). Asymmetric cost → bias the estimate up.

- **Recommendations are advice, never enforcement.** This module returns a
  recommendation string + the numbers behind it. It does not abort, kill, or
  subsample anything — the agent (and ultimately the human/audit loop) decide.
"""
from __future__ import annotations

import dataclasses
import enum


# Fraction added to the linear extrapolation to cover warmup optimism, the
# tail-slowdown of long generations, and per-unit variance. 25% is a
# deliberately conservative default: the cost of overestimating is "shard a
# run that might have just fit"; the cost of underestimating is "the run is
# SIGKILLed at the wall having produced nothing finishable" (the d5 failure).
MARGIN_FRACTION: float = 0.25

# A probe batch smaller than this is too noisy to extrapolate from: one slow
# item dominates and the estimate is meaningless. We still return a verdict
# but flag it UNRELIABLE so the caller widens the probe rather than trusting
# a 1–2 item measurement.
MIN_RELIABLE_PROBE_UNITS: int = 5


class Recommendation(str, enum.Enum):
    """What to do given the projection vs the wall. String-valued so it
    serialises cleanly into JSON / prompt text."""

    PROCEED = "proceed"          # fits the wall with margin → launch the full run
    SHARD = "shard"              # too big for one wall, but parallelisable / resumable
    SUBSAMPLE = "subsample"      # fits only at reduced N — allowed ONLY with disclosure
    REQUEST = "request"          # doesn't fit any honest local plan → RESOURCE_REQUEST.md


@dataclasses.dataclass(frozen=True)
class ProbeEstimate:
    """The result of extrapolating a small-batch probe to the full run.

    All times are in seconds. ``projected_full_wall_s`` already includes the
    safety margin and the fixed overhead — it is the number to compare
    against the wall, and it is what ``recommendation`` is computed from.
    """

    probe_units: int
    probe_seconds: float
    full_units: int
    per_unit_seconds: float
    fixed_overhead_s: float
    margin_fraction: float
    projected_full_wall_s: float
    wall_budget_s: float
    fits: bool                 # projected (with margin) <= wall budget
    reliable: bool             # probe was large enough to trust
    recommendation: Recommendation
    shards_needed: int         # ceil(projected / wall); 1 when it fits, 0 when no wall given
    max_units_within_wall: int  # largest N whose projection still fits the wall

    def human_summary(self) -> str:
        """A one-paragraph, copy-into-summary.md explanation of the verdict.

        Deliberately includes the raw probe numbers so a reviewer can redo the
        arithmetic by hand — the estimate must be auditable, not a black box.
        """
        wall_min = self.wall_budget_s / 60.0
        proj_min = self.projected_full_wall_s / 60.0
        rate = (
            f"{self.per_unit_seconds:.3g}s/unit"
            if self.per_unit_seconds >= 0.001
            else f"{self.per_unit_seconds*1000:.3g}ms/unit"
        )
        parts = [
            f"Probe: {self.probe_units} units in {self.probe_seconds:.1f}s "
            f"({rate}).",
            f"Extrapolated to {self.full_units} units with "
            f"{int(self.margin_fraction*100)}% margin "
            f"+ {self.fixed_overhead_s:.0f}s fixed overhead "
            f"→ ~{proj_min:.1f} min projected wall.",
        ]
        if self.wall_budget_s > 0:
            verb = "fits within" if self.fits else "EXCEEDS"
            parts.append(f"This {verb} the {wall_min:.0f}-min phase wall.")
        if not self.reliable:
            parts.append(
                f"WARNING: probe of {self.probe_units} units is below the "
                f"reliable threshold ({MIN_RELIABLE_PROBE_UNITS}); widen the "
                f"probe before trusting this estimate."
            )
        parts.append(f"Recommendation: {self.recommendation.value.upper()}.")
        if self.recommendation is Recommendation.SHARD and self.shards_needed > 1:
            parts.append(
                f"Split into ~{self.shards_needed} resumable shards "
                f"(≤{self.max_units_within_wall} units each) or run the full "
                f"job as a detached/checkpointed job that survives the wall."
            )
        elif self.recommendation is Recommendation.SUBSAMPLE:
            parts.append(
                f"At most ~{self.max_units_within_wall} units fit the wall; a "
                f"subsampled result may be reported ONLY with explicit "
                f"disclosure (subsample N, full N, why) and NEVER as the final "
                f"/ benchmark number — prefer SHARD or REQUEST."
            )
        elif self.recommendation is Recommendation.REQUEST:
            parts.append(
                "File RESOURCE_REQUEST.md (more wall / a faster device / a GPU) "
                "rather than silently degrading the science to beat the clock."
            )
        return " ".join(parts)


def estimate_from_probe(
    *,
    probe_units: int,
    probe_seconds: float,
    full_units: int,
    wall_budget_s: float,
    fixed_overhead_s: float = 0.0,
    margin_fraction: float = MARGIN_FRACTION,
    shardable: bool = True,
) -> ProbeEstimate:
    """Extrapolate a timed small-batch probe to the full run and recommend.

    Args:
      probe_units:      number of units (items / steps / folds) timed.
      probe_seconds:    wall seconds the probe batch took (the repeated work
                        only — exclude one-time setup; pass that as
                        ``fixed_overhead_s`` so it isn't multiplied by N).
      full_units:       total units the real run must process.
      wall_budget_s:    the phase wall budget in seconds (``max_phase_wall_
                        minutes * 60``). Pass 0 / negative when there is no
                        wall (then ``fits`` is True and the recommendation is
                        always PROCEED — we still return the projection).
      fixed_overhead_s: one-time cost (model load, data load, JIT) added once,
                        not per unit.
      margin_fraction:  safety margin on the per-unit extrapolation.
      shardable:        whether the work can be split into independent /
                        resumable shards (most batch generation + bootstrap +
                        per-sample inference can; a single indivisible fit
                        cannot). Decides SHARD vs SUBSAMPLE/REQUEST when the
                        full run overflows the wall.

    Raises:
      ValueError: on non-positive ``probe_units`` / ``full_units`` or negative
        times — the math is undefined and a silent bad estimate is worse than
        a loud failure here (this is a go/no-go gate for hours of compute).
    """
    if probe_units <= 0:
        raise ValueError(f"probe_units must be > 0, got {probe_units}")
    if full_units <= 0:
        raise ValueError(f"full_units must be > 0, got {full_units}")
    if probe_seconds < 0 or fixed_overhead_s < 0:
        raise ValueError("probe_seconds and fixed_overhead_s must be >= 0")
    if margin_fraction < 0:
        raise ValueError(f"margin_fraction must be >= 0, got {margin_fraction}")

    per_unit = probe_seconds / probe_units
    projected = per_unit * full_units * (1.0 + margin_fraction) + fixed_overhead_s

    has_wall = wall_budget_s > 0
    reliable = probe_units >= MIN_RELIABLE_PROBE_UNITS

    if not has_wall:
        # No wall configured → nothing to overflow. Report the projection but
        # always recommend PROCEED (the operator opted out of a wall).
        return ProbeEstimate(
            probe_units=probe_units,
            probe_seconds=float(probe_seconds),
            full_units=full_units,
            per_unit_seconds=per_unit,
            fixed_overhead_s=float(fixed_overhead_s),
            margin_fraction=margin_fraction,
            projected_full_wall_s=projected,
            wall_budget_s=0.0,
            fits=True,
            reliable=reliable,
            recommendation=Recommendation.PROCEED,
            shards_needed=1,
            max_units_within_wall=full_units,
        )

    fits = projected <= wall_budget_s

    # Largest N whose projection still fits the wall (invert the projection,
    # holding fixed_overhead constant). Used to size shards / a subsample.
    usable = wall_budget_s - fixed_overhead_s
    if usable <= 0 or per_unit <= 0:
        max_units = 0
    else:
        max_units = int(usable / (per_unit * (1.0 + margin_fraction)))
    max_units = max(0, min(max_units, full_units))

    if fits:
        rec = Recommendation.PROCEED
        shards = 1
    elif shardable and max_units >= 1:
        rec = Recommendation.SHARD
        # ceil division: how many wall-sized shards cover the full run.
        shards = -(-full_units // max(1, max_units))
    elif max_units >= 1:
        # Not shardable but a reduced N would fit → subsample (with the heavy
        # disclosure caveat the summary spells out). Prefer the operator
        # reads that and chooses REQUEST instead for a final/benchmark number.
        rec = Recommendation.SUBSAMPLE
        shards = 1
    else:
        # Not even one unit fits inside the wall after fixed overhead → no
        # honest local plan; ask for more resources.
        rec = Recommendation.REQUEST
        shards = 0

    return ProbeEstimate(
        probe_units=probe_units,
        probe_seconds=float(probe_seconds),
        full_units=full_units,
        per_unit_seconds=per_unit,
        fixed_overhead_s=float(fixed_overhead_s),
        margin_fraction=margin_fraction,
        projected_full_wall_s=projected,
        wall_budget_s=float(wall_budget_s),
        fits=fits,
        reliable=reliable,
        recommendation=rec,
        shards_needed=shards,
        max_units_within_wall=max_units,
    )
