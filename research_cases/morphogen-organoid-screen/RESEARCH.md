---
research_mode: CONFIRMATORY
domain: developmental_neuroscience_scRNAseq
topic_slug: morphogen-organoid-screen

research_question: |
  Sanchís-Calleja et al. (Nature Methods, 2026, DOI:10.1038/s41592-025-02927-5)
  reported a systematic multiplexed scRNA-seq screen of human neural
  organoid response to morphogens, covering (i) timing, concentration,
  and combinatorial morphogen treatments across 3 hPSC lines (HES3, H9,
  WTC; E-MTAB-15667; ~100,538 cells / 97 conditions), (ii) a patterning
  reproducibility screen across 4 hPSC lines × 2 neural induction media
  × 2 technical batches (E-MTAB-15622; ~209,902 cells / 192 samples),
  and (iii) a MiSTR microfluidic gradient vs. discrete-step organoid
  comparison in H9. The authors claim that morphogen timing,
  concentration, and combinations drive reproducible shifts in regional
  cell-type composition along the AP and DV axes, and that hPSC line
  and neural induction method (MNIM vs. dual SMAD inhibition) modulate
  these outcomes at both the cell-type and regulon level.

  The goal of this OmiCairn run is honest computational reproduction of the
  paper's quantitative claims using only the publicly deposited raw /
  processed data. We do NOT attempt to re-run wet-lab experiments;
  instead we re-derive the key figures from the deposited count
  matrices and compare to the published values. Specifically:

    H1 (primary — cell-type composition reproducibility):
        Re-processing the E-MTAB-15667 patterning condition screen
        (QC + normalisation + RSS integration + clustering + cluster
        annotation per the published marker panels) recovers the
        18-cluster taxonomy (Fig. 1c, Extended Data Fig. 2a–c), such
        that the cluster-level composition percentages per condition
        agree with the published stacked bar plots (Fig. 1j,
        Extended Data Fig. 1b–d) within a tolerance of ±10 percentage
        points per (condition, cluster) for the HES3 sub-cohort.

    H2 (secondary — morphogen enrichment/depletion scores):
        Two-sided Fisher exact tests with Bonferroni correction,
        comparing each morphogen-treated condition against its
        matched control, reproduce the enrichment / depletion
        signatures shown in Fig. 2a,b and Extended Data Fig. 3 for
        the HES3 line. Concretely: the sign of the log2 odds-ratio
        must match the paper for ≥ 90% of (cluster, condition) cells
        where the paper reports p_adj < 0.01, and the published
        marquee findings must replicate as directional effects:
          - RA t9-20 c2-3 → enrichment of retinal progenitors
          - RA t9-20 c4-5 → enrichment of hindbrain + posterior
            floor plate
          - High SHH concentration (c4-5, t3-14) → transition from
            telencephalic to hypothalamic progenitors
          - XAV939 early/intermediate → telencephalic enrichment
          - CHIR high concentration → neural crest / PNS / mesenchyme
          - BMP4 (late) → retinal + non-neural ectoderm
          - BMP7 (early) → mesenchymal cells / pluripotency retention

    H3 (secondary — regulon inference consistency):
        Running pySCENIC on the HES3 subset of E-MTAB-15667 with the
        clustered v10 motif database (exactly per Methods) and
        correlating regulon AUCell scores against morphogen
        concentration / timing, we recover the qualitative
        morphogen-regulon associations reported in Fig. 2e–h:
          - SHH ↔ NKX2-1, NKX6-2, GLI1, HESX1, TCF7L2 (ventral /
            hypothalamic / thalamic regulons, concentration-
            dependent)
          - RA ↔ HOXB3, HOXB7, HOXB8, HOXA2, FOXA2 (HOX regulons
            concentration-dependent; FOXA2 timing-dependent)
          - FGF8 ↔ retinal / telencephalic regulons,
            concentration-dominant
          - BMP4 ↔ MSX2, PAX6, VSX2 (dorsal / retinal)
        A SCENIC run is stochastic; we require that ≥ 70% of the
        "top-10 by weight" morphogen-regulon edges per morphogen in
        our run overlap the paper's reported network (Fig. 2e and
        Supplementary Table 4), with no sign-flip on the
        correlation-to-concentration axis for the overlapping edges.

    H4 (secondary — reproducibility screen hierarchy):
        On the E-MTAB-15622 dataset, the distance-based variability
        analysis (KL divergence / MMD / E-distance) reproduces the
        published ordering:
          - Dual SMAD induction shows higher cross-line variability
            than MNIM (Fig. 4i, Extended Data Fig. 8i,j; paired
            comparison, expected direction).
          - Higher morphogen concentrations yield lower cross-line
            KLD than low concentrations (Fig. 4j; expected
            monotonic trend for SHH and RA).
          - Batch-to-batch KLD (Fig. 4h) is lower than cross-line
            KLD within the same condition for all 4 hPSC lines.
        The predicted AP/DV scores on the radial-glia–trained
        elastic-net model should place SHH-c5 and RA-c5 at
        concordant coordinates across cell lines (Fig. 4l).

    H5 (secondary — MiSTR vs. organoid DV patterning):
        On the MiSTR + discrete-organoid H9 dataset (deposited under
        the same Zenodo / ArrayExpress; see Data availability), the
        integrated Harmony/CSS embedding recovers the 9-cluster
        forebrain taxonomy (Fig. 6c, Extended Data Fig. 10e) and
        reproduces the headline qualitative finding: organoids reach
        more ventral identities (FOXA2+ floor plate, hypothalamic)
        than MiSTR segments at matched SHH doses, i.e. the organoid
        FBaxis_rank distribution is shifted ventrally relative to
        MiSTR (Fig. 6i; Kolmogorov–Smirnov test, expected direction,
        p < 0.01).

  A negative-leaning outcome on H1 combined with positive outcomes on
  H2/H4/H5 would itself be scientifically informative: it would
  indicate that exact cell-type proportions are brittle to
  re-processing (a common problem with RSS + Seurat clustering
  reproductions) even when the directional biological conclusions
  hold. OmiCairn must report these discrepancies honestly rather than
  tune clustering resolution until H1 passes.

