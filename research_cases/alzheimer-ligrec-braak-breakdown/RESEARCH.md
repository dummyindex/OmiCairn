---
research_mode: DISCOVERY
domain: neurodegeneration_scRNAseq
topic_slug: alzheimer-ligrec-braak-breakdown

research_question: |
  Direction d5 from the Alzheimer's-mechanism IDEATE run
  (workspaces/ideate-alzheimer-mechanism-20260606-153912/000_ideate).

  Which ligand-receptor (L-R) communication axes between microglia,
  astrocytes, and excitatory/inhibitory neurons are most disrupted with
  advancing Braak stage in human AD snRNA-seq atlases; do the disrupted
  signals cluster in pathways encoded by AD GWAS risk genes; and can
  the identity of disrupted signals predict the next region of tau
  spread (EC→hippocampus→PFC sequence)?

  De Strooper's "network disease" framing motivates this: AD emerges
  from the breakdown of coordinated glial-neuronal responses, not from
  any single cell-autonomous lesion. Prior work identified dysregulated
  L-R interactions in AD PFC and integrated GWAS risk into that
  framework, but did not address the TEMPORAL dynamics across Braak
  stage. This OmiCairn run quantifies cell-cell communication across Braak
  stage in open human AD snRNA-seq using open L-R inference frameworks
  (e.g. CellChat / NicheNet), tests GWAS enrichment of the disrupted
  axes, and explores whether disrupted signals presage tau spread.

  Hypotheses (pre-registered, lifted from d5):

    H1 (primary — three most-disrupted axes, monotone with Braak):
        Complement-related signaling (C1Q-C3-CR3 axis: microglia→neuron),
        SPP1 (microglia→astrocyte/oligodendrocyte), and CXCL10-CXCR3
        (glia→T-cell recruitment) are the three most disrupted
        communication axes in AD, with disruption magnitude increasing
        monotonically with Braak stage.
        Decision rule: per-donor pseudobulk L-R interaction scores
        (CellChat communication probability or equivalent) regressed on
        Braak stage (ordinal); report slope + p-value + 95% CI per axis.
        H1 passes if the three named axes rank among the top disrupted
        interactions AND each shows a positive monotone slope with Braak
        (p<0.05, 95% CI excluding 0). Report the full ranked disruption
        table regardless — if a different axis dominates, that is the
        finding.

    H2 (secondary — GWAS enrichment of disrupted axes):
        Disrupted L-R interactions at Braak stage III-IV are
        significantly enriched in pathways encoded by genes in the top
        20 AD GWAS loci (specifically BIN1, CLU, CR1, MS4A, PICALM,
        TREM2), indicating that GWAS-identified risk converges on
        inter-cellular communication failure rather than purely
        cell-autonomous dysfunction.
        Decision rule: hypergeometric / permutation enrichment test of
        the Braak III-IV disrupted-interaction gene set against the
        top-20 AD GWAS locus gene set; report odds ratio / enrichment
        statistic + p-value + 95% CI. H2 passes if enrichment is
        significant (p<0.05) with a 95% CI excluding the null.

  A negative or partial result is fully informative. If complement /
  SPP1 / CXCL10 are NOT the top disrupted axes, report the actual
  ranking as the headline. If GWAS enrichment is absent, report that
  the disrupted communication network does not preferentially involve
  GWAS loci. Do not re-tune the L-R inference until H1/H2 pass.

  The tau-spread prediction (EC→hippocampus→PFC) is exploratory and
  multi-region: with a single-region primary atlas it can only be
  framed as a hypothesis-generating direction, not tested. Report it as
  such unless a multi-region open atlas (SEA-AD) is successfully
  fetched and harmonised.

