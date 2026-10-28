# Math Paper Review + Formalization — Design

Status: **v1 shipped** (2026-06-23). New module `omicairn/paper_review.py`, CLI
`omicairn paperreview`, a paper-indexed folded-JSONL store under
`lab_theorems/paper_review/store/`, Lean sources under
`lab_theorems/paper_review/lean/`, and a seed set of landmark papers whose
core theorems are machine-verified (sorry-free, axiom-clean) against the
local Mathlib toolchain. This doc records the architecture, the
literature-management decision, and the roadmap.

> **Not manuscript review.** This subsystem formalizes the core theorems
> of landmark *published* mathematics papers in Lean. It does not review,
> draft or score manuscripts; OmiCairn has no manuscript pipeline.
>
> **Naming + scope.** The subsystem lives in a NEW namespace
> (`lab_theorems/paper_review/`) so it stays disjoint from the existing
> theorem store (`lab_theorems/store/`) which a sibling effort owns. It
> REUSES `omicairn/lean_verify.py` (compile + axiom/substance gate) and the
> `omicairn/lab_theorems.py` store/verdict pattern verbatim; it does NOT edit
> the existing seed scripts, theorem store, or lean files.

## 1. Motivation

The owner's vision: ingest landmark/founding math papers — from antiquity
(Euclid, Archimedes, the Pythagoreans) through to contemporary work — and
**machine-verify their core results**, building a verified bridge from the
math literature into our Lean theorem store. A "result in a famous paper"
is, for our purposes, only as trustworthy as a proof that **compiles**
sorry-free and axiom-clean on our local toolchain.

This is the literature analogue of `lab_theorems`: where `lab_theorems`
accumulates Lean-verified *lemmas* the lab can build on, `paper_review`
accumulates Lean-verified *claims attributed to source papers*, with
provenance edges `paper → claim → theorem` so a verified result always
points back to where it came from.

**Honest scope.** Formalizing all of math history is ASPIRATIONAL, not v1.
v1 is the working pipeline + a small seed set of LANDMARK, TRACTABLE results
proven end-to-end, plus an HONEST deferred list for the hard ones
(Gödel incompleteness, quadratic reciprocity, …) recorded as
`status=deferred` with a stated reason — never faked.

## 2. Literature-management decision (reuse vs build)

The owner explicitly left this open. I **evaluated reusing**
`omicairn/literature.py` + `omicairn/paper_library.py` + `omicairn/lab_knowledge.py`
and **recommend building a thin `paper_review`-specific index** instead,
while REUSING the lower-level proof machinery. Rationale:

- **`literature.py` is a live-fetch corpus engine.** Its center of gravity
  is online retrieval (PubMed / Semantic Scholar / CrossRef / Unpaywall),
  query generation from a research question, and PDF→markdown conversion —
  geared to the per-run RESEARCH pipeline. Our papers are *historical*
  (Euclid, Archimedes); there is no PDF to fetch and no live query. Wiring
  through it would force a `Paper` record that has no slot for the thing
  that matters here (per-claim Lean formalization status) and would drag in
  an HTTP/SSRF surface we don't need for a CPU+Lean offline workflow.
- **`paper_library.py` is a shared blob/manifest store** for fetched PDFs +
  their markdown, keyed by DOI/title with link-into-workspace plumbing.
  Again: no PDFs, and its manifest has no concept of a *claim with a
  verdict*.
- **What we genuinely need beyond them** is small and specific: a
  `MathPaper` with `claims[]`, each claim carrying
  `lean_statement / lean_proof / verdict / axioms / linked_theorem`, plus
  `paper→claim→theorem` graph edges. That is a *different data model*, not a
  filter over the existing one.

So: **build a thin index** (one new module, one new store), and **reuse the
parts that are already right** — `lean_verify.verify_lean_source` (compile),
`lean_verify.library_trivia_warnings` (the M3b substance gate), the
`lab_theorems` folded-JSONL op-log + atomic-lock pattern, and
`lab_theorems.add_theorem` to LINK a verified claim into the shared theorem
store. Net new third-party dependency footprint: **zero** (pure stdlib +
existing omicairn modules).

A `paper_review`→`paper_library` citation bridge is left as a forward hook:
if a *contemporary* paper that genuinely lives in `paper_library` ever needs
formalizing, `paper_review` can take its citation string — but it does not
depend on that module.

## 3. Architecture — the pipeline

