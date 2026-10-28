#!/usr/bin/env python3
"""
Fetcher stub for parkinson-multimodal-ppmi-trajectory.

This case fetches only published supplementary tables (no raw
PPMI individual-level data). Total fetch ~200 MB.
"""
from pathlib import Path
import json
import time

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"

ACCESSIONS = [
    {
        "name": "russo_2025_supp",
        "url": "https://www.thelancet.com/journals/laneur/",
        "role": "Russo 2025 Lancet Neurology CSF SAA Vmax/Tmax/AUC group means",
        "target": "./datasets/russo_2025_supp/",
        "note": "Exact DOI to be confirmed during READ; provisional 10.1016/S1474-4422(25)00094-2.",
    },
    {
        "name": "bayati_2024_supp",
        "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10927779/",
        "role": "Bayati 2024 Acta Neuropathologica Olink + CSF + plasma + urine protein medians",
        "target": "./datasets/bayati_2024_supp/",
    },
    {
        "name": "hallqvist_2024_supp",
        "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11189460/",
        "role": "Hällqvist 2024 Nat Commun 8-protein plasma panel by visit (supp tables only)",
        "target": "./datasets/hallqvist_2024_supp/",
    },
    {
        "name": "burciu_2023_supp",
        "url": "https://www.frontiersin.org/journals/neurology",
        "role": "Burciu 2023 Frontiers Neurology per-visit free-water dMRI group means",
        "target": "./datasets/burciu_2023_supp/",
    },
    {
        "name": "simuni_2024_supp",
        "url": "https://movementdisorders.onlinelibrary.wiley.com/journal/15318257",
        "role": "Simuni 2024 Movement Disorders LRRK2-G2019S SAA-stratified group-mean curves",
        "target": "./datasets/simuni_2024_supp/",
    },
    {
        "name": "marek_2018_supp",
        "url": "https://onlinelibrary.wiley.com/doi/10.1002/acn3.644",
        "role": "Marek 2018 + downstream PPMI baseline + longitudinal MDS-UPDRS-III group means",
        "target": "./datasets/marek_2018_supp/",
    },
]


def write_data_sources_doc():
    lines = ["# parkinson-multimodal-ppmi-trajectory data sources", "",
             "GPLVM-based 1-D disease-time inference from publicly-summarised",
             "longitudinal PPMI endpoints. No individual-level data, no DUA.",
             "Total ~200 MB.",
             ""]
    for a in ACCESSIONS:
        lines += [f"## {a['name']}", "",
                  f"- Role: {a['role']}",
                  f"- Primary URL: {a['url']}"]
        if "note" in a:
            lines.append(f"- Note: {a['note']}")
        lines += [f"- Target path: `{a['target']}`", ""]
    lines.append("## Fetchability audit (H5)")
    lines.append("")
    lines.append("EXECUTE must classify each supplement as one of:")
    lines.append("- (a) machine-readable TSV / XLSX")
    lines.append("- (b) PDF-table only (requires `tabula-py` or similar)")
    lines.append("- (c) image-only / not fetchable")
    lines.append("")
    lines.append(f"_Manifest written {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}._")
    (DATA_DIR / "DATA_SOURCES.md").write_text("\n".join(lines))
    print("wrote DATA_SOURCES.md")


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    write_data_sources_doc()
    (DATA_DIR / "fetch_manifest.json").write_text(json.dumps(ACCESSIONS, indent=2))
    print("wrote fetch_manifest.json")
    print("Nothing fetched; EXECUTE will fetch on-the-way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
