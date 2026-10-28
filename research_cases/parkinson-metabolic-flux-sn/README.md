# parkinson-metabolic-flux-sn

Constraint-based metabolic flux modelling (iMAT + TIMBR + E-Flux)
applied to Fullard 2024 SNpc dopaminergic-neuron pseudobulk; first
DA-neuron-context test of Supandi 2018's cortical-bulk glycolysis-
decrease finding.

- **Mode:** DISCOVERY (systems biology, flux balance analysis).
- **snRNA-seq input:** Fullard 2024 SNpc slab (CELLxGENE
  d5d0df8f-...; DA-neuron + microglia pseudobulk).
- **Metabolic models:** Recon3D (primary), Human-GEM
  (cross-validation), iDopaNeuro1 (DA-neuron-specific, if
  fetchable).
- **Contextualisation methods:** iMAT (primary), TIMBR (H2 +
  biomarker prediction), E-Flux (H4 concordance check).
- **Cross-resource references:** Shao 2024 npj-PD metabolomics
  meta-analysis Supp Table 1; Supandi 2018 cortex-bulk
  predictions; Hong 2025 PD-iron.
- **Primary metric:** median PD-vs-ctrl flux ratio in cytoplasmic
  glycolysis < 0.9 with bootstrap 95% CI excluding 1.0; sign-
  concordance with Supandi 2018 cortex bulk ≥ 70%.
- **Expected outcome:** mixed — glycolysis-down likely replicates
  in DA neurons (matches DA bioenergetic vulnerability literature),
  but iron-up (H3) is the higher-risk hypothesis.
- **Why this case is in the suite:** lightest-data v2 case
  (~5 GB) but heaviest-pipeline (COBRApy + 3 contextualisation
  methods). Tests OmiCairn's ability to install + run a non-default
  scientific computing stack (COBRApy 0.29.0, TIMBR).
- **Hardest OmiCairn phase:** ANALYZE (FBA / iMAT / TIMBR not in the
  default Python data-science stack; install + model-licence
  compliance).

**Implementation choice:** iMAT (Shlomi 2008) is the primary
contextualisation; iDopaNeuro1 (Preciat 2021) is preferred when
fetchable. ATP-maintenance objective replaces biomass for neurons
(5 mmol/gDW/hr, literature standard).

Data fetched on-the-way (light: ~5 GB total).
