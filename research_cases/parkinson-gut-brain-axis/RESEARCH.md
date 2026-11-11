---
research_mode: DISCOVERY
domain: neurodegeneration_microbiome_transcriptomic_integration
topic_slug: parkinson-gut-brain-axis

research_question: |
  Wallen, Z.D., Demirkan, A., Twa, G., ..., Payami, H.
  (Nat Commun 13:6958, 2022; DOI 10.1038/s41467-022-34667-x;
  SRA PRJNA834801; Zenodo 7246185) reported that >30% of bacterial
  species, genes, and metabolic pathways are altered in PD vs.
  controls in a shotgun-metagenomic cohort of 490 PD + 234 controls,
  describing a "disease-permissive" PD microbiome (elevated
  opportunistic pathogens, reduced SCFA producers, dysregulated
  neuroactive signaling).

  The Fullard / N M et al. multi-region snRNA-seq atlas (Sci Data
  11:1274, 2024; CELLxGENE collection d5d0df8f-4eee-49d8-a221-
  a288f50a1590; 100 donors, 5 regions, 2.1M nuclei) includes the
  dorsal motor nucleus of the vagus (DMNX) — the Braak stage 1
  region that receives vagal afferents from the gut and is among
  the earliest CNS sites of Lewy pathology.

  Despite a decade of "gut-brain-axis in PD" review literature, no
  published study has formally tested whether the PD-gut-dysbiosis
  signature (Wallen 2022) is paralleled in DMNX-resident neuron
  transcriptomic dysregulation in PD (Fullard 2024 DMNX slab), and
  whether plasma DDC / immunometabolic proteomic markers (Bayati,
  Hällqvist) sit on the mechanistic path.

  Because the Wallen and Fullard cohorts have no individual-donor
  overlap, this proposal does not test individual-level mediation
  but tests *signature-level* alignment via canonical correlation,
  pathway enrichment, and triple-omics meta-analysis. This is the
  informative design under public-data constraints.

  Hypotheses (pre-registered):

    H1 (primary — bacterial-pathway-to-DMNX-gene-pathway
         enrichment alignment):
        The Wallen 2022 PD-vs-ctrl differential MetaCyc pathway list
        (q < 0.05 from ANCOM-BC, top 100 by effect size) and the
        Fullard DMNX-derived PD-vs-ctrl Reactome/KEGG enrichment list
        (computed on per-donor pseudobulk, top 100) share
        significantly more pathway-membership genes / shared KEGG
        modules than expected by chance, with hypergeometric p <
        0.01. Concretely: of the top 100 Wallen pathways, ≥ 15 map
        via KEGG-Reactome bridging to KEGG modules that also appear
        in the top 100 Fullard DMNX enriched modules, where chance
        expectation is ≈ 6 under a hypergeometric null.

    H2 (secondary — vagal-target gene module alignment):
        A pre-defined "vagal-afferent target" gene module (vagal-
        sensory neuron markers + their transcriptional targets:
        TRPV1, ASIC3, P2RX2, CHRNA3, CHAT, PHOX2B, MNX1; their KEGG
        pathway "Cholinergic synapse" hsa04725 + "Vagal innervation"
        downstream targets) is differentially expressed in DMNX
        between PD and ctrl with FDR < 0.05, AND its expression
        direction is anti-correlated with the Wallen 2022 SCFA-
        producer abundance signature: PD = decreased SCFA producers
        + decreased vagal-target signal (Spearman ρ < -0.3 on
        random-pair permutation null over pseudo-cohort matched
        means).

    H3 (secondary — neuroinflammatory cross-modality):
        DMNX microglia in the Fullard atlas show PD-vs-ctrl up-
        regulation of the LPS-response and pattern-recognition-
        receptor (TLR4, TLR2, CD14, NLRP3) module, AND the
        magnitude of this upregulation correlates positively with
        the Wallen 2022 LPS-producer abundance signature when both
        are normalised to z-scores within their respective cohorts.

    H4 (secondary — protein-MR triangulation):
        Of the proteins nominated as causal for PD by the Storm
        2025 Mendelian-randomisation analysis (UKB Olink × Nalls
        2019 GWAS — public sumstats only, no individual-level UKB
        access required for the MR direction we test), the subset
        with bacterial-pathway-membership in Wallen 2022's PD-
        enriched pathways constitutes a "triangulated" panel; we
        test whether this panel size > expected by chance under
        permutation null over the full Storm-nominated protein
        set.

    H5 (secondary — alternative axes):
        We pre-register and test 3 *alternate* gene modules
        (catecholamine biosynthesis, ferroptosis pathway,
        cuproptosis pathway) as negative controls — these should
        NOT show signature alignment with gut-microbiome
        signatures via the same H1 methodology. If they DO, the
        H1 signal is suspect.

  A null finding on H1 would be informative — it would mean that
  signature-level gut-brain harmonisation is undetectable from
  bulk-pathway alignment alone, motivating per-individual
  multi-omic data acquisition (PPMI Project 196 + microbiome).

