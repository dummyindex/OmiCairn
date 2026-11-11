#!/usr/bin/env python3
"""
Fetcher stub for parkinson-cellchat-immune-axis.
"""
from pathlib import Path
import json
import time

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"

ACCESSIONS = [
    {
        "name": "GSE223138",
        "url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE223138",
        "role": "Peripheral scRNA-seq (Hong 2025 PD PBMC)",
        "target": "./datasets/GSE223138/",
    },
    {
        "name": "ma_2025_SN_Tcell",
        "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12322057/",
        "role": "Brain snRNA + scTCR + Visium (Ma 2025 PD SN)",
        "target": "./datasets/ma_2025_SN_Tcell/",
        "note": "Confirm exact GEO / Synapse accession in Data Availability statement. Fallback: drop brain CD8+T to prior using only Ma 2025 Fig. 3 L-R list.",
    },
    {
        "name": "cellxgene_d5d0df8f_fullard",
        "url": "https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590",
        "role": "Brain microglia substrate (Fullard 2024 SN microglia slab)",
        "target": "./datasets/cellxgene_d5d0df8f_fullard/",
    },
    {
        "name": "cellchatdb_v2",
        "url": "https://github.com/jinworks/CellChat",
        "role": "CellChatDB v2 L-R interaction list (consumed via LIANA-py)",
        "target": "./datasets/cellchatdb_v2/",
    },
    {
        "name": "liana_py",
        "url": "https://github.com/saezlab/liana-py",
        "role": "LIANA-py consensus L-R inference (CellPhoneDB + CellChat + NATMI + Connectome + scTalk + NicheNet)",
        "target": "./datasets/liana_py/",
    },
    {
        "name": "hong_2025_supp",
        "url": "https://www.nature.com/articles/s41531-025-",
        "role": "Hong 2025 Supp Table 2 (28 MR-implicated immune genes)",
        "target": "./datasets/hong_2025_supp/",
        "note": "Exact DOI / npj Parkinson's volume to be confirmed during READ; provisional.",
    },
    {
        "name": "onek1k_eqtl",
        "url": "https://onek1k.org/",
        "role": "OneK1K reference eQTL (Yazar 2022; PBMC cell-type calibration)",
        "target": "./datasets/onek1k_eqtl/",
    },
]


def write_data_sources_doc():
    lines = ["# parkinson-cellchat-immune-axis data sources", "",
             "LIANA-py consensus L-R inference on Hong 2025 PBMC vs. Ma 2025",
             "brain SN immune axis; pre-registered direction-specificity control.",
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