data:
  location: ./research_cases/alzheimer-ligrec-braak-breakdown/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** Every dataset below is publicly
    deposited and MUST be fetched at run time by EXECUTE into the shared
    cache `./datasets/<accession>/`. EXECUTE MUST use REAL fetched
    public data — prefer openly-downloadable GEO supplementary files
    (processed h5ad / matrix + barcodes + features). If a dataset or a
    required covariate (Braak stage, cell-type labels) is blocked,
    missing, or unparseable, report that honestly in
    `analysis.md § Limitations` and fall back to the remaining open
    data — NEVER simulate or fabricate. Acceptable reasons to defer a
    fetch: (1) HTTP 404/410 / accession removed; (2) sha256
    verification fails; (3) `budgets.allow_web` is false. Size alone is
    NOT acceptable.

    All reported effects MUST carry effect size + p-value + 95% CI
    together. A p-value alone is not a result.

    **Primary OPEN dataset:**

      - **Lau 2020 (PFC reference atlas).**
        GEO **GSE157827** — prefrontal cortex snRNA-seq, 169,496 nuclei
        from AD and control donors. Openly downloadable at NCBI GEO.
        Sufficient for a first-pass L-R disruption analysis (H1) and
        the GWAS-enrichment test (H2).
          https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE157827
        Target path: ./datasets/GSE157827/
        NOTE: Braak-stage / disease-severity metadata is required to
        regress L-R scores on Braak (H1). If only binary AD-vs-control
        labels are present, EXECUTE must report that the monotone-Braak
        part of H1 is untestable and fall back to an AD-vs-control L-R
        disruption contrast.

    **Secondary OPEN dataset (multi-region, neuropathology):**

      - **SEA-AD (Gabitto 2024).** Open multimodal AD atlas with
        snRNA-seq across multiple regions and the full severity
        spectrum, plus quantitative neuropathology. Use for the
        Braak-stage trajectory (H1) and, if multiple regions harmonise,
        the exploratory tau-spread direction (EC→hippocampus→PFC).
        Allen Brain Map portal / AWS open data registry / Synapse
        syn64410371.
          https://portal.brain-map.org/explore/seattle-alzheimers-disease
        Target path: ./datasets/seaad_syn64410371/

    **Optional / aspirational secondary — ACCESS-CONTROLLED, do NOT
    depend on:**

      - **Mathys 2019 (ROSMAP PFC snRNA-seq).** Synapse syn3219045 —
        ACCESS-CONTROLLED via the AD Knowledge Portal; fetching requires
        a data-use application. Listed only as an aspirational secondary
        for a future iteration. The case MUST NOT depend on it. EXECUTE
        must NOT attempt to fetch it and must NOT block on it.
          https://www.synapse.org/#!Synapse:syn3219045
        (controlled-access; not fetched in this run)

    **Reference gene sets:**

      - **Top-20 AD GWAS loci** (BIN1, CLU, CR1, MS4A, PICALM, TREM2,
        and the remaining top loci) — for the H2 enrichment test. Lift
        from a published AD GWAS meta-analysis (e.g. Bellenguez 2022 /
        Nalls 2024) and record the source + locus list in
        `analysis.md`.
        Target path: ./datasets/ad_gwas_loci/

    **L-R inference reference:** CellChatDB / NicheNet ligand-receptor
    databases (open). Record version + source in `analysis.md`.

    **Shared dataset cache convention:** every accession is downloaded
    into `./datasets/<accession>/` with a sidecar `SOURCE.txt` recording
    URL, fetch timestamp, sha256, byte count, and the parsing tool used
    (scanpy.read_h5ad, anndata.read, scanpy.read_mtx, etc.). Subsequent
    runs check the cache first and skip re-downloads.

  cohorts: [lau_2020_PFC, seaad_2024_multiregion]
  modalities: [snRNA_seq_10x]

success_criteria:
  primary_metric: ligrec_disruption_braak_trajectory
  baseline: complement_spp1_cxcl10_named_axes
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = H1: the three named axes (complement C1Q-C3-CR3,
    SPP1, CXCL10-CXCR3) rank among the top disrupted L-R interactions
    AND each shows a positive monotone slope with Braak stage (p<0.05,
    95% CI excluding 0). If a different axis dominates, report the
    actual ranked disruption table as the headline finding. If only
    binary AD-vs-control labels are available, report the
    monotone-Braak claim as untestable and fall back to an AD-vs-control
    contrast.

    H2 success = disrupted Braak III-IV interactions are significantly
    enriched in the top-20 AD GWAS locus gene set (p<0.05, 95% CI
    excluding the null). Report the enrichment statistic + CI either
    way.

    Tau-spread (EC→hippocampus→PFC) is exploratory; only attempt if a
    multi-region open atlas (SEA-AD) is fetched and harmonised.
    Otherwise report it as a hypothesis-generating direction.

    All effects reported as effect size + p-value + 95% CI together.

