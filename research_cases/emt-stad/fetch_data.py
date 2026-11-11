#!/usr/bin/env python3
"""
Fetch EMT (epithelial-mesenchymal transition) gene expression + clinical/
survival data for gastric cancer (STAD) from cBioPortal public REST API.

Discovery cohort : TCGA-STAD (stad_tcga_pan_can_atlas_2018)
Validation cohort: OncoSG Gastric Cancer 2018 (stad_oncosg_2018)

The EMT gene panel is based on published EMT-related prognostic signatures
in gastric cancer (Xu et al. BioMed Res Int 2021; Zhang et al. Front Genet
2021; He et al. Front Immunol 2022). We use a 20-gene panel spanning core
EMT transcription factors, mesenchymal markers, and EMT-effector genes
that have been repeatedly linked to gastric cancer prognosis.

Output (saved to ./data/):
  {study}_expression_zscores.csv   sampleId + gene columns
  {study}_clinical.csv             sampleId + clinical attributes
  manifest.json                    sample counts, gene availability

Requires: requests, pandas
"""

import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

# -- configuration ---------------------------------------------------------
BASE = "https://www.cbioportal.org/api"
HEADERS = {"Accept": "application/json"}
TIMEOUT = 90

# 20-gene EMT panel for gastric cancer
# Core EMT transcription factors: SNAI1, SNAI2, TWIST1, ZEB1, ZEB2
# Mesenchymal markers: VIM, CDH2, FN1, ACTA2
# EMT effectors/ECM: SERPINE1, MMP2, MMP9, COL1A1, COL3A1, SPARC
# Signaling: TGFB1, TGFB2, WNT5A
# Published gastric-specific: THBS2, PLOD2
GENE_PANEL = [
    "SNAI1", "SNAI2", "TWIST1", "ZEB1", "ZEB2",
    "VIM", "CDH2", "FN1", "ACTA2",
    "SERPINE1", "MMP2", "MMP9", "COL1A1", "COL3A1", "SPARC",
    "TGFB1", "TGFB2", "WNT5A",
    "THBS2", "PLOD2",
]

TCGA_STUDY = "stad_tcga_pan_can_atlas_2018"
TCGA_PROFILE = "stad_tcga_pan_can_atlas_2018_rna_seq_v2_mrna_median_Zscores"
TCGA_SAMPLE_LIST = "stad_tcga_pan_can_atlas_2018_all"

CANDIDATE_VALIDATION_STUDIES = [
    "stad_oncosg_2018",
]

DATA_DIR = Path(__file__).resolve().parent / "data"
RANDOM_SEED = 42
VALIDATION_FRACTION = 0.30


# -- helpers ---------------------------------------------------------------
def api_get(endpoint: str, params: dict | None = None, retries: int = 3):
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


def resolve_entrez_ids(symbols: list[str]) -> dict[str, int]:
    mapping = {}
    for sym in symbols:
        data = api_get(f"/genes/{sym}")
        if data and "entrezGeneId" in data:
            mapping[sym] = data["entrezGeneId"]
            print(f"  {sym} -> {data['entrezGeneId']}")
        else:
            print(f"  WARNING: could not resolve {sym}")
    return mapping


def fetch_expression(profile_id: str, sample_list_id: str,
                     entrez_ids: dict[str, int],
                     gene_panel: list[str]) -> pd.DataFrame:
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
    df_wide = df_long.pivot(index="sampleId", columns="gene", values="value")
    df_wide = df_wide.reset_index()
    gene_cols = [g for g in gene_panel if g in df_wide.columns]
    df_wide = df_wide[["sampleId"] + gene_cols]
    return df_wide