data:
  location: ./test-research-runs/organoid-morphogen-screen/data
  shared_cache: ./datasets
  description: |
    **Nothing is pre-staged on disk.** Every dataset below is publicly
    deposited and MUST be fetched by phase sessions into the shared
    cache `./datasets/<accession>/` on the way. Acceptable reasons
    to defer a fetch, documented in `analysis.md § Limitations`:
    (1) the accession URL returns HTTP 404/410 (permanently removed);
    (2) the archive fails sha256 verification or cannot be parsed by
    scanpy / anndata / Seurat; (3) `budgets.allow_web` is `false`.
    Size alone is NOT an acceptable reason to skip — the shared cache
    is explicitly designed to amortise these downloads across runs,
    and the 100 MB per-file / 2 GB per-stage advisory limits in
    `prompts/execute.md` are overridden here by this RESEARCH.md.

    **Primary datasets to fetch:**

      - **E-MTAB-15667** — morphogen patterning condition screen.
        ArrayExpress:
          https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-15667
        Scope: 100,538 cells, 97 morphogen conditions, 3 hPSC lines
        (HES3, H9, WTC). 10x Genomics 3' v3/v3.1 chemistry + cell
        hashing (TotalSeq-A). Contains both (a) raw FASTQs (large;
        skip unless re-alignment is truly required) and (b) processed
        count matrices + hashtag matrices per 10x library. Expected
        ingest: processed matrices only. Size ~5-20 GB processed.
        Target path: `./datasets/E-MTAB-15667/`

      - **E-MTAB-15622** — morphogen reproducibility screen.
        ArrayExpress:
          https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-15622
        Scope: 209,902 cells, 192 samples, 4 hPSC lines (H1, H9,
        WIBJ2, WTC) × 2 NIMs (MNIM, dual SMAD) × 2 technical batches
        × 12 morphogen conditions. Split-pool combinatorial indexing
        (Parse Biosciences WT Mega v3, MG100). Expected ingest:
        processed matrices. Size ~10-30 GB processed.
        Target path: `./datasets/E-MTAB-15622/`

      - **Zenodo 10.5281/zenodo.17225179** — processed Seurat / h5ad
        objects + VCF files for demultiplexing + MiSTR-vs-organoid
        H9 dataset + summary pseudo-bulk / forebrain-axis products.
        This is the single most valuable resource for reproduction:
        fetching this directly gives us the authors' annotated objects
        and lets us verify H1–H5 without re-running QC + integration
        from raw counts. Always try this first.
        URL: https://zenodo.org/records/17225179
        Target path: `./datasets/zenodo_17225179/`

    **Reference atlases needed for projection (H4 elastic-net model,
    Extended Data Fig. 8f-h, Extended Data Fig. 9e-f):**

      - **Braun et al. 2023 first-trimester human brain cell atlas**
        (Science 382, eadf1226). scANVI model + underlying h5ad.
        Available through the authors' HCA portal / CellXGene. We
        need the radial-glia subset used to train AP and DV axis
        scoring models. Size ~5-15 GB.
        Primary landing (verify on the way):
          https://github.com/linnarsson-lab/developing-human-brain
          https://cellxgene.cziscience.com (search "Braun 2023 brain")
        Target path: `./datasets/braun_2023_human_brain/`

      - **HNOCA — He et al. 2024 integrated human neural organoid
        cell atlas** (Nature 635:690-698). scPoli model + integrated
        h5ad used for presence-score projection (Ext. Data Fig. 9e,f).
        Size ~2-10 GB.
        Primary landing (verify on the way):
          https://github.com/quadbio/human_neural_organoid_atlas
        Target path: `./datasets/hnoca/`

    **Analysis code (mandatory for method parity):**

      - **quadbio/organoid_patterning_screen** — authors' analysis
        scripts, including `Morphogen_Screen_Enrichment.R` used to
        build Supplementary Table 3, the SCENIC wrapper, and the
        forebrain-axis scoring code. Clone once into the shared
        cache; re-use across all phases.
        URL: https://github.com/quadbio/organoid_patterning_screen
        Target path: `./datasets/organoid_patterning_screen_code/`

    **Reference databases for SCENIC (H3):**

      - pySCENIC v0.12.1-compatible clustered v10 motif database +
        TF list (hg38). Available via the aertslab S3 / CistargetDB.
        Size ~2-5 GB combined.
          https://resources.aertslab.org/cistarget/databases/
          https://github.com/aertslab/pySCENIC
        Target path: `./datasets/scenic_v10_clustered/`

    **Shared dataset cache convention:** As in prior OmiCairn runs, every
    accession is downloaded into `./datasets/<accession>/` with a
    sidecar `SOURCE.txt` that records URL, fetch timestamp, sha256 of
    each file, byte count, and the parsing tool used
    (scanpy.read_h5ad, anndata.read, Seurat::readRDS, etc.).
    Subsequent runs check the cache first and skip re-downloads.

  cohorts:
    [morphogen_condition_screen_HES3, morphogen_condition_screen_H9,
     morphogen_condition_screen_WTC, reproducibility_H1,
     reproducibility_H9, reproducibility_WIBJ2, reproducibility_WTC,
     mistr_vs_organoid_H9]
  modalities: [scRNA_seq_10x_3prime, scRNA_seq_parse_splitpool,
               cell_hashing_HTO, genotype_SNP_demultiplexing,
               live_imaging_lightsheet, HCR_in_situ]

