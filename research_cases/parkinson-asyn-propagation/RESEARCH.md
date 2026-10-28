---
research_mode: HYBRID
domain: neurodegeneration_dynamic_systems
topic_slug: parkinson-asyn-propagation

research_question: |
  Does a Susceptible-Infected-Removed (SIR) compartmental model of
  α-synuclein spread between brain regions, parameterised by Fullard
  2024 multi-region snRNA-seq cell-type fractions and SNCA
  expression, plus Allen Human Brain Connectivity Atlas projection
  edges, reproduce the canonical Braak staging order
  (DMNX → SNpc → cingulate → M1 → V1) at qualitatively correct
  relative timing?

  Braak staging (Braak 2003 Neurobiol Aging) is the most-cited
  spatial-temporal claim in PD neuropathology, but its mechanistic
  explanation — that α-syn spreads cell-to-cell along a connectome
  gradient, modulated by selective vulnerability — has been tested
  in mouse atlases (Henderson 2019, Zheng 2019) but not at human
  cell-type granularity. Fullard 2024 provides the missing
  per-region cell-fraction and SNCA-expression priors.

  Hypotheses (pre-registered):

    H1 (primary — Braak rank-order recovery):
        An SIR + connectivity model with SNCA-expression-weighted
        per-region susceptibility recovers the rank-order of
        predicted regional peak-prevalence times consistent with
        Braak rank, with Spearman ρ ≥ 0.7 across the 5 modelled
        regions (DMNX, SNpc, cingulate, M1, V1). Bootstrap 95%
        CI of ρ excludes 0.

    H2 (secondary — SNCA-weighting necessity):
        Ablating the SNCA-expression weighting (uniform
        susceptibility across regions) degrades rank-order
        recovery by ≥ 0.3 in Spearman ρ. (Test that cell-specific
        susceptibility is necessary, not sufficient.)

    H3 (secondary — cingulate cell-type prediction):
        The model predicts which cingulate cell types reach
        infection threshold first; we pre-register "excitatory
        layer-5/6 neurons" as the top prediction. The top-3
        model-predicted cell types overlap ≥ 2 with the top-3 Lewy-
        burdened cell types reported by Goralski 2024 LAMDA
        (Zenodo 10.5281/zenodo.10729767).

    H4 (secondary — vagal-route ablation):
        Removing the DMNX → SN projection edge (parameter ablation)
        shifts predicted peak-time rank-order by > 1 rank
        position. A falsifiable mechanistic claim about the vagal
        spread route.

    H5 (secondary — connectome-only vs. cell-autonomous):
        Compare three models — (A) connectome-only SIR, no
        cell-type weighting; (B) cell-type-weighted SIR
        (this proposal's primary); (C) cell-autonomous-only
        (each region's I trajectory independent of others).
        Report which best fits Braak rank-order. A finding that
        (A) or (C) wins is an informative inversion of the
        spread-only hypothesis.

  Null payoff. If no SIR variant recovers Braak rank-order, the
  connectome-only hypothesis is incomplete and pure
  cell-autonomous selective vulnerability (independent of spread)
  dominates. Informative as a head-to-head test of two competing
  field-level hypotheses.

data:
  location: ./research_cases/parkinson-asyn-propagation/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Fetch into
    `./datasets/<accession>/`. Total ~12 GB after pseudobulk.

    **Primary snRNA-seq:**

      - **Fullard 2024 multi-region atlas** (CELLxGENE
        d5d0df8f-...; all 5 regions, ~30 GB raw → ~2 GB after
        per-region per-cell-type pseudobulk).
        Target path: ./datasets/cellxgene_d5d0df8f_fullard/

    **Connectome:**

      - **Allen Human Brain Connectivity** (download via Allen
        SDK; ~5 GB after region-of-interest filtering).
          https://human.brain-map.org/
        Target path: ./datasets/allen_human_connectivity/

    **Spatial / supplementary:**

      - **Goralski LAMDA** (Zenodo 10.5281/zenodo.10729767;
        cingulate spatial Lewy-burden profile; ~2 GB).
          https://zenodo.org/records/10729767
        Target path: ./datasets/goralski_lamda/

      - **Henderson 2019 mouse atlas** (PMC6372945; parameter-
        prior initialisation; ~500 MB).
        Target path: ./datasets/henderson_2019_mouse/

  cohorts: [fullard_2024_5region, allen_human_connectome,
            goralski_2024_lamda_cingulate]
  modalities: [snRNA_seq_10x, brain_connectome, spatial_transcriptomics,
               sir_compartmental_model]

success_criteria:
  primary_metric: spearman_rho_predicted_peak_vs_braak
  baseline: connectome_only_no_SNCA_weight
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = Spearman ρ ≥ 0.7 between predicted regional
    t_peak and Braak rank, with bootstrap CI excluding 0.
    n = 5 regions makes power tight; bootstrap CI is essential.

    Honest-failure: if no SIR variant (connectome-only, cell-
    weighted, or cell-autonomous) achieves ρ ≥ 0.5, the model
    family does not explain Braak. `analysis.md` must say this up
    front, NOT tune until a variant fits.

    Parameter ranges pre-registered in this YAML (β_0 ∈ [0.01, 1.0],
    γ ∈ [0.001, 0.1]); OmiCairn must NOT extend the search beyond
    these without writing a rationale into `analysis.md`.

figures_planned:
  - id: F1
    title: "Model parameterisation: per-region cell-type fractions + SNCA expression + connectome"
    panels:
      - "Per-region cell-type composition stacked bar (5 regions × ~10 cell types) from Fullard pseudobulk"
      - "Per-region SNCA mean expression bar (DA + non-DA cells)"
      - "Allen connectome adjacency heatmap (5x5) for the 5 modelled regions; log-projection strength"
      - "Per-region population N_i and SIR initial conditions table"
  - id: F2
    title: "SIR simulation results (H1, H2)"
    panels:
      - "Time-course of I_i (infected fraction) per region (5 traces)"
      - "Predicted t_peak per region vs. Braak rank (scatter with regression line + 95% CI)"
      - "Spearman ρ point estimate + bootstrap CI"
      - "Ablation: SNCA-weighting removed, side-by-side Spearman ρ"
  - id: F3
    title: "Cingulate cell-type prediction vs. LAMDA (H3)"
    panels:
      - "Model-predicted infection-threshold-first cell types in cingulate (ranked list)"
      - "Goralski LAMDA cingulate cell-type Lewy-burden ranking (reference)"
      - "Top-3 overlap Venn"
      - "Rank-rank scatter: model prediction rank vs. LAMDA-observed rank"
  - id: F4
    title: "Vagal-route ablation (H4) + model-family comparison (H5)"
    panels:
      - "Δ rank-position per region after DMNX → SN edge removal"
      - "Spearman ρ comparison: 3 SIR variants (connectome-only, cell-weighted, cell-autonomous)"
      - "ROC-style trade-off: parameter (β_0) scan vs. Braak ρ"
      - "Pre-registered parameter range honesty plot: where in {β_0, γ} grid each variant peaks"
  - id: F5
    title: "Honest-failure + sensitivity panel"
    panels:
      - "5-region permutation null: random rank order Spearman ρ distribution"
      - "Down-sampling sensitivity: 4-region and 3-region sub-models (which subsets are necessary?)"
      - "Parameter-prior sensitivity: Henderson-2019-mouse-derived prior vs. uniform prior"
      - "Per-region marginal sensitivity (which region's parameters most affect ρ)"

figures_not_in_scope:
  - "MCMC parameter inference (computationally expensive; closed-form SIR variant first)"
  - "Wet-lab α-syn injection validation"
  - "Whole-brain regional extension (5-region subset only)"
  - "Connectome inference from raw DTI (Allen-provided projections only)"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 1440
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# parkinson-asyn-propagation — Multi-scale α-synuclein propagation modelling across Braak-stage regions

## Source materials

- Braak, H., Del Tredici, K., Rüb, U., et al. *Staging of brain
  pathology related to sporadic Parkinson's disease.* Neurobiol
  Aging 24:197 (2003). The original Braak staging.
- N M, P., Fullard, J.F., Clarence, T., et al. *A multi-region single
  nucleus transcriptomic atlas of Parkinson's disease.* Sci Data
  11:1274 (2024). 5-region atlas; CELLxGENE d5d0df8f-... .
- Goralski, T.M., Henrich, M.T., Schulze, M.S., et al. *Spatial
  transcriptomic landscape unveils dopaminergic neurons and immune
  cells changes in Parkinson's disease and dementia with Lewy
  bodies.* Nat Commun 15:2642 (2024). LAMDA. Zenodo
  10.5281/zenodo.10729767.
- Henderson, M.X., Cornblath, E.J., Darwich, A., et al. *Spread of
  α-synuclein pathology through the brain connectome is modulated
  by selective vulnerability and predicted by network analysis.*
  Nat Neurosci 22:1248 (2019). The mouse-atlas precedent for this
  proposal's modelling family.
- Allen Institute. *Allen Human Brain Connectivity Atlas.*
  https://human.brain-map.org/ .

## Why this matters for OmiCairn

This is the v2 batch's only mechanistic-dynamics case. It tests
OmiCairn's ability to:

1. **Stand up a parameterised ODE model** (scipy.integrate.solve_ivp)
   rather than just describe one — closed-form SIR with explicit
   region-level state.
2. **Coordinate three input modalities** (snRNA-seq pseudobulk +
   connectome edges + spatial-transcriptomic validation) without
   pre-existing donor matching.
3. **Pre-register parameter ranges + falsifiable variants** (H5
   model-family comparison). The risk register flags over-fitting
   to Braak as the dominant failure mode; this YAML pre-empts it.
4. **Handle a 5-data-point Spearman statistic with bootstrap CI**
   honestly (low power; CI essential).

## Known prior results (cross-check)

| Claim | Reference |
|---|---|
| Braak stages: DMNX → SNpc → meso-cortex → neocortex (V1 last) | Braak 2003 |
| Mouse-atlas SIR + connectome recovers PFF-spread order ρ ≈ 0.7 | Henderson 2019 |
| LAMDA cingulate Lewy-burden enriches in excitatory layer-5/6 | Goralski 2024 Fig. 3 |
| Allen Human Connectome shows DMNX-to-midbrain projection | Allen Institute database |

## Out of scope

- Wet-lab α-syn injection experiments.
- MCMC posterior inference (closed-form ODE variant first).
- Whole-brain ≥ 50-region extension.
- Raw DTI re-inference of connectome (Allen-deposited projections only).

## Implementation choices

- Discretise the brain into 5 regions: DMNX, SNpc, cingulate, M1,
  V1. Per-region: total cell count, SNCA mean expression,
  cell-type composition (all from Fullard pseudobulk).
- Connectivity matrix C_{ij}: Allen-Brain log-transformed,
  row-normalised projection strengths.
- State vector per region: (S_i, I_i, R_i), S+I+R = N_i. Infection
  rate β_{ij} = β_0 × C_{ij} × SNCA_j. Removal rate γ uniform.
- Integrate forward with `scipy.integrate.solve_ivp` over 50
  simulation time units; record per-region t_peak.
- Compare predicted t_peak rank-order to Braak rank-order via
  Spearman ρ + bootstrap 95% CI.
- For H4 (DMNX→SN ablation), set C_{DMNX,SN} = 0 and re-solve.
- Parameter ranges pre-registered: β_0 ∈ [0.01, 1.0], γ ∈ [0.001,
  0.1] — do NOT extend without analysis.md justification.
