# research_cases/

Test research cases for OmiCairn smoke testing and integration
validation. Each subdirectory is a self-contained research case that
can be fed to `omicairn init` + `omicairn run`.

## Adding a new test case

### 1. Create the directory

```bash
mkdir research_cases/<slug>/
mkdir research_cases/<slug>/data/
```

The `<slug>` should be lowercase-hyphenated and descriptive:
`<topic>-<disease/domain>` (e.g., `ferroptosis-hcc`, `emt-stad`).

### 2. Write RESEARCH.md

Create `research_cases/<slug>/RESEARCH.md`. This is the single
input file OmiCairn reads. Format: YAML inside Markdown front-matter fences.

**Required fields:**

```yaml
---
topic_slug: <slug>
research_question: |
  <Free-text question the OmiCairn run will answer.>
data:
  location: ./research_cases/<slug>/data
---
```

**Recommended fields:**

```yaml
research_mode: METHOD          # METHOD / DISCOVERY / HYBRID
domain: cancer_genomics        # free-text domain label
data:
  description: "<one-line summary of datasets>"
  cohorts: [TCGA-XYZ, External-ABC]
  modalities: [RNA-seq, clinical]
success_criteria:
  primary_metric: c_index      # or auc, accuracy, etc.
  baseline: "clinical-only Cox model"
  pre_registration: required   # required or optional
  honest_validation: required  # required or optional
```

> **NOTE on budgets/bounds/auto_approve.** Historically RESEARCH.md also
> carried `budgets:`, `bounds:`, `auto_approve:`, and `data.shared_cache:`
> for orchestrator config. These still work for backward compatibility,
> but the **canonical place for orchestration knobs is now
> `omicairn_config.yaml`** alongside RESEARCH.md (see §2 below). New cases
> should put orchestration config in `omicairn_config.yaml`.

Full schema: `docs/references/research-md-schema.md`.

### 2b. Write omicairn_config.yaml (orchestration config)

Alongside RESEARCH.md, write a `omicairn_config.yaml` to hold all
orchestration knobs (budgets, bounds, retry caps, claude model,
auto_approve, shared cache):

```yaml
budgets:
  max_phase_wall_minutes: 240
  max_total_wall_minutes: 1440
  # Per-phase override. EXECUTE is usually the heaviest.
  max_phase_wall_minutes_by_phase:
    execute: 360
    review: 60
  max_phase_budget_usd: 25.0
  allow_web: true
  claude_model: claude-sonnet-4-6
bounds:
  max_iters_per_phase: 5
  max_backtracks: 5
auto_approve: true
shared_cache: ./datasets
```

The orchestrator's `_run_llm_stage` consults
`max_phase_wall_minutes_by_phase.get(phase_name)` first, falling back
to `max_phase_wall_minutes` then to the phase's built-in default.

Full schema: `docs/references/omicairn-config-schema.md`.

### 3. Provide data

Two patterns:

**A. Synthetic data (like `smoke-tiny/`)** — write a `generate_data.py`
that creates deterministic CSVs in `data/`. Check in both the script
and the generated data so the case runs without network access.

**B. Real data (like `pyroptosis-luad/`)** — write a `fetch_data.py`
that downloads from a public API (cBioPortal, GEO, etc.) and saves
CSVs to `data/`. Check in the script AND the fetched data. This makes
the case runnable offline and avoids API breakage in CI.

Data conventions:
- One CSV per cohort, named descriptively (e.g., `tcga_lihc_expression_zscores.csv`)
- Include a clinical CSV with survival columns (`OS_STATUS`, `OS_MONTHS` or equivalent)
- Expression data: rows = samples, columns = gene/feature names
- No preprocessing beyond what `fetch_data.py` does — let OmiCairn handle it

### 4. Add a README.md (optional but recommended)

A short `research_cases/<slug>/README.md` explaining:
- What biological/scientific question this tests
- Why this case was chosen (strong external validation, known positive, etc.)
- Expected outcome (positive/negative/uncertain)
- Cohort sizes and data sources

### 5. Verify

```bash
conda activate omicairn
cd engine && pip install -e .
cd ..

# Parse check — validates RESEARCH.md can be loaded
python -c "
from omicairn.research_md import load
rm = load('research_cases/<slug>/RESEARCH.md')
print(f'topic_slug: {rm.topic_slug}')
print(f'data_dir:   {rm.data_dir}')
"

# Quick dry-run — verifies init + phase loop without API calls
OMICAIRN_DRY_RUN=1 omicairn init <slug> \
  --from-research-md research_cases/<slug>/RESEARCH.md \
  --workspaces-root /tmp/omicairn-verify
OMICAIRN_DRY_RUN=1 omicairn run /tmp/omicairn-verify/<slug>-* --phase scope
```

If the dry-run completes, the case is wired correctly. For a real
smoke test, drop `OMICAIRN_DRY_RUN=1` and run `--phase scope` first,
then `--phase all` once scope is clean.

## Existing cases

