# SCOPE phase

You are running the **SCOPE** phase of a OmiCairn research pipeline.
Your workspace is `{{workspace_root}}`. Your stage directory is
`{{stage_dir}}` (stage index {{stage_index}}, iteration
{{iter}}/{{max_iter}}).

Read `RESEARCH.md` and `CLAUDE.md` first.

## Context

- **Topic:** {{topic_slug}}
- **Mode:** {{research_mode}}
- **Domain:** {{domain}}
- **Research question:** {{research_question}}
- **Data location (read-only):** {{data_location}}
- **Shared dataset cache:** {{shared_cache_dir}} (inspect-only here; EXECUTE writes to it)

## Previous attempt note (READ FIRST IF NON-EMPTY)

{{retry_note}}

A non-empty retry note means a prior `back_to_scope` decision asked
us to re-scope. The data reality contradicted the original brief
somehow — read that prior verdict's `proposed_fix` and adjust the
scoping accordingly.

## Prior stages in this workspace

{{stage_listing}}

## Lessons from earlier attempts

{{knowledge_summary}}

## Cross-workspace lessons (shared paper-library)

These are lessons promoted from prior OmiCairn workspaces on the same
phase. Use them to avoid repeating known failure modes. Resolves to
"(none)" when no shared lessons exist yet.

{{shared_lessons_for_phase}}

## Pending routing suggestion

If a prior agent has written a routing suggestion this iteration,
read it as advisory input only — the orchestrator chooses what to
run next, not the suggestion. Resolves to "(none)" when no
suggestion is pending.

{{routing_suggestion}}

## Domain-specific guidance

{{domain_pack_guidance}}

{{user_notes}}

## Active human steering (if any)

{{steering}}

## Adopted research directions (from IDEATE, if any)

If the block below lists directions (especially any marked ✓ADOPTED),
the user ran the IDEATE brainstorm stage and chose a direction. Frame
the scope around the adopted direction and its seed hypotheses; treat a
matching steering/user note above as authoritative. If it says the graph
is empty, proceed from `RESEARCH.md` as usual.

{{knowledge_graph_projection}}

## Prior lab knowledge (across past runs)

{{lab_knowledge}}

If the lab has already supported/refuted related hypotheses or holds
relevant datasets (above), fold that into the scope — don't re-derive
what's known; build on it and cite which prior run established it.

## Asset-probe pre-flight (advisory)

The orchestrator scanned `RESEARCH.md` for URLs and DOIs at workspace
init time and probed each via HTTP HEAD (or ranged GET). The summary
below tells you which accessions are reachable, missing, or
unreachable BEFORE you commit to a hypothesis set in PLAN. Quote it
verbatim into `data_profile.md` so PLAN's pre-registration can
acknowledge each `missing` / `unreachable` asset with either a
`fallback_if_blocked` sub-hypothesis or a documented Limitations
deferral. The probe is best-effort and may have false negatives
(rate-limited hosts, transient outages); when in doubt, document the
uncertainty rather than silently dropping the asset.

```
{{asset_probe_summary}}
```

## Contribution directive (decides what KIND of study this must be)

{{contribution_directive}}

This is BINDING. If it says METHOD, the deliverable is a new
method/model/operator that beats real baselines — a discovery /
measurement study is insufficient, so `goal.md` must name the intended
method AND the ≥2 real baselines it will beat. If it says THEORY, the
deliverable is proven mathematical result(s) and `goal.md` must name the
formal claim(s) to be Lean-verified. If it says ANALYSIS, scope a clean
discovery/characterisation study as usual.

## Research arc (optional advisory hint — which way the argument flows)

{{research_arc_directive}}

This is an OPTIONAL ADVISORY hint, NOT a rule: when present it shapes the
EMPHASIS of `goal.md` (which way the argument flows between math and data),
but it NEVER changes the stage list (the pipeline is fixed) and NEVER
constrains the contribution type. When the hint is unset, leave the flow to
the science and the brief — force nothing.

## Feasibility / statistical-power gate

{{feasibility_guidance}}

This gate exists because OmiCairn once ran a pre-registered confirmatory
replication on 52 & 27 cells vs the original's 22,048 — faithfully
producing an underpowered null whose true cause was its own data
handling. A real PI catches that at data-profiling, in minutes. Profile
the ACTUALLY extractable analysis units (after the gating you intend to
apply), not the nominal totals, and compare them honestly to what the
hypothesis / reference effect needs.

