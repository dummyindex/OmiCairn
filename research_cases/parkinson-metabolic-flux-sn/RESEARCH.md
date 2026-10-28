---
research_mode: DISCOVERY
domain: neurodegeneration_metabolic_flux
topic_slug: parkinson-metabolic-flux-sn

research_question: |
  When the human genome-scale metabolic model Recon3D / Human-GEM
  is contextualised to PD-vs-control substantia-nigra pseudobulk
  expression (Fullard 2024 atlas, DA-neuron subset) via iMAT, do
  the predicted flux changes recapitulate the Supandi 2018
  cortical-bulk findings (decreased glycolysis, decreased TCA flux
  midway, increased lactate) *specifically inside dopaminergic
  neurons* — a cell type that bulk microarray averages over?

  Supandi 2018 used PLOS One bulk microarray on PD cortex and
  predicted lower glycolytic and TCA flux. Their bulk preparation
  could not address SNpc DA neurons specifically. The 2024
  Yamamori TIMBR paper (J Biotechnol Adv) extended GEM-based PD
  metabolic-biomarker prediction across 13 SN datasets, but
  remained bulk. Single-cell snRNA-seq atlases now make a
  DA-neuron-context constraint-based flux analysis possible. This
  OmiCairn run is the first to attempt it on Fullard data.

  Hypotheses (pre-registered):

    H1 (primary — cortex-bulk concordance + DA glycolysis):
        In Recon3D contextualised by iMAT on Fullard SNpc DA
        pseudobulk, the PD-vs-control predicted flux change in
        cytoplasmic glycolysis (reactions PGK / HEX1 / PFK / ENO1
        + 4 supporting reactions) is negative, with median signed
        flux ratio < 0.9 and bootstrap 95% CI excluding 1.0
        (10,000 donor bootstraps). Sign concordance with
        Supandi 2018 cortical-bulk predictions on shared
        reactions is ≥ 70% (binomial test p < 0.01 against null
        0.5).

    H2 (secondary — TIMBR biomarker recovery):
        TIMBR-predicted top-10 PD biomarker metabolites
        (Yamamori 2024) recover ≥ 4 metabolites whose abundance
        is also flagged by the Shao 2024 npj-PD plasma+CSF
        metabolomics meta-analysis (Supplementary Table 1) —
        a hypergeometric q < 0.05 cross-resource check.

    H3 (secondary — iron-metabolism elevation):
        Iron-metabolism flux (FECH-, FTH1-, TFRC-dependent
        reactions) is elevated in DA-neuron-context PD-vs-control
        flux ratio > 1.1 with bootstrap 95% CI excluding 1.0,
        consistent with the iron-accumulation vulnerability
        hypothesis (Hong 2025 PD-iron).

    H4 (secondary — contextualisation-method concordance):
        Repeat the analysis with three contextualisation methods —
        iMAT, TIMBR, E-Flux — and report Spearman ρ across
        per-reaction PD-vs-ctrl flux changes. ρ ≥ 0.5 between any
        two methods is the minimum agreement bar. (Per the v2
        risk register, this is the dominant failure mode for
        constraint-based PD flux work.)

    H5 (secondary — DA-vs-glia cell-type separation):
        Repeat the analysis with Fullard microglia pseudobulk
        instead of DA-neuron pseudobulk. If H1's glycolysis-down
        finding is reproduced *only* in DA neurons and not in
        microglia, the bulk Supandi finding is DA-neuron-driven.
        If it is reproduced in both, bulk Supandi was averaging
        an organ-wide signal. Both outcomes are informative.

  Null payoff. A null DA-neuron H1 (median flux ratio ≥ 0.9 or
  bootstrap CI overlapping 1.0) means Supandi's bulk findings
  were driven by non-DA cell types (likely glia), not the
  vulnerable neurons themselves. Biologically informative.

