"""Deterministic synthetic data for the single-H vs dynamic-Hs
demonstration (#49 / docs/design-docs/dynamic-hypotheses.md §5).

NOT real data. Purpose: a dataset where an honest analysis of the
PRE-REGISTERED primary hypothesis (feature1 → outcome) leaves a strong,
real SECONDARY signal on the table — a feature2×subgroup interaction —
that only a discovery / dynamic-hypothesis workflow would surface and
test. This is what lets the two cases diverge for a fair comparison:

  - Primary signal (both cases find it): `feature1` is prognostic for
    `outcome`. A single-hypothesis run reports this and stops.
  - Hidden secondary signal (only dynamic-Hs surfaces it): within the
    subgroup `group == "B"`, `feature2` strongly predicts `outcome`;
    within `group == "A"` it does not. A discovery analysis notices the
    subgroup heterogeneity, raises an on-the-fly hypothesis
    ("feature2 is prognostic in group B only"), tests it, and the
    knowledge graph ends strictly richer.

The single-H case, by construction, cannot represent that second
finding — its RESEARCH.md declares one hypothesis and disables
on-the-fly registration. That is the failure mode #49 demonstrates:
one-hypothesis runs discard real signal.

train.csv  (120 rows) / holdout.csv (80 rows), same schema:
  sample_id, feature1, feature2, feature3, group, outcome
"""
import csv
import pathlib
import random

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
DATA.mkdir(exist_ok=True)

random.seed(20260601)


def _gen(name: str, n: int) -> None:
    path = DATA / name
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sample_id", "feature1", "feature2", "feature3",
                    "group", "outcome"])
        for i in range(n):
            f1 = random.gauss(0, 1)
            f2 = random.gauss(0, 1)
            f3 = random.gauss(0, 1)
            group = "B" if random.random() < 0.5 else "A"
            # Primary effect: feature1 (present in BOTH groups).
            z = 1.2 * f1
            # Secondary effect: feature2 matters ONLY in group B.
            if group == "B":
                z += 1.5 * f2
            z += random.gauss(0, 0.6)
            y = 1 if z > 0 else 0
            w.writerow([f"{name[:2]}_{i:03d}", f"{f1:.4f}", f"{f2:.4f}",
                        f"{f3:.4f}", group, y])
    print(f"wrote {path}  ({n} rows)")


if __name__ == "__main__":
    _gen("train.csv", 120)
    _gen("holdout.csv", 80)
