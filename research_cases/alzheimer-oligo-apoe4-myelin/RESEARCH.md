---
research_mode: DISCOVERY
domain: neurodegeneration_scRNAseq
topic_slug: alzheimer-oligo-apoe4-myelin

research_question: |
  Direction d1 from the second Alzheimer's-mechanism IDEATE run
  (workspaces/ideate-alzheimer-mechanism-20260607-044616/000_ideate).

  Does APOE4 carrier status predict an oligodendrocyte-specific
  transcriptional and chromatin-accessibility signature of cholesterol
  metabolism failure and early myelin breakdown in human brain,
  measurable before widespread amyloid or tau pathology (Braak 0-II),
  and if so, which regulatory elements (CREs) mediate this APOE4 effect?

  The lab's prior AD runs have probed microglial APOE4 effects (LDAM/DAM
  lipid droplets) and ligand-receptor neuroinflammation, but have not
  touched oligodendrocytes. The Gabitto 2024 SEA-AD atlas identified an
  early OPC remyelination response that precedes the late exponential
  pathology phase, suggesting myelin stress is an early event. Huang's
  group established that APOE4 disrupts cholesterol biosynthesis in
  oligodendroglia, reducing the cholesterol available for myelin
  synthesis; APOE4 carries one of the strongest GWAS signals, with CLU
  (an oligodendrocyte-expressed gene) as a co-regulatory target in the
  Morabito 2021 multiome (GSE174367). This OmiCairn run re-analyses open
  human snRNA-seq (and the Morabito snATAC arm) by APOE genotype,
  computes oligodendrocyte cholesterol-biosynthesis and myelin programs,
  and asks which cis-regulatory elements mediate the APOE4 effect.

  Hypotheses (pre-registered, lifted from d1):

    H1 (primary — oligodendrocyte cholesterol-biosynthesis deficit):
        APOE4 homozygous individuals show a statistically significant
        reduction in cholesterol biosynthesis gene expression (HMGCS1,
        FDFT1, SQLE) specifically in oligodendrocytes relative to APOE3
        carriers at Braak 0-II, with effect size d > 0.3 in pseudobulk
        analysis.
        Decision rule: per-donor pseudobulk DE within the
        oligodendrocyte compartment, APOE4/4 vs APOE3/3 restricted to
        Braak 0-II donors; report log2FC + Benjamini-Hochberg FDR +
        95% bootstrap CI per gene, plus a cholesterol-biosynthesis
        module Cohen's d. H1 passes if the module effect size d > 0.3 in
        the reduction direction AND >=2 of the 3 named genes are down at
        FDR<0.05.

    H2 (secondary — CLU/APOE locus accessibility and CRE motifs):
        Chromatin accessibility at the CLU/APOE co-regulatory locus is
        reduced in oligodendrocytes from APOE4 vs. APOE3 donors in
        GSE174367 snATAC data, with accessible CREs enriched for SREBF1
        and NKX2-2 binding motifs.
        Decision rule: per-donor pseudobulk differential accessibility
        on oligodendrocyte snATAC peaks at/near the CLU and APOE loci,
        APOE4 vs APOE3; report log2FC + FDR + 95% CI for the locus
        accessibility, and a motif-enrichment test (e.g.
        chromVAR / HOMER) for SREBF1 and NKX2-2 in the differentially
        accessible oligodendrocyte CREs with enrichment statistic +
        p-value + 95% CI. H2 passes if the CLU/APOE locus accessibility
        is significantly reduced (FDR<0.05, 95% CI excluding 0) AND at
        least one of SREBF1/NKX2-2 is significantly enriched.

  IMPORTANT — covariate dependency: H1 requires APOE genotype and
  Braak/tau metadata in the same donors; H2 requires APOE genotype plus
  a usable snATAC arm with oligodendrocyte peaks. EXECUTE MUST first
  verify these are present in the fetched objects. If APOE genotype is
  missing, the genotype contrast cannot be tested — report that honestly
  and fall back to a Braak-stratified oligodendrocyte cholesterol/myelin
  description without the genotype split. If Braak staging is missing,
  the Braak 0-II restriction cannot be applied — say so and report the
  whole-cohort oligodendrocyte contrast, flagged as not early-stage
  specific. If no fetched dataset carries a usable snATAC arm, H2 is
  untestable — report that and restrict to the H1 transcriptional axis.
  Do NOT impute or fabricate genotype, neuropathology, or peaks.

  A negative or partial result is fully informative. If APOE4 does not
  reduce oligodendrocyte cholesterol biosynthesis, or the CLU/APOE locus
  shows no accessibility change, that must be the headline rather than
  re-tuning cluster definitions or marker sets until H1/H2 pass.

