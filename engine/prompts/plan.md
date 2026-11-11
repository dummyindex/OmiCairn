# PLAN phase

You are running the **PLAN** phase of OmiCairn. Your stage directory
is `{{stage_dir}}` (stage index {{stage_index}}, iteration
{{iter}}/{{max_iter}}).

Read `CLAUDE.md`, `RESEARCH.md`, and the latest scope stage's
`goal.md` + `data_profile.md` under `{{latest_scope_dir}}/` first.

## Analysis-rigor rules (READ — these are standing rules)

The OmiCairn analysis-rigor rules live in `rules/analysis_rigor.md` at the
OmiCairn package root (the directory that contains the `omicairn/` package; locate
it with
`python3 -c "import omicairn, pathlib; print(pathlib.Path(omicairn.__file__).resolve().parent.parent / 'rules' / 'analysis_rigor.md')"`
and read it). Design the plan to satisfy them up front:

- **R1 — Subsampling is for testing only.** Plan the FINAL analysis on
  the FULL dataset. Subsampling is fine for a quick sketch but never for
  the reported result, and **especially never for a method benchmark**
  (subsampling biases method comparisons). If you suspect full-data is
  infeasible in budget, say so in the plan and prescribe R3 (resource
  request), not a subsample.
- **R2 — Breadth AND depth.** The plan must use ALL datasets named in
  `RESEARCH.md`, not just the primary one; prescribe cross-cohort /
  multi-dataset replication wherever ≥2 relevant cohorts exist; and back
  each major claim with orthogonal / complementary methods plus
  sensitivity + robustness analyses. For biomedical computational studies,
  breadth (multi-dataset/validation) AND depth (orthogonal analyses) are
  both expected — a single-method, single-cohort plan is under-scoped.
- **R3 — Estimate runtime/memory; request resources, don't degrade**
  (see step 3 § Resource estimate).

## Contribution directive (decides the pre-registration's contribution shape)

{{contribution_directive}}

This is BINDING and shapes what you pre-register. The detailed protocol —
what counts as a "method" contribution, what a "real baseline" is (vs an
adversarial control), and the Lean-verified-math protocol for proven
claims — lives in `rules/method_depth.md` at the OmiCairn package root (locate
it with
`python3 -c "import omicairn, pathlib; print(pathlib.Path(omicairn.__file__).resolve().parent.parent / 'rules' / 'method_depth.md')"`
and read it when the directive is METHOD or THEORY).

- If **METHOD**: pre-register (a) the concrete method/model/operator you
  will contribute, (b) the ≥2 REAL competing-method baselines it must beat
  (fine-tuning, prompting, an alternative algorithm — NOT random/shuffled
  sanity controls), each with a pre-specified paired Δ(method, baseline)
  decision rule, and (c) the ablation(s) isolating the method's
  contributing component. A plan whose only "baselines" are adversarial
  controls is under-scoped for a method case.
- If **THEORY**: pre-register the theorem/formula/bound statement(s), name
  the HEADLINE one in `preregistration.json.load_bearing_theorem`, and state
  the target `proof_status` for each — `proven_lean` (Lean-verified before
  being cited as proven), or, for a result you will DERIVE and empirically
  VALIDATE but whose Lean proof is a parallel obligation,
  `derived_validated_proof_pending` (labeled proof-pending in
  `results.json` / `analysis.md`, never "proven"; the Lean proof closed later by a `omicairn steer`
  deepening). A `proof_status` of `proven_lean` requires a passing Lean
  verdict; see `rules/method_depth.md`.
- If **ANALYSIS**: existing rules apply; an adversarial baseline suffices.

## Research arc (optional advisory hint — which way the argument flows)

{{research_arc_directive}}

This is an OPTIONAL ADVISORY hint, NOT a rule: when present it shapes the
EMPHASIS of the pre-registration (which way the argument flows between math
and data), but it NEVER changes the stage list and NEVER constrains the
contribution type. When unset, let the science and the brief determine the
flow. Regardless of the arc, a derived result that is empirically validated
but whose Lean proof is still pending may legitimately ship as
`derived_validated_proof_pending` (see the contribution / THEORY directive).

