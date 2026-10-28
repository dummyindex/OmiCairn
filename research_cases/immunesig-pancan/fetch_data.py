#!/usr/bin/env python3
"""
Fetch pan-cancer TCGA expression + clinical data for an immune
gene panel from cBioPortal public REST API.

Cohort: 33 TCGA PanCancer Atlas 2018 studies, ~10,000 samples.
Restricts expression to a curated immune gene panel (~60 genes
spanning cytotoxic T, regulatory T, NK, dendritic, MHC class I/II,
checkpoint, cytokines) drawn from the ImmPort + Thorsson 2018
"Immune Landscape" canonical lists. The OmiCairn agent can subset
further within RESEARCH.md's `~400 immune-related genes`
flexibility window.

Output (saved to ./data/):
  pancancer_expression_zscores.csv     sampleId + immune genes
                                        cancerType column appended
  pancancer_clinical.csv               sampleId + clinical/survival
  cancer_codes.txt                     one TCGA cancer code per line

Concurrency: takes data/.fetch.lock per COLLAB_PROTOCOL § 9.

Requires: requests, pandas. ~30 min, ~2-3 GB.
"""

import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

BASE = "https://www.cbioportal.org/api"
HEADERS = {"Accept": "application/json"}
TIMEOUT = 60

# 33 TCGA PanCancer Atlas 2018 cancer codes (lowercase). Each
# resolves to study id `<code>_tcga_pan_can_atlas_2018`.
TCGA_PANCAN_CODES = [
    "acc", "blca", "brca", "cesc", "chol", "coadread", "dlbc",
    "esca", "gbm", "hnsc", "kich", "kirc", "kirp", "laml",
    "lgg", "lihc", "luad", "lusc", "meso", "ov", "paad",
    "pcpg", "prad", "sarc", "skcm", "stad", "tgct", "thca",
    "thym", "ucec", "ucs", "uvm",
    # NOTE: some codes (acrotic-melanoma, mesothelioma) not all
    # covered by PanCan Atlas — skip if /studies returns 404.
]

# Curated immune gene panel (~60 genes) spanning functional
# categories. Drawn from ImmPort core + Thorsson 2018 Immune
# Landscape canonical signatures. The OmiCairn agent may subset.
IMMUNE_PANEL = [
    # Cytotoxic / effector T cell
    "CD8A", "CD8B", "GZMA", "GZMB", "GZMK", "PRF1", "GNLY", "NKG7",
    # IFN-gamma response
    "IFNG", "IFNGR1", "STAT1", "IRF1", "TBX21", "EOMES",
    # T regulatory
    "FOXP3", "IL2RA", "CTLA4", "TNFRSF18", "TIGIT",
    # T helper
    "CD4", "GATA3", "RORC", "BCL6", "TBX21",
    # NK cell
    "KLRD1", "KLRC1", "NCR1", "NCR3", "FCGR3A",
    # Dendritic / antigen presentation
    "ITGAX", "CD1C", "CLEC4C", "FLT3",
    # MHC class I
    "B2M", "HLA-A", "HLA-B", "HLA-C", "TAP1", "TAP2",
    # MHC class II
    "HLA-DRA", "HLA-DRB1", "HLA-DPA1", "HLA-DPB1", "HLA-DQA1", "HLA-DQB1",
    # Immune checkpoint
    "PDCD1", "CD274", "LAG3", "HAVCR2", "TIGIT", "VSIR",
    # Cytokines / chemokines
    "CXCL9", "CXCL10", "CXCL11", "CCL2", "CCL3", "CCL4", "CCL5",
    "IL6", "TNF", "IL10", "TGFB1",
    # B cell
    "CD19", "CD20", "MS4A1", "CD79A",
    # Macrophage / myeloid
    "CD68", "CD163", "ITGAM", "MRC1",
]

DATA_DIR = Path(__file__).resolve().parent / "data"
LOCK_PATH = DATA_DIR / ".fetch.lock"