data:
  location: ./research_cases/parkinson-metabolic-flux-sn/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Fetch into
    `./datasets/<accession>/`. This is the lightest of the v2
    proposals (~5 GB total).

    **Primary snRNA-seq:**

      - **Fullard 2024 SNpc slab** (CELLxGENE d5d0df8f-...) —
        used for DA-neuron pseudobulk and microglia pseudobulk
        (~3 GB after per-cell-type aggregation).
        Target path: ./datasets/cellxgene_d5d0df8f_fullard/

    **Genome-scale metabolic models:**

      - **Recon3D model SBML** (GitHub SBRG/Recon3D, ~50 MB; public).
          https://github.com/SBRG/Recon3D
        Target path: ./datasets/recon3d/

      - **Human-GEM model SBML** (GitHub SysBioChalmers/Human-GEM,
        ~80 MB; public).
          https://github.com/SysBioChalmers/Human-GEM
        Target path: ./datasets/human_gem/

      - **iDopaNeuro1 DA-neuron-context model SBML** (Preciat 2021
        bioRxiv; Zenodo TBD).
        Target path: ./datasets/idopaneuro1/
        Note: if Zenodo unreachable, fall back to Recon3D + iMAT
        only and report iDopaNeuro1 absence in Limitations.

    **Metabolomic + flux-method references:**

      - **TIMBR Python implementation** (Yamamori 2024 supplementary
        code on Zenodo). Target path: ./datasets/timbr/

      - **Shao 2024 npj-PD metabolomics meta-analysis**
        Supplementary Table 1, plasma + CSF reproducibly-associated
        metabolites.
          https://www.nature.com/articles/s41531-024-00732-z
        Target path: ./datasets/shao_2024_metabolomics/

      - **Supandi 2018 PLOS One cortical bulk-microarray flux
        predictions** Supplementary Tables 1-3 (open access).
          https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0203687
        Target path: ./datasets/supandi_2018_cortex/

      - **Hong 2025 PD-iron supplementary table** (Acta Neuropathol;
        Supp Table 2 lists iron-metabolism markers).
        Target path: ./datasets/hong_2025_pdiron/

  cohorts: [fullard_2024_SNpc]
  modalities: [snRNA_seq_10x, genome_scale_metabolic_model,
               metabolomics_meta_analysis_summary_tables]

success_criteria:
  primary_metric: glycolysis_flux_ratio_da_neurons
  baseline: supandi_2018_cortex_bulk_sign_concordance
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = DA-neuron pseudobulk Recon3D + iMAT predicts
    median PD-vs-ctrl glycolytic flux ratio < 0.9 with bootstrap
    95% CI excluding 1.0, AND sign-concordance with Supandi
    cortex-bulk on shared reactions ≥ 70%.

    Honest-failure: median ratio ≥ 0.9 or CI overlapping 1.0 means
    the bulk Supandi finding was glia-driven and DA neurons are
    metabolically intact — an informative inversion of the field's
    assumption.

    Cross-method concordance (H4) ≥ 0.5 Spearman between any two
    of {iMAT, TIMBR, E-Flux}. Below this, conclusions are
    method-dependent and `analysis.md` must say so up front.

figures_planned:
  - id: F1
    title: "Pseudobulk pipeline + Recon3D / Human-GEM contextualisation QC"
    panels:
      - "DA neuron pseudobulk: PCA of PD vs. ctrl donors (after iMAT discretisation)"
      - "iMAT-retained reactions: fraction kept per pathway (glycolysis / TCA / ETC / iron / lysosome / dopamine)"
      - "Reaction-count comparison: Recon3D vs. Human-GEM contextualised models"
      - "Donor-level reaction-flux variance pre vs. post-iMAT"
  - id: F2
    title: "Glycolysis + TCA flux PD-vs-ctrl in DA neurons (H1)"
    panels:
      - "Per-reaction flux distribution (boxplots): PGK, HEX1, PFK, ENO1, GAPDH, PKM, LDHA, TPI1 (10 reactions)"
      - "Bootstrap median flux ratio + 95% CI per glycolytic reaction"
      - "Concordance scatter: Supandi 2018 cortex-bulk sign vs. our DA-neuron sign per shared reaction"
      - "TCA-cycle flux summary (8 reactions): combined boxplot"
  - id: F3
    title: "TIMBR biomarker metabolite cross-validation (H2)"
    panels:
      - "TIMBR top-10 predicted metabolites: barplot of predicted score"
      - "Cross-reference table: TIMBR top-10 ∩ Shao 2024 meta-analysis (hits highlighted)"
      - "Hypergeometric enrichment q-value bar"
      - "Per-metabolite literature-evidence annotation (PD-relevance score)"
  - id: F4
    title: "Iron metabolism (H3) + dopamine + lysosome flux"
    panels:
      - "Iron-metabolism reaction flux ratio PD/ctrl (FECH, FTH1, TFRC, FTL)"
      - "Dopamine biosynthesis flux (TH, DDC reactions)"
      - "Lysosomal flux (GBA1, SMPD1, CTSD reactions)"
      - "Per-pathway aggregate flux-shift heatmap"
  - id: F5
    title: "Contextualisation-method concordance (H4) + DA-vs-glia (H5)"
    panels:
      - "Spearman correlation matrix across {iMAT, TIMBR, E-Flux} per-reaction PD-vs-ctrl flux"
      - "Per-reaction scatter: iMAT vs. TIMBR sign agreement"
      - "DA-vs-microglia H1 reproduction: paired comparison of glycolysis flux ratios"
      - "Honest-failure ribbon: H1 outcome × {iMAT / TIMBR / E-Flux} × {DA / microglia}"

