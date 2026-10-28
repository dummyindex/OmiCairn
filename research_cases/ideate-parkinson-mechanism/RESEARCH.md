---
research_mode: DISCOVERY
domain: neurodegeneration_parkinsons
topic_slug: ideate-parkinson-mechanism


research_question: |
  Research purpose: understand the MECHANISM of Parkinson's disease (PD)
  — the molecular, cellular, circuit, and systems-level processes that
  drive dopaminergic neuron vulnerability and disease progression — and
  propose PROMISING FUTURE RESEARCH DIRECTIONS that are concrete enough
  for an autonomous computational pipeline to pursue on PUBLIC data.

  Targets:
  (1) Survey PD-mechanism evidence broadly: papers (single-cell/spatial
      atlases, genetics/GWAS, proteomics, alpha-synuclein biology,
      mitochondrial/lysosomal pathways, neuroinflammation, gut-brain
      axis), plus stated positions from leading PD researchers and
      relevant philosophy-of-medicine views on causation/biomarkers.
  (2) Propose directions that are NOVEL relative to the lab's EXISTING
      Parkinson's test cases (see the steering note for the current
      roster) — do not re-propose work already covered; find the gaps
      and the cross-cutting syntheses between them.
  (3) For each proposed direction, identify CANDIDATE PUBLIC DATASETS
      (named atlases / accessions / repositories: GEO, SRA, PRIDE,
      Allen, PPMI-derived public tables, etc.) that the direction would
      use, so the team can collect them. Put dataset pointers in the
      direction's rationale.

  Use the IDEATE brainstorm stage; this is a survey + proposal task, not
  a data-analysis task.

data:
  location: ./research_cases/ideate-parkinson-mechanism/data
  description: "No primary dataset — IDEATE surveys the PD-mechanism literature/landscape."
  modalities: [snrna_seq, spatial, proteomics, genetics]

success_criteria:
  primary_metric: not_applicable_at_ideate_stage
---

# ideate-parkinson-mechanism — IDEATE case for PD mechanism

Real research-survey IDEATE case. Run `omicairn ideate <ws>` to propose
mechanism-focused directions + candidate public datasets, then
`omicairn ideate <ws> --adopt <id>` to launch one. Collection targets are
tuned up in `omicairn_config.yaml` (this is a real survey, not a smoke test).