| Case | Type | Cohorts | Primary test purpose |
|------|------|---------|---------------------|
| `smoke-tiny` | Synthetic | train/holdout (60/40) | Fast end-to-end pipeline validation |
| `brca-honest-validation` | Real | TCGA-BRCA + METABRIC | External validation path |
| `ferroptosis-hcc` | Real | TCGA-LIHC (70/30 split) | Single-cohort split path |
| `pyroptosis-luad` | Real | TCGA + OncoSG + CAS | Multi-cohort, primary integration test |
| `angiogenesis-crc` | Real | TCGA-COAD + Sidra-LUMC + CPTAC | Strong external validation candidate |
| `ddr-blca` | Real | TCGA-BLCA + IMvigor210 + BCAN | Cross-context validation |
| `emt-stad` | Real | TCGA-STAD (70/30 split) | Strong published priors |
| `mitoxy-brca` | Real | TCGA-BRCA + METABRIC (CONFIRMATORY) | LASSO-Cox 17-gene Mitoxyperilysis signature; honest external validation |
| `immunesig-pancan` | Real | 33 TCGA cancers (Thorsson 2018) | Blind replication of a known high-impact paper |
| `morphogen-organoid-screen` | Real | E-MTAB-15667 + E-MTAB-15622 (scRNA-seq, CONFIRMATORY) | Reproduction of Sanchís-Calleja et al. Nature Methods 2026 morphogen organoid screen |
| `parkinson-da-vulnerability` | Real | Kamath SNpc + Wang + Adams + Fullard SNpc slab; Martirosyan blind external | Cross-cohort replication of Kamath 2022 AGTR1+ vulnerable DA subtype |
| `parkinson-lamda-multiregion` | Real | Goralski LAMDA + Fullard 5-region atlas | Generalisation of cortical Lewy signature across Braak regions |
| `parkinson-gut-brain-axis` | Real | Wallen 2022 fecal shotgun + Fullard DMNX + Storm MR + Hällqvist plasma | Signature-level multi-omics integration without donor overlap |
| `parkinson-tcell-foothold` | Real | Ma 2025 SN TCR + Kamath/Wang/Adams/Fullard | Transcriptomic-footprint inference of α-syn-reactive CD8+ T cells |
| `parkinson-plasma-prodromal-panel` | Real | PRIDE PXD046355 + Gan 2025 UKB Olink supp + Bayati DDC | Independent DIA-spectra re-analysis of Hällqvist 8-panel |
| `parkinson-scenic-regulons` | Real | Fullard SNpc + Smajic + Wang + Adams + Kim 2024 GWAS | Cross-atlas zero-shot AUCell of pySCENIC TF-regulons (systems bio) |
| `parkinson-celloracle-ko` | Real | Fullard SNpc + Adams Multiome + Kim 2024 GWAS + Hemedan 2024 modules | In-silico KO of top-10 PD-risk genes; perturbation / mechanism |
| `parkinson-metabolic-flux-sn` | Real | Fullard SNpc + Recon3D + Human-GEM + iDopaNeuro1 + Shao 2024 metabolomics | Constraint-based flux modelling of DA neurons; ~5 GB |
| `parkinson-asyn-propagation` | Real | Fullard 5-region + Allen Human Connectivity + Goralski LAMDA | SIR + connectome modelling of α-syn spread; multi-scale dynamics |
| `parkinson-foundation-embedding` | Real | Geneformer 95M + scGPT + Fullard / Kamath / Wang / Adams | Foundation-model benchmark on PD donor classification; METHOD |
| `parkinson-lincs-reversal` | Real | LINCS L1000 + Fullard SNpc DA + Open Targets + omicSynth | Drug-repositioning connectivity reversal; cell-type-resolved |
| `parkinson-multimodal-ppmi-trajectory` | Real | 6 PPMI supplementary tables (no raw data) | GPLVM disease-time inference; ~200 MB, summary-table-only |
| `parkinson-multimodal-spatial-proteomics` | Real | Martirosyan snRNA + Visium + Ma 2025 GeoMx + Zhu 2024 LC-MS/MS | 3-modality MOFA+ with pre-registered pairwise ablation |
| `parkinson-prs-portability` | Real | Kim 2024 + Nalls 2019 sumstats + 1000 Genomes + Saffie-Awad 2025 supp | Multi-ancestry PRS portability via 1000G (no UKB/GP2); METHOD |
| `parkinson-cellchat-immune-axis` | Real | Hong 2025 PBMC + Ma 2025 SN + Fullard microglia + OneK1K | LIANA-py L-R consensus across PBMC × brain immune axis |

## Design principles

- **Each case is self-contained.** Everything needed to run the case
  (RESEARCH.md, data, fetch script) lives in its directory.
- **Data is checked in.** Cases must work offline. Don't rely on
  API availability at run time.
- **Cases test different pipeline paths.** Each case should exercise
  something the others don't: single-cohort split vs. multi-cohort,
  strong vs. weak external validation, different domains, etc.
- **RESEARCH.md is the only interface.** The case directory structure
  is opaque to OmiCairn — only `RESEARCH.md` and the `data/` path matter.