data:
  location: ./research_cases/parkinson-gut-brain-axis/data
  shared_cache: ./datasets
  description: |
    **No pre-staged raw data on disk.** All data fetched into
    `./datasets/<accession>/`.

    **Primary datasets:**

      - **Wallen 2022 PD gut shotgun metagenomics.**
        SRA BioProject: PRJNA834801
          https://www.ncbi.nlm.nih.gov/bioproject/PRJNA834801
        Zenodo post-QC counts + taxonomic + MetaCyc-pathway tables:
          https://zenodo.org/records/7246185
        Companion code: https://github.com/zackmwallen/PD_metagenomics_2022
        Content: 724 fecal samples (490 PD + 234 ctrl), Illumina
        NovaSeq 6000, ~40M reads/sample. Post-QC bracken / kraken2
        species table + HUMAnN3 UniRef90 gene families + MetaCyc
        pathway abundance tables.
        Target path: ./datasets/wallen_2022_zenodo/  (fetch Zenodo
        post-QC tables first; raw FASTQ from PRJNA834801 are
        ~3-5 TB and NOT needed for signature-level analysis.)
        UAB Digital Commons mirror:
          https://digitalcommons.library.uab.edu/datasets/80/

      - **Fullard / N M 2024 multi-region snRNA-seq atlas
        (DMNX slab is the primary test substrate; full atlas for
        cross-region negative controls).**
        CELLxGENE collection (direct h5ad fetch):
          https://cellxgene.cziscience.com/collections/d5d0df8f-4eee-49d8-a221-a288f50a1590
        Target path: ./datasets/cellxgene_d5d0df8f_fullard/
        DMNX slab ~ 200-400k nuclei.

      - **Storm 2025 protein-MR result table (proteins causal for
        PD).**
        Preprint medRxiv 2024.02.24.24303314 (public).
        GitHub: https://github.com/catherinestorm/MR_proteins_neurodegeneration
        Specifically the supplementary "MR causal proteins" table.
        Target path: ./datasets/storm_2025_mr_proteins/

      - **Hällqvist 2024 plasma proteomic prodromal-PD panel.**
        PRIDE: PXD046355
          https://www.ebi.ac.uk/pride/archive/projects/PXD046355
        For H4 triangulation: their 8-protein panel + full
        protein-level tables.
        Target path: ./datasets/PXD046355_hallqvist/

      - **Reference databases:**
        - KEGG pathway maps (hsa* + bacterial maps):
            https://rest.kegg.jp/
        - Reactome v90 GMT for human:
            https://reactome.org/download-data
        - MSigDB Hallmark / C2:CP / C5:GO:
            https://www.gsea-msigdb.org/gsea/msigdb/human/
        - MetaCyc (Wallen used) — accessible via HUMAnN3
          metadata bundled in the Zenodo deposit; no separate
          fetch needed.
        Target paths: ./datasets/kegg/, ./datasets/reactome/,
                      ./datasets/msigdb/

  cohorts:
    [wallen_2022_PD_microbiome,
     fullard_2024_DMNX,
     fullard_2024_SNpc_negctrl,
     fullard_2024_V1_negctrl,
     hallqvist_2024_PXD046355_plasma,
     storm_2025_MR_proteins]
  modalities: [shotgun_metagenomics_NovaSeq,
               snRNA_seq_10x,
               lcms_dia_plasma_proteomics,
               summary_statistics_GWAS_MR]

