#!/usr/bin/env python3
"""
Fetcher stub for parkinson-tcell-foothold.
"""
from pathlib import Path
import json
import time

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"

ACCESSIONS = [
    {
        "name": "ma_2025_SN_Tcell",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC12322057/",
        "preprint": "https://www.biorxiv.org/content/10.1101/2024.01.08.574736v3",
        "role": "Signature derivation: clonally expanded CD8+ T cell transcriptomic module (Ma 2025)",
        "content": "44 donors / 265,284 nuclei + Visium + scTCR-seq across PD SN + cingulate",
        "target": "./datasets/ma_2025_SN_Tcell/",
        "note": "EXECUTE must read Data Availability of PMC12322057 to confirm GEO/Synapse ID. Falls back to Supplementary Data T-cell gene module if accession unreachable.",
    },
    {
        "name": "GSE178265",
        "url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE178265",
        "role": "replication atlas 1 (Kamath 2022 SNpc + caudate)",
        "target": "./datasets/GSE178265/",
    },
    {
        "name": "GSE184950",
        "url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE184950",
        "role": "replication atlas 2 (Wang 2024 SNpc)",
        "target": "./datasets/GSE184950/",
    },
    {
        "name": "GSE193688",
        "url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE193688",
        "role": "replication atlas 3 (Adams 2024 SN — RNA arm)",
        "target": "./datasets/GSE193688/",
    },
    {
        "name": "cellxgene_d5d0df8f_fullard",
        "url": "https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590",
        "role": "replication atlas 4 + H2 region-specificity test (Fullard 5-region atlas)",
        "target": "./datasets/cellxgene_d5d0df8f_fullard/",
    },
    {
        "name": "GSE243639",
        "url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE243639",
        "role": "H4 spatial validation (Martirosyan 2024 Visium portion)",
        "target": "./datasets/GSE243639/",
    },
    {
        "name": "msigdb",
        "url": "https://www.gsea-msigdb.org/gsea/msigdb/human/",
        "role": "H5 exhaustion-module negative control gene list",
        "target": "./datasets/msigdb/",
    },
]


def write_data_sources_doc():
    lines = ["# parkinson-tcell-foothold data sources", "",
             "Cross-atlas replication of Ma 2025 PD-SN T-cell transcriptomic",
             "signature on 4 atlases that lack paired TCR-seq.",
             ""]
    for a in ACCESSIONS:
        lines += [f"## {a['name']}", "",
                  f"- Role: {a['role']}",
                  f"- Primary URL: {a['url']}"]
        if "preprint" in a:
            lines.append(f"- Preprint: {a['preprint']}")
        if "content" in a:
            lines.append(f"- Content: {a['content']}")
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
    print("Nothing fetched; EXECUTE will fetch on-the-way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
