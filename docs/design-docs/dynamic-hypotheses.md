# Dynamic hypotheses & the knowledge-graph substrate

**Status:** design spec, implementation in progress (2026-05-28)
**Beliefs:** core-beliefs.md #11 (knowledge base is the product) and
#12 (workspace is a multi-goal, multi-hypothesis accumulator)
**Arch summary:** ARCHITECTURE.md § "Target: knowledge graph as the
product"

---

## 1. Why

Today OmiCairn freezes hypotheses at PLAN time (`001_plan/hypotheses.md`),
and tests exactly those in EXECUTE. Two limits fall out:

1. **No on-the-fly hypotheses.** When EXECUTE discovers something the
   pre-registered set didn't anticipate (e.g. "the simple baseline
   *beats* the foundation model on cohort X"), there is nowhere to put
   it. It dies in `analysis.md` prose.
2. **One headline claim per run.** The biomedical single-claim
   convention under-uses the analysis that actually happened. Modern
   research is network-like and dense.

The fix is to make the **knowledge graph** the thing that accumulates.
Hypotheses become first-class graph nodes with a lifecycle, and
EXECUTE/REVIEW can add new ones mid-run.

## 2. Data model

Persisted per workspace at `<ws>/knowledge/graph.jsonl` — an
append-only op-log (`{"op":"upsert_node"|"upsert_edge", ...}`) folded to
current state on read. Mirrors the existing JSONL discipline
(`events.jsonl`, `provenance.jsonl`). No database (belief: flat files;
ARCHITECTURE "no database").

### 2.1 Nodes

```
Node:
  id            str   # stable: "<type>:<slug-or-uuid>"
  type          str   # goal | hypothesis | finding | result_ref
                      #   | dataset_ref | metadata
  title         str
  body          str   # type-specific payload, ≤4 KB
  status        str   # type-specific (see hypothesis lifecycle below)
  origin        str   # preregistered | on_the_fly | manual
  source_phase  str   # which phase wrote it
  source_stage  str   # e.g. 002_execute
  evidence_paths list[str]   # repo-relative; must exist on disk
  tags          list[str]
  created_at    str
  updated_at    str
  extra         dict        # escape hatch
```

### 2.2 Edges

```
Edge:
  id        str   # "<src>--<rel>-->{dst}"
  src       str   # node id
  rel       str   # tests | supports | refutes | derived_from
                  #   | raises | cites
  dst       str   # node id OR external ref (e.g. "doi:10.1038/...")
  weight    float # optional strength/confidence in [0,1]
  source_phase str
  created_at str
  extra     dict
```

Edge semantics:
- `tests`     hypothesis → result_ref (this result was computed to test H)
- `supports`  result_ref/finding → hypothesis (evidence for)
- `refutes`   result_ref/finding → hypothesis (evidence against)
- `derived_from` node → node (lineage)
- `raises`    finding → hypothesis (the on-the-fly path: this finding
              gave birth to this new hypothesis)
- `cites`     node → external DOI

### 2.3 Hypothesis lifecycle

A `hypothesis` node's `status`:

```
proposed ──▶ testing ──▶ supported
                    ├──▶ refuted
                    ├──▶ inconclusive
                    └──▶ superseded   (replaced by a sharper hypothesis;
                                        extra.superseded_by = <id>)
```

`origin`:
- `preregistered` — declared in RESEARCH.md / PLAN before EXECUTE.
- `on_the_fly`    — raised during EXECUTE/REVIEW. Always `posthoc=true`
                    in `extra`, so multiple-testing accounting (W10) can
                    separate confirmatory from exploratory.

**Honesty rule (mechanical):** a hypothesis may only move to
`supported`/`refuted` if at least one `supports`/`refutes` edge points
at it from a node whose `evidence_paths` resolve on disk. Enforced in
`knowledge_graph.set_status` and re-checked by a new cross-check
(`check_knowledge_graph`).

## 3. RESEARCH.md: multiple goals

Back-compatible extension. Today:

```yaml
research_question: "Does X predict Y?"
# hypotheses live in prose in 001_plan/hypotheses.md
```

New optional block:

```yaml
goals:
  - id: g1
    question: "Does X predict Y in cohort A?"
    hypotheses:
      - "H1: X is prognostic for Y (HR>1.5)"
      - "H2: the X effect is independent of stage"
  - id: g2
    question: "Does the X signal transfer to cohort B?"
    hypotheses:
      - "H3: an A-trained model retains AUC>0.65 on B"
allow_on_the_fly_hypotheses: true     # default true
max_on_the_fly_hypotheses: 5          # budget guard, default 5
```

