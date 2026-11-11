#!/usr/bin/env python3
"""
Fetcher stub for parkinson-plasma-prodromal-panel.
"""
from pathlib import Path
import json
import time

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"

ACCESSIONS = [
    {
        "name": "PXD046355_hallqvist",
        "url": "https://www.ebi.ac.uk/pride/archive/projects/PXD046355",
        "role": "Primary spectra (Hällqvist 2024 LC-MS/MS DIA plasma)",
        "content": "~250 DIA runs; 99 PD + 36 HC + 72 iRBD (18 conv / 54 non-conv) + 41 disease ctrl",
        "target": "./datasets/PXD046355_hallqvist/",
    },
    {
        "name": "gan_2025_UKB_supp",
        "url": "https://pubmed.ncbi.nlm.nih.gov/39979637/",
        "role": "Cross-platform direction-concordance reference (Gan/You 2025 UKB Olink)",
        "content": "Supplementary tables: per-protein incident-PD HR + p in UKB Olink Explore 1536",
        "target": "./datasets/gan_2025_UKB_supp/",
    },
    {
        "name": "bayati_2024_supp",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC10927779/",
        "role": "DDC sanity-check (Bayati 2024)",
        "content": "Supplementary tables: DDC PD-vs-HC effect-size across 7 cohorts × 3 platforms",
        "target": "./datasets/bayati_2024_supp/",
    },
    {
        "name": "uniprot_human_canonical",
        "url": "https://www.uniprot.org/proteomes/UP000005640",
        "role": "DIA-NN spectral library generation",
        "content": "SwissProt human canonical proteome FASTA",
        "target": "./datasets/uniprot_human_canonical/",
    },
]


def write_data_sources_doc():
    lines = ["# parkinson-plasma-prodromal-panel data sources", "",
             "Independent re-analysis of the Hällqvist 2024 8-protein plasma",
             "panel on the deposited DIA spectra. PRIDE PXD046355 is the only",
             "data dependency that is large (~10-30 GB) — fetched on the way.",
             "Supplementary tables are small and can be fetched as text.",
             ""]
    for a in ACCESSIONS:
        lines += [f"## {a['name']}", "",
                  f"- Role: {a['role']}",
                  f"- Primary URL: {a['url']}",
                  f"- Content: {a['content']}",
                  f"- Target path: `{a['target']}`",
                  ""]
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