success_criteria:
  primary_metric: cluster_composition_concordance
  baseline: authors_published_percentages
  pre_registration: required
  honest_validation: required
  notes: |
    Authors' published percentages (Fig. 1j for HES3; Fig. 4d for H9
    reproducibility batches; Fig. 6f-h for MiSTR vs. organoid) serve
    as the ground truth. Our re-processed composition must agree
    within ±10 pp per (condition, cluster). For regulon analysis
    (H3), agreement is measured by top-K edge overlap (K=10 per
    morphogen) rather than exact weight reproduction, because
    GRNBoost2 is stochastic. For the reproducibility-screen distance
    metrics (H4), we require the *ordering* of distances (dual SMAD
    > MNIM, low conc > high conc) to reproduce, not exact values.
    Any deviation beyond these tolerances must be called out as a
    replication failure, not massaged away by clustering-parameter
    search.

figures_planned:
  - id: F1
    title: "Re-derived single-cell transcriptomic atlas of the morphogen patterning condition screen (E-MTAB-15667)"
    panels:
      - "RSS-UMAP of all cells, colored by cluster (target: 18 clusters matching Fig. 1c)"
      - "RSS-UMAP colored by hPSC line (HES3 / H9 / WTC), replicating Fig. 1f in control cells only"
      - "Cluster marker dot plot: cell-class markers (POU5F1, SOX2, STMN2, TFAP2A, FOXG1, VSX2, HOXB2, FOXA2, NKX2-1) replicating Extended Data Fig. 2a"
      - "Composition stacked bar: % cells per cluster per morphogen condition for HES3 (direct reproduction of Fig. 1j) with Pearson r and MAE vs. published percentages"
  - id: F2
    title: "Morphogen timing × concentration enrichment/depletion in HES3"
    panels:
      - "Heatmap of Fisher-exact enrichment/depletion scores across all 18 clusters × all HES3 conditions (target: Fig. 2a,b match)"
      - "RA timing + concentration barplots reproducing Fig. 2c (retinal / hindbrain / posterior floor plate / non-neural ectoderm)"
      - "SHH concentration barplots reproducing Extended Data Fig. 3d (neuroepithelium / telencephalic / hypothalamic progenitors)"
      - "Concordance scatter: our log2OR vs. paper's enrichment score, colored by p_adj tier; annotate outliers"
  - id: F3
    title: "Regulon inference on HES3: morphogen–TF networks (pySCENIC reproduction)"
    panels:
      - "Force-directed graph of morphogen → regulon edges (weight > 200), layout matching Fig. 2e topology"
      - "SHH regulon scatter: correlation-to-timing vs. correlation-to-concentration, labelled (NKX2-1, TCF7L2, GLI1, HESX1, GBX2, EMX1, POU3F3); reproduction target Fig. 2f"
      - "FGF8 regulon scatter reproducing Fig. 2g (NFIC, SALL4, POU3F3 etc.)"
      - "Overlap Venn / bar: our top-10 morphogen-regulon edges per morphogen vs. Supplementary Table 4; report % overlap"
  - id: F4
    title: "Morphogen combination non-linearity (HES3 + WTC)"
    panels:
      - "MMD distances ±FGF8 across all base conditions (reproduce Fig. 3c for HES3 and Extended Data Fig. 6c for WTC)"
      - "NNLS residuals as non-additivity metric across all 2-morphogen combinations (reproduce Fig. 3e)"
      - "Pie-chart grid of cell-type composition for the 5×4 XAV/SHH/CHIR/RA × Cyc/SHH combinatorial panel (reproduce Fig. 3a)"
      - "Regulon-interaction bar plots for XAV+SHH and RA+SHH synergies (reproduce Fig. 3g: GTF3A, POU5F1, ZNF217, SOX3, FEZF1, SP8)"
  - id: F5
    title: "Patterning reproducibility across hPSC lines and neural induction methods (E-MTAB-15622)"
    panels:
      - "RSS-UMAP of all 209,902 cells, colored by cluster (target: 13 clusters of Fig. 4b)"
      - "Stacked bar for H9, both batches, both NIMs, all 12 conditions (reproduce Fig. 4d)"
      - "KLD boxplots per cell line and per NIM (reproduce Fig. 4h,i)"
      - "Per-condition cross-line KLD, faceted by NIM (reproduce Fig. 4j)"
  - id: F6
    title: "Regulon consistency across cell lines and NIM (E-MTAB-15622)"
    panels:
      - "Morphogen–regulon graphs for MNIM vs dual SMAD side-by-side (reproduce Fig. 5a)"
      - "Per-cell-line regulon-activity heatmaps for consistent (HOXC6, GLI3, HOXB3) vs variable (FOXG1, RFX2, POU3F2) regulons (reproduce Fig. 5b)"
      - "Correlation heatmap: regulon activity vs. morphogen concentration, faceted by morphogen and NIM (reproduce Fig. 5c for CHIR, RA, FGF8, SHH)"
      - "Consistency summary: fraction of regulons with matching sign across the 4 hPSC lines, per morphogen × NIM"

  - id: F7
    title: "AP/DV axis scoring via Braun-atlas-trained elastic net"
    panels:
      - "Scatter of (AP_score, DV_score) for every (line × NIM × condition) pseudo-sample, reproducing Fig. 4l (two panels: MNIM, dual SMAD)"
      - "FBaxis_rank ridge/jitter plot per condition, reproducing Fig. 6i"
      - "Elastic-net model diagnostic: 5-fold CV accuracy on Braun radial glia labels (AP 4-way, DV binary) — document if we fall short of the authors' implicit threshold"
      - "Dorsalising vs. ventralising gene module scores (PAX6/GLI3/BARHL1 vs NKX2-1/NKX2-2/SHH/FOXA2) across MiSTR sections A-E vs organoid conditions Ctrl-E (reproduce Extended Data Fig. 10a,b)"
  - id: F8
    title: "MiSTR gradient vs. discontinuous organoid DV patterning (H9)"
    panels:
      - "Harmony-integrated UMAP of MiSTR + organoid cells, colored by cluster, source, and segment (reproduce Fig. 6c,d,e)"
      - "Cell-type composition per MiSTR segment A-E vs organoid Ctrl-E (reproduce Fig. 6g,h)"
      - "DV distribution histogram binned by DV_rank, MiSTR vs organoid (reproduce Extended Data Fig. 10d)"
      - "Primary-atlas / HNOCA presence-score projection: max presence per condition for Braun regions (cortex / hypothalamus / midbrain / medulla / pons), reproducing Extended Data Fig. 8g,h and 9e,f"