Parser rule (`research_md.py`):
- If `goals:` present → multi-goal.
- Else → synthesize one goal `g1` from `research_question`
  (`origin=preregistered`). **Zero behavior change** for existing cases.

## 4. Orchestrator integration

Additive and gated, so existing runs are unaffected.

1. **SCOPE/PLAN** seed the graph: one `goal` node per goal, one
   `preregistered` `hypothesis` node per declared hypothesis.
2. **EXECUTE** writes `result_ref` nodes for each `results.json` key it
   produced, `tests` edges from the hypothesis it addressed, and
   `supports`/`refutes` edges with the decision. *If* it surfaced a new
   hypothesis, it writes an `on_the_fly` `hypothesis` node + a `raises`
   edge from the finding. Bounded by `max_on_the_fly_hypotheses`.
3. **Dynamic-H loop (bounded).** After CROSS_CHECK, the orchestrator
   asks: are there `proposed` + `on_the_fly` hypotheses that are cheap
   to test (a flag on the node, set by the extractor)? If yes and the
   `dynamic_h_budget_minutes` (omicairn_config.yaml, default 0 = off) allows,
   it schedules one more EXECUTE iteration scoped to those hypotheses.
   Off by default → no surprise compute; turned on for the dynamic-Hs
   e2e case.
4. **REVIEW** reads the graph projection alongside the latest
   `results.json`, may register further on-the-fly hypotheses via
   `graph_updates.json`, and records every `refuted`/`inconclusive`/
   untestable hypothesis in `negative_results.md`.

All graph writes go through `knowledge_graph.upsert_*`, never free-form
agent writes, so the schema and the on-disk-evidence rule hold.

## 5. Single-H vs dynamic-Hs — the comparison

We validate the idea with two `research_cases` over the **same**
synthetic dataset, differing only in configuration:

| | `synth-singleH-control` | `synth-dynamicH` |
|---|---|---|
| RESEARCH.md | one `research_question`, fixed H1 | `goals[]` (2 goals, 3 seed H) + `allow_on_the_fly_hypotheses: true` |
| on-the-fly | disabled | enabled, budget 5 |
| dynamic-H loop | off | on (`dynamic_h_budget_minutes>0`) |
| expected graph | 1 hypothesis resolved | ≥3 (seed) + ≥1 (on-the-fly) hypotheses resolved |

The synthetic data is designed so that an honest analysis of the seed
hypotheses *surfaces* a real follow-up pattern (e.g. an interaction
term that's significant but not pre-registered) — giving the dynamic-Hs
system something genuine to raise.

**Comparison harness** (`tests/test_dynamic_vs_single_e2e.py`, run under
`OMICAIRN_DRY_RUN` for CI + a real-data variant for manual runs) tabulates:
- # hypotheses tested (graph: `hypothesis` nodes with a `tests` edge)
- # findings captured (`finding` nodes)
- # on-the-fly hypotheses (`origin=on_the_fly`)
- graph density proxy (`cites` and `raises` edges)
- honesty (every `supported` H has on-disk evidence)

Success = the dynamic-Hs workspace's graph is strictly richer (more
hypotheses tested, ≥1 on-the-fly, more findings) **without** losing the
honesty invariant, and the single-H control is unchanged from today's
behavior (regression guard).

## 6. Test plan (all three levels)

**Unit** (`tests/test_knowledge_graph.py`, `tests/test_hypothesis_lifecycle.py`,
`tests/test_research_md_goals.py`):
- node/edge upsert, dedup by id, op-log fold, round-trip
- lifecycle transitions + the on-disk-evidence honesty rule (illegal
  transition raises)
- provenance traversal (`raises` chains, `derived_from` ancestry)
- multi-goal parse + single-goal back-compat synthesis

**Integration** (`tests/test_orchestrator_dynamic_h.py`, dry-run):
- SCOPE/PLAN seed the graph from a multi-goal RESEARCH.md
- a stubbed EXECUTE that raises an on-the-fly hypothesis grows the graph
  and emits a `findings_extracted`/`hypothesis_raised` event
- the dynamic-H loop schedules exactly one extra EXECUTE when budgeted,
  and zero when the budget is 0 (the control path)

**End-to-end** (`tests/test_dynamic_vs_single_e2e.py` + the two research
cases): the § 5 comparison, dry-run in CI and real-data on demand.

## 7. Rollout / back-compat

- Graph writing is **purely additive**. Existing cases keep producing
  the same analysis artifacts; they just also get a
  `knowledge/graph.jsonl`.