success_criteria:
  primary_metric: pathway_signature_alignment_overlap
  baseline: hypergeometric_null_pathway_overlap
  pre_registration: required
  honest_validation: required
  notes: |
    Primary success = ≥ 15 shared KEGG modules between Wallen-top-100
    bacterial pathways and Fullard-DMNX-top-100 enriched gene modules,
    hypergeometric p < 0.01. Failure (< 15 shared modules, ≥ chance)
    is a clean null and the headline finding becomes "signature-level
    gut-brain alignment is not detectable from bulk pathway
    enrichment alone".

    Negative-control specificity (H5) is mandatory: if catecholamine
    biosynthesis (which is biologically expected to NOT align with
    gut microbiome) shows H1-level alignment, the H1 signal is
    rejected as artifactual.

    Honest framing: this proposal accepts that *no* causal claim can
    be made from cross-cohort signature alignment alone. The
    contribution is to formally establish whether the signature-
    level alignment is detectable at all, motivating future
    individual-level multi-omic studies.

figures_planned:
  - id: F1
    title: "PD-microbiome signature reproduction from Wallen 2022 post-QC tables"
    panels:
      - "Top-20 PD-enriched bacterial species (volcano: log2FC vs. -log10 q from ANCOM-BC re-run on Zenodo counts)"
      - "Top-20 PD-depleted species (same)"
      - "Top-30 PD-altered MetaCyc pathways (heatmap PD vs. ctrl mean abundance + sign)"
      - "Concordance to Wallen-published findings (Fig 2 of original): reproduction r and per-species log2FC scatter"
  - id: F2
    title: "Fullard DMNX PD-vs-ctrl differential expression baseline"
    panels:
      - "Per-cell-type DE volcano in DMNX (motor neurons, glutamatergic neurons, microglia, astrocytes, oligodendrocytes)"
      - "Donor-pseudobulk PCA of DMNX colored by PD/ctrl"
      - "Reactome pathway enrichment in DMNX neurons (top 50)"
      - "Microglia LPS-response & inflammation module score, PD vs. ctrl"
  - id: F3
    title: "Signature alignment hypergeometric test (H1)"
    panels:
      - "Bipartite graph: top-100 Wallen MetaCyc pathways (left) ↔ shared KEGG modules ↔ top-100 Fullard-DMNX enriched modules (right); edges = membership"
      - "Permutation null distribution of shared-module count vs. observed (H1 p-value)"
      - "Heatmap of the shared modules: rows = KEGG modules, cols = (Wallen rank, Fullard rank), color = effect-size"
      - "Per-cell-type version of H1: alignment count split by DMNX cell type (neurons vs. microglia vs. astrocytes)"
  - id: F4
    title: "Vagal-target module + SCFA anti-correlation (H2)"
    panels:
      - "DMNX vagal-target module score boxplot PD vs. ctrl (donor-pseudobulk)"
      - "SCFA producer abundance vs. vagal-target module score Spearman scatter (permutation null), per-cohort means"
      - "Per-donor decomposition of vagal-target signal across DMNX cell types"
      - "Anti-correlation magnitude with bootstrap CI"
  - id: F5
    title: "Microglial neuroinflammation × bacterial LPS-producer (H3)"
    panels:
      - "DMNX microglia TLR4 / TLR2 / CD14 / NLRP3 z-scores PD vs. ctrl"
      - "Wallen-cohort LPS-producer normalised abundance PD vs. ctrl"
      - "z-correlation across the matched signature axis"
      - "Compare to V1 (Braak 6) microglia and SNpc (Braak 3) microglia as positive and negative spatial controls"
  - id: F6
    title: "Protein-MR triangulation (H4) + negative controls (H5)"
    panels:
      - "Storm 2025 MR-causal proteins × Wallen-bacterial-pathway membership matrix (top 100 each)"
      - "Hällqvist 8-panel × Wallen-pathway membership"
      - "H4 permutation null: triangulated panel size vs. random"
      - "H5 negative-control: catecholamine, ferroptosis, cuproptosis modules vs. Wallen pathways — should NOT enrich"

