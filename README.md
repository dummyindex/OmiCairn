# OmiCairn

**An accountable, self-learning multi-agent platform for hypothesis-driven biomedical multi-omics analysis.**

OmiCairn turns an open-ended biomedical research question and its data into an
auditable, end-to-end analysis. It plans the study, executes the analyses,
cross-checks every reported number against the code that produced it, and
reviews whether the evidence actually answers the question — recording every
step so a human expert can inspect, redirect, or reproduce it.

This repository is the academic code release accompanying the OmiCairn
manuscript (see [Citation](#citation)).

---

## How it works

Given a `RESEARCH.md` (the research question, hypotheses, data sources and
budgets), OmiCairn runs a five-phase pipeline. Four phases are multi-turn
[Claude Code](https://docs.claude.com/en/docs/claude-code/overview) agent
sessions; `CROSS_CHECK` is a deterministic Python verification step.

```
SCOPE → PLAN → EXECUTE → CROSS_CHECK → REVIEW → DONE
  ▲       ▲       ▲  ▲          │           │
  │       │       │  └─ rework ─┘           │
  └───────┴───────┴──── more analysis ──────┘
```

| Phase | What it produces |
|-------|------------------|
| `SCOPE` | Operationalized question, feasibility and statistical-power check, data plan |
| `PLAN` | Pre-registered analysis plan, decision rules and figures contract |
| `EXECUTE` | Code, results (`results.json`), analysis report (`analysis.md`), figures and per-stage provenance |
| `CROSS_CHECK` | Structural gates: every reported number, figure and file must resolve to executed outputs |
| `REVIEW` | Analysis-adequacy review: does the evidence answer the pre-registered question? Writes `review_notes.md` and `negative_results.md`, then finishes (`done`) or routes back for more analysis |

The deliverables of a run are plain files in its workspace:

- `results.json`, `analysis.md`, `figures/` and `code/` from the latest
  `EXECUTE` stage;
- the `CROSS_CHECK` findings and verdict;
- `review_notes.md` and `negative_results.md` from `REVIEW` — the latter is
  the consolidated record of refuted, inconclusive and untestable hypotheses
  and of dropped or failed analysis arms, each with evidence paths;
- the per-workspace knowledge graph (`knowledge/graph.jsonl`) and
  per-stage provenance.

Key properties:

- **Accountability.** Each stage writes contract files and a provenance stamp
  (code version, inputs, parameters, timings). Cross-check gates route a run
  back for rework instead of letting unsupported results through.
- **Self-learning, bounded.** Validated code, debugging traces and
  cross-check and review feedback are distilled into a cross-run knowledge
  base (`omicairn labkb`) and a shared literature library
  (`omicairn paperlib`) that later runs retrieve. Model weights, prompts,
  cross-check gates and orchestration logic stay fixed.
- **Expert in the loop.** Runs can be steered at any point (`omicairn note`,
  `omicairn steer`, `omicairn ideate --adopt`), and every artifact is a plain
  file in the run workspace.

More detail: [docs/OVERVIEW.md](docs/OVERVIEW.md) and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### Scope of this release

OmiCairn performs analyses; it does not draft or format manuscripts, by
design. A run ends with reviewed analyses — results, figures, code,
cross-check findings, review notes, a negative-results record and a
knowledge graph — that a human expert inspects and interprets. The release
contains no manuscript drafting, claim auditing of manuscript text, simulated
peer review, typesetting, venue formatting or submission export, and every
agent session, including ad-hoc steering steps, is instructed not to produce
manuscript text.

---

## Installation

Requirements: Python ≥ 3.11 and the [Claude Code](https://docs.claude.com/en/docs/claude-code/overview)
CLI (`claude`) on your `PATH`.

```bash
git clone https://github.com/dummyindex/OmiCairn.git
cd OmiCairn
python -m venv .venv-omicairn
source .venv-omicairn/bin/activate
pip install -e engine
```

### Credentials

OmiCairn launches `claude` sessions, which authenticate the way Claude Code
normally does (an existing `claude` login, `ANTHROPIC_API_KEY`, or a
`CLAUDE_CODE_OAUTH_TOKEN`). Optional keys can be placed in a `.env` file at
the repository root — copy [`.env.example`](.env.example). `.env` is
gitignored; never commit credentials.

### Compute (optional)

By default a run uses the local machine. To describe GPU servers or remote
hosts, copy [`COMPUTATION_RESOURCES.example.md`](COMPUTATION_RESOURCES.example.md)
to `COMPUTATION_RESOURCES.md` at the repository root and edit it; a research
case selects a resource with `compute.resource` in its `omicairn_config.yaml`.
Schema: [docs/references/computation-resources-schema.md](docs/references/computation-resources-schema.md).

---

## Quick start

The `smoke-tiny` case ships with ~20 rows of synthetic data, so it runs
without downloading anything:

```bash
omicairn init smoke-tiny \
  --from-research-md research_cases/smoke-tiny/RESEARCH.md \
  --workspaces-root workspaces

omicairn run    workspaces/smoke-tiny-<timestamp> --phase scope   # one phase
omicairn run    workspaces/smoke-tiny-<timestamp>                 # resume to completion
omicairn status workspaces/smoke-tiny-<timestamp>
omicairn inspect workspaces/smoke-tiny-<timestamp> --phase execute
omicairn inspect workspaces/smoke-tiny-<timestamp> --phase review
```

To check the pipeline wiring without calling a model, add `--dry-run`
(or set `OMICAIRN_DRY_RUN=1`).

### Writing your own case

A research case is a directory with a `RESEARCH.md` (and optionally
`omicairn_config.yaml` and a `data/` folder or `fetch_data.py`). Start from
any case in [`research_cases/`](research_cases/) and see
[docs/references/research-md-schema.md](docs/references/research-md-schema.md)
and [docs/references/omicairn-config-schema.md](docs/references/omicairn-config-schema.md).

---

## Research cases

[`research_cases/`](research_cases/) contains the biomedical cases used to
develop and evaluate OmiCairn, including the open-data neurodegeneration
cases (Parkinson's disease, Alzheimer's disease and cross-disease
comparisons), cancer-genomics cases (TCGA cohorts), a published-study
reproduction, two IDEATE (brainstorm) cases and synthetic control cases.
Real-cohort data are not committed; each case's `fetch_data.py` or
`data/DATA_SOURCES.md` documents how to obtain the public data.
See [research_cases/README.md](research_cases/README.md).

---

## Command reference

```
omicairn init       <slug> --from-research-md PATH [--workspaces-root DIR] [--skip-literature]
omicairn run        <ws> [--phase {all,scope,plan,execute,cross_check,review}]
                         [--stop-after PHASE] [--max-stages N] [--dry-run]
omicairn status     <ws>                   # run state summary
omicairn fleet      [<root>]               # one line per run across a workspaces root
omicairn inspect    <ws> [--phase NAME]    # list a phase's outputs
omicairn note       <ws> [--phase NAME] [--once] <text>
omicairn steer      <ws> "<request>"       # plans a change; the next `run` executes it
omicairn ideate     <ws> [--list | --adopt ID]
omicairn literature <ws> [--max-papers N]
omicairn audit      <ws> [--replay-gates]    # replay the cross-check gates (read-only)
omicairn audit      --all [--workspaces-root DIR] [--json]
omicairn continue   <ws> [--note TEXT]       # add a note and resume
omicairn labkb | paperlib | feedback | project | reap | preflight
```

Run `omicairn <command> --help` for all options.

---

## Repository layout

```
engine/           Python package (omicairn/), phase prompts, sub-agent specs,
                  domain packs and tests
research_cases/   Biomedical research cases (RESEARCH.md + data fetchers)
docs/             Overview, architecture, design notes and input schemas
```

## Tests

```bash
cd engine
pip install pytest
python -m pytest tests -q
```

The suite needs no network access and no Claude CLI.

---

## Citation

If you use OmiCairn, please cite the accompanying manuscript (see also
[`CITATION.cff`](CITATION.cff)):

> Su H., Chen Y., Gao Y., Yuan J., Yu Z., Chen Y., Cui X., Yin D., Zhang J.,
> Yu Y., Wu Y., Chen M., Gui Z., Ni K. *OmiCairn: an accountable self-learning
> platform for hypothesis-driven biomedical multi-omics analysis.* 2026
> (manuscript in preparation).

## License

Released under the [MIT License](LICENSE).

## Disclaimer

OmiCairn is a research tool for hypothesis generation and analysis support.
It is not intended for clinical decision-making; its outputs require expert
review and independent validation.
