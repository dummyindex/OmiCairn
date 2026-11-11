#!/usr/bin/env python3
"""
Fetcher stub for parkinson-lamda-multiregion.

Generalisation test for the Goralski 2024 cortical LAMDA spatial
signature against the Fullard / N M 2024 multi-region snRNA-seq
atlas. Heavy data fetched on the way during EXECUTE.
"""
from pathlib import Path
import json
import time

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"

ACCESSIONS = [
    {
        "name": "zenodo_10729767_goralski",
        "url": "https://zenodo.org/records/10729767",
        "role": "Signature derivation (LAMDA gene module + GeoMx/CosMx counts)",
        "content": "Goralski 2024 cingulate cortex spatial transcriptomic atlas; ~9,941 CosMx cells; 8 PD/PDD/DLB donors + PFF mouse",
        "alt_code": "https://zenodo.org/records/10732492 (notebook)",
        "target": "./datasets/zenodo_10729767_goralski/",
    },
    {
        "name": "cellxgene_d5d0df8f_fullard",
        "url": "https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590",
        "role": "Test substrate (Fullard 2024 multi-region atlas; 5 regions: DMNX/GPi/SNpc/M1/V1)",
        "content": "100 donors (75 PD + 25 ctrl); 2,096,155 nuclei",
        "alt_code": "https://github.com/DiseaseNeuroGenomics/AMP-PD_SciData",
        "target": "./datasets/cellxgene_d5d0df8f_fullard/",
    },
    {
        "name": "allen_brain_ref",
        "url": "https://celltypes.brain-map.org/api/v2/well_known_file_download/",
        "role": "Cross-region cell-type harmonisation reference",
        "content": "Hodge 2019 + Bakken 2021 human cortex + Linnarsson 2023 subcortex marker panels",
        "alt_code": None,
        "target": "./datasets/allen_brain_ref/",
    },
    {
        "name": "msigdb",
        "url": "https://www.gsea-msigdb.org/gsea/msigdb/human/",
        "role": "Functional sub-program annotation (Reactome + Hallmark)",
        "content": "MSigDB v2023.2.Hs",
        "alt_code": None,
        "target": "./datasets/msigdb/",
    },
]


def write_data_sources_doc():
    lines = ["# parkinson-lamda-multiregion data sources",
             "",
             "Goralski 2024 LAMDA signature → Fullard 2024 multi-region atlas.",
             "Nothing pre-staged; EXECUTE fetches everything on the way.",
             ""]
    for a in ACCESSIONS:
        lines += [f"## {a['name']}", "",
                  f"- Role: {a['role']}",
                  f"- Content: {a['content']}",
                  f"- Primary URL: {a['url']}"]
        if a["alt_code"]:
            lines.append(f"- Companion code: {a['alt_code']}")
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