## Your job

Everything you produce must be written INSIDE `{{stage_dir}}/` — do
not write files at the workspace root.

1. Read `RESEARCH.md` to understand the full research brief.
2. Inspect the data at `{{data_location}}`:
   - List the files, their sizes, their schemas.
   - For each tabular file: report row count, column names, key
     dtypes, missingness for the most important columns.
   - Identify which sample groups / datasets are present (the
     domain pack may call these "cohorts", "splits",
     "experiments", or something else), which columns contain
     the outcome variables, which columns contain the key
     features.
   - If the data contradicts `RESEARCH.md` (e.g. a promised column is
     missing), say so explicitly in `{{stage_dir}}/data_profile.md`.
   - Use the Bash tool to run small python snippets — do not try to
     hold the whole dataset in your context.
3. Write `{{stage_dir}}/goal.md`:
   - A 300–500 word restatement of the research question in your own
     words.
   - The specific, falsifiable primary hypothesis you plan to test
     (if METHOD mode) or the discovery objective (if DISCOVERY mode).
   - **Intended contribution (per the Contribution directive above).**
     If the directive is METHOD: name the concrete method/model/operator
     you intend to contribute AND the ≥2 real competing-method baselines
     it must beat (e.g. fine-tuning, prompting, an alternative algorithm —
     not adversarial/sanity controls). If THEORY: name the formal claim(s)
     (formula / bound / theorem) you intend to prove and Lean-verify. If
     ANALYSIS: state the discovery objective and ≥1 adversarial baseline.
   - What "success" looks like in concrete numeric terms.
   - What a negative result looks like, and why it would still be
     valuable.
4. Write `{{stage_dir}}/data_profile.md`:
   - One section per dataset / sample group. Row count, column
     count, key column summaries, any red flags (batch effects,
     missing covariates, heavy imbalance).
   - End with a "data contract" table: columns you will use ×
     dataset.
5. Write `{{stage_dir}}/summary.md`: a 1–3 line summary of what this
   scope stage concluded. This line will appear in the prior-stages
   table shown to downstream phases.
6. **If the feasibility gate is ACTIVE** (see the section above), write
   `{{stage_dir}}/feasibility.json` following the schema there. Base
   `extractable_n_estimate` on a quick ACTUAL probe (e.g. count the cells
   that pass your intended gate), not a guess. Key the verdict on power /
   data feasibility ONLY — a well-powered NULL is `feasible`. If a
   required dataset/gene-set is missing and web is allowed, recommend
   `acquire_data` (fetch it), not silent exclusion. When the gate is not
   active this file is optional and may be omitted.
7. When `goal.md`, `data_profile.md`, and `summary.md` are all
   written and substantive (plus `feasibility.json` if the gate is
   active), write `{{stage_dir}}/done.flag` with the single character `1`.

## Constraints

- **Do not write to `{{data_location}}`.** Read-only.
- **Do not fabricate statistics** you have not actually computed.
- **Do not download external data during SCOPE.** If the research
  brief names remote accessions, list them in `data_profile.md`
  under a `## Remote inputs to resolve in EXECUTE` subsection with
  one row per accession (slug, source URL, expected files, target
  path under `{{shared_cache_dir}}/<slug>/`). EXECUTE handles the
  actual fetch via its own protocol. You MAY, however, inspect
  `{{shared_cache_dir}}` for accessions that previous runs have
  already cached and note their availability here so PLAN can take
  them as given.
- **Do not read literature yet** — PLAN phase handles that.
- Keep each python invocation focused and short; use pandas or
  polars via `python3 -c '...'` for quick inspection.
- Budget: finish in under 30 minutes of wall clock.

## Contract (required files)

- `{{stage_dir}}/goal.md` (required, substantive)
- `{{stage_dir}}/data_profile.md` (required, substantive)
- `{{stage_dir}}/summary.md` (required, one short paragraph)
- `{{stage_dir}}/feasibility.json` (required IFF the feasibility gate is
  active for this case — see the gate section; optional otherwise)
- `{{stage_dir}}/done.flag` (required, one char)

Write the `done.flag` LAST, only after everything else is in place.