def api_get(endpoint: str, params: dict | None = None, retries: int = 3):
    url = f"{BASE}{endpoint}"
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = 2 ** (attempt + 1)
                print(f"    rate-limited, waiting {wait}s ...")
                time.sleep(wait)
                continue
            if r.status_code == 404:
                return None
            print(f"    HTTP {r.status_code} for {url}")
            return None
        except requests.RequestException as exc:
            print(f"    request error (attempt {attempt+1}): {exc}")
            time.sleep(2)
    return None


def acquire_lock() -> bool:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        try:
            holder = int(LOCK_PATH.read_text().strip())
        except ValueError:
            holder = 0
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
    mapping = {}
    for sym in symbols:
        data = api_get(f"/genes/{sym}")
        if data and "entrezGeneId" in data:
            mapping[sym] = data["entrezGeneId"]
        else:
            print(f"    WARNING: could not resolve {sym}")
    print(f"  resolved {len(mapping)}/{len(symbols)} immune-panel genes")
    return mapping


def fetch_study_expression(
    code: str, entrez_ids: dict[str, int]
) -> pd.DataFrame:
    profile = f"{code}_tcga_pan_can_atlas_2018_rna_seq_v2_mrna_median_Zscores"
    sample_list = f"{code}_tcga_pan_can_atlas_2018_all"
    records = []
    for sym, eid in entrez_ids.items():
        data = api_get(
            f"/molecular-profiles/{profile}/molecular-data",
            params={"sampleListId": sample_list, "entrezGeneId": eid},
        )
        if data:
            for entry in data:
                records.append({
                    "sampleId": entry["sampleId"],
                    "gene": sym,
                    "value": entry.get("value"),
                })
    if not records:
        return pd.DataFrame()
    df_long = pd.DataFrame(records)
    df_wide = df_long.pivot(
        index="sampleId", columns="gene", values="value"
    ).reset_index()
    df_wide["cancerType"] = code.upper()
    return df_wide


def fetch_study_clinical(code: str) -> pd.DataFrame:
    study_id = f"{code}_tcga_pan_can_atlas_2018"
    all_data = []
    page = 0
    while True:
        data = api_get(
            f"/studies/{study_id}/clinical-data",
            params={
                "clinicalDataType": "SAMPLE",
                "pageSize": 10000,
                "pageNumber": page,
            },
        )
        if not data:
            break
        all_data.extend(data)
        if len(data) < 10000:
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
    df_wide["cancerType"] = code.upper()
    return df_wide


def write_atomic(df: pd.DataFrame, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    df.to_csv(tmp, index=False)
    tmp.replace(path)
    print(f"  wrote {path.name} ({len(df)} rows)")


def main() -> int:
    if not acquire_lock():
        return 2
    try:
        print("Resolving Entrez IDs for the immune panel ...")
        entrez = resolve_entrez_ids(IMMUNE_PANEL)
        if not entrez:
            print("ERROR: zero immune genes resolved. Aborting.")
            return 1

        all_expr = []
        all_clin = []
        good_codes = []
        for code in TCGA_PANCAN_CODES:
            print(f"\n=== {code.upper()} ===")
            study_id = f"{code}_tcga_pan_can_atlas_2018"
            study_check = api_get(f"/studies/{study_id}")
            if not study_check:
                print(f"  study {study_id} not found — skipping")
                continue
            expr = fetch_study_expression(code, entrez)
            if expr.empty:
                print(f"  no expression for {code} — skipping")
                continue
            clin = fetch_study_clinical(code)
            print(f"  expr: {len(expr)} samples; clin: {len(clin)} samples")
            all_expr.append(expr)
            all_clin.append(clin)
            good_codes.append(code)

        if not all_expr:
            print("ERROR: no studies fetched successfully")
            return 1
        print(f"\nConcatenating {len(all_expr)} cancer types ...")
        full_expr = pd.concat(all_expr, ignore_index=True, sort=False)
        full_clin = pd.concat(all_clin, ignore_index=True, sort=False)

        write_atomic(full_expr, DATA_DIR / "pancancer_expression_zscores.csv")
        write_atomic(full_clin, DATA_DIR / "pancancer_clinical.csv")
        codes_path = DATA_DIR / "cancer_codes.txt"
        codes_path.write_text("\n".join(good_codes) + "\n")
        print(f"  wrote {codes_path.name} ({len(good_codes)} codes)")

        print("\nDone.")
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
