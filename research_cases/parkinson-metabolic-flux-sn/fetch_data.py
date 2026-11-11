#!/usr/bin/env python3
"""
Fetcher stub for parkinson-metabolic-flux-sn.
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
        "role": "snRNA-seq input for DA + microglia pseudobulk (Fullard 2024 SNpc slab)",
        "target": "./datasets/cellxgene_d5d0df8f_fullard/",
    },
    {
        "name": "recon3d",
        "url": "https://github.com/SBRG/Recon3D",
        "role": "Recon3D human GEM SBML (primary metabolic model)",
        "target": "./datasets/recon3d/",
    },
    {
        "name": "human_gem",
        "url": "https://github.com/SysBioChalmers/Human-GEM",
        "role": "Human-GEM SBML (cross-validation metabolic model)",
        "target": "./datasets/human_gem/",
    },
    {
        "name": "idopaneuro1",
        "url": "https://doi.org/10.1101/2021.06.30.450562",
        "role": "iDopaNeuro1 DA-neuron-context model (Preciat 2021); Zenodo TBD",
        "target": "./datasets/idopaneuro1/",
        "note": "If Zenodo link unreachable, fall back to Recon3D+iMAT and document in Limitations.",
    },
    {
        "name": "timbr",
        "url": "https://github.com/marinmas/TIMBR",
        "role": "TIMBR Python implementation (Yamamori 2024)",
        "target": "./datasets/timbr/",
    },
    {
        "name": "shao_2024_metabolomics",
        "url": "https://www.nature.com/articles/s41531-024-00732-z",
        "role": "Shao 2024 npj-PD metabolomics meta-analysis Supp Table 1 (PD-altered metabolites)",
        "target": "./datasets/shao_2024_metabolomics/",
    },
    {
        "name": "supandi_2018_cortex",
        "url": "https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0203687",
        "role": "Supandi 2018 cortical-bulk Lsei-FBA flux predictions (Supp Tables 1-3)",
        "target": "./datasets/supandi_2018_cortex/",
    },
    {
        "name": "hong_2025_pdiron",
        "url": "https://pubmed.ncbi.nlm.nih.gov/?term=Hong+Parkinson+iron+2025",
        "role": "Hong 2025 PD-iron supplementary table (iron metabolism markers)",
        "target": "./datasets/hong_2025_pdiron/",
        "note": "Exact PMC ID to be confirmed during READ; provisional.",
    },
]


def write_data_sources_doc():
    lines = ["# parkinson-metabolic-flux-sn data sources", "",
             "Constraint-based metabolic flux modelling of Fullard 2024 PD",
             "SNpc DA-neuron pseudobulk via iMAT + TIMBR + E-Flux.",
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