figures_not_in_scope:
  - "Live light-sheet imaging reconstruction (Fig. 6m, Extended Data Fig. 10f) — requires raw imaging data beyond ArrayExpress scope"
  - "HCR in situ hybridization images (Fig. 2d, Fig. 6j-l, Extended Data Figs. 3l,m and 4c) — image-level reproduction is out of scope for a dry-lab run"
  - "Wet-lab protocol reproduction of any kind (organoid culture, MiSTR device, cell hashing)"

budgets:
  max_phase_wall_minutes: 120
  max_total_wall_minutes: 900
  allow_web: true
  claude_model: claude-opus-4-7
  max_phase_budget_usd: 30.0

auto_approve: true
---

# Reproduction of Sanchís-Calleja et al. 2026 morphogen screening atlas

## Source materials

This test case is built from a single user-provided PDF in this
directory:

- `s41592-025-02927-5.pdf` — the published paper:
  Sanchís-Calleja, Azbukina, Jain, He, et al., *Systematic scRNA-seq
  screens profile neural organoid response to morphogens*, Nature
  Methods 23, 465–478 (February 2026).
  DOI: `https://doi.org/10.1038/s41592-025-02927-5`

All raw / processed data required for reproduction are publicly
deposited. Nothing is pre-staged on disk; every accession listed
above is fetched on the way and cached under
`./datasets/<accession>/`.