def fetch_clinical(study_id: str) -> pd.DataFrame:
    print(f"  Fetching clinical data for {study_id} ...")
    all_data = []
    page = 0
    page_size = 10000
    while True:
        data = api_get(
            f"/studies/{study_id}/clinical-data",
            params={"clinicalDataType": "SAMPLE", "pageSize": page_size, "pageNumber": page},
        )
        if not data:
            break
        all_data.extend(data)
        if len(data) < page_size:
            break
        page += 1

    page = 0
    patient_data = []
    while True:
        data = api_get(
            f"/studies/{study_id}/clinical-data",
            params={"clinicalDataType": "PATIENT", "pageSize": page_size, "pageNumber": page},
        )
        if not data:
            break
        patient_data.extend(data)
        if len(data) < page_size:
            break
        page += 1

    if not all_data and not patient_data:
        return pd.DataFrame()

    df_sample = pd.DataFrame()
    if all_data:
        df_s = pd.DataFrame(all_data)
        df_s = df_s[["sampleId", "clinicalAttributeId", "value"]].drop_duplicates(
            subset=["sampleId", "clinicalAttributeId"]
        )
        df_sample = df_s.pivot(
            index="sampleId", columns="clinicalAttributeId", values="value"
        ).reset_index()

    df_patient = pd.DataFrame()
    if patient_data:
        df_p = pd.DataFrame(patient_data)
        if "patientId" in df_p.columns:
            df_p = df_p[["patientId", "clinicalAttributeId", "value"]].drop_duplicates(
                subset=["patientId", "clinicalAttributeId"]
            )
            df_patient = df_p.pivot(
                index="patientId", columns="clinicalAttributeId", values="value"
            ).reset_index()

    if not df_sample.empty and not df_patient.empty:
        if "patientId" not in df_sample.columns:
            df_sample["patientId"] = df_sample["sampleId"].str.rsplit("-", n=1).str[0]
        df_clinical = df_sample.merge(df_patient, on="patientId", how="left",
                                       suffixes=("", "_patient"))
    elif not df_sample.empty:
        df_clinical = df_sample
    else:
        df_clinical = df_patient
        if "patientId" in df_clinical.columns:
            df_clinical = df_clinical.rename(columns={"patientId": "sampleId"})

    print(f"    {len(df_clinical)} samples with clinical data")
    return df_clinical


def check_study_exists(study_id: str) -> bool:
    data = api_get(f"/studies/{study_id}")
    return data is not None and "studyId" in (data if isinstance(data, dict) else {})


def find_rna_profile(study_id: str) -> str | None:
    profiles = api_get(f"/studies/{study_id}/molecular-profiles")
    if not profiles:
        return None
    for p in profiles:
        pid = p.get("molecularProfileId", "")
        if "zscores" in pid.lower() or "z_scores" in pid.lower():
            print(f"  -> Selected z-score profile: {pid}")
            return pid
    for p in profiles:
        if p.get("molecularAlterationType") == "MRNA_EXPRESSION":
            pid = p["molecularProfileId"]
            print(f"  -> Fallback RNA profile: {pid}")
            return pid
    return None


def find_sample_list(study_id: str) -> str | None:
    lists = api_get(f"/studies/{study_id}/sample-lists")
    if not lists:
        return None
    for sl in lists:
        sid = sl.get("sampleListId", "")
        if sid.endswith("_all"):
            return sid
    return lists[0]["sampleListId"] if lists else None


def check_survival_data(df_clinical: pd.DataFrame) -> dict:
    survival_cols = {
        "OS_STATUS": False, "OS_MONTHS": False,
        "DFS_STATUS": False, "DFS_MONTHS": False,
        "PFS_STATUS": False, "PFS_MONTHS": False,
    }
    for col in survival_cols:
        if col in df_clinical.columns or f"{col}_patient" in df_clinical.columns:
            survival_cols[col] = True
    return survival_cols


def make_study_slug(study_id: str) -> str:
    return study_id.replace("-", "_")


