# Lab Theorem Library + Virtual Mathematician — Design

Status: **expanded + EXECUTE-retrieval wired** (2026-06-19). Core module
`omicairn/lab_theorems.py`, CLI `omicairn labtheorems`, seeded **55-theorem**
Lean-validated library (all compiled sorry-free against local Mathlib), a
working virtual-mathematician demo, and the **EXECUTE-stage retrieval seam**
(`omicairn labtheorems retrieve` + a `{{lab_theorems}}` prompt token + the
`math-formalization` agent step). This doc records the architecture and the
roadmap to the full CMU-grad curriculum.

> **Naming:** the subsystem is `lab_theorems` (NOT "mathlib") — it parallels
> `lab_knowledge` / `lab_library` and avoids colliding with Lean's own Mathlib
> dependency (the `.lean-mathlib-omicairn` Lake project). The data dir is
> `lab_theorems/` at the repo root; per-theorem provenance is in
> `lab_theorems/sources.md`.

## 1. Motivation

OmiCairn theory phases (the interp/fairness runs already ship Lean theorems
— see the d5/d6/d7 Cauchy–Schwarz/witness proofs) are limited to the base
LLM's pretrained math and whatever the web fetch grabbed. A "fact" stated
by an LLM is only as good as a proof that **compiles**. We want the lab to
**accumulate** a body of machine-validated mathematics it can build on,
exactly the way `lab_knowledge` accumulates empirical findings across runs.

The library is therefore the math analogue of `lab_knowledge`: a
compounding, cross-run, **validate-don't-trust** store. The unit of trust
is a Lean proof checked sorry-free by the **local toolchain**, never an LLM
assertion.

## 2. Storage schema

A theorem entry (`omicairn.lab_theorems.TheoremEntry`) carries:

| field | meaning |
|---|---|
| `name` | canonical Lean declaration name (store key) |
| `statement` | human/LaTeX-ish statement |
| `lean_source` | the Lean program (statement + proof) |
| `lean_file` | tracked `.lean` path (relative to `lab_theorems/`) |
| `status` | `verified` \| `unverified` \| `refuted` (`TheoremStatus`) |
| `compiled` / `sorry_free` | the two-part validation verdict |
| `theorems_checked` | declaration names Lean parsed from the source |
| `substance_warnings` | M3b proof-substance hints, **reused verbatim** from `lean_verify.substance_warnings` |
| `prerequisites` | names of library theorems this one depends on (graph edges) |
| `curriculum_level` | `CurriculumLevel` band: elementary → … → phd |
| `tactics` | Lean tactics the proof uses (parsed; graph metadata) |
| `citation` | source (CMU course / textbook / origin) |
| `verdict` | full `LeanVerdict.to_dict()` — the audit record |
| `added_at` / `verified_at` | ISO timestamps |

**Storage discipline mirrors `lab_knowledge` exactly** (no database): a
single append-only JSONL op-log `lab_theorems/store/theorems.jsonl`, folded to
current state on read (last-write-per-name wins), guarded by an
atomic-create lockfile (`.math_lib.lock`). Per-entry audit verdicts are
written to `lab_theorems/store/verdicts/<name>.verdict.json` in the SAME format
EXECUTE writes (`<stage>/lean/<Claim>.verdict.json`). The `.lean` sources
live in `lab_theorems/lean/` (tracked).

Location resolution (`resolve_root`, highest precedence first): explicit
`--root` → `$OMICAIRN_LAB_THEOREMS` → `<repo>/lab_theorems/store/` (the default
tracked location that ships the seeded library).

## 3. Knowledge graph

The library projects into a `omicairn.knowledge_graph.Graph` (`build_graph()`)
using the **same `Node`/`Edge`/`Graph` types** the run-level knowledge
graph uses — the math library is a first-class citizen of the lab graph
model, not a parallel invention. Each theorem is a node
(`type="theorem"`); each prerequisite is a `depends_on` edge (theorem →
prerequisite). Curriculum band + tactics + status ride along in
`Node.tags`/`Node.extra`.

Two graph utilities support curriculum learning and the agent loop:

- `prerequisites_closure(name)` — cycle-safe transitive prereq closure
  (deeper prereqs first), the lemma set a new proof can lean on.
- `check_curriculum_monotonic()` — invariant: a theorem may not depend on
  a prerequisite at a **strictly higher** curriculum band (and every
  prereq must exist). The seeded library passes this clean.

## 4. Validation pipeline (the load-bearing contract)

`validate_entry()` is where "validate, don't trust" lives. It:

1. calls `omicairn.lean_verify.verify_lean_source(source, workdir=<Lake proj>)`
   against the reusable Mathlib Lake project `.lean-mathlib-omicairn`
   (LEAN_CC=gcc + LIBRARY_PATH workaround on this host; see memory
   `omicairn-lean-mathlib-local-setup`);