figures_not_in_scope:
  - "Per-individual mediation (no cohort overlap)"
  - "Re-alignment from raw FASTQ (use post-QC Zenodo tables)"
  - "Mouse vagotomy experiments — wet lab"
  - "Direct fecal-brain transcriptomic correlation in individual donors (no such dataset exists publicly)"
  - "Single-bacterial-strain CNS-effect inference"

budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 1440
  allow_web: true
  claude_model: claude-sonnet-4-6
  max_phase_budget_usd: 22.0

auto_approve: true
---

# parkinson-gut-brain-axis — Cross-cohort signature-level alignment between Wallen 2022 gut microbiome and Fullard 2024 DMNX transcriptome

## Source materials

- Wallen Z.D., Demirkan A., Twa G., Cohen G., Dean M.N.,
  Standaert D.G., Sampson T.R., Payami H.
  *Metagenomics of Parkinson's disease implicates the gut microbiome
  in multiple disease mechanisms.* Nature Communications 13:6958
  (2022). DOI: 10.1038/s41467-022-34667-x. PMC9663292.
- N M, P., Fullard, J.F., Clarence, T., et al.
  *A multi-region single nucleus transcriptomic atlas of Parkinson's
  disease.* Scientific Data 11:1274 (2024). PMC11585549.
- Storm, C.S., et al. *A Mendelian randomization study identifies
  proteins involved in neurodegenerative diseases.* Brain 148:2412
  (2025). DOI: 10.1093/brain/awae404.
- Hällqvist J., Bartl M., Dakna M., et al. *Plasma proteomics identify
  biomarkers predicting Parkinson's disease up to 7 years before
  symptom onset.* Nature Communications 15:4759 (2024). PMC11189460.

PDFs in `claude_docs/claude_2026-05-21/parkinson_research/pdfs/`
(wallen_2022_NatComm.pdf, nm_2024_atlas_SciData.pdf,
hallqvist_2024_NatComm.pdf).

## Why this matters for OmiCairn

This is a multi-modality integration test under realistic
public-data constraints. The cohorts do not overlap individually,
so per-individual mediation is impossible — the proposal embraces
that constraint and tests signature-level alignment instead. This
exercises OmiCairn's ability to:

1. **Refuse the wrong test** (per-individual mediation, when
   cohorts don't overlap).
2. **Construct cohort-bridging surrogate metrics** (pathway
   alignment via KEGG/Reactome).
3. **Pre-register negative controls** (H5: catecholamine,
   ferroptosis, cuproptosis modules) so the H1 signal can be
   judged against same-style null distributions.
4. **Report honestly when no signal exists.** Field-level
   "gut-brain axis" claims are often overcalled; a null here would
   be a public service.

## Known prior results (cross-check)

| Claim | Reference |
|---|---|
| 30% of bacterial species / genes / pathways altered in PD | Wallen 2022 Fig. 2 |
| Loss of SCFA producers + gain of opportunistic pathogens | Wallen 2022 Fig. 4 |
| Fullard atlas DMNX represents Braak stage 1 region | Fullard 2024 |
| DDC consistently upregulated in CSF / urine of de-novo PD | Bayati 2024 |
| Hällqvist 8-panel achieves AUC up to 1.0 on 7-yr iRBD prodromal | Hällqvist 2024 |

## Out of scope

- Per-individual gut-brain mediation (no cohort overlap).
- Wet-lab fecal-transplant or vagotomy experiments.
- Animal-model integration.
- Single-strain mechanism inference.
- Treatment-response prediction.
