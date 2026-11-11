#!/usr/bin/env python3
"""
Fetcher stub for parkinson-scenic-regulons.

This case fetches all data ON-THE-WAY during EXECUTE per
RESEARCH.md's data.shared_cache convention. This script does NOT
pre-stage raw data on disk — it writes a DATA_SOURCES.md manifest
and a fetch_manifest.json that EXECUTE will consume.
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
        "role": "regulon source (Fullard 2024 multi-region atlas; use SNpc slab)",
        "donors": "100 (75 PD + 25 ctrl)",
        "target": "./datasets/cellxgene_d5d0df8f_fullard/",
    },
    {
        "name": "GSE157783",
        "url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE157783",
        "role": "replication atlas 1 (Smajic 2022 SNpc snRNA-seq)",
        "target": "./datasets/GSE157783/",
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
        "name": "scenic_resources",
        "url": "https://resources.aertslab.org/cistarget/",
        "role": "pySCENIC motif + cisTarget databases (human v10)",
        "target": "./datasets/scenic_resources/",
        "note": "Fetch hg38__refseq-r80__500bp_up_and_100bp_down_tss.mc9nr.feather + hg38__refseq-r80__10kb_up_and_down_tss.mc9nr.feather + motifs-v10nr_clust-nr.hgnc-m0.001-o0.0.tbl",
    },
    {
        "name": "GCST90275127_kim",
        "url": "https://www.ebi.ac.uk/gwas/studies/GCST90275127",
        "role": "Kim 2024 PD multi-ancestry GWAS sumstats + fine-mapping table",
        "target": "./datasets/GCST90275127_kim/",
    },
]


def write_data_sources_doc():
    lines = ["# parkinson-scenic-regulons data sources", "",
             "Cross-atlas replication of pySCENIC TF-regulons inferred from",
             "Fullard 2024 SNpc DA neurons; zero-shot AUCell scoring on",
             "Smajic / Wang / Adams held-out atlases.",
             ""]
    for a in ACCESSIONS:
        lines += [f"## {a['name']}", "",
                  f"- Role: {a['role']}",
                  f"- Primary URL: {a['url']}"]
        if "donors" in a:
            lines.append(f"- Donors: {a['donors']}")
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