## Why this matters for OmiCairn

This is a reproduction test case of very different character from the
Mitoxyperilysis cancer-genomics run:

1. **Full pipeline reproduction, not hypothesis generation.** The
   scientific claims are already made by the authors; our job is to
   verify that the deposited data, when re-processed with the
   documented methods, gives the published numbers.

2. **Single-cell, multimodal, multi-atlas.** Unlike bulk Cox
   regression, this requires RSS integration, SCENIC regulon
   inference, elastic-net AP/DV scoring on an external reference
   atlas, and distance-metric-based reproducibility analysis. OmiCairn
   must orchestrate scanpy / anndata / pySCENIC / scArches /
   (optionally Seurat via rpy2 or R kernel) in one pipeline.

3. **Method parity is load-bearing.** The authors explicitly used
   RSS integration (their own method, CSS/RSS from He 2020) against
   the Braun 2023 atlas. Swapping in Harmony or scVI will perturb
   cluster boundaries and break H1. EXECUTE should clone
   `quadbio/organoid_patterning_screen` and call the authors'
   scripts rather than reimplement.

4. **Honest failure reporting.** scRNA-seq reproductions often miss
   the exact cluster count by ±1–2 even with faithful methods
   (resolution sensitivity, random-seed drift in UMAP). OmiCairn should
   report "we recovered 17 clusters where the paper reports 18;
   sub-cluster X was merged with Y in our run" rather than tune
   clustering until the number matches.

