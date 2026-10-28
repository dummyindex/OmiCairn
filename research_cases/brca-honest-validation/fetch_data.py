#!/usr/bin/env python3
"""
Fetch breast-cancer expression + clinical/survival data for the
17-gene Mitoxyperilysis panel from cBioPortal public REST API.

Discovery cohort : TCGA-BRCA  (brca_tcga_pan_can_atlas_2018)
Validation cohort: METABRIC    (brca_metabric)

Output (saved to ./data/):
  tcga_brca_expression_zscores.csv     sampleId + 17 gene columns
  tcga_brca_clinical.csv               sampleId + clinical/survival
  metabric_expression_zscores.csv      sampleId + 17 gene columns
  metabric_clinical.csv                sampleId + clinical/survival

Shared by two test cases (brca-honest-validation, mitoxy-brca);
see README.md in those dirs for the symlink convention.

Requires: requests, pandas
Concurrency: takes research_cases/brca-honest-validation/data/.fetch.lock
             per COLLAB_PROTOCOL § 9 to prevent two agents from re-fetching.
"""

import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

# ── configuration ──────────────────────────────────────────────────
BASE = "https://www.cbioportal.org/api"
HEADERS = {"Accept": "application/json"}
TIMEOUT = 60

# 17-gene Mitoxyperilysis panel — shared by brca-honest-validation
# AND mitoxy-brca cases. Per RESEARCH.md of both:
GENE_PANEL = [
    "MTOR", "RPTOR", "RICTOR",         # mTOR axis
    "BAX", "BAK1", "BID",              # MOMP / pro-apoptotic
    "RHOA",                            # GTPase modulator
    "NLRP3", "NINJ1",                  # inflammasome
    "TNFRSF1A", "TNFRSF1B", "MYD88",   # TNF / TLR signalling
    "TLR2", "TLR4", "TLR7", "TLR8",    # TLR family
    "NFE2L2",                          # NRF2 redox axis
]

TCGA_STUDY = "brca_tcga_pan_can_atlas_2018"
TCGA_PROFILE = "brca_tcga_pan_can_atlas_2018_rna_seq_v2_mrna_median_Zscores"
TCGA_SAMPLE_LIST = "brca_tcga_pan_can_atlas_2018_all"

METABRIC_STUDY = "brca_metabric"
# cBioPortal lists METABRIC z-scores under
# `brca_metabric_mrna_median_all_sample_Zscores`, NOT
# `brca_metabric_mrna_median_Zscores` (the latter pattern matches
# TCGA studies but not METABRIC). Verified live 2026-05-03.
METABRIC_PROFILE = "brca_metabric_mrna_median_all_sample_Zscores"
METABRIC_SAMPLE_LIST = "brca_metabric_all"

DATA_DIR = Path(__file__).resolve().parent / "data"
LOCK_PATH = DATA_DIR / ".fetch.lock"


# ── helpers ────────────────────────────────────────────────────────
def api_get(endpoint: str, params: dict | None = None, retries: int = 3):
    """GET with retries and back-off."""
    url = f"{BASE}{endpoint}"
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = 2 ** (attempt + 1)
                print(f"  rate-limited, waiting {wait}s ...")
                time.sleep(wait)
                continue
            print(f"  HTTP {r.status_code} for {url}")
            return None
        except requests.RequestException as exc:
            print(f"  request error (attempt {attempt+1}): {exc}")
            time.sleep(2)
    return None