data:
  location: ./research_cases/alzheimer-oligo-apoe4-myelin/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Every dataset below is publicly
    deposited and MUST be fetched at run time by EXECUTE into the shared
    cache `./datasets/<accession>/`. EXECUTE MUST use REAL fetched
    public data — prefer openly-downloadable GEO supplementary files
    (processed h5ad / matrix + barcodes + features). If a dataset or a
    required covariate (APOE genotype, Braak/tau) is blocked, missing,
    or unparseable, report that honestly in `analysis.md § Limitations`
    and fall back to the remaining open data — NEVER simulate or
    fabricate. Acceptable reasons to defer a fetch: (1) HTTP 404/410 /
    accession removed; (2) sha256 verification fails; (3)
    `budgets.allow_web` is false. Size alone is NOT acceptable.

    All reported effects MUST carry effect size + p-value + 95% CI
    together. A p-value alone is not a result.

    **Primary OPEN dataset (multiome — RNA + ATAC):**

      - **Morabito 2021 (PFC multiome).**
        GEO **GSE174367** — snRNA-seq + snATAC-seq (191,890 nuclei) of
        prefrontal cortex from AD and control donors. Openly
        downloadable at NCBI GEO. Has oligodendrocyte clusters and an
        APOE-CLU regulatory module. Use the snRNA arm for H1 and the
        snATAC arm for H2.
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE174367
        Target path: ./datasets/GSE174367/
        NOTE: APOE genotype + tau/Braak metadata must be present for the
        genotype contrast and the Braak 0-II restriction. EXECUTE must
        confirm these columns exist in the fetched object before testing;
        if a covariate is absent, report honestly and fall back (see
        research_question above).

    **Secondary OPEN dataset (transcriptional replication):**

      - **Lau 2020 (PFC snRNA-seq).**
        GEO **GSE157827** — ~170,000 nuclei, prefrontal cortex, AD vs
        control; includes oligodendrocyte transcriptional changes.
        Openly downloadable at NCBI GEO. Use as a replication cohort for
        the H1 oligodendrocyte cholesterol-biosynthesis contrast.
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE157827
        Target path: ./datasets/GSE157827/

    **Optional tertiary (spatial localisation):**

      - **SEA-AD (Gabitto 2024).** Open multimodal AD atlas with MERFISH
        spatial data that can localise myelin lesions near plaques and
        carries APOE genotype + quantitative neuropathology. Candidate —
        EXECUTE to verify access; report blocked if unavailable, do not
        fabricate. Allen Brain Map portal / AWS open data registry /
        Synapse syn64410371.
          https://portal.brain-map.org/explore/seattle-alzheimers-disease
        Target path: ./datasets/seaad_syn64410371/

    **Shared dataset cache convention:** every accession is downloaded
    into `./datasets/<accession>/` with a sidecar `SOURCE.txt` recording
    URL, fetch timestamp, sha256, byte count, and the parsing tool used
    (scanpy.read_h5ad, anndata.read, scanpy.read_mtx, snapatac2, etc.).
    Subsequent runs check the cache first and skip re-downloads.

  cohorts: [morabito_2021_PFC, lau_2020_PFC, seaad_2024_multiregion]
  modalities: [snRNA_seq_10x, snRNA_seq_10x_multiome, snATAC_seq_10x]

success_criteria:
  primary_metric: apoe4_oligo_cholesterol_biosynthesis_deficit
  baseline: oligodendrocyte_cholesterol_and_myelin_canonical_marker_sets
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = H1 oligodendrocyte cholesterol-biosynthesis module
    shows a reduction with effect size d > 0.3 and >=2 of HMGCS1/FDFT1/
    SQLE down at FDR<0.05 in APOE4/4 vs APOE3/3 oligodendrocytes
    restricted to Braak 0-II, in at least one atlas with the required
    covariates. If no fetched atlas carries APOE genotype, H1 is
    untestable — report that as the headline limitation, not a failure
    to fix by imputation.

    H2 success = CLU/APOE oligodendrocyte locus accessibility is
    significantly reduced (FDR<0.05, 95% CI excluding 0) in APOE4 vs
    APOE3 AND at least one of SREBF1/NKX2-2 is significantly enriched in
    the differentially accessible CREs. If no usable snATAC arm is
    fetched, mark H2 untestable and report the H1 transcriptional axis
    only.

    All effects reported as effect size + p-value + 95% CI together.
    Partial results (e.g. transcriptional deficit present but no
    accessibility change) must be stated plainly in `analysis.md` and
    `negative_results.md`, not framed as success.