```
ingest paper            (record: id, title, author, year/era, citation)
  → for each key claim  (informal statement + informal proof sketch)
     → formalize        (lean_statement: the Lean binder+goal)
     → port the proof   (lean_proof: full Lean source)
     → verify           lean_verify.verify_lean_source  (compile)
        + axiom-check    (#print axioms ⊆ {propext, Classical.choice, Quot.sound})
        + substance gate (library_trivia_warnings — no tautology theater)
     → VERDICT           verified | formalized_stmt_only | failed | deferred
     → record claim      (folded-JSONL op-log)
     → on verified:      LINK into lab_theorems store (add_theorem) +
                         paper→claim→theorem edge
```

The `Reviewer` loop (analogous to `lab_theorems.VirtualMathematician`)
drives ingest→formalize→verify→record for a paper. Verification is
**validate-don't-trust**: a claim becomes `verified` ONLY when Lean accepts
it sorry-free AND it is axiom-clean AND it clears the substance gate.

### Verdicts (the four-way distinction the owner asked for)

| verdict | meaning |
|---|---|
| `verified` | Lean statement formalized AND proof compiles sorry-free, axiom-clean, substance-gate pass |
| `formalized_stmt_only` | Lean statement compiles, but the proof is `sorry`/incomplete OR not axiom-clean — we have the *statement* but not a clean proof |
| `failed` | attempted, did not compile (recorded honestly with errors) |
| `deferred` | known-hard; not attempted in v1, recorded with a stated reason (e.g. Gödel incompleteness) |

## 4. Data model

`omicairn.paper_review.PaperClaim`:

| field | meaning |
|---|---|
| `claim_id` | stable id within the paper (`<paper_id>::<slug>`) |
| `paper_id` | the owning paper |
| `informal_statement` | the result as stated in the paper |
| `informal_proof` | one-line proof sketch / method |
| `lean_statement` | the Lean binder+goal (statement only) |
| `lean_proof` | the full Lean source (statement + proof) |
| `verdict` | one of the four above |
| `axioms` | the axiom set the proof depends on (post `#print axioms`) |
| `axiom_clean` | bool — axioms ⊆ the allowed kernel set |
| `linked_theorem` | the `lab_theorems` theorem name this verified claim was linked to |
| `lean_file` | tracked `.lean` path under `paper_review/lean/` |
| `substance_warnings`, `theorems_checked`, `lean_verdict` | the audit record |
| `defer_reason` | why a `deferred` claim is hard |

`omicairn.paper_review.MathPaper`:

| field | meaning |
|---|---|
| `paper_id` | store key (e.g. `euclid-elements`) |
| `title`, `author`, `year`, `era` | bibliographic + era band (antiquity…modern) |
| `citation` | source string |
| `claim_ids` | the claims belonging to this paper |

**Storage** = two folded-JSONL op-logs (`papers.jsonl`, `claims.jsonl`)
under `paper_review/store/`, folded on read (last-write-wins per key),
guarded by the same atomic-create lockfile pattern as `lab_theorems`.
**Knowledge-graph edges**: `paper --presents--> claim --formalized_as-->
theorem`, projected into the same `knowledge_graph.Graph` type.

## 5. Axiom-clean gate (the HARD BAR)

A claim marked `verified` must be genuinely axiom-clean. `paper_review`
appends `#print axioms <decl>` to the Lean source, compiles, and parses the
emitted dependency set; `verified` requires it ⊆ `{propext,
Classical.choice, Quot.sound}` (Lean's standard classical kernel). A proof
that compiles but depends on `sorryAx` or any other axiom is downgraded to
`formalized_stmt_only` (not `verified`). This is re-verified by the LEAN
toolchain on every `review`, never self-attested.

## 6. CLI

`omicairn paperreview <list|show|add|review|stats>` (mirrors `omicairn labtheorems`):

- `list [--era <band>] [--verdict <v>]` — papers + their claim verdicts
- `show <paper_id>` — one paper's full record + every claim + verdict
- `stats` — counts by era / verdict / axiom-clean
- `review <paper_id>` — re-run verification on a paper's recorded claims
- `add <paper_id>` — register a paper shell (claims added programmatically)

## 7. v1 seed set

See `lab_theorems/paper_review/seed_papers.py` and the owner-review note.
Spans eras (antiquity → modern), each core theorem verified axiom-clean or
honestly deferred. The deferred list (Gödel incompleteness, quadratic
reciprocity, …) is recorded, not faked.

## 8. Roadmap

1. LLM drafter (`claude -p`) for the formalize step, batched per-paper.
2. More eras / papers (medieval, Renaissance, 19th–20th c.), quality-gated.
3. Promote stable verified claims into the main `lab_theorems` curriculum
   as named, reusable lemmas (already wired via `linked_theorem`).
