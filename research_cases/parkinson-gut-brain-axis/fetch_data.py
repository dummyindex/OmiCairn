#!/usr/bin/env python3
"""
Fetcher stub for parkinson-gut-brain-axis.
"""
from pathlib import Path
import json
import time

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"

ACCESSIONS = [
    {
        "name": "wallen_2022_zenodo",
        "url": "https://zenodo.org/records/7246185",
        "role": "PD gut shotgun metagenomic post-QC + MetaCyc pathway tables",
        "content": "Wallen 2022; 490 PD + 234 ctrl; NovaSeq 6000 ~40M reads/sample",
        "alt": "SRA PRJNA834801 (raw FASTQ, ~3-5 TB; NOT fetched)",
        "code": "https://github.com/zackmwallen/PD_metagenomics_2022",
        "target": "./datasets/wallen_2022_zenodo/",
    },
    {
        "name": "cellxgene_d5d0df8f_fullard",
        "url": "https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590",
        "role": "Fullard 2024 multi-region atlas — DMNX slab is primary; SNpc + V1 controls",
        "content": "100 donors (75 PD + 25 ctrl); DMNX ~ 200-400k nuclei",
        "code": "https://github.com/DiseaseNeuroGenomics/AMP-PD_SciData",
        "target": "./datasets/cellxgene_d5d0df8f_fullard/",
    },
    {
        "name": "storm_2025_mr_proteins",
        "url": "https://github.com/catherinestorm/MR_proteins_neurodegeneration",
        "role": "Protein-MR causal proteins (H4 triangulation)",
        "content": "Storm 2025 supplementary MR table; UKB Olink × Nalls 2019",
        "target": "./datasets/storm_2025_mr_proteins/",
    },
    {
        "name": "PXD046355_hallqvist",
        "url": "https://www.ebi.ac.uk/pride/archive/projects/PXD046355",
        "role": "Hällqvist 2024 plasma proteomic 8-panel (H4 triangulation)",
        "content": "~250 LC-MS/MS DIA runs across 99 PD + 36 HC + 72 iRBD + 41 disease-ctrl",
        "target": "./datasets/PXD046355_hallqvist/",
    },
    {
        "name": "kegg",
        "url": "https://rest.kegg.jp/",
        "role": "KEGG human + bacterial pathway maps for H1 bridging",
        "target": "./datasets/kegg/",
    },
    {
        "name": "reactome",
        "url": "https://reactome.org/download-data",
        "role": "Reactome v90 GMT for Fullard DMNX gene-pathway enrichment",
        "target": "./datasets/reactome/",
    },
]


def write_data_sources_doc():
    lines = ["# parkinson-gut-brain-axis data sources", "",
             "Cohort-bridging multi-omics integration test. No individual",
             "donor overlap between Wallen 2022 (fecal) and Fullard 2024 (brain).",
             "Tests SIGNATURE-LEVEL alignment via pathway-bridging, not",
             "per-individual mediation.",
             ""]
    for a in ACCESSIONS:
        lines += [f"## {a['name']}", "",
                  f"- Role: {a['role']}",
                  f"- Primary URL: {a['url']}"]
        if "content" in a:
            lines.append(f"- Content: {a['content']}")
        if "code" in a:
            lines.append(f"- Companion code: {a['code']}")
        if "alt" in a:
            lines.append(f"- Alt: {a['alt']}")
        lines += [f"- Target path: `{a['target']}`", ""]
    lines.append(f"_Manifest written {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}._")
    (DATA_DIR / "DATA_SOURCES.md").write_text("\n".join(lines))
    print("wrote DATA_SOURCES.md")


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    write_data_sources_doc()
    (DATA_DIR / "fetch_manifest.json").write_text(json.dumps(ACCESSIONS, indent=2))
    print("Nothing fetched; EXECUTE will fetch on-the-way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
