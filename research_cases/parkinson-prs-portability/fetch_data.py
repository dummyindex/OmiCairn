#!/usr/bin/env python3
"""
Fetcher stub for parkinson-prs-portability.
"""
from pathlib import Path
import json
import time

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"

ACCESSIONS = [
    {
        "name": "GCST90275127_kim",
        "url": "https://www.ebi.ac.uk/gwas/studies/GCST90275127",
        "role": "Kim 2024 multi-ancestry PD GWAS sumstats (primary)",
        "target": "./datasets/GCST90275127_kim/",
    },
    {
        "name": "GCST009325_nalls",
        "url": "https://www.ebi.ac.uk/gwas/studies/GCST009325",
        "role": "Nalls 2019 EUR-only PD GWAS sumstats (H4 head-to-head)",
        "target": "./datasets/GCST009325_nalls/",
    },
    {
        "name": "1000_genomes_phase3_v5",
        "url": "https://www.internationalgenome.org/data-portal/data-collection/phase-3",
        "role": "Individual-genotype substrate (2,504 individuals across 26 sub-populations)",
        "target": "./datasets/1000_genomes_phase3_v5/",
        "note": "VCF → Plink2 binary; QC: MAF>0.01, HWE>1e-6, missingness<0.02.",
    },
    {
        "name": "ldpred_funct",
        "url": "https://github.com/carlaml/LDpred-funct",
        "role": "LDpred-funct PRS construction tool + LD reference matrices",
        "target": "./datasets/ldpred_funct/",
    },
    {
        "name": "storm_2025_pqtl",
        "url": "https://academic.oup.com/brain/article-abstract/148/8/2412/",
        "role": "Storm 2025 pQTL-instrumented PD-target list (H3 proteinMR overlay)",
        "target": "./datasets/storm_2025_pqtl/",
    },
    {
        "name": "saffie_awad_2025_supp",
        "url": "https://www.nature.com/articles/s41531-025-",
        "role": "Saffie-Awad 2025 per-ancestry AUC benchmarks (Supp Table 3)",
        "target": "./datasets/saffie_awad_2025_supp/",
        "note": "Exact DOI / npj Parkinson's volume to be confirmed during READ; provisional.",
    },
]


def write_data_sources_doc():
    lines = ["# parkinson-prs-portability data sources", "",
             "Multi-ancestry PD PRS portability via 1000 Genomes + public",
             "sumstats. No UKB, no GP2 raw access.",
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
