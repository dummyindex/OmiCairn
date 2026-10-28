#!/usr/bin/env python3
"""
Fetcher for mitoxy-brca. Shares data with brca-honest-validation:
both cases use the same TCGA-BRCA + METABRIC cohorts and the same
17-gene Mitoxyperilysis panel. The canonical fetcher lives at
../brca-honest-validation/fetch_data.py.

This script:
  1. Triggers the canonical fetcher if data isn't already on disk.
  2. Symlinks ./data → ../brca-honest-validation/data so both cases
     resolve to the same physical files (per COLLAB_PROTOCOL § 3.1
     "real-cohort data; must never be re-fetched concurrently").
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent / "brca-honest-validation"
SHARED_DATA = SHARED / "data"
LOCAL_DATA = HERE / "data"

REQUIRED_FILES = (
    "tcga_brca_expression_zscores.csv",
    "tcga_brca_clinical.csv",
    "metabric_expression_zscores.csv",
    "metabric_clinical.csv",
)


def shared_data_complete() -> bool:
    return all((SHARED_DATA / f).exists() for f in REQUIRED_FILES)


def main() -> int:
    if not shared_data_complete():
        print("Shared brca-tcga-metabric data missing; running canonical "
              "fetcher in ../brca-honest-validation/ ...")
        rc = subprocess.call(
            [sys.executable, str(SHARED / "fetch_data.py")]
        )
        if rc != 0:
            print(f"Canonical fetcher exited {rc}")
            return rc
    if not shared_data_complete():
        print("ERROR: canonical fetcher ran but required files are still "
              f"missing under {SHARED_DATA}")
        return 1
    # Symlink local data → shared data (idempotent).
    if LOCAL_DATA.is_symlink() or LOCAL_DATA.exists():
        if LOCAL_DATA.is_symlink() and LOCAL_DATA.resolve() == SHARED_DATA.resolve():
            print(f"data symlink already in place: {LOCAL_DATA} → {SHARED_DATA}")
            return 0
        print(f"WARNING: {LOCAL_DATA} exists and is not the expected symlink; "
              "leaving it alone. Remove it and re-run if you want the symlink.")
        return 0
    LOCAL_DATA.symlink_to(SHARED_DATA, target_is_directory=True)
    print(f"linked {LOCAL_DATA} → {SHARED_DATA}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
