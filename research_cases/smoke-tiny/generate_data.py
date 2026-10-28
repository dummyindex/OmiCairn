"""Generate a deterministic synthetic CSV for the smoke-tiny OmiCairn test.

This is NOT real biological data. Its only purpose is to smoke-test
the OmiCairn orchestrator, prompts, and contracts. RESEARCH.md explicitly
states the data is synthetic and no publication claim is warranted.

Two CSVs:
  train.csv — 60 rows, 3 features, binary outcome
  holdout.csv — 40 rows, same schema

Outcome is a deterministic function of feature1 + 0.5*feature2 + noise,
so a logistic regression should achieve AUC ≈ 0.85.
"""
import csv
import pathlib
import random

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
DATA.mkdir(exist_ok=True)

random.seed(42)

def gen(name: str, n: int) -> None:
    path = DATA / name
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sample_id", "feature1", "feature2", "feature3", "outcome"])
        for i in range(n):
            f1 = random.gauss(0, 1)
            f2 = random.gauss(0, 1)
            f3 = random.gauss(0, 1)
            z = f1 + 0.5 * f2 + random.gauss(0, 0.6)
            y = 1 if z > 0 else 0
            w.writerow([f"{name[:2]}_{i:03d}", f"{f1:.4f}", f"{f2:.4f}", f"{f3:.4f}", y])
    print(f"wrote {path}  ({n} rows)")

gen("train.csv", 60)
gen("holdout.csv", 40)
