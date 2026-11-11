---
research_mode: DISCOVERY
domain: neurodegeneration_alzheimers
topic_slug: ideate-alzheimer-mechanism


research_question: |
  Research purpose: understand the MECHANISM of Alzheimer's disease (AD)
  — the molecular, cellular, circuit, and systems-level processes that
  drive selective neuronal vulnerability and disease progression
  (amyloid-β, tau/NFTs, APOE & genetics/GWAS, microglia &
  neuroinflammation, synaptic loss, vascular/BBB dysfunction,
  lysosomal-autophagy, astrocytes) — and propose PROMISING FUTURE
  RESEARCH DIRECTIONS that are concrete enough for an autonomous
  computational pipeline to pursue on PUBLIC data.

  Targets:
  (1) Survey AD-mechanism evidence broadly: papers (single-cell/spatial
      atlases, genetics/GWAS, proteomics, amyloid-β biology, tau/NFT
      pathology, microglia/neuroinflammation, synaptic loss, vascular/BBB,
      lysosomal-autophagy, astrocytes, selective neuronal vulnerability
      e.g. entorhinal cortex / EC), plus stated positions from leading
      AD researchers and relevant philosophy-of-medicine views on
      causation/biomarkers (e.g. the amyloid-cascade debate).
  (2) Propose directions that are NOVEL relative to the lab's EXISTING
      test cases (see the steering note for the current roster) — do not
      re-propose work already covered; find the gaps and the
      cross-cutting syntheses between them.
  (3) For each proposed direction, identify CANDIDATE PUBLIC DATASETS
      (named atlases / accessions / repositories: GEO, SRA, PRIDE,
      Synapse, cellxgene, EBI, Allen, ADNI-derived public tables, etc.)
      that the direction would use, so the team can collect them. Put
      dataset pointers in the direction's rationale.

      IMPORTANT — downloadability: prefer OPENLY-DOWNLOADABLE datasets
      (GEO supplementary, Synapse-open, cellxgene, EBI) so that the
      downstream EXECUTE stage can actually fetch the data. For any
      dataset that is access-controlled (dbGaP, controlled ROSMAP,
      controlled SEA-AD), EXPLICITLY FLAG it as access-controlled so the
      team knows fetching requires an application. As examples it may
      consider, well-known OPEN human AD single-cell/transcriptomic
      datasets (treat as candidates and VERIFY accessions — do not assume):
      Mathys 2019 (prefrontal cortex snRNA-seq), Grubman 2019
      (candidate GSE138852, entorhinal cortex), Lau 2020 (candidate
      GSE157827, prefrontal cortex), Leng 2021 (entorhinal + superior
      frontal gyrus), Morabito 2021 (candidate GSE174367, snRNA + snATAC),
      Gerrits 2021, Sayed 2021.

  Use the IDEATE brainstorm stage; this is a survey + proposal task, not
  a data-analysis task.

data:
  location: ./research_cases/ideate-alzheimer-mechanism/data
  description: "No primary dataset — IDEATE surveys the AD-mechanism literature/landscape."
  modalities: [snrna_seq, spatial, proteomics, genetics]

success_criteria:
  primary_metric: not_applicable_at_ideate_stage
---

# ideate-alzheimer-mechanism — IDEATE case for AD mechanism

Real research-survey IDEATE case. Run `omicairn ideate <ws>` to propose
mechanism-focused directions + candidate public datasets, then
`omicairn ideate <ws> --adopt <id>` to launch one. Collection targets are
tuned up in `omicairn_config.yaml` (this is a real survey, not a smoke test).
