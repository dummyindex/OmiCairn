# OmiCairn architecture — the pipeline & the lab-knowledge layer

> **⚠ Living document.** This describes how the OmiCairn system works *today*.
> The architecture evolves; when you change the pipeline shape, the
> knowledge stores, the distill/render hooks, or the lab-KB location
> resolution, **update this file in the same change**. See the maintenance
> note in `the design principles` / `ARCHITECTURE.md`. Last reviewed: 2026-06-07.

This doc explains how one OmiCairn run executes, and — the part most people
ask about — how knowledge **compounds across runs** via the lab knowledge
base.

---

## 1. The whole system: one run + the cross-run loop

```
                        ┌─────────────────────────────────────────────────────────┐
                        │   LAB KNOWLEDGE BASE   (cross-run, compounding memory)    │
                        │   <workspaces_root>/lab_knowledge/                        │
                        │     • lab_knowledge.jsonl   (append-only, deduped on read)│
                        │     • LAB_KNOWLEDGE_INDEX.md (human-readable URI index)   │
                        └─────────────────────────────────────────────────────────┘
                            ▲  distill at run-end                 │ render into prompts
                            │  (_maybe_distill_to_lab)            │ ({{lab_knowledge}})
                            │                                     ▼
   RESEARCH.md ───►  ┌──────────────────────────────────────────────────────────────┐
   omicairn_config.yaml  │                 ONE OmiCairn RUN (a workspace)                 │
                     │                                                                │
                     │  IDEATE → SCOPE → PLAN → EXECUTE → CROSS_CHECK → REVIEW → DONE │
                     │   (standalone)  ▲     ▲      │  ▲         │          │         │
                     │                 │     │      │  └ retry ──┘          │         │
                     │                 │     │      │   _execute            │         │
                     │                 │     └──────┼──── back_to_plan ─────┤         │
                     │                 └────────────┼──── back_to_scope ────┤         │
                     │                              │  graph_               │ graph_  │
                     │                              ▼  updates.json         ▼ updates │
                     │           ┌──────────────────────────────────────────────┐     │
                     │           │  knowledge/graph.jsonl                        │     │
                     │           │  (per-run knowledge graph: goals·hypotheses·  │     │
                     │           │   findings·result_refs·datasets·sources       │     │
                     │           │   + edges; H lifecycle)                       │     │
                     │           └──────────────────────────────────────────────┘     │
                     │                                                                │
                     │   REVIEW also writes review_notes.md + negative_results.md     │
                     │   (also) IDEATE sources → literature/papers.json               │
                     └──────────────────────────────────────────────────────────────┘
                            │                                     ▲
                            └─────────────────────────────────────┘
                              the NEXT run's IDEATE/SCOPE reads the lab KB,
                              so it doesn't re-propose what's done & reuses datasets
```

Two loops:

- **Inner loop (within a run).** The orchestrator drives stages.
  CROSS_CHECK can send EXECUTE back (`retry_execute`); the REVIEW analysis
  review can send the run back (`retry_execute` / `back_to_plan` /
  `back_to_scope`) or finish it (`done`) — the failure-driven iteration. A
  retry that targets an iteration-exhausted phase halts the run, leaving
  every stage on disk for inspection. Every stage's signal lands in the
  **per-run `knowledge/graph.jsonl`** (seeded at SCOPE from RESEARCH.md
  goals/hypotheses, grown by EXECUTE's and REVIEW's `graph_updates.json`,
  hypothesis statuses advanced as evidence arrives).
- **Outer loop (across runs).** At the end of *every* drive the run's
  graph is **distilled** into the lab KB; the next run **renders** that
  lab KB into its IDEATE/SCOPE prompts. That is how the lab compounds.

Stages are numbered globally (`000_ideate`, `001_scope`, …); retries
insert a new stage dir at the next index. Prior stage dirs are immutable.

---

## 2. Zoom in: the lab-knowledge layer

