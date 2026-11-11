# parkinson-cellchat-immune-axis

Peripheral × brain CD8+T-cell-to-myeloid ligand-receptor
communication axis in PD: LIANA-py consensus L-R inference on
Hong 2025 PBMC (GSE223138) vs. Ma 2025 SN brain (PMC12322057)
+ Fullard SN microglia slab.

- **Mode:** DISCOVERY (multi-modal cell communication).
- **Peripheral scRNA-seq:** Hong 2025 GSE223138 PBMC.
- **Brain scRNA-seq:** Ma 2025 PMC12322057 SN + scTCR-seq
  (fallback: Fullard SN microglia only if Ma 2025 unreachable —
  drop brain CD8+T to prior).
- **Brain microglia substrate:** Fullard 2024 SN microglia slab
  (CELLxGENE d5d0df8f-...).
- **L-R inference:** LIANA-py 0.4.1 (Dimitrov 2024) consensus
  across CellChat + CellPhoneDB + NATMI + Connectome + scTalk +
  NicheNet.
- **Cross-resource references:** Hong 2025 Supp Table 2 (28 MR-
  implicated immune genes); OneK1K Yazar 2022 (PBMC cell-type
  calibration).
- **Cross-workspace input:** v1 `parkinson-tcell-foothold`
  `results.json` per-donor brain T-cell module scores (H3).
- **Primary metric:** ≥ 5 PD-up-regulated L-R pairs shared
  between PBMC CD8+T → monocyte (Hong) and brain CD8+T →
  microglia (Ma) directions; permutation null p < 0.01.
- **Expected outcome:** 5-15 shared pairs in the optimistic
  case; 0-3 if peripheral and brain immune axes are decoupled —
  both informative.
- **Why this case is in the suite:** completes the
  peripheral × brain immune axis story started by v1
  parkinson-tcell-foothold. Tests OmiCairn's ability to run an L-R
  inference pipeline (Python-native LIANA-py), compare two
  cohort-disjoint L-R outputs at the pair level, and pre-register
  direction-specificity as a negative control (H4).
- **Hardest OmiCairn phase:** EXECUTE — CellChat is R-native; LIANA-py
  is the Python alternative. Must use LIANA-py 0.4.1 (Dimitrov
  2024) for consensus.

**Implementation choice:** LIANA-py 0.4.1 (Python) over R
CellChat directly. Direction-specificity (CD8+T → myeloid, NOT
the reverse) is the pre-registered negative control.

Data fetched on-the-way (~24 GB total).