## Known prior results (for OmiCairn cross-check, NOT for the reproduction report to cite as "our numbers")

From the paper's own figures and tables (these values are here so a
reviewer can sanity-check OmiCairn's output, not so OmiCairn can copy them):

| Claim                                        | Paper value |
|----------------------------------------------|-------------|
| Total cells, patterning condition screen     | 100,538     |
| Total cells, reproducibility screen          | 209,902     |
| Clusters, patterning condition screen        | 18          |
| Clusters, reproducibility screen             | 13          |
| # morphogen conditions (screen 1)            | 97          |
| # samples (screen 2)                         | 192         |
| hPSC lines in screen 1                       | HES3, H9, WTC |
| hPSC lines in screen 2                       | H1, H9, WIBJ2, WTC |
| NIMs compared in screen 2                    | MNIM, dual SMAD |
| Regulons detected in screen 1 (full SCENIC)  | 413 (avg size 141 genes) |
| Consensus regulons in screen 2 (100 SCENIC runs per line) | 136 (avg size 88 genes) |
| MiSTR-vs-organoid integrated clusters        | 9           |

Marquee directional findings OmiCairn must recover (paper-level evidence
listed in the F2/F4/F5 panels above):

- RA low concentration → retinal; RA high → hindbrain + floor plate
  (Fig. 2c, Extended Data Fig. 3).
- SHH high → NKX2-1+ hypothalamic fate (Extended Data Fig. 3d, 4).
- Dual SMAD induction produces ~85–95% neurons vs. ~15% in MNIM
  (Extended Data Fig. 9c, 2-sided t-test adj. p = 9.4×10⁻¹¹).
- Cross-line variability is higher under dual SMAD than MNIM
  (Fig. 4i).

If OmiCairn's numbers disagree with these by > 10% without a
methodological explanation, that is a cross-check failure and
belongs in `analysis.md § Limitations`, not in the headline result.

## Out of scope (for this iteration)

- **Wet-lab reproduction** of any protocol (organoid generation,
  MiSTR device assembly, cell hashing antibody staining, HCR
  in situ). This is purely a dry-lab reproduction.
- **Raw FASTQ re-alignment.** We use the authors' processed count
  matrices from ArrayExpress / Zenodo unless they are missing,
  corrupt, or fail parsing. Re-running Cell Ranger / Parse pipeline
  on raw FASTQ is allowed only if the processed objects are
  genuinely unrecoverable.
- **Light-sheet imaging analysis** (Fig. 6m, Extended Data Fig. 10f).
  The imaging data are not in ArrayExpress; reconstructing the
  NKX2.1-GFP focal-spread analysis is a separate test case.
- **Image quantification of HCR stainings** (Fig. 2d, Fig. 6j-l).
  Requires raw microscopy data not deposited with the transcriptomic
  accessions.
- **CellFlow / generative flow-matching modelling** (ref. 57 /
  bioRxiv 2025.04.11.648220). The paper only gestures at this as a
  downstream application; reproducing it is a follow-up project.

## Appendix A — Figure-by-figure reproduction plan

The eight planned figures (F1–F8) map onto the paper's main and
extended figures as follows. OmiCairn should treat this mapping as
authoritative for scoping; deviations must be justified in
`analysis.md`.

### F1 ← Paper Fig. 1 + Extended Data Figs. 1–2
Re-derived cell atlas of the patterning condition screen (E-MTAB-15667).
Authority: the paper's Methods section "Single-cell gene expression
data analysis" — QC thresholds (nGene > 1000, % mito < 10),
SCTransform normalisation, RSS integration against Braun 2023,
SCENIC exclusion of non-neural clusters for regulon analysis.

### F2 ← Paper Fig. 2a–d + Extended Data Figs. 3, 4, 5
Morphogen timing × concentration effects in HES3.
Authority: the authors' own `Morphogen_Screen_Enrichment.R` script
(GitHub: quadbio/organoid_patterning_screen). EXECUTE should call
it with `command="Rscript ..."` rather than reimplement Fisher's
exact testing. Supplementary Table 3 is the ground-truth for
enrichment scores.