figures_planned:
  - id: F1
    title: "Oligodendrocyte taxonomy and myelin/cholesterol programs"
    panels:
      - "UMAP of all cells with oligodendrocyte and OPC compartments labeled"
      - "Marker dot plot: oligodendrocyte (PLP1, MBP, MOG) and cholesterol-biosynthesis (HMGCS1, FDFT1, SQLE) genes across cell types"
      - "Per-donor oligodendrocyte fraction and myelin module score, grouped by APOE genotype and Braak stage"
  - id: F2
    title: "APOE4 oligodendrocyte cholesterol-biosynthesis deficit (H1)"
    panels:
      - "Per-donor pseudobulk forest plot: log2FC + 95% CI for HMGCS1, FDFT1, SQLE, APOE4/4 vs APOE3/3 at Braak 0-II"
      - "Cholesterol-biosynthesis module Cohen's d with 95% CI, APOE4/4 vs APOE3/3"
      - "Volcano: oligodendrocyte DE APOE4/4 vs APOE3/3 with the cholesterol panel annotated"
  - id: F3
    title: "CLU/APOE locus accessibility and CRE motifs (H2)"
    panels:
      - "Oligodendrocyte snATAC pseudobulk track at the CLU and APOE loci, APOE4 vs APOE3"
      - "Differential accessibility log2FC + 95% CI for CLU/APOE-region peaks"
      - "Motif-enrichment bar plot for SREBF1 and NKX2-2 in differentially accessible oligodendrocyte CREs, enrichment + 95% CI"
  - id: F4
    title: "Covariate-availability and honest-failure panel"
    panels:
      - "Covariate audit table: APOE genotype / Braak / snATAC-arm availability per fetched atlas"
      - "Cluster-resolution sensitivity of the oligodendrocyte split"
      - "Sensitivity to cholesterol-biosynthesis / myelin marker-set definition"

figures_not_in_scope:
  - "Raw FASTQ re-alignment (processed objects suffice)"
  - "Direct lipidomics / cholesterol mass-spec (transcriptional + accessibility surrogate only)"
  - "iPSC oligodendrocyte or mouse myelin validation — wet-lab"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 360
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# alzheimer-oligo-apoe4-myelin — APOE4 oligodendrocyte cholesterol/myelin failure (IDEATE d1)

## Source materials

Transcribed faithfully from IDEATE direction **d1**
(`workspaces/ideate-alzheimer-mechanism-20260607-044616/000_ideate/directions.json`).
Anchor and supporting literature lifted from the direction's `cites`:

- Morabito S. et al. *Single-nucleus chromatin accessibility and
  transcriptomic characterization of Alzheimer's disease.* Nature
  Genetics (2021). GEO GSE174367 (snRNA + snATAC multiome, PFC;
  oligodendrocyte-APOE-CLU regulatory module).
- Gabitto M.I., Travaglini K.J. et al. *Integrated multimodal cell atlas
  of Alzheimer's disease.* Nature Neuroscience (2024). SEA-AD (early OPC
  remyelination response precedes late exponential pathology).
- *Are oligodendrocytes the missing link in Alzheimer's disease and
  related dementia research?* Molecular Neurodegeneration (2024).
- *APOE4 impairs myelination via altered cholesterol biosynthesis and
  transport in oligodendroglia* (2025).
- *Cell type-specific roles of APOE4 in Alzheimer disease.* Nature
  Reviews Neuroscience (2024).
- Yadong Huang (Gladstone): APOE4 cell-type-specific pathology
  (2024 Sobrato Prize).
- Lau S.F. et al. *Single-nucleus transcriptome analysis.* (2020). GEO
  GSE157827 (PFC, includes oligodendrocyte changes).

## Why this matters for OmiCairn

APOE4 is the dominant common-variant risk factor for AD, and the field
has focused its lipid story on microglia. Oligodendrocytes — the
brain's principal cholesterol consumers for myelin — are an
under-explored APOE4 axis. This case asks OmiCairn to test whether APOE4
starves oligodendrocyte cholesterol biosynthesis early (Braak 0-II) and
whether the effect is written in chromatin at the CLU/APOE locus. The
answer is informative either way, and the case deliberately stresses
honest handling of missing covariates and a missing snATAC arm.

OmiCairn challenges this exercises:

1. **Genotype-stratified, early-Braak-restricted contrast** within the
   oligodendrocyte compartment.
2. **Per-donor pseudobulk DE** rather than per-cell DE.
3. **snATAC differential accessibility + motif enrichment** at a named
   locus for H2.
4. **Honest covariate auditing** — the genotype contrast, the Braak
   0-II restriction, and H2 are each untestable if the relevant
   metadata or modality is absent; the agent must say so rather than
   impute.

## Critical correctness rules (EXECUTE must obey)

- Use REAL fetched public data only. Prefer openly-downloadable GEO
  supplementary files. If blocked/missing → say so in Limitations and
  fall back; NEVER simulate.
- Report effect size + p-value + 95% CI together for every claim.
- Audit covariate availability (APOE genotype, Braak/tau) and snATAC-arm
  availability BEFORE asserting H1/H2 results; if a covariate or
  modality is missing, mark the relevant hypothesis untestable and fall
  back to a descriptive analysis.
- Never fabricate accessions. SEA-AD is a candidate — verify at fetch
  time; report blocked if unavailable.

## Out of scope (for this iteration)

- Raw FASTQ re-alignment.
- Direct lipidomics / cholesterol mass-spec (transcriptional +
  accessibility surrogate only).
- iPSC oligodendrocyte or mouse myelin validation.
