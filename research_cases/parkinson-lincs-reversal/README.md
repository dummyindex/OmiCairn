# parkinson-lincs-reversal

LINCS L1000 connectivity reversal scoring of the Fullard 2024 SNpc
DA-neuron PD-vs-ctrl signature, with H3 cell-type-resolved
microglia comparison and H4 neuronal-cell-line-only subset
analysis.

- **Mode:** DISCOVERY (drug repositioning).
- **PD signature source:** Fullard 2024 SNpc DA + microglia
  pseudobulk (CELLxGENE d5d0df8f-...).
- **Perturbation library:** LINCS L1000 Phase II Level 5
  signatures (GSE92742 + GSE70138).
- **Prior-evidence references:** Open Targets release 24.06,
  omicSynth Tang 2024, Bayati 2024, Storm 2025.
- **Internal positive controls (H5):** levodopa, ropinirole,
  pramipexole, rasagiline — pre-registered to catch signature-
  direction errors.
- **Primary metric:** ≥ 5 of the top-50 reversing compounds have
  prior Open Targets PD evidence or appear in a published PD
  repositioning hit list (hypergeometric q < 0.01).
- **Expected outcome:** 3-8 overlap. The bigger question is H4 —
  whether neuronal-cell-line-only L1000 outperforms cancer-line
  L1000 for PD repositioning.
- **Why this case is in the suite:** the v2 batch's only drug-
  repositioning case. Tests OmiCairn's ability to cross-reference
  multiple public databases (LINCS + Open Targets + omicSynth
  + clue.io) at scale, and to pre-register internal positive
  controls.
- **Hardest OmiCairn phase:** EXECUTE — L1000 Level 5 file is dense
  HDF5; cmapPy required.

**Implementation choice:** Fullard SNpc DA PD-vs-ctrl pseudobulk
restricted to L1000 978-landmark genes (apples-to-apples). CMAP
connectivity score via `gseapy.ssgsea` "norm-2018" mode. Open
Targets MONDO:0005180 filter for PD-evidence cross-check.

Data fetched on-the-way (~20 GB total).
