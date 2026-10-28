#!/usr/bin/env python3
"""
Fetcher stub for parkinson-multimodal-spatial-proteomics.
"""
from pathlib import Path
import json
import time

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"

ACCESSIONS = [
    {
        "name": "GSE243639",
        "url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE243639",
        "role": "snRNA-seq + Visium (Martirosyan 2024 PD SN)",
        "target": "./datasets/GSE243639/",
    },
    {
        "name": "ma_2025_geomx",
        "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12322057/",
        "role": "GeoMx spatial (Ma 2025 SN). Confirm exact NCBI accession in Data Availability.",
        "target": "./datasets/ma_2025_geomx/",
        "fallback": "https://zenodo.org/records/10729767",
        "note": "If Ma 2025 GeoMx unreachable, use Goralski Zenodo 10.5281/zenodo.10729767 cingulate GeoMx as fallback.",
    },
    {
        "name": "PXD030142_zhu",
        "url": "https://www.ebi.ac.uk/pride/archive/projects/PXD030142",
        "role": "Zhu 2024 DLPFC LC-MS/MS proteomics",
        "target": "./datasets/PXD030142_zhu/",
    },
    {
        "name": "goralski_lamda",
        "url": "https://zenodo.org/records/10729767",
        "role": "Goralski LAMDA cortical signature gene list (H3 enrichment reference)",
        "target": "./datasets/goralski_lamda/",
    },
    {
        "name": "hemedan_2024_boolean",
        "url": "https://gitlab.lcsb.uni.lu/lcsb-biocore/publications/hemedan23-boolean-modelling-of-pd",
        "role": "Hemedan 2024 Boolean-PD key-regulator gene list (H3 enrichment reference)",
        "target": "./datasets/hemedan_2024_boolean/",
    },
]


def write_data_sources_doc():
    lines = ["# parkinson-multimodal-spatial-proteomics data sources", "",
             "Three-modality MOFA+ joint embedding of PD SN: snRNA + Visium",
             "(Martirosyan 2024) + GeoMx (Ma 2025) + LC-MS/MS proteomics",
             "(Zhu 2024). Pre-registered pairwise-vs-3-modal ablation.",
             ""]
    for a in ACCESSIONS:
        lines += [f"## {a['name']}", "",
                  f"- Role: {a['role']}",
                  f"- Primary URL: {a['url']}"]
        if "fallback" in a:
            lines.append(f"- Fallback: {a['fallback']}")
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