2. records the **real** `LeanVerdict` (`compiled`, `theorems_checked`,
   `errors`, `returncode`, `substance_warnings`, …);
3. folds in a direct `sorry`/`admit` check (`has_sorry`) — a `sorry`
   compiles (Lean warns, not errors) but proves nothing, exactly the
   failure mode the library exists to prevent — so `sorry_free =
   compiled AND not has_sorry`;
4. sets `status`: `verified` iff sorry-free; `unverified` if it only
   compiled via a placeholder or the toolchain is missing; `refuted` if it
   failed to compile.

It **reuses** the existing M3b proof-substance machinery
(`lean_verify.substance_warnings`, the 3-layer trivial-pattern /
elementary / linear-rearrangement heuristics) rather than reinventing it —
the warnings are stored on the entry and surfaced by `omicairn labtheorems show`.
This is the same enforcement surface cross_check W18 reads, so a library
theorem and a run's theorem are judged for substance identically.

`add_theorem(..., require_verified=True)` (the default) refuses to write a
non-verified entry to the store — a small clean validated core beats a
large unvalidated list. `require_verified=False` records an honest
`unverified`/`refuted` placeholder when we want a known-hard theorem
tracked.

## 5. Virtual mathematician (the agent loop)

`omicairn.lab_theorems.VirtualMathematician.prove(name, statement, ...)`:

```
state a theorem
  → retrieve relevant prerequisites from the graph   (retrieve())
  → draft a Lean proof                                (pluggable Drafter)
  → compile via the local toolchain                  (lean_verify)
  → on failure: feed errors back, repair (bounded max_iters)
  → on clean sorry-free compile: CONTRIBUTE to the library
      (entry + depends_on edges to the retrieved prereqs)
```

- **Retrieval** prefers the explicit hint-prereq transitive closure, then
  fills with lexical overlap over statements/names, requiring a minimum of
  2 CONTENT tokens (stopwords excluded) so an unrelated lemma is not
  dragged in. Returns **verified** entries only — the mathematician builds
  on proven ground.
- **Drafter is injected.** The default `_template_drafter` is a
  retrieval/template baseline (no LLM) — enough to close the seeded
  inequality family offline and in CI. **The production hook is an
  LLM-backed drafter** (`claude_runner`) that receives the statement + the
  retrieved lemma sources + the previous compile errors and drafts/repairs
  Lean — i.e. exactly the existing `agents/math-formalization.md` loop,
  but seeded with retrieved library lemmas instead of cold-proving.
- **Contribution** is gated on a real sorry-free verdict; nothing is
  written on failure (the full attempt trail is returned for diagnosis).

## 6. OmiCairn integration point

The library plugs into the EXECUTE/theory path at **two** seams:

1. **Retrieval before cold-proving — BUILT.** Two pieces, both shipped:
   - **A CLI retrieval seam:** `omicairn labtheorems retrieve "<goal/keywords>"`
     returns the most relevant VERIFIED theorems WITH their compiled Lean
     source (`VirtualMathematician.retrieve` + `cmd_labtheorems`). An EXECUTE
     agent (which has Bash) calls this before cold-proving and imports the
     returned lemmas as scaffolding.
   - **A prompt render-token:** `{{lab_theorems}}`
     (`lab_theorems.render_availability_md` → `orchestrator._lab_theorems_summary`)
     lists the available verified theorems in the EXECUTE/theory prompt
     (`prompts/execute.md`) and the `math-formalization` agent (step 0), with
     the `retrieve` instruction, so a theory run knows the library exists and
     how to pull from it.
   The effect: fewer wasted repair iterations, and a run's Lean builds on a
   validated base rather than re-deriving AM-GM/Cauchy–Schwarz every run.
2. **Contribution after a run.** When a run finalizes a substantive,
   sorry-free theorem (the d5 witness, the d7 Cauchy–Schwarz ceiling), a
   post-run distill step (sibling of `lab_knowledge.distill_workspace`)
   contributes it to the library with its curriculum band + prereq edges,
   so the next theory run starts from a richer base. This closes the
   compounding loop.

Both seams reuse existing machinery: the same `lean_verify` verdict, the
same M3b substance gate, the same Lake project, the same knowledge-graph
Node/Edge types. The library adds the **store + retrieval + curriculum**
layer on top; it does not fork the proof-checking or substance-judgment
contracts.

## 6a. Retrieval mechanism: CLI seam vs. MCP server (decision + rationale)

The EXECUTE stage needs to GET knowledge from the library. Two candidate
mechanisms were considered:

