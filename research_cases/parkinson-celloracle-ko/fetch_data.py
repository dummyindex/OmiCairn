#!/usr/bin/env python3
"""
Fetcher stub for parkinson-celloracle-ko.
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
        "role": "snRNA-seq input + observed PD-vs-ctrl DA centroid shift (Fullard 2024 SNpc slab)",
        "target": "./datasets/cellxgene_d5d0df8f_fullard/",
    },
    {
        "name": "GSE193688",
        "url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE193688",
        "role": "Multiome ATAC fraction for atlas-specific base-GRN (Adams 2024)",
        "target": "./datasets/GSE193688/",
    },
    {
        "name": "celloracle_basegrn",
        "url": "https://zenodo.org/records/7755357",
        "role": "CellOracle midbrain reference base-GRN (H5 sensitivity baseline)",
        "target": "./datasets/celloracle_basegrn/",
    },
    {
        "name": "GCST90275127_kim",
        "url": "https://www.ebi.ac.uk/gwas/studies/GCST90275127",
        "role": "Kim 2024 multi-ancestry PD GWAS + fine-mapping Supp Table S5",
        "target": "./datasets/GCST90275127_kim/",
        "note": "Supp Table S5 lists posterior probabilities of causal association for top genes.",
    },
    {
        "name": "hemedan_2024_boolean",
        "url": "https://gitlab.lcsb.uni.lu/lcsb-biocore/publications/hemedan23-boolean-modelling-of-pd",
        "role": "Hemedan 2024 Boolean PD prodromal-cluster module gene list (H3 enrichment)",
        "target": "./datasets/hemedan_2024_boolean/",
    },
]


def write_data_sources_doc():
    lines = ["# parkinson-celloracle-ko data sources", "",
             "In-silico KO of Kim 2024 top-10 PD-risk genes via CellOracle, ",
             "scored against observed Fullard SNpc DA PD-vs-ctrl shift.",
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
