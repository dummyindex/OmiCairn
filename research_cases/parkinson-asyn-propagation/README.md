# parkinson-asyn-propagation

Multi-scale SIR + connectome modelling of α-synuclein propagation
across the 5 modelled brain regions (DMNX, SNpc, cingulate, M1, V1)
of the Fullard 2024 multi-region atlas, parameterised by per-region
SNCA expression + cell-type fractions and Allen Human Connectivity
edges.

- **Mode:** HYBRID (mechanistic dynamics + cross-modality data
  integration).
- **snRNA-seq input:** Fullard 2024 multi-region atlas (CELLxGENE
  d5d0df8f-...; all 5 regions, pseudobulked).
- **Connectome:** Allen Human Brain Connectivity Atlas
  (https://human.brain-map.org/).
- **Spatial validation:** Goralski 2024 LAMDA cingulate
  (Zenodo 10.5281/zenodo.10729767) for H3 cell-type prediction.
- **Modelling family:** closed-form SIR variants A/B/C (H5);
  scipy.integrate.solve_ivp over 50 simulation time units.
- **Primary metric:** Spearman ρ between predicted regional t_peak
  and Braak rank ≥ 0.7 with bootstrap 95% CI excluding 0
  (n = 5 regions; CI is essential at this n).
- **Expected outcome:** ρ ∈ [0.6, 0.85] for the cell-weighted SIR
  (H1), degrading to [0.3, 0.6] without SNCA weighting (H2). The
  vagal-ablation (H4) is the riskiest hypothesis.
- **Why this case is in the suite:** the v2 batch's only
  mechanistic-dynamics case. Tests OmiCairn's ability to stand up a
  parameterised ODE model, pre-register parameter ranges + model-
  family alternatives, and report on a 5-point Spearman with
  honest bootstrap CI.
- **Hardest OmiCairn phase:** EXECUTE — risk of over-fitting to
  Braak in parameter search; pre-registered parameter ranges
  guard against this.

**Implementation choice:** closed-form ODE (no MCMC) — three
model-family variants are pre-registered in H5 to avoid post-hoc
variant-picking. Parameter ranges (β_0, γ) are locked in YAML.

Data fetched on-the-way (~12 GB after pseudobulk).
