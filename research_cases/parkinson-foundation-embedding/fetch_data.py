#!/usr/bin/env python3
"""
Fetcher stub for parkinson-foundation-embedding.
"""
from pathlib import Path
import json
import time

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"

ACCESSIONS = [
    {
        "name": "geneformer_95m",
        "url": "https://huggingface.co/ctheodoris/Geneformer",
        "role": "Geneformer 95M-cell weights (V2; primary foundation model)",
        "target": "./datasets/geneformer_95m/",
        "note": "Use ctheodoris/Geneformer-V2-95M variant; fp16 inference + chunked batches.",
    },
    {
        "name": "scgpt_human",
        "url": "https://github.com/bowang-lab/scGPT",
        "role": "scGPT human-pan weights (second foundation model)",
        "target": "./datasets/scgpt_human/",
    },
    {
        "name": "cellxgene_d5d0df8f_fullard",
        "url": "https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590",
        "role": "Primary embedding source (Fullard 2024 SNpc slab)",
        "target": "./datasets/cellxgene_d5d0df8f_fullard/",
    },
    {
        "name": "GSE178265",
        "url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE178265",
        "role": "Fine-tune label source (Kamath 2022 SNpc)",
        "target": "./datasets/GSE178265/",
    },
    {
        "name": "GSE184950",
        "url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE184950",
        "role": "Held-out transfer atlas (Wang 2024 SNpc)",
        "target": "./datasets/GSE184950/",
    },
    {
        "name": "GSE193688",
        "url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE193688",
        "role": "Held-out transfer atlas (Adams 2024 SN; RNA arm)",
        "target": "./datasets/GSE193688/",
    },
    {
        "name": "scEval_benchmark",
        "url": "https://github.com/raphael-group/scEval",
        "role": "log-CPM-PCA-LR baseline harness (Boiarsky 2024) for matched comparison",
        "target": "./datasets/scEval_benchmark/",
    },
]


def write_data_sources_doc():
    lines = ["# parkinson-foundation-embedding data sources", "",
             "Geneformer + scGPT vs. log-CPM-PCA-LR baseline on PD-vs-ctrl",
             "donor-level classification of Fullard SNpc DA nuclei.",
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