figures_planned:
  - id: F1
    title: "Cell-type taxonomy and global communication network"
    panels:
      - "UMAP of PFC nuclei colored by major cell type (microglia, astrocyte, excitatory/inhibitory neuron, oligo, OPC, endothelial)"
      - "CellChat global interaction-strength network, AD vs control"
      - "Differential interaction heatmap: AD-vs-control change per sender→receiver pair"
  - id: F2
    title: "Disrupted L-R axes across Braak stage (H1)"
    panels:
      - "Ranked disruption bar chart: top L-R interactions by |Braak slope|, the three named axes annotated"
      - "Per-axis communication probability vs Braak stage with slope + 95% CI (complement, SPP1, CXCL10-CXCR3)"
      - "Sender→receiver chord diagrams at Braak 0/II/IV for the named axes"
  - id: F3
    title: "GWAS convergence on disrupted communication (H2)"
    panels:
      - "Enrichment: Braak III-IV disrupted-interaction genes vs top-20 AD GWAS loci; OR + 95% CI + p"
      - "Locus-level overlap map: which GWAS loci (BIN1, CLU, CR1, MS4A, PICALM, TREM2) sit in disrupted axes"
      - "Permutation null distribution vs observed enrichment"
  - id: F4
    title: "Exploratory multi-region tau-spread and honest-failure panel"
    panels:
      - "If SEA-AD multi-region available: disrupted-axis intensity across EC / hippocampus / PFC vs regional tau"
      - "Metadata audit: Braak / region / cell-type-label availability per fetched atlas"
      - "Sensitivity of H1 ranking to L-R database (CellChatDB vs NicheNet) and to cell-type label granularity"

figures_not_in_scope:
  - "Raw FASTQ re-alignment (processed objects suffice)"
  - "Spatial-transcriptomics tau-spread mapping (snRNA only here)"
  - "Any analysis that depends on access-controlled Mathys 2019 / ROSMAP (syn3219045)"
  - "Wet-lab validation of ligand-receptor signaling"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 360
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 25.0

auto_approve: true
---

# alzheimer-ligrec-braak-breakdown — L-R communication breakdown across Braak stage (IDEATE d5)

## Source materials

Transcribed faithfully from IDEATE direction **d5**
(`workspaces/ideate-alzheimer-mechanism-20260606-153912/000_ideate/directions.json`).
Anchor and supporting literature lifted from the direction's `cites`:

- Lau S.F., Cao H., Fu A.K.Y., Ip N.Y. *Single-nucleus transcriptome
  analysis reveals dysregulation of angiogenic endothelial cells and
  neuroprotective glia in Alzheimer's disease.* PNAS 117 (2020).
  GEO GSE157827.
- Cui M.Z. et al. *Characterizing dysregulations via cell-cell
  communications in Alzheimer's brains using single-cell
  transcriptomes.* (2024). (107 dysregulated L-R interactions, PFC.)
- Wang P. et al. *Unraveling the intercellular communication disruption
  and key pathways in Alzheimer's disease.* (2024). (GWAS integration.)
- Carpanini S.M. et al. *Complement-mediated synapse loss in
  Alzheimer's disease.* Trends in Neurosciences (2024).
- Jin M. et al. *Infiltrating CD8+ T cells exacerbate Alzheimer's
  disease pathology* (2023). (CXCL10-CXCR3 axis.)
- Gabitto M.I. et al. *SEA-AD* (2024). OPEN — Synapse syn64410371.
- Mathys H. et al. *Single-cell transcriptomic analysis of Alzheimer's
  disease.* Nature (2019). ROSMAP/Synapse syn3219045 —
  **ACCESS-CONTROLLED** (AD Knowledge Portal application); NOT a
  dependency of this case.

## Why this matters for OmiCairn

If AD is a "network disease," then the disease signature should live in
the *edges* of the glial-neuronal communication graph, not only in the
nodes. This case asks OmiCairn to test the strongest version of that claim:
that specific, named L-R axes break down monotonically with Braak stage
and that GWAS risk converges on them. The question is informative
whether or not the named axes win, and the design deliberately forces
the agent to report the true ranked disruption table.

OmiCairn challenges this exercises:

1. **Cell-cell communication inference** with an open L-R database
   (CellChat / NicheNet) on real human snRNA-seq.
2. **Per-donor pseudobulk + ordinal Braak regression** of interaction
   scores rather than per-cell tests.
3. **Gene-set enrichment** against an externally-fixed GWAS locus list
   for H2.
4. **Honest scoping** — the multi-region tau-spread sub-question is
   only testable with multi-region open data (SEA-AD); otherwise it is
   explicitly hypothesis-generating, and the access-controlled Mathys
   2019 atlas must NOT become a dependency.

## Critical correctness rules (EXECUTE must obey)

- Use REAL fetched public data only. Prefer openly-downloadable GEO
  supplementary files. If blocked/missing → say so in Limitations and
  fall back; NEVER simulate.
- Report effect size + p-value + 95% CI together for every claim.
- Primary atlas is the OPEN GSE157827. Do NOT attempt to fetch or
  depend on access-controlled syn3219045 (Mathys 2019); it is
  aspirational-only.
- Report the full ranked L-R disruption table, not only the three
  pre-named axes.

## Out of scope (for this iteration)

- Raw FASTQ re-alignment.
- Spatial-transcriptomics tau-spread mapping.
- Any dependency on access-controlled ROSMAP / Mathys 2019.
- Wet-lab validation of ligand-receptor signaling.