# -- main -----------------------------------------------------------------
def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("=== Resolving gene IDs ===")
    entrez_map = resolve_entrez_ids(GENE_PANEL)
    if len(entrez_map) < len(GENE_PANEL):
        missing = set(GENE_PANEL) - set(entrez_map.keys())
        print(f"WARNING: could not resolve {missing}. Proceeding with {len(entrez_map)} genes.")

    print("\n=== Fetching TCGA-STAD expression z-scores (Discovery) ===")
    tcga_expr = fetch_expression(TCGA_PROFILE, TCGA_SAMPLE_LIST, entrez_map, GENE_PANEL)
    if tcga_expr.empty:
        print("ERROR: No expression data retrieved for TCGA-STAD. Exiting.")
        sys.exit(1)
    print(f"  TCGA-STAD expression: {tcga_expr.shape}")

    print("\n=== Fetching TCGA-STAD clinical data ===")
    tcga_clin = fetch_clinical(TCGA_STUDY)
    if tcga_clin.empty:
        print("ERROR: No clinical data retrieved for TCGA-STAD. Exiting.")
        sys.exit(1)
    survival_info = check_survival_data(tcga_clin)

    tcga_expr.to_csv(DATA_DIR / "tcga_stad_expression_zscores.csv", index=False)
    tcga_clin.to_csv(DATA_DIR / "tcga_stad_clinical.csv", index=False)
    print(f"  Saved TCGA-STAD data.")

    print("\n=== Searching for external validation cohorts ===")
    validation_cohorts = []
    files_dict = {
        "tcga_stad_expression": "tcga_stad_expression_zscores.csv",
        "tcga_stad_clinical": "tcga_stad_clinical.csv",
    }

    for candidate in CANDIDATE_VALIDATION_STUDIES:
        if len(validation_cohorts) >= 2:
            break
        print(f"\n  Checking {candidate} ...")
        if not check_study_exists(candidate):
            print(f"    Study {candidate} not found")
            continue

        profile = find_rna_profile(candidate)
        if not profile:
            print(f"    No RNA profile found for {candidate}")
            continue

        slist = find_sample_list(candidate)
        if not slist:
            print(f"    No sample list found for {candidate}")
            continue

        val_expr = fetch_expression(profile, slist, entrez_map, GENE_PANEL)
        if val_expr.empty:
            print(f"    No expression data for {candidate}")
            continue

        val_clin = fetch_clinical(candidate)
        if val_clin.empty:
            print(f"    No clinical data for {candidate}")
            continue

        val_survival = check_survival_data(val_clin)
        has_os = val_survival.get("OS_STATUS", False) and val_survival.get("OS_MONTHS", False)

        # Require at least 50 expression samples for a useful validation cohort
        if val_expr.shape[0] < 50:
            print(f"    Only {val_expr.shape[0]} expression samples — too few for validation, skipping.")
            continue

        slug = make_study_slug(candidate)
        val_expr.to_csv(DATA_DIR / f"{slug}_expression_zscores.csv", index=False)
        val_clin.to_csv(DATA_DIR / f"{slug}_clinical.csv", index=False)

        validation_cohorts.append({
            "study_id": candidate,
            "profile_id": profile,
            "sample_list_id": slist,
            "n_samples_expr": int(val_expr.shape[0]),
            "n_samples_clin": int(val_clin.shape[0]),
            "genes_available": [g for g in GENE_PANEL if g in val_expr.columns],
            "has_os_data": has_os,
            "survival_columns": val_survival,
        })
        files_dict[f"{slug}_expression"] = f"{slug}_expression_zscores.csv"
        files_dict[f"{slug}_clinical"] = f"{slug}_clinical.csv"

    # If no external validation, create random split
    cohort_names = ["tcga_stad_discovery"]
    split_created = False

    if len(validation_cohorts) < 1:
        print("\n  No external validation found — creating 70/30 split.")
        import random
        samples = tcga_expr["sampleId"].tolist()
        rng = random.Random(RANDOM_SEED)
        rng.shuffle(samples)
        n_val = int(len(samples) * VALIDATION_FRACTION)
        val_samples = sorted(samples[:n_val])
        disc_samples = sorted(samples[n_val:])
        with open(DATA_DIR / "discovery_samples.txt", "w") as f:
            f.write("\n".join(disc_samples) + "\n")
        with open(DATA_DIR / "validation_samples.txt", "w") as f:
            f.write("\n".join(val_samples) + "\n")
        files_dict["discovery_samples"] = "discovery_samples.txt"
        files_dict["validation_samples"] = "validation_samples.txt"
        cohort_names.append("tcga_stad_validation_split")
        split_created = True
    else:
        for vc in validation_cohorts:
            cohort_names.append(vc["study_id"])

    # Write manifest
    manifest = {
        "study_discovery": TCGA_STUDY,
        "discovery_profile": TCGA_PROFILE,
        "gene_panel": GENE_PANEL,
        "entrez_ids": entrez_map,
        "genes_resolved": len(entrez_map),
        "genes_requested": len(GENE_PANEL),
        "n_discovery_samples_expr": int(tcga_expr.shape[0]),
        "n_discovery_samples_clin": int(tcga_clin.shape[0]),
        "discovery_genes_available": [g for g in GENE_PANEL if g in tcga_expr.columns],
        "discovery_survival_columns": check_survival_data(tcga_clin),
        "validation_cohorts": validation_cohorts,
        "split_fallback_used": split_created,
        "random_seed": RANDOM_SEED if split_created else None,
        "cohort_names": cohort_names,
        "files": files_dict,
    }
    with open(DATA_DIR / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    print("\n=== SUMMARY ===")
    print(f"  Genes resolved: {len(entrez_map)}/{len(GENE_PANEL)}")
    print(f"  Discovery (TCGA-STAD): {tcga_expr.shape[0]} samples")
    for vc in validation_cohorts:
        print(f"  Validation ({vc['study_id']}): {vc['n_samples_expr']} samples, OS: {vc['has_os_data']}")
    if split_created:
        print(f"  Validation: 70/30 split (seed={RANDOM_SEED})")
    print(f"  Data saved to: {DATA_DIR}")


if __name__ == "__main__":
    main()
