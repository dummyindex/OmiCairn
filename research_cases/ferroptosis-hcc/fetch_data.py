#!/usr/bin/env python3
"""
Fetch ferroptosis gene expression + clinical/survival data for HCC
from cBioPortal public REST API.

Discovery cohort : TCGA-LIHC (lihc_tcga_pan_can_atlas_2018)
Validation cohort: a second HCC study if available, otherwise a 70/30
                   random split of TCGA-LIHC.

Output (saved to ./data/):
  tcga_lihc_expression_zscores.csv   sampleId + 20 gene columns
  tcga_lihc_clinical.csv             sampleId + clinical attributes
  (if split) discovery_samples.txt / validation_samples.txt

Requires: requests, pandas
"""

import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

# ── configuration ──────────────────────────────────────────────────
BASE = "https://www.cbioportal.org/api"
HEADERS = {"Accept": "application/json"}
TIMEOUT = 60  # seconds per request

GENE_PANEL = [
    "GPX4", "SLC7A11", "ACSL4", "TFRC", "LPCAT3", "NCOA4",
    "FTH1", "FTL", "SLC3A2", "HMOX1", "NFS1", "CISD1", "CS",
    "ACSF2", "DPP4", "CDKN1A", "FANCD2", "CHAC1", "EMC2", "SAT1",
]

TCGA_STUDY = "lihc_tcga_pan_can_atlas_2018"
TCGA_PROFILE = "lihc_tcga_pan_can_atlas_2018_rna_seq_v2_mrna_median_Zscores"
TCGA_SAMPLE_LIST = "lihc_tcga_pan_can_atlas_2018_all"

# Candidate external HCC studies (in priority order)
CANDIDATE_VALIDATION_STUDIES = [
    "lihc_riken_jp_2020",
    "hcc_inserm_fr_2015",
    "lihc_amc_prv",
    "hepatocellular_carcinoma_msk",
]

