#!/usr/bin/env python3
"""
Fetch pyroptosis gene expression + clinical/survival data for LUAD
from cBioPortal public REST API.

Discovery cohort : TCGA-LUAD (luad_tcga_pan_can_atlas_2018)
Validation cohorts: up to 2 independent LUAD cohorts from cBioPortal.

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
TIMEOUT = 90  # seconds per request

GENE_PANEL = [
    "SCAF11", "NOD1", "NLRP2", "NLRP1", "GPX4", "CASP8", "AIM2",
    "GSDMD", "GSDME", "CASP1", "CASP3", "CASP4", "IL1B", "IL18",
    "NLRP3", "PYCARD",
]

TCGA_STUDY = "luad_tcga_pan_can_atlas_2018"
TCGA_PROFILE = "luad_tcga_pan_can_atlas_2018_rna_seq_v2_mrna_median_Zscores"
TCGA_SAMPLE_LIST = "luad_tcga_pan_can_atlas_2018_all"

# Candidate external LUAD studies (in priority order)
CANDIDATE_VALIDATION_STUDIES = [
    "luad_oncosg_2020",
    "luad_cas_2020",
    "lung_msk_2017",
    "nsclc_tcga_broad_2016",
    "nsclc_pd1_msk_2018",
    "luad_broad",
    "luad_mskcc_2015",
    "luad_tsp",
    "nsclc_mskcc_2018",
]

DATA_DIR = Path(__file__).resolve().parent / "data"
RANDOM_SEED = 42
VALIDATION_FRACTION = 0.30


# -- helpers ---------------------------------------------------------------
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
            # non-retryable error
            print(f"  HTTP {r.status_code} for {url}")
            return None
        except requests.RequestException as exc:
            print(f"  request error (attempt {attempt+1}): {exc}")
            time.sleep(2)
    return None


def api_post(endpoint: str, body: dict, retries: int = 3):
    """POST with retries and back-off."""
    url = f"{BASE}{endpoint}"
    for attempt in range(retries):
        try:
            r = requests.post(
                url, headers={**HEADERS, "Content-Type": "application/json"},
                json=body, timeout=TIMEOUT,
            )
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = 2 ** (attempt + 1)
                print(f"  rate-limited, waiting {wait}s ...")
                time.sleep(wait)
                continue
            print(f"  HTTP {r.status_code} for POST {url}: {r.text[:200]}")
            return None
        except requests.RequestException as exc:
            print(f"  request error (attempt {attempt+1}): {exc}")
            time.sleep(2)
    return None


def resolve_entrez_ids(symbols: list[str]) -> dict[str, int]:
    """Map HUGO symbols -> Entrez gene IDs via cBioPortal /api/genes."""
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
                     entrez_ids: dict[str, int]) -> pd.DataFrame:
    """
    Fetch z-score expression for the given genes using per-gene fetching.
    """
    print(f"  Fetching expression from {profile_id} ...")
    records = []

    for sym, eid in entrez_ids.items():
        data = api_get(
            f"/molecular-profiles/{profile_id}/molecular-data",
            params={
                "sampleListId": sample_list_id,
                "entrezGeneId": eid,
            },
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

    # Re-order columns: sampleId first, then genes in panel order
    gene_cols = [g for g in GENE_PANEL if g in df_wide.columns]
    df_wide = df_wide[["sampleId"] + gene_cols]
    return df_wide


def fetch_clinical(study_id: str) -> pd.DataFrame:
    """Fetch clinical attributes for all samples in a study."""
    print(f"  Fetching clinical data for {study_id} ...")

    # Fetch sample-level clinical data with pagination
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

    # Also fetch patient-level clinical data (OS_STATUS, OS_MONTHS often here)
    page = 0
    patient_data = []
    while True:
        data = api_get(
            f"/studies/{study_id}/clinical-data",
            params={
                "clinicalDataType": "PATIENT",
                "pageSize": page_size,
                "pageNumber": page,
            },
        )
        if not data:
            break
        patient_data.extend(data)
        if len(data) < page_size:
            break
        page += 1

    if not all_data and not patient_data:
        return pd.DataFrame()

    # Pivot sample-level data
    df_sample = pd.DataFrame()
    if all_data:
        df_s = pd.DataFrame(all_data)
        df_s = df_s[["sampleId", "clinicalAttributeId", "value"]].drop_duplicates(
            subset=["sampleId", "clinicalAttributeId"]
        )
        df_sample = df_s.pivot(
            index="sampleId", columns="clinicalAttributeId", values="value"
        ).reset_index()

    # Pivot patient-level data
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

    # Merge sample + patient level data
    if not df_sample.empty and not df_patient.empty:
        if "patientId" not in df_sample.columns:
            # Derive patientId from sampleId (TCGA convention: strip last -XX suffix)
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
    """Check whether a cBioPortal study exists."""
    data = api_get(f"/studies/{study_id}")
    return data is not None and "studyId" in (data if isinstance(data, dict) else {})


def find_rna_profile(study_id: str) -> str | None:
    """Find the mRNA z-score or expression profile for a study."""
    profiles = api_get(f"/studies/{study_id}/molecular-profiles")
    if not profiles:
        return None

    # Print all profiles for debugging
    print(f"    Available profiles for {study_id}:")
    for p in profiles:
        pid = p.get("molecularProfileId", "")
        mtype = p.get("molecularAlterationType", "")
        print(f"      {pid} ({mtype})")

    # Prefer z-scores
    for p in profiles:
        pid = p.get("molecularProfileId", "")
        if "zscores" in pid.lower() or "z_scores" in pid.lower():
            print(f"  -> Selected z-score profile: {pid}")
            return pid

    # Fall back to any MRNA_EXPRESSION profile
    for p in profiles:
        if p.get("molecularAlterationType") == "MRNA_EXPRESSION":
            pid = p["molecularProfileId"]
            print(f"  -> Fallback RNA profile: {pid}")
            return pid

    return None


def find_sample_list(study_id: str) -> str | None:
    """Find the 'all samples' list for a study."""
    lists = api_get(f"/studies/{study_id}/sample-lists")
    if not lists:
        return None
    for sl in lists:
        sid = sl.get("sampleListId", "")
        if sid.endswith("_all"):
            return sid
    # fallback: first list
    return lists[0]["sampleListId"] if lists else None


def check_survival_data(df_clinical: pd.DataFrame) -> dict:
    """Check what survival columns are available."""
    survival_cols = {
        "OS_STATUS": False,
        "OS_MONTHS": False,
        "DFS_STATUS": False,
        "DFS_MONTHS": False,
        "PFS_STATUS": False,
        "PFS_MONTHS": False,
    }
    for col in survival_cols:
        # Check both direct column and _patient suffix
        if col in df_clinical.columns or f"{col}_patient" in df_clinical.columns:
            survival_cols[col] = True
    return survival_cols


def make_study_slug(study_id: str) -> str:
    """Convert study ID to a filesystem-friendly slug."""
    return study_id.replace("-", "_")


# -- main -----------------------------------------------------------------
def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Resolve Entrez gene IDs
    print("=== Resolving gene IDs ===")
    entrez_map = resolve_entrez_ids(GENE_PANEL)
    if len(entrez_map) < len(GENE_PANEL):
        missing = set(GENE_PANEL) - set(entrez_map.keys())
        print(f"WARNING: could not resolve {missing}. Proceeding with {len(entrez_map)} genes.")

    # 2. Fetch TCGA-LUAD expression
    print("\n=== Fetching TCGA-LUAD expression z-scores (Discovery) ===")
    tcga_expr = fetch_expression(TCGA_PROFILE, TCGA_SAMPLE_LIST, entrez_map)
    if tcga_expr.empty:
        print("ERROR: No expression data retrieved for TCGA-LUAD. Exiting.")
        sys.exit(1)
    print(f"  TCGA-LUAD expression: {tcga_expr.shape}")

    # 3. Fetch TCGA-LUAD clinical
    print("\n=== Fetching TCGA-LUAD clinical data ===")
    tcga_clin = fetch_clinical(TCGA_STUDY)
    if tcga_clin.empty:
        print("ERROR: No clinical data retrieved for TCGA-LUAD. Exiting.")
        sys.exit(1)
    print(f"  TCGA-LUAD clinical: {tcga_clin.shape}")
    survival_info = check_survival_data(tcga_clin)
    print(f"  Survival columns: {survival_info}")

    # Save TCGA discovery data
    tcga_expr_path = DATA_DIR / "tcga_luad_expression_zscores.csv"
    tcga_clin_path = DATA_DIR / "tcga_luad_clinical.csv"
    tcga_expr.to_csv(tcga_expr_path, index=False)
    tcga_clin.to_csv(tcga_clin_path, index=False)
    print(f"  Saved {tcga_expr_path}")
    print(f"  Saved {tcga_clin_path}")

    # 4. Search for validation cohorts
    print("\n=== Searching for external validation cohorts ===")
    validation_cohorts = []
    files_dict = {
        "tcga_luad_expression": "tcga_luad_expression_zscores.csv",
        "tcga_luad_clinical": "tcga_luad_clinical.csv",
    }

    for candidate in CANDIDATE_VALIDATION_STUDIES:
        if len(validation_cohorts) >= 2:
            break
        print(f"\n  Checking {candidate} ...")
        if not check_study_exists(candidate):
            print(f"    Study {candidate} not found on cBioPortal")
            continue

        profile = find_rna_profile(candidate)
        if not profile:
            print(f"    No RNA profile found for {candidate}")
            continue

        slist = find_sample_list(candidate)
        if not slist:
            print(f"    No sample list found for {candidate}")
            continue

        print(f"\n=== Fetching validation cohort: {candidate} ===")
        val_expr = fetch_expression(profile, slist, entrez_map)
        if val_expr.empty:
            print(f"    No expression data for {candidate}")
            continue

        val_clin = fetch_clinical(candidate)
        if val_clin.empty:
            print(f"    No clinical data for {candidate}")
            continue

        val_survival = check_survival_data(val_clin)
        has_os = val_survival.get("OS_STATUS", False) and val_survival.get("OS_MONTHS", False)

        slug = make_study_slug(candidate)
        val_expr_path = DATA_DIR / f"{slug}_expression_zscores.csv"
        val_clin_path = DATA_DIR / f"{slug}_clinical.csv"
        val_expr.to_csv(val_expr_path, index=False)
        val_clin.to_csv(val_clin_path, index=False)
        print(f"  Saved {val_expr_path}")
        print(f"  Saved {val_clin_path}")

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

        print(f"  Validation cohort {len(validation_cohorts)}: {candidate}")
        print(f"    Expression: {val_expr.shape}")
        print(f"    Clinical:   {val_clin.shape}")
        print(f"    Has OS data: {has_os}")

    # 5. If fewer than 2 external cohorts, add a random split of TCGA-LUAD
    cohort_names = ["tcga_luad_discovery"]
    split_created = False

    if len(validation_cohorts) < 1:
        # No external validation at all -- must split TCGA
        print("\n  No external validation cohort found — creating 70/30 random split of TCGA-LUAD.")
        import random
        samples = tcga_expr["sampleId"].tolist()
        rng = random.Random(RANDOM_SEED)
        rng.shuffle(samples)
        n_val = int(len(samples) * VALIDATION_FRACTION)
        val_samples = sorted(samples[:n_val])
        disc_samples = sorted(samples[n_val:])

        disc_path = DATA_DIR / "discovery_samples.txt"
        val_path = DATA_DIR / "validation_samples.txt"
        with open(disc_path, "w") as f:
            f.write("\n".join(disc_samples) + "\n")
        with open(val_path, "w") as f:
            f.write("\n".join(val_samples) + "\n")
        print(f"  Discovery: {len(disc_samples)} samples -> {disc_path}")
        print(f"  Validation: {len(val_samples)} samples -> {val_path}")

        files_dict["discovery_samples"] = "discovery_samples.txt"
        files_dict["validation_samples"] = "validation_samples.txt"
        cohort_names.append("tcga_luad_validation_split")
        split_created = True
    else:
        for vc in validation_cohorts:
            cohort_names.append(vc["study_id"])

    # 6. Write manifest
    print("\n=== Writing manifest ===")
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

    manifest_path = DATA_DIR / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"  Saved {manifest_path}")

    # Summary
    print("\n=== SUMMARY ===")
    print(f"  Genes resolved: {len(entrez_map)}/{len(GENE_PANEL)}")
    print(f"  Discovery (TCGA-LUAD): {tcga_expr.shape[0]} samples, "
          f"{len([g for g in GENE_PANEL if g in tcga_expr.columns])} genes")
    for vc in validation_cohorts:
        print(f"  Validation ({vc['study_id']}): {vc['n_samples_expr']} samples, "
              f"{len(vc['genes_available'])} genes, OS data: {vc['has_os_data']}")
    if split_created:
        print(f"  Validation: 70/30 split of TCGA-LUAD (seed={RANDOM_SEED})")
    print(f"\n  Total cohorts: {len(cohort_names)}")
    print(f"  Data saved to: {DATA_DIR}")


if __name__ == "__main__":
    main()