def acquire_lock() -> bool:
    """PID-tagged lock per COLLAB_PROTOCOL § 9. Returns True on
    acquire, False if a live peer holds it."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        try:
            holder = int(LOCK_PATH.read_text().strip())
        except ValueError:
            holder = 0
        # Stale-clear if process is dead.
        if holder > 0:
            try:
                os.kill(holder, 0)
                print(f"  fetch lock held by live PID {holder} — abort")
                return False
            except ProcessLookupError:
                print(f"  stale lock from dead PID {holder} — clearing")
        LOCK_PATH.unlink(missing_ok=True)
    LOCK_PATH.write_text(f"{os.getpid()}\n")
    return True


def release_lock():
    LOCK_PATH.unlink(missing_ok=True)


def resolve_entrez_ids(symbols: list[str]) -> dict[str, int]:
    """Map HUGO symbols → Entrez gene IDs."""
    mapping = {}
    for sym in symbols:
        data = api_get(f"/genes/{sym}")
        if data and "entrezGeneId" in data:
            mapping[sym] = data["entrezGeneId"]
            print(f"  {sym} → {data['entrezGeneId']}")
        else:
            print(f"  WARNING: could not resolve {sym}")
    return mapping


def fetch_expression(profile_id: str, sample_list_id: str,
                     entrez_ids: dict[str, int]) -> pd.DataFrame:
    """Fetch z-score expression per gene; return wide-format frame."""
    print(f"  Fetching expression from {profile_id} ...")
    records = []
    for sym, eid in entrez_ids.items():
        data = api_get(
            f"/molecular-profiles/{profile_id}/molecular-data",
            params={"sampleListId": sample_list_id, "entrezGeneId": eid},
        )
        if data:
            for entry in data:
                records.append({
                    "sampleId": entry["sampleId"],
                    "gene": sym,
                    "value": entry.get("value"),
                })
            print(f"    {sym}: {len(data)} samples")
        else:
            print(f"    WARNING: no data for {sym}")
    if not records:
        return pd.DataFrame()
    df_long = pd.DataFrame(records)
    df_wide = df_long.pivot(index="sampleId", columns="gene", values="value").reset_index()
    cols = [g for g in GENE_PANEL if g in df_wide.columns]
    return df_wide[["sampleId"] + cols]


def fetch_clinical(study_id: str) -> pd.DataFrame:
    """Sample-level clinical attributes via paginated /clinical-data."""
    print(f"  Fetching clinical data for {study_id} ...")
    all_data = []
    page = 0
    page_size = 10000
    while True:
        data = api_get(
            f"/studies/{study_id}/clinical-data",
            params={
                "clinicalDataType": "SAMPLE",
                "pageSize": page_size,
                "pageNumber": page,
            },
        )
        if not data:
            break
        all_data.extend(data)
        if len(data) < page_size:
            break
        page += 1
    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data)
    df_wide = df.pivot_table(
        index="sampleId",
        columns="clinicalAttributeId",
        values="value",
        aggfunc="first",
    ).reset_index()
    return df_wide


def write_atomic(df: pd.DataFrame, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    df.to_csv(tmp, index=False)
    tmp.replace(path)
    print(f"  wrote {path.name} ({len(df)} rows)")


# ── main ───────────────────────────────────────────────────────────
def main() -> int:
    if not acquire_lock():
        return 2
    try:
        print("Resolving Entrez IDs for the 17-gene panel ...")
        entrez = resolve_entrez_ids(GENE_PANEL)
        if len(entrez) < len(GENE_PANEL):
            print(f"WARNING: only resolved {len(entrez)}/{len(GENE_PANEL)} genes")

        # TCGA-BRCA
        print(f"\n=== TCGA-BRCA ({TCGA_STUDY}) ===")
        tcga_expr = fetch_expression(TCGA_PROFILE, TCGA_SAMPLE_LIST, entrez)
        tcga_clin = fetch_clinical(TCGA_STUDY)
        write_atomic(tcga_expr, DATA_DIR / "tcga_brca_expression_zscores.csv")
        write_atomic(tcga_clin, DATA_DIR / "tcga_brca_clinical.csv")

        # METABRIC
        print(f"\n=== METABRIC ({METABRIC_STUDY}) ===")
        meta_expr = fetch_expression(
            METABRIC_PROFILE, METABRIC_SAMPLE_LIST, entrez
        )
        meta_clin = fetch_clinical(METABRIC_STUDY)
        write_atomic(meta_expr, DATA_DIR / "metabric_expression_zscores.csv")
        write_atomic(meta_clin, DATA_DIR / "metabric_clinical.csv")

        print("\nDone.")
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