figures_not_in_scope:
  - "Wet-lab metabolomic validation"
  - "Patient-level metabolomics (Shao 2024 provides summary tables only)"
  - "Flux variability analysis (FVA) beyond pathway-level summary"
  - "Multi-tissue brain model (cortex bulk reuse only as concordance check)"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 1440
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# parkinson-metabolic-flux-sn — Constraint-based metabolic flux modelling of PD substantia nigra DA neurons

## Source materials

- Supandi, F., van Beek, J.H.G.M. *Computational prediction of
  changes in brain metabolic fluxes during Parkinson's disease
  from mRNA expression.* PLOS One 13:e0203687 (2018).
- Yamamori, T., Marin de Mas, I., Carrasco-Hernandez, R., et al.
  *Transcriptome-based biomarker prediction for Parkinson's disease
  using genome-scale metabolic modeling.* J Biotechnol Adv (2024).
- Preciat, G., Sahoo, S., Sahdo, B., et al. *Mechanistic
  model-driven exometabolomic characterisation of human
  dopaminergic neuronal metabolism.* bioRxiv 2021.06.30.450562
  (2021). iDopaNeuro1 / iDopaNeuroC / iDopaNeuroCT models.
- Shao, Y., Anumanthan, G., Le, W., et al. *Metabolomics meta-
  analysis identifies reproducibly altered metabolites in
  Parkinson's disease.* npj Parkinson's Disease 10:160 (2024).
- Hong, X., Wang, Y. et al. *Iron metabolism in Parkinson's disease:
  multi-omic markers.* (2025).
- N M, P., Fullard, J.F., Clarence, T., et al. Sci Data 11:1274
  (2024). Fullard SNpc slab.

## Why this matters for OmiCairn

This is the v2 batch's lightest data footprint (~5 GB) but
highest-complexity computational stack. It exercises OmiCairn's:

1. **Genome-scale metabolic modelling outside the data-science
   default stack** — COBRApy 0.29.0, iMAT, TIMBR, E-Flux.
2. **Multi-method concordance** as a pre-registered honest-failure
   ribbon (H4), rather than method-picking after seeing results.
3. **Cell-type-resolved flux** — the question Supandi 2018 explicitly
   could not address. The novelty is the snRNA-seq pseudobulk →
   genome-scale-model bridge.

## Known prior results (cross-check)

| Claim | Reference |
|---|---|
| Bulk PD cortex: decreased glycolysis, decreased TCA midway, increased lactate | Supandi 2018 Fig. 2 |
| TIMBR predicts metabolite biomarkers from 13 SN bulk transcriptomes | Yamamori 2024 |
| iDopaNeuro1 captures DA-neuron-specific dopamine + redox flux | Preciat 2021 bioRxiv |
| Shao 2024 npj-PD meta: ~30 reproducibly altered plasma+CSF metabolites | Shao 2024 Supp Table 1 |
| Iron accumulation in PD SN (FECH, FTH1, TFRC upregulated) | Hong 2025 + classical neuropath literature |

## Out of scope

- Wet-lab metabolomic validation.
- Patient-level metabolomic data (summary tables only).
- Brain-region beyond SN (we use Fullard SNpc slab + cortex-bulk reuse for concordance).
- Multi-compartment fluxomics (cytoplasm + mitochondrion handled at single compartment).

## Implementation choices

- iMAT (Shlomi 2008) is the primary contextualisation method.
  TIMBR + E-Flux are H4 sensitivity checks. Reporting all three
  is mandatory.
- Discretise gene expression at cohort-wide tertile (high / mid /
  low) per donor per gene.
- FBA biomass objective is swapped for an ATP-maintenance objective
  (5 mmol/gDW/hr — neuron-literature standard).
- Constraint scope: glycolysis (10 rxn) + TCA (8 rxn) + ETC (4 cpx)
  + iron metabolism (4 rxn) + dopamine synthesis (3 rxn) + GBA /
  lysosomal (3 rxn).
- iDopaNeuro1 (DA-neuron-specific model) is preferred over
  Recon3D when fetchable; falls back to Recon3D with iMAT.
