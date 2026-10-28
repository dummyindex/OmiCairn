#!/usr/bin/env python3
"""
Fetcher stub for parkinson-lincs-reversal.
"""
from pathlib import Path
import json
import time

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"

ACCESSIONS = [
    {
        "name": "lincs_l1000",
        "url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE92742",
        "alt": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE70138",
        "role": "LINCS L1000 Phase II Level 5 signatures (HDF5; ~5 GB combined)",
        "target": "./datasets/lincs_l1000/",
        "note": "Fetch both GSE92742 (touchstone) and GSE70138 (expansion); concat with cmapPy.",
    },
    {
        "name": "clue_metadata",
        "url": "https://clue.io/api",
        "role": "LINCS chemical metadata (MoA, ATC class, ChEMBL ID)",
        "target": "./datasets/clue_metadata/",
    },
    {
        "name": "cellxgene_d5d0df8f_fullard",
        "url": "https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590",
        "role": "PD-vs-ctrl signature source (Fullard 2024 SNpc + microglia pseudobulk)",
        "target": "./datasets/cellxgene_d5d0df8f_fullard/",
    },
    {
        "name": "opentargets_2406",
        "url": "https://platform.opentargets.org/downloads",
        "role": "Open Targets release 24.06 (PD MONDO:0005180 target scores; ~1 GB JSON)",
        "target": "./datasets/opentargets_2406/",
    },
    {
        "name": "tang_2024_omicsynth",
        "url": "https://www.cell.com/iscience/fulltext/S2589-0042(24)00018-1",
        "role": "omicSynth Tang 2024 PD druggable target list",
        "target": "./datasets/tang_2024_omicsynth/",
    },
    {
        "name": "bayati_2024_supp",
        "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10927779/",
        "role": "Bayati 2024 PD-DDC biomarker supplementary",
        "target": "./datasets/bayati_2024_supp/",
    },
    {
        "name": "storm_2025_pqtl",
        "url": "https://academic.oup.com/brain/article-abstract/148/8/2412/",
        "role": "Storm 2025 protein-MR pQTL-instrumented PD-target list",
        "target": "./datasets/storm_2025_pqtl/",
    },
]


def write_data_sources_doc():
    lines = ["# parkinson-lincs-reversal data sources", "",
             "LINCS L1000 connectivity-reversal scoring of Fullard SNpc-DA",
             "PD-vs-ctrl transcriptomic signature.",
             ""]
    for a in ACCESSIONS:
        lines += [f"## {a['name']}", "",
                  f"- Role: {a['role']}",
                  f"- Primary URL: {a['url']}"]
        if "alt" in a:
            lines.append(f"- Alt: {a['alt']}")
        if "note" in a:
            lines.append(f"- Note: {a['note']}")
        lines += [f"- Target path: `{a['target']}`", ""]
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