- Every new behavior is gated: `allow_on_the_fly_hypotheses`,
  `dynamic_h_budget_minutes`. Defaults reproduce
  current behavior.
- `cross_check.check_knowledge_graph` is silent-skip when the graph is
  absent, like the always-on W-checks.

## 7b. Source skepticism & optional credibility weighting (belief 13)

External literature is evidence, not ground truth. Two mechanisms:

1. **Internal evidence outranks external claims (always on).** A
   hypothesis can only reach `supported`/`refuted` via a `supports`/
   `refutes` edge from a node with an on-disk `evidence_path`
   (§ 2.3 honesty rule). A `cites`-only edge never moves a hypothesis's
   status — a publication saying so is not enough.

2. **Credibility weighting (OFF by default, opt-in).** Config knob
   `prefer_reputable_sources: bool` in omicairn_config.yaml. When on, papers
   and datasets get a prior weight from a **user-supplied data file**
   (never hardcoded — belief 5), e.g. `reputable_sources.yaml` listing
   trusted institutions / journals / repositories and any
   integrity-flagged ones. The weight rides on the `cites`/`derived_from`
   edge's `weight` field and on `result_ref`/`dataset_ref` node
   `extra.credibility`. It affects *ranking and priors only* (which
   prior literature SCOPE and PLAN trust) and
   **never** overrides reproducible on-disk evidence. Default off because
   it is an admitted bias the team expects to revisit; it must remain a
   knob, not an assumption.

Implementation note: the scorer (`source_credibility.py`) reads the
user data file and returns a weight in [0,1]; absent the file or the
flag, every source weights 1.0 (neutral). Tracked as its own unit.

## 7c. IDEATE — the brainstorm front-stage (implemented 2026-06-02)

When the user has a research *purpose* but no concrete question, the
IDEATE stage runs *before* SCOPE and seeds the graph from the OUTSIDE
world, complementing the on-the-fly hypotheses that EXECUTE raises from
the user's own data. It is the front-door of the same knowledge-graph
model:

- **Sources as nodes.** IDEATE surveys papers + high-impact blogs +
  stated positions across a provenance taxonomy (`SourceType`:
  paper / blog / academia / professor / nobel / philosopher / industry)
  and writes each as a `source` node. Provenance is first-class so
  directions can be weighted by where the idea came from (belief 13,
  `prefer_reputable_sources`).
- **Directions as proposed goals.** Each proposed research direction is
  a `goal` node (`extra.kind="direction"`, `adopted=False`) that `cites`
  the sources motivating it — every proposal is traceable to provenance.
  Seed hypotheses live in `extra.seed_hypotheses` until the direction is
  adopted, so un-chosen candidates don't pollute the hypothesis space.
- **Adoption triggers the pipeline.** `omicairn ideate <ws> --adopt <id>`
  marks the direction adopted, materializes its seed hypotheses as
  pre-registered `hypothesis` nodes, and writes a sticky steering note.
  SCOPE/PLAN then read the adopted direction via BOTH `{{user_notes}}`
  and `{{knowledge_graph_projection}}` (now wired into scope.md/plan.md).

This makes the workspace knowledge graph the single substrate for the
whole arc: brainstorm directions (IDEATE) → pre-registered hypotheses
(PLAN) → on-the-fly hypotheses (EXECUTE/REVIEW) → resolved statuses with
evidence and a negative-results record (REVIEW).
Spec lives here; implementation in `omicairn/knowledge_graph.py` (IDEATE
section), `prompts/ideate.md`, `omicairn/cli.py::cmd_ideate`. Standalone
(not in PIPELINE_ORDER), mirroring FREE.

## 8. Open questions (resolved unless noted)

- **Where the graph lives:** per-workspace `<ws>/knowledge/graph.jsonl`;
  a shared cross-workspace promotion store (like `shared_lessons.jsonl`)
  is a later phase. *(resolved: per-workspace first.)*
- **Branch vs defer for on-the-fly H:** bounded in-workspace dynamic-H
  loop, off by default. *(resolved per user 2026-05-28: multi-H within a
  workspace is desired.)*
- **Node id stability for dedup:** `<type>:<slug>` where slug is a
  sha1 of (title + goal_id) for hypotheses, the results.json key for
  result_refs. *(resolved.)*
- **MTC accounting for post-hoc H:** record `posthoc=true`; W10 reports
  pre-registered vs post-hoc counts separately. Full correction policy
  is a science decision deferred to the REVIEW prompt. *(open — flag for
  user.)*