## Context

- **Topic:** {{topic_slug}}
- **Mode:** {{research_mode}}
- **Domain:** {{domain}}
- **Primary metric:** {{primary_metric}}
- **Baseline:** {{baseline}}
- **Web allowed:** {{allow_web}}

## Previous attempt note (READ FIRST IF NON-EMPTY)

{{retry_note}}

If the block above names a prior verdict-emitting stage, this is a
retry triggered by a `back_to_plan` decision — normally from the
analysis REVIEW (`{{latest_review_dir}}/verdict.json`,
`review_notes.md` and `negative_results.md`) or from the SCOPE
feasibility gate (`{{latest_scope_dir}}/feasibility.json`). Read the
emitting stage's files, then re-design the experiment plan around the
specific structural concerns flagged. Don't just re-run a default
LASSO-Cox plan if the review said the design is wrong-headed.

## Prior stages in this workspace

{{stage_listing}}

## Lessons from earlier attempts

{{knowledge_summary}}

## Cross-workspace lessons (shared paper-library)

{{shared_lessons_for_phase}}

## Pending routing suggestion

{{routing_suggestion}}

## Domain-specific guidance

{{domain_pack_guidance}}

{{user_notes}}

## Active human steering (if any)

{{steering}}

## Adopted research directions + knowledge graph (from IDEATE, if any)

If directions are listed below (especially ✓ADOPTED), the user picked a
direction in the IDEATE brainstorm stage. Pre-register its seed
hypotheses as your H1..Hn, and reuse the sources it cites as a starting
point for the literature review. Empty = plan from `RESEARCH.md`.

{{knowledge_graph_projection}}

## Your job

1. **Literature review (grounded).**

   **Before starting your main work**, dispatch the `literature-fetcher`
   subagent in the background via the Agent tool. It will populate
   `literature/` with verified paper metadata, abstracts, and OA PDFs
   while you work on hypotheses and experiment design.

   When you reach this step, check if `literature/papers.json` exists
   and contains entries. If it does:
   - Start with `literature/LITERATURE_INDEX.md` for a one-screen scan
     of what's available (access tier, short reference, which abstract
     / PDF is on disk). Use this to pick which papers to read in depth.
   - Read `literature/papers.json` and skim abstracts in `literature/abstracts/`.
   - Write `{{stage_dir}}/literature.md` (600–1200 words) using EXACT metadata
     from `papers.json` (authors, year, journal, volume, pages, DOI).
     Do NOT guess bibliographic details when they are in front of you.
   - Tag citations: `[GROUNDED]` (from papers.json with verified DOI/PMID)
     vs `[MODEL]` (from your domain knowledge, unverified).
   - Organize by: (a) seminal foundations, (b) methodological precedents,
     (c) recent results on the specific question, (d) negative/failed
     replication attempts.
   - Include a causal/theoretical chain paragraph with one grounded
     citation per link (the mechanism connecting the research question's
     variables to its outcome).
   - Identify gaps: "No paper in the literature folder addresses X;
     this should be verified in REVIEW."

   If `literature/papers.json` is empty or missing (subagent still
   running, or init ran with `--skip-literature`):
   - Fall back to domain-knowledge-only mode.
   - Tag all citations `[MODEL: verify in REVIEW]`.

   You MAY use WebSearch (up to 5 calls) to fill gaps not covered by
   the pre-fetched literature.
2. **Hypotheses.** Write `{{stage_dir}}/hypotheses.md` with:
   - Primary hypothesis (H1), falsifiable, stated as a numeric
     threshold on the primary metric vs the declared baseline
     (e.g., "H1: The primary model's {{primary_metric}} will
     exceed `{{baseline}}` by ≥0.02 with a 95% CI excluding 0").
   - Up to 3 secondary hypotheses.
   - For each: the statistical test, the null, the decision rule.
   - **Power / sample-size check.** Add a `## Power` subsection
     that, given the data's n (from the scope stage's
     `data_profile.md`) and the declared baseline, estimates
     (a) the minimum detectable effect at 80% power and α=0.05
     under the primary test's statistical assumptions, and
     (b) the power to detect the effect size H1 postulates.
     For bootstrap or permutation tests the estimate can be a
     back-of-envelope simulation sketch (the executor will run
     the real calculation). If the study is underpowered for H1
     (<80%), state this explicitly AND state what effect sizes
     it CAN reliably detect — this reframes the study's
     contribution before EXECUTE touches the data. A missing power
     analysis is a rigor gap the REVIEW phase flags.