```
  ╔═════════════════════════ DISTILL PATH (run end) ═════════════════════════╗
  ║  knowledge/graph.jsonl ──► distill_workspace(ws_root, root=R)             ║
  ║      pulls only DURABLE items:                                            ║
  ║        • hypotheses with status ∈ {supported, refuted, inconclusive}      ║
  ║        • findings                                                         ║
  ║        • dataset_refs                                                     ║
  ║        • directions (proposed + adopted)                                  ║
  ║      each → an entry with:                                                ║
  ║        key  = "{kind}:{slug(title)}:{workspace}"   ← dedup identity       ║
  ║        evidence_uris = [ file://…/00N_execute/results.json , … ]          ║
  ║                         └─ or s3://bucket/… when --uri-base given         ║
  ║                            (records the URI only; OmiCairn does NOT upload)   ║
  ║      append entries ──► R/lab_knowledge.jsonl   (+ rewrite INDEX.md)      ║
  ║      (folded on read: last write per key wins → re-distill = update)      ║
  ╚═══════════════════════════════════════════════════════════════════════════╝

  ╔══════════════════════════ READ PATH (run start) ═════════════════════════╗
  ║  render_lab_knowledge_md(root=R) → {{lab_knowledge}} token               ║
  ║     "Hypotheses already resolved",  "Directions already explored          ║
  ║      (adopted = don't duplicate)",  "Datasets the lab already holds"      ║
  ║   injected into the IDEATE and SCOPE prompts of the NEXT run             ║
  ╚═══════════════════════════════════════════════════════════════════════════╝

  ╔═══════════════ WHERE IS R?  resolve_root() — configurable ════════════════╗
  ║   1. explicit  ──  `omicairn labkb … --root /path`   (highest)                ║
  ║   2. $OMICAIRN_LAB_KB                                                        ║
  ║   3. <workspaces_root>/lab_knowledge   ◄── default (visible, no dot)       ║
  ║   4. <cwd>/workspaces/lab_knowledge    (CLI fallback, no ws context)       ║
  ║   • created on demand during a run  • old ~/.cache path retired           ║
  ╚═══════════════════════════════════════════════════════════════════════════╝
```

### Two stores — do not conflate

| | Per-run graph | Lab knowledge base |
|---|---|---|
| **File** | `<ws>/knowledge/graph.jsonl` | `<workspaces_root>/lab_knowledge/lab_knowledge.jsonl` |
| **Scope** | one workspace | all runs in the tree |
| **Holds** | every node/edge, full detail, live | distilled durable items only |
| **Written** | continuously during the run | once per drive, at the end (distill) |
| **Dedup key** | node id | `kind:slug:workspace` |
| **Read by** | SCOPE / PLAN / EXECUTE / REVIEW prompts (graph projection), `omicairn labkb distill` | IDEATE / SCOPE of *future* runs |

The lab KB is deliberately a **thin, URI-indexed digest** — it does not
copy results, it **points** at them (`evidence_uris` → the exact
`results.json`). That is what lets a brand-new IDEATE run avoid
re-proposing a refuted hypothesis and reuse / avoid known datasets.

### A separate store: the literature bibliography

`<ws>/literature/papers.json` is a per-workspace **bibliography** (papers,
DOIs, abstracts), fed by `omicairn literature` and the IDEATE→literature
bridge, and read by PLAN's literature review. It is *papers*, not
findings — its own thing, NOT part of the lab KB.

---

## 3. Code map (where each piece lives)

| Concept | Code |
|---|---|
| Stage driving, retry routing, retry caps | `omicairn/orchestrator.py` (`run_all`, `decide_next_phase`) |
| Per-run graph store + lifecycle | `omicairn/knowledge_graph.py` |
| EXECUTE/REVIEW→graph ingest | orchestrator `_ingest_stage_graph_updates` (reads `graph_updates.json`) |
| Distill to lab KB (run end) | orchestrator `_maybe_distill_to_lab` → `lab_knowledge.distill_workspace` |
| Lab-KB location resolution | `lab_knowledge.resolve_root` (env / `--root` / `<workspaces_root>/lab_knowledge`) |
| Render lab KB into prompts | orchestrator `_lab_knowledge_summary` → `lab_knowledge.render_lab_knowledge_md` → `{{lab_knowledge}}` |
| Lab-KB CLI | `omicairn labkb {distill,list,search,stats} [--root]` (`omicairn/cli.py` `cmd_labkb`) |
| Literature bibliography | `omicairn/literature.py` (`export_sources_to_literature`) |
| Constants (paths, env, tokens) | `omicairn/constants.py` (`ENV_LAB_KB`, `LAB_KB_DIRNAME`, `LAB_KB_FILE`, …) |

---

## 4. Inspecting the lab KB

```
omicairn labkb stats                 # entry counts by kind + #workspaces
omicairn labkb list --kind finding   # list distilled items
omicairn labkb search "APOE4"        # text search, prints evidence URIs
omicairn labkb distill <workspace>   # (re)distill one run; idempotent
# override location: --root /path   or   OMICAIRN_LAB_KB=/path
```
