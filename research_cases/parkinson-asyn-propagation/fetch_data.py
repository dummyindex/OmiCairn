#!/usr/bin/env python3
"""
Fetcher stub for parkinson-asyn-propagation.
"""
from pathlib import Path
import json
import time

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"

ACCESSIONS = [
    {
        "name": "cellxgene_d5d0df8f_fullard",
        "url": "https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590",
        "role": "5-region snRNA-seq atlas for per-region SNCA + cell-type fractions (Fullard 2024)",
        "target": "./datasets/cellxgene_d5d0df8f_fullard/",
        "note": "Use all 5 regions: DMNX, SNpc, GPi (or cingulate), M1, V1.",
    },
    {
        "name": "allen_human_connectivity",
        "url": "https://human.brain-map.org/",
        "role": "Allen Human Brain Connectivity Atlas projection strengths (connectome matrix)",
        "target": "./datasets/allen_human_connectivity/",
        "note": "Fetch via allensdk Python client; row-normalise + log-transform projection strengths.",
    },
    {
        "name": "goralski_lamda",
        "url": "https://zenodo.org/records/10729767",
        "role": "Cingulate spatial Lewy-burden profile for H3 cell-type prediction (Goralski 2024)",
        "target": "./datasets/goralski_lamda/",
    },
    {
        "name": "henderson_2019_mouse",
        "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6372945/",
        "role": "Henderson 2019 mouse-atlas SIR-connectome parameter prior (H5 prior initialisation)",
        "target": "./datasets/henderson_2019_mouse/",
    },
]


def write_data_sources_doc():
    lines = ["# parkinson-asyn-propagation data sources", "",
             "Multi-scale SIR + connectome model of α-synuclein spread across",
             "the Fullard 2024 5-region atlas, validated against Braak rank-",
             "order and Goralski LAMDA cingulate cell-type ranking.",
             ""]
    for a in ACCESSIONS:
        lines += [f"## {a['name']}", "",
                  f"- Role: {a['role']}",
                  f"- Primary URL: {a['url']}"]
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