3. **Experiment plan.** Write `{{stage_dir}}/experiment_plan.md`:
   - Data flow: which input is the training / discovery partition,
     which is the held-out / validation partition. If the domain
     pack uses field-specific terminology ("external cohort",
     "replication dataset", "out-of-sample split"), use that.
   - Feature construction: which columns, any transforms, handling
     of missingness.
   - Model family and hyperparameter search strategy
     (cross-validation or equivalent honest tuning on the training
     partition only).
   - Baseline conditions (at least one adversarial baseline — i.e.,
     a baseline your model should be able to beat *trivially* if
     it is doing anything at all; the domain pack lists
     field-standard adversarial baselines). **When the Contribution
     directive is METHOD, the adversarial baseline is necessary but NOT
     sufficient: you must ALSO pre-register ≥2 REAL competing-method
     baselines (e.g. fine-tuning, prompting, an alternative algorithm)
     that your proposed method is expected to beat — these are what
     establish a method contribution, not just that a measurement is
     real.**
   - Evaluation metrics with CIs and **a paired Δ statistic**
     (primary − baseline) with 95% CI and directional p-value,
     computed via resampling with the SAME indices for primary and
     baseline. This paired comparison is REQUIRED whenever a
     baseline is specified — it is what answers the question "is
     the primary model actually better than the baseline?". A
     simple arithmetic difference of point estimates is NOT
     sufficient. The execute phase must emit one paired-delta
     block per baseline into `results.json`.
   - Subgroup analyses, if any. If a secondary hypothesis tests a
     subgroup effect and the domain pack specifies a
     stratum-trained confirmation protocol, follow that guidance.
   - **Breadth + depth plan (R2).** A `## Breadth and depth` subsection
     that (a) lists every dataset named in `RESEARCH.md` and how the plan
     uses each one (no named dataset left untouched without a stated
     reason); (b) names the cross-cohort / multi-dataset replication when
     ≥2 relevant cohorts exist; and (c) names the orthogonal /
     complementary method(s) and the sensitivity + robustness analyses
     that back each major claim. Breadth (multi-dataset/validation) AND
     depth (orthogonal analyses) are both expected for a biomedical
     computational study.
   - **Resource estimate (R3).** A `## Resource estimate` subsection
     giving a rough wall-time and peak-memory estimate for the FULL-data
     analysis (from n, feature count, #bootstraps, model cost). Size it
     against the **compute envelope** configured for this run:

     {{compute_resources}}

     If the analysis will not finish within that envelope, state the
     plan's response: a more efficient strategy, OR a directive for
     EXECUTE to file `RESOURCE_REQUEST.md` (more wall time / CPU / a GPU —
     request a GPU only if one is NOT already listed as available above)
     and proceed with an honest partial. Do NOT plan to subsample the
     final/benchmark analysis to fit the clock.
   - Stopping rules: "held-out / validation partition touched
     exactly once after lockdown".
   - **Data fetch plan** (only if the experiment needs inputs NOT
     present under `{{data_location}}`). List each required
     accession/slug, its canonical public source URL, the expected
     file(s), and the target path inside the shared dataset cache
     `{{shared_cache_dir}}/<accession_or_slug>/`. The shared cache
     lives outside any single workspace, so downloads are reused
     across runs. EXECUTE will fetch following the generic protocol
     in its prompt; the plan's job here is only to name the inputs
     and their sources so the downloader has a target list. If
     `{{allow_web}}` is `False`, do NOT include a fetch plan — list
     the missing inputs under `## Figures deferred` instead with a
     `allow_web=False` reason and the planned analysis restricted to
     what IS locally available.
   - **Figures planned.** A `## Figures planned` subsection listing
     every figure the EXECUTE phase must produce. If
     `RESEARCH.md` has a `figures_planned:` block, use it as the
     source of truth and expand each entry with the data
     dependencies and statistics each panel needs. Otherwise
     propose the multi-panel figures that together show the
     evidence for and against each hypothesis. For each figure write:
     `### Figure N. <title>` followed by a bullet list of panels
     `- (a) <panel description>: <data source + statistic>`.
     Name figures so EXECUTE can save them as
     `figures/figN_<slug>.png` (and `.pdf`) under its stage
     directory. The plan must commit to a specific count so EXECUTE
     knows the target. A study-design schematic is optional; if you
     plan one, declare it with `kind: "schematic"` in the figures
     contract (drawn with matplotlib primitives, no data).
   - **Figures NOT in scope.** A `## Figures deferred` subsection
     enumerating any figures in `RESEARCH.md` under
     `figures_not_in_scope:` (or that you judge infeasible) with
     a one-line reason each. This protects EXECUTE from chasing
     figures whose data is unavailable.
   - **Canonical results.json schema.** When you sketch the
     expected `results.json` shape for the executor, use the
     canonical form the orchestrator enforces. Never prescribe
     legacy `conditions.*` dicts or sibling-scalar
     `ci_lower_95`/`ci_upper_95` fields — use `ci_95: [lo, hi]`
     arrays inside `primary_model`, `baselines.*`, and
     `comparisons.*`. For every baseline the plan specifies,
     add an entry under `comparisons` named
     `delta_primary_vs_<baseline>` with `delta`, `ci_95`, and
     `p_directional` keys. The executor will be held to this
     schema by an orchestrator cross-check; prescribing a
     legacy shape here forces an unnecessary retry.
4. **Figures contract (machine-readable).** Write
   `{{stage_dir}}/figures_contract.json`: the structured version of
   your `## Figures planned` prose, which EXECUTE consumes as its
   production target and CROSS_CHECK enforces.

   ```json
   {
     "figures": [
       {
         "id": "F1",
         "title": "Study design — variable → mechanism → outcome",
         "required": true,
         "kind": "schematic",
         "panels": [
           {"id": "A", "description": "study design overview"}
         ],
         "output_paths": ["figures/fig1_schematic.png", "figures/fig1_schematic.pdf"],
         "results_tables": []
       },
       {
         "id": "F4",
         "title": "Clinicopathological integration + nomogram",
         "required": true,
         "kind": "results",
         "panels": [
           {
             "id": "C",
             "description": "nomogram for 3- and 5-year OS",
             "data_deps": ["metabric.clinical", "risk_scores.tsv"],
             "statistics": ["coxph_multivariable", "calibration_3y_5y"]
           }
         ],
         "output_paths": ["figures/fig4_panelC_nomogram.pdf"],
         "results_tables": ["results/nomogram_coxph_summary.csv"]
       }
     ],
     "deferred": [
       {"id": "F7", "reason": "scRNA data not available locally this run"}
     ]
   }
   ```

   Rules:
   - Include **every** figure the analysis is targeting. The example
     above shows an optional study-design schematic (`kind:
     "schematic"`, empty `results_tables`); every other figure is
     `kind: "results"` with real `panels[].data_deps` / `statistics`
     and backing `results_tables`.
   - `output_paths` must be stage-dir-relative (e.g.
     `figures/figN_slug.pdf`) — EXECUTE will aim to write them there.
   - `results_tables` lists the CSV/TSV/JSON files that back the
     figure's numbers. Leave empty for schematics.
   - `required: true` marks figures you judge load-bearing for the
     analysis. `required: false` marks supporting figures
     that would be nice but are not essential. This is a *signal*
     to EXECUTE and REVIEW, not a hard gate — see below.
   - Items in `deferred` are figures the run will **not** attempt
     this iteration (e.g. because required data is absent). Give a
     one-sentence reason. Deferred items are not expected in EXECUTE.
   - Synthesise from both `figures_planned` YAML and any free-form
     figure guidance in RESEARCH.md (e.g. appendices). The JSON is
     authoritative; the YAML is only a seed hint.

   **The contract is a reference, not a hard gate.** If EXECUTE
   cannot produce a `required: true` figure for a legitimate
   scientific reason (method failed, data insufficient, infeasible
   within budget), it must document that in `analysis.md` under a
   `## Limitations and deviations from the figures contract` section
   with the figure id and the specific reason. REVIEW then judges
   whether the gap is honestly disclosed or whether it looks like a
   producible-but-skipped figure worth retrying.
   CROSS_CHECK surfaces every gap as an informational warning but
   never fails the execute verdict on figure-contract gaps alone.

5. **Pre-registration.** Write `{{stage_dir}}/preregistration.json` with this
   schema:
   ```json
   {
     "primary_hypothesis": "...",
     "primary_metric": "...",
     "baseline": "...",
     "analysis_plan": "...",
     "training_partition": "...",
     "validation_partition": "...",
     "success_threshold": "...",
     "stopping_rule": "one shot at the validation partition; no re-tuning after unblinding",
     "family_correction": "bonferroni | bh_fdr | none_with_justification | per_hypothesis",
     "family_correction_rationale": "<one sentence; required when family_correction is 'none_with_justification'>",
     "mde": <number or null>,
     "power_target": <number, default 0.80>,
     "requires_stratum_confirmation": <bool>,
     "load_bearing_arm": "delta_<treatment>_vs_<baseline>",
     "discriminating_arm": "delta_<deciding_experiment>",
     "discriminating_arm_needs_scarce_resource": <bool>,
     "contribution_type": "analysis | method | theory",
     "load_bearing_theorem": "<headline Lean theorem name; theory cases only>",
     "baselines": {
       "shared_protocol_feasible": <bool>,
       "candidates": ["<named published/competing method on the same task+data>"],
       "no_baseline_rationale": "<one sentence; required when shared_protocol_feasible is false>"
     },
     "hypotheses": [
       {
         "id": "H1",
         "decision_rule": "primary metric exceeds baseline by ≥0.02 with 95% CI excluding 0",
         "decision_rule_structured": {
           "metric": "comparisons.delta_primary_vs_baseline.delta",
           "op": ">=",
           "threshold": 0.02,
           "ci_excludes": 0
         },
         "depends_on": ["dataset:E-MTAB-15667", "code:quadbio/organoid_patterning_screen"],
         "fallback_if_blocked": "H1_SURROGATE_<short_label>"
       }
     ]
   }
   ```
   If the domain pack uses field-specific terminology, you MAY
   rename `training_partition` / `validation_partition` to the
   field-standard terms (e.g. `discovery_cohort` /
   `external_cohort`) — just keep the two-partition structure.

   **Hypothesis dependency DAG (W9, mandatory).** Every hypothesis
   in `hypotheses[]` MUST list its data / code / compute
   dependencies under `depends_on` using the canonical
   `<kind>:<slug>` form, where `<kind>` is one of `dataset`,
   `code`, `compute`, `model`. Resolve each `dataset:<slug>` against
   `<workspace>/asset_probe.json` from the SCOPE phase: if the probe
   classified it as `missing` or `unreachable`, the hypothesis MUST
   declare a `fallback_if_blocked` sub-hypothesis (a different test
   that does not need the missing asset) OR explicitly defer the
   hypothesis with `"fallback_if_blocked": "DEFER_TO_LIMITATIONS"`.
   CROSS_CHECK W9 raises an ERROR for any hypothesis whose
   dependencies are blocked AND whose fallback is missing or
   unrecognised.

   **Machine-checkable decision rule (W20, strongly recommended).**
   The prose `decision_rule` is for humans; W9 only checks it is
   PRESENT, never that it was HONORED. Emit its machine-checkable
   TWIN as `decision_rule_structured` so CROSS_CHECK can mechanically
   verify the rule against `results.json` (no prose parsing). It is a
   small JSON object — the SAME quantitative test the prose states,
   in structured form:
   ```json
   "decision_rule_structured": {
     "metric": "<dotted key path into results.json, e.g. comparisons.delta_primary_vs_baseline.delta or h2_spearman.r_s>",
     "op": ">= | > | <= | < | ==",
     "threshold": <number>,
     "ci_excludes": <number or null; the metric's ci_95 sibling must EXCLUDE this (typically 0); omit/null when the rule has no CI clause>
   }
   ```
   `metric` MUST resolve to a numeric scalar in the `results.json`
   EXECUTE writes (follow the dotted path); `ci_excludes`, when set,
   requires a two-element `ci_95` list ALONGSIDE that scalar. Keep it
   the EXACT quantity the prose rule names — do NOT silently reframe
   to a different quantity when reporting. After EXECUTE, W20 reads the
   metric, evaluates `op` vs `threshold` (+ `ci_excludes`), and raises
   an ERROR if the hypothesis is framed SUPPORTED (see the
   per-hypothesis status below) while the structured rule is VIOLATED.
   The field is OPTIONAL (legacy runs predate it and W20 silently
   passes when it is absent) but you SHOULD emit it for every
   hypothesis with a quantitative rule — it is how OmiCairn keeps a run
   from claiming a hypothesis "proven" while its own pre-registered
   rule is unmet (the cross-model-transfer H3 failure mode).

   To let W20 know the run FRAMES a hypothesis as resolved (so a
   violation ERRORs rather than merely WARNs), EXECUTE should record a
   STRUCTURED per-hypothesis verdict in `results.json` — either a
   `hypotheses` list of `{"id": "H1", "status": "supported | refuted
   | inconclusive"}` entries, or a `hypothesis_status` dict
   `{"H1": "supported", ...}`. Set `status` to `supported` ONLY when
   the structured rule actually holds; an honest `refuted` /
   `inconclusive` keeps a violated rule advisory (WARN), not a block.

   **Load-bearing arm (W13, mandatory).** Set `load_bearing_arm` to
   the single `comparisons.delta_*` key whose completion the run's
   HEADLINE depends on — the one cell of the central results table the
   contribution cannot survive without (e.g. `delta_steered_vs_unsteered`,
   `delta_primary_vs_null`). It MUST be the name of a comparison you
   declare under `comparisons` in `experiment_plan.md` step 4, and it
   reuses the same DAG you wrote above: the hypothesis backing this arm
   carries its own `depends_on` + `fallback_if_blocked`. Name exactly
   one key (or a short list if the headline is genuinely a conjunction
   of arms — keep it minimal). After EXECUTE, CROSS_CHECK W13 raises an
   ERROR if any named `comparisons.delta_*` key is ABSENT from
   `results.json` — the run may not be reported with a hole in the
   headline cell. If the arm turns out resource-blocked, REVIEW routes a
   re-headline of the results on a completed arm (a `scoped` framing)
   rather than accepting the hole. Omit the field only for legacy/descriptive
   studies with no single headline comparison (W13 then no-ops).

   **Discriminating arm (W17, the key rigor lever — mandatory when the
   study has a central mechanistic/causal question).** The `load_bearing_arm`
   above is the HEADLINE arm — it demonstrates the effect EXISTS. Set
   `discriminating_arm` to a DIFFERENT key: the single `comparisons.delta_*`
   experiment whose outcome would most ADJUDICATE the study's central
   question — the one that would *settle* the mechanism/cause, not merely
   show the effect is real. **This is the experiment you would otherwise
   defer as "future work that would settle it" — surface it NOW, at PLAN,
   not after the analysis is done.** (Concretely, in the
   2026-06 wave: the perceiver-layer steering test that would resolve the
   architecture collapse; the soft-gate counterfactual that would generalise
   the gate-starvation cause; the plausibility-balanced Winoground contrast;
   the per-group calibration that would pin the fairness floor; the race arm
   on a race-primary dataset.) Set
   `discriminating_arm_needs_scarce_resource: true` when it needs a GPU,
   a gated dataset/token, or other contended resource — EXECUTE then
   SCHEDULES it FIRST, because the discriminating arm is exactly the one
   that gets starved by host-contention and gating. After EXECUTE,
   CROSS_CHECK W17 ERRORs if the named `discriminating_arm` is ABSENT from
   `results.json` AND the run frames the central question as RESOLVED
   (`results.json.central_question.discriminating_arm_resolution` is
   `"resolved"` or absent). The two legal exits, never "ship with the hole":
   (a) run the arm and re-emit results.json; or (b) narrow the claim —
   re-headline the results on what the completed arms establish and set
   `central_question.discriminating_arm_resolution: "scoped"` (then W17
   downgrades to advisory). Omit the field only for purely descriptive
   studies with no central question to adjudicate (W17 then warns that PLAN
   should consider naming one, but never blocks).

   **Salvage-provenance of the headline arm (W15, enforced at EXECUTE).**
   The load-bearing arm must not only be COMPLETE (W13) but
   PROVENANCE-CLEAN: its number must be **re-scorable** from on-disk
   code+seed, not recovered via salvage (host-scratch, a partial job's
   logs, or a manual one-off). At EXECUTE each `comparisons.delta_*`
   entry is tagged `provenance: "fresh" | "salvaged"` (see execute.md
   § Canonical results.json schema); CROSS_CHECK W15 raises an ERROR if
   the named `load_bearing_arm` resolves to a `"salvaged"` entry — the
   headline cannot rest on the least-reproducible number while finished
   arms are clean. Plan the headline arm so its compute is re-runnable on
   the available envelope; if it can only be salvaged, REVIEW routes a
   re-headline on a clean arm. Nothing extra to declare here — W15
   reads the same `load_bearing_arm` field plus the EXECUTE provenance
   tag.

   **Theorem-depth of the headline theorem (W18, enforced at EXECUTE for
   THEORY cases).** Set `contribution_type` (`analysis | method | theory`)
   and, for a `theory` case, `load_bearing_theorem` to the headline Lean
   theorem's name — the theory analogue of `load_bearing_arm`. EXECUTE's
   `math-formalization` skill writes a `<stage>/lean/<Claim>.verdict.json`
   per theorem carrying `substance_warnings` (the M3b trivial-pattern +
   elementary-theorem heuristics in `omicairn.lean_verify`). CROSS_CHECK W18
   reads those warnings and ENFORCES them: it raises an ERROR when the
   load-bearing theorem carries a substance_warning while framed as a
   first-class `theorem` — a theory case may not rest on an elementary/
   trivial theorem (e.g. an XOR-table parity fact closed by `ring`) AS its
   headline theorem. The two legal exits: (a) re-aim the headline at a
   SUBSTANTIVE theorem (a constructed witness whose truth is not visible
   from the statement, or a derived bound) and re-verify; or (b) HONESTLY
   DEMOTE the elementary result — set `contribution_role` to
   `conjecture` / `formalized_observation` on its verdict.json — so it is
   stated as a labeled observation, not a deep theorem (W18 then downgrades
   to advisory). Omit `contribution_type`/`load_bearing_theorem` for
   non-theory cases (W18 then no-ops).

   **Shared-protocol baseline (W14, advisory).** A comparison-driven
   result is judged partly on whether it is POSITIONED against a named
   alternative: an analysis with a movable method or a deployable
   positive artifact is weaker when it declines a head-to-head against a competing/published method that runs on the
   SAME task + data. Decide this at PLAN time and record it under
   `baselines`:
   - `shared_protocol_feasible` — `true` when a published or competing
     method exists that COULD be run on your protocol (same task,
     comparable data + metric); `false` when none applies (genuinely
     first-of-kind, or a pure null / descriptive / mechanism-analysis
     study whose contribution is a measurement, not a method to beat).
   - `candidates` — when feasible, name the specific competing method(s)
     a comparison could run against. Draw these from the methods your
     own literature review surfaced — a baseline you already CITE is a
     baseline you should not decline. These flow into the
     `comparisons.delta_*` arms you declare in `experiment_plan.md`.
   - `no_baseline_rationale` — when `shared_protocol_feasible` is
     `false`, one concrete sentence saying WHY (e.g. "first protocol of
     this kind; no prior method operates on this measurement" or "pure
     refutation of an existing claim; the comparator is the claim
     itself, not a competing method"). A bare `false` with no rationale
     is what CROSS_CHECK W14 warns on.
   This is ADVISORY, not a gate: it complements the METHOD-case rule in
   `rules/method_depth.md` (≥2 real baselines, hard-enforced for
   `method` contributions) by extending baseline-AWARENESS to analysis /
   diagnostic cases, where a comparison is sometimes legitimately N/A.
   W14 only WARNS; REVIEW decides (see review.md). Backward compatible:
   omit `baselines` entirely and W14 no-ops.

   **Multiple-testing correction (W10, mandatory).** When the
   pre-registration declares ≥2 hypotheses,
   `family_correction` MUST be set. The four legal values are:
   - `bonferroni` (most conservative; α/k threshold per test)
   - `bh_fdr` (Benjamini-Hochberg FDR control at α)
   - `none_with_justification` (requires
     `family_correction_rationale` — typical only when the
     hypotheses are sequentially dependent or the family is
     pre-screened)
   - `per_hypothesis` (each H carries its own α; rationale recorded
     in `decision_rule`)
   CROSS_CHECK W10 raises an ERROR if the field is missing on a
   ≥2-hypothesis plan.

   **Structured fields for W2/W7 (parser audit Step C, since 2026-05).**
   Set these explicitly so cross_check reads them directly instead of
   parsing prose:

   - `mde` — minimum detectable effect at 80% power (number, or
     `null` if your study is descriptive). W2 uses this; without it,
     W2 falls back to scanning hypotheses.md / experiment_plan.md for
     a `## Power` section + numeric MDE pattern (warn-only fallback).
   - `power_target` — target power (default 0.80).
   - `requires_stratum_confirmation` — boolean. `true` when the plan
     commits to fitting the model separately within a named subgroup
     (W7); `false` when the primary fit on the pooled cohort
     suffices. W7 uses this; without it, W7 falls back to scanning
     plan prose for stratum/subgroup commitment language (warn-only).
   - `load_bearing_arm` — the `comparisons.delta_*` key the headline
     depends on (see W13 above). Unlike W2/W7 this has no prose
     fallback: an absent field makes W13 a no-op, a named-but-missing
     key makes W13 ERROR post-EXECUTE.
   - `discriminating_arm` — the `comparisons.delta_*` key of the
     experiment that ADJUDICATES the central question (see W17 above;
     distinct from `load_bearing_arm`). Optional
     `discriminating_arm_needs_scarce_resource` (bool) flags it for
     first-on-the-scarce-resource scheduling. Absent → W17 advisory WARN;
     named-but-missing key with a `"resolved"` framing → W17 ERROR.
   - `baselines` — the shared-protocol baseline declaration (see W14
     above). When `shared_protocol_feasible` is `false`, set
     `no_baseline_rationale` so W14 reads the justification directly;
     an empty/blank rationale on a `false` declaration (or a `false`
     with no rationale field) is what W14 WARNS on. Omit `baselines`
     entirely on legacy plans and W14 no-ops.
6. When all six contract files (`literature.md`, `hypotheses.md`,
   `experiment_plan.md`, `figures_contract.json`,
   `preregistration.json`, `summary.md`) are written and
   substantive, write `{{stage_dir}}/done.flag` with `1`.

## Constraints

- **Do not start implementing code.** EXECUTE phase does that. If
  you sketch pseudocode, keep it in `{{stage_dir}}/experiment_plan.md`
  as inline fenced blocks.
- **Do not fabricate citations.** If you name a paper, it must
  exist — or state "illustrative, verify in REVIEW phase".
- **Do not write to data directory.** Read-only.
- Budget: ≤30 minutes wall clock.

## Contract (required files)

- `{{stage_dir}}/literature.md`
- `{{stage_dir}}/hypotheses.md`
- `{{stage_dir}}/experiment_plan.md`
- `{{stage_dir}}/figures_contract.json`  (must parse as JSON; see step 4)
- `{{stage_dir}}/preregistration.json`  (must parse as JSON)
- `{{stage_dir}}/summary.md`  (1–3 line takeaway for downstream stages)
- `{{stage_dir}}/done.flag`

Write `done.flag` LAST.