DATA_DIR = Path(__file__).resolve().parent / "data"
RANDOM_SEED = 42
VALIDATION_FRACTION = 0.30


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
            # non-retryable error
            print(f"  HTTP {r.status_code} for {url}")
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
    Fetch z-score expression for the given genes.
    Uses the /molecular-profiles/{id}/molecular-data endpoint.
    Falls back to per-gene fetching if bulk fails.
    """
    id_to_sym = {v: k for k, v in entrez_ids.items()}
    all_ids = list(entrez_ids.values())

    # Try bulk fetch (comma-separated entrez IDs)
    print(f"  Fetching expression from {profile_id} ...")
    records = []

    # cBioPortal REST API: fetch per gene (most reliable)
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

    # Use the /clinical-data/samples endpoint with pagination
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

    # Also fetch patient-level clinical data (OS_STATUS, OS_MONTHS are often patient-level)
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
        # Map patient -> sample via patientId if present
        if "patientId" in df_p.columns:
            df_p = df_p[["patientId", "clinicalAttributeId", "value"]].drop_duplicates(
                subset=["patientId", "clinicalAttributeId"]
            )
            df_patient = df_p.pivot(
                index="patientId", columns="clinicalAttributeId", values="value"
            ).reset_index()

    # Merge: if we have both, join on patientId
    if not df_sample.empty and not df_patient.empty:
        # Derive patientId from sampleId if not present
        if "patientId" not in df_sample.columns:
            # For TCGA, patientId = sampleId minus last -01 suffix
            # Try to find it from sample data
            # Use PATIENT_ID column if it exists, else strip suffix
            df_sample["patientId"] = df_sample["sampleId"].str.rsplit("-", n=1).str[0]
        df_clinical = df_sample.merge(df_patient, on="patientId", how="left",
                                       suffixes=("", "_patient"))
    elif not df_sample.empty:
        df_clinical = df_sample
    else:
        # Only patient data — create sampleId approximation
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
    """Find the mRNA z-score profile for a study."""
    profiles = api_get(f"/studies/{study_id}/molecular-profiles")
    if not profiles:
        return None
    # Prefer z-scores
    for p in profiles:
        pid = p.get("molecularProfileId", "")
        if "zscores" in pid.lower() or "z_scores" in pid.lower():
            print(f"  Found z-score profile: {pid}")
            return pid
    # Fall back to any RNA-Seq profile
    for p in profiles:
        if p.get("molecularAlterationType") == "MRNA_EXPRESSION":
            pid = p["molecularProfileId"]
            print(f"  Fallback RNA profile: {pid}")
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


# ── main ───────────────────────────────────────────────────────────
def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Resolve Entrez gene IDs
    print("=== Resolving gene IDs ===")
    entrez_map = resolve_entrez_ids(GENE_PANEL)
    if len(entrez_map) < len(GENE_PANEL):
        missing = set(GENE_PANEL) - set(entrez_map.keys())
        print(f"WARNING: could not resolve {missing}. Proceeding with {len(entrez_map)} genes.")

    # 2. Fetch TCGA-LIHC expression
    print("\n=== Fetching TCGA-LIHC expression z-scores ===")
    tcga_expr = fetch_expression(TCGA_PROFILE, TCGA_SAMPLE_LIST, entrez_map)
    if tcga_expr.empty:
        print("ERROR: No expression data retrieved for TCGA-LIHC. Exiting.")
        sys.exit(1)
    print(f"  TCGA-LIHC expression: {tcga_expr.shape}")

    # 3. Fetch TCGA-LIHC clinical
    print("\n=== Fetching TCGA-LIHC clinical data ===")
    tcga_clin = fetch_clinical(TCGA_STUDY)
    if tcga_clin.empty:
        print("ERROR: No clinical data retrieved for TCGA-LIHC. Exiting.")
        sys.exit(1)
    print(f"  TCGA-LIHC clinical: {tcga_clin.shape}")

    # 4. Look for a validation cohort
    print("\n=== Searching for external validation cohort ===")
    val_study = None
    val_profile = None
    val_sample_list = None
    for candidate in CANDIDATE_VALIDATION_STUDIES:
        print(f"  Checking {candidate} ...")
        if check_study_exists(candidate):
            profile = find_rna_profile(candidate)
            if profile:
                slist = find_sample_list(candidate)
                if slist:
                    val_study = candidate
                    val_profile = profile
                    val_sample_list = slist
                    print(f"  -> Using {candidate} as validation cohort")
                    break
            else:
                print(f"    No RNA profile found for {candidate}")
        else:
            print(f"    Study {candidate} not found")

    # If external validation found, fetch it
    val_expr = pd.DataFrame()
    val_clin = pd.DataFrame()
    use_split = True  # default to split

    if val_study:
        print(f"\n=== Fetching validation cohort: {val_study} ===")
        val_expr = fetch_expression(val_profile, val_sample_list, entrez_map)
        if not val_expr.empty:
            val_clin = fetch_clinical(val_study)
            if not val_clin.empty:
                use_split = False
                print(f"  Validation expression: {val_expr.shape}")
                print(f"  Validation clinical:   {val_clin.shape}")
            else:
                print("  WARNING: No clinical data for validation study. Falling back to split.")
        else:
            print("  WARNING: No expression data for validation study. Falling back to split.")

    # 5. Save TCGA-LIHC data
    print("\n=== Saving data ===")
    tcga_expr_path = DATA_DIR / "tcga_lihc_expression_zscores.csv"
    tcga_clin_path = DATA_DIR / "tcga_lihc_clinical.csv"
    tcga_expr.to_csv(tcga_expr_path, index=False)
    tcga_clin.to_csv(tcga_clin_path, index=False)
    print(f"  Saved {tcga_expr_path}")
    print(f"  Saved {tcga_clin_path}")

    if not use_split:
        # Save external validation
        val_expr_path = DATA_DIR / f"{val_study}_expression_zscores.csv"
        val_clin_path = DATA_DIR / f"{val_study}_clinical.csv"
        val_expr.to_csv(val_expr_path, index=False)
        val_clin.to_csv(val_clin_path, index=False)
        print(f"  Saved {val_expr_path}")
        print(f"  Saved {val_clin_path}")
    else:
        # 70/30 random split
        print("\n  No external validation cohort available — creating 70/30 random split.")
        samples = tcga_expr["sampleId"].tolist()
        import random
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

    # 6. Write manifest
    manifest = {
        "study_discovery": TCGA_STUDY,
        "study_validation": val_study if not use_split else f"{TCGA_STUDY} (70/30 split)",
        "gene_panel": GENE_PANEL,
        "entrez_ids": entrez_map,
        "n_discovery_samples": int(tcga_expr.shape[0]) if use_split else int(tcga_expr.shape[0]),
        "n_validation_samples": int(val_expr.shape[0]) if not use_split else "see split files",
        "split_method": "external_cohort" if not use_split else "random_70_30",
        "random_seed": RANDOM_SEED if use_split else None,
        "files": {
            "tcga_expression": "tcga_lihc_expression_zscores.csv",
            "tcga_clinical": "tcga_lihc_clinical.csv",
        },
    }
    if not use_split:
        manifest["files"]["validation_expression"] = f"{val_study}_expression_zscores.csv"
        manifest["files"]["validation_clinical"] = f"{val_study}_clinical.csv"
    else:
        manifest["files"]["discovery_samples"] = "discovery_samples.txt"
        manifest["files"]["validation_samples"] = "validation_samples.txt"

    manifest_path = DATA_DIR / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"  Saved {manifest_path}")

    # Summary
    print("\n=== Done ===")
    print(f"  Genes resolved: {len(entrez_map)}/{len(GENE_PANEL)}")
    print(f"  TCGA-LIHC expression shape: {tcga_expr.shape}")
    print(f"  TCGA-LIHC clinical shape:   {tcga_clin.shape}")
    if not use_split:
        print(f"  Validation ({val_study}) expression shape: {val_expr.shape}")
        print(f"  Validation ({val_study}) clinical shape:   {val_clin.shape}")
    else:
        print(f"  Validation: 70/30 random split of TCGA-LIHC (seed={RANDOM_SEED})")


if __name__ == "__main__":
    main()