| | **CLI-retrieval seam (CHOSEN, built)** | local-stdio **MCP server** (deferred) |
|---|---|---|
| How EXECUTE calls it | `omicairn labtheorems retrieve` via the Bash tool it already has | an MCP tool the agent harness must register + start |
| Headless / cron robustness | **robust** — a plain subprocess, always present | **fragile** — interactively-authed/registered MCP servers can be ABSENT in cron/headless runs (observed; see memory `omicairn-continuous-loop-cron` and the MCP-absence lesson) |
| New runtime dependency | none (reuses the existing `omicairn` CLI) | an MCP server process + protocol surface |
| Surface to maintain | one CLI subcommand | a server, tool schema, lifecycle, transport |
| Read-only local lookup fit | exact (this IS a local read) | heavier than needed |
| Composability with other agents | any tool with shell access | only MCP-aware agents |

**Decision: implement the CLI-retrieval seam + render-token NOW; DESIGN (don't
build) the MCP server as an optional future enhancement.** For a *local,
read-only* lookup that must work in the unattended cron/headless loop that
drives OmiCairn, the CLI seam is strictly lower-friction and strictly more robust:
it adds no runtime dependency and cannot silently vanish in a headless run the
way an interactively-registered MCP server can. The MCP server would be a
nicer *abstraction* (typed tool, discoverable), but it buys nothing for a
single local read and adds the exact failure mode (headless absence) the
continuous loop is most exposed to. If, later, the library is exposed to
EXTERNAL agents or needs typed multi-tool access (retrieve + contribute +
develop as first-class tools), an MCP wrapper over the same `lab_theorems`
functions becomes worthwhile — it would wrap the identical `retrieve` /
`add_theorem` / `prove` calls the CLI already exposes, so it is purely
additive. Until then, the CLI seam is the right call.

## 7. CLI surface

```
omicairn labtheorems list [--level <band>] [--status verified|unverified|refuted]
omicairn labtheorems search "<text>"
omicairn labtheorems retrieve "<goal/keywords>"   # EXECUTE-stage retrieval seam:
                                              # verified theorems + compiled Lean
omicairn labtheorems stats
omicairn labtheorems graph        # prerequisite DAG edges
omicairn labtheorems check        # curriculum-monotonicity invariant
omicairn labtheorems index        # markdown index grouped by band
omicairn labtheorems show <name>  # full record + verdict + Lean source
```

## 8. Roadmap to the CMU-grad curriculum

The slice seeds elementary→graduate. Scaling to the full
curriculum-learning order (elementary → junior-high → … → PhD):

1. **Curriculum expansion.** Seed each band from genuine CMU-grad
   prerequisite courses (21-127 Concepts, 21-228 Discrete, 21-241/242
   Linear Algebra, 21-355/356 Real Analysis, 21-373 Algebraic Structures,
   21-602 Set Theory, 21-720 Measure/Integration, 21-700 Logic, …). Enter
   each theorem ONLY after its Lean proof compiles sorry-free; record
   honest `unverified` for ones that don't yet.
2. **Mathlib alignment harvest.** Many CMU-grad theorems already exist in
   Mathlib; the library entry can be a thin Mathlib-citing wrapper
   (`theorem X := <mathlib_lemma>`) validated the same way — fast to seed,
   and the prereq graph captures the curriculum structure Mathlib does not
   surface pedagogically.
3. **LLM drafter for the virtual mathematician.** Wire `claude_runner` as
   the production `Drafter` (statement + retrieved lemmas + repair errors
   → Lean), with the existing `agents/math-formalization.md` substance
   directives in the system prompt so contributed theorems clear M3b, not
   just `compiled`.
4. **Automated curriculum scheduler.** A `omicairn labtheorems develop <band>`
   loop that picks the next un-proven theorem whose prereqs are all
   verified (topological frontier), dispatches the virtual mathematician,
   and contributes on success — autonomous library growth, band by band.
5. **Substance-gated contribution.** Refuse to contribute a theorem whose
   `substance_warnings` flag it as proof-theater (reuse the W18/M3b gate),
   so the library accumulates real theorems, not tautologies.
6. **Cross-run distill.** A `distill_theorems(ws_root)` that harvests every
   finalized run's sorry-free Lean into the library (seam #2 above),
   making each run a contribution to the lab's math base.

## 9. What is a working slice vs a stub

**Working (validated end-to-end):** the store + op-log + graph + curriculum
invariant + retrieval + the validation pipeline (real Lean compile) + the
virtual-mathematician loop with the offline template drafter + the CLI +
the **55-theorem** seeded library (all compiled sorry-free against local
Mathlib, 100% pass rate; elementary→graduate across 7 domains — inequalities,
linear algebra, real analysis, probability, number theory, group/ring,
combinatorics — see `lab_theorems/sources.md`) + the demo proving a NEW theorem
and contributing it + **the EXECUTE-stage retrieval seam** (the `retrieve` CLI
action, the `{{lab_theorems}}` prompt token, and the `math-formalization`
agent step 0).

**Stub / hook (designed, not yet wired):** the LLM-backed drafter (the
default drafter is the offline baseline); the post-run contribution distill
(§6.2); the curriculum scheduler (§8.4); the optional MCP server (§6a —
deliberately deferred in favor of the headless-robust CLI seam).