### F3 ← Paper Fig. 2e–h + Extended Data Fig. 5
SCENIC regulon inference on HES3.
Authority: pySCENIC v0.12.1 + clustered v10 motif database +
GRNBoost2 with morphogen + timing + interaction terms as
pseudo-features. Supplementary Table 4 is the ground-truth
morphogen–regulon edge table. SCENIC is stochastic; fix
`random_state=0` and report top-K edge overlap rather than exact
weights.

### F4 ← Paper Fig. 3 + Extended Data Fig. 6
Morphogen combinations and non-linearity (HES3 + WTC).
Authority: pertpy ≥ 0.9.4 for MMD on 20-dim PCA of RSS
embeddings, scipy.optimize.nnls for non-additivity residuals.

### F5 ← Paper Fig. 4 + Extended Data Figs. 7–9
Patterning reproducibility screen (E-MTAB-15622).
Authority: scanpy (v≥1.10.3) pipeline as documented in Methods
"For reproducibility experiment analysis, we used the Scanpy
Python package"; KLD via scipy.stats.entropy, MMD / E-distance
via pertpy. The split-pool Parse v3 data has different QC
thresholds than the 10x data (nUMI > 750, < 20000, % mito < 10).

### F6 ← Paper Fig. 5
Consensus SCENIC across 4 hPSC lines × 2 NIMs.
Authority: 100 subsampled SCENIC runs per line per NIM, consensus
regulon construction, Ramsey RESET test for non-linearity. This
is the most compute-heavy single component (thousands of core-hours
in the original paper); EXECUTE should budget for a reduced-run
variant (e.g. 20 SCENIC runs per line) with explicit acknowledgement
of the reduced statistical power, and compare consensus stability
at 20 runs vs. the published 100-run consensus.

### F7 ← Paper Fig. 4l + Extended Data Fig. 8f–h + Fig. 6i
AP/DV scoring via Braun-atlas-trained elastic net.
Authority: glmnet default parameters on scANVI latent
representation, label smoothing via Jaccard-weighted neighbourhood
majority, ventral/dorsal marker modules as defined in Methods
"Estimation of AP and DV scores".

### F8 ← Paper Fig. 6 + Extended Data Fig. 10
MiSTR vs. discrete-organoid DV patterning (H9 only).
Authority: Harmony / RPCA / CCA / CSS integration (any of these is
acceptable per Methods; pick one and document), forebrain-axis
scoring on telencephalic + hypothalamic progenitor subset only.

## Appendix B — Method-parity checklist

Before declaring a figure "reproduced", EXECUTE MUST verify that
the following toolchain decisions match the paper's Methods:

1. **Integration method**: RSS (via the authors' `CSS`/`RSS` R
   package, He et al. 2020) against the Braun 2023 atlas, NOT
   Harmony / scVI as default. If RSS is unavailable, document the
   substitution and quantify cluster-agreement loss.
2. **Cell-cycle regression**: PCs with |cor with S.Score or
   G2M.Score| > 0.3 are dropped from downstream clustering.
   Mitochondrial + ribosomal genes are ignored throughout.
3. **Hashtag demultiplexing**: Seurat's `HTODemux` with
   `positive.quantile ∈ [0.99, 0.999]` (batch-dependent).
4. **Cell-line demultiplexing**: `demuxlet` (screen 1) or
   `cellsnp-lite` + `vireo` (screen 2); Y-chromosome gene average
   as tiebreaker for ambiguous cells.
5. **SCENIC**: pySCENIC 0.12.1, clustered v10 motif DB, AUCell for
   per-cell scoring, GRNBoost2 for morphogen-regulon linking with
   `weight > 200` edge threshold and Pearson correlation to
   concentration / timing.
6. **Distance metrics**: pertpy implementations of MMD and
   E-distance on 20-dim PCA of RSS embeddings; scipy `entropy` for
   KLD on cell-type proportion vectors.

Any deviation from this checklist belongs in the "Methods
deviations" subsection of `analysis.md`.
