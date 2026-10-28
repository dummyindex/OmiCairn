# OmiCairn method-depth + math-rigor rules

These are standing rules for every OmiCairn run, parallel to
`rules/analysis_rigor.md`. They encode two GENERAL research expectations
the owner cares about — they are not specific to one phase, run, or
domain:

1. **Method/model depth** — a case declared `contribution_type: method`
   must CONTRIBUTE a method that beats real baselines, not merely FIND and
   MEASURE a phenomenon.
2. **Math rigor via Lean** — any mathematical claim stated as PROVEN must
   be formalized in Lean 4 and the proof must check.

SCOPE/PLAN/EXECUTE point the agent here when the contribution directive is
`method` or `theory`; REVIEW scrutinises against these rules.

## M1 — Contribution type is declared and enforced

Each case declares `contribution_type` in `omicairn_config.yaml` (default
`analysis`). The orchestrator renders a directive block into SCOPE/PLAN/
EXECUTE (`{{contribution_directive}}`). The three types:

- **`analysis` (DEFAULT)** — a discovery / measurement / characterisation
  study. A clean analysis with ≥1 adversarial baseline, honest CIs, and a
  falsifiable finding is a SUFFICIENT contribution. Do not manufacture a
  method to look deeper than the science supports.
- **`method`** — a method/model/operator is the deliverable. See M2.
- **`theory`** — proven mathematical result(s) are the deliverable. See M3
  and the Lean protocol (M4–M6).

## M2 — What a `method` contribution requires

A run that only finds and measures a phenomenon is INSUFFICIENT for a
`method` case. The run MUST deliver, end to end:

- **A concrete, named method/model/operator** that is itself the
  contribution (a new operator, architecture, training objective,
  estimator, algorithm — not an analysis of an existing one).
- **≥2 REAL competing-method baselines** it beats. A *real baseline* is an
  alternative WAY TO SOLVE THE SAME PROBLEM that a reviewer would expect
  you to compare against — e.g. fine-tuning, prompting, a published method,
  or a strong heuristic. An *adversarial/sanity control* (random direction,
  shuffled labels, no-op) confirms a measurement is real; it is NOT a real
  baseline and does NOT establish a method contribution. A method case
  needs BOTH: controls for validity AND real baselines for contribution.
- **A paired Δ(method, baseline) per baseline** on the pre-registered
  primary metric, with a 95% CI and directional p-value, computed with the
  SAME resample indices for method and baseline. One
  `delta_primary_vs_<baseline>` block per baseline in `results.json`.
- **≥1 ablation** that isolates the component the method claims to
  contribute (turn it off / replace it with the trivial version; show the
  gain attributes to it). Without an ablation, "the method works because of
  X" is unsupported.

The d7 compositional-steering run is the canonical FAILURE mode: it found
a steering direction and measured a +7.6-point gain, with random/shuffled/
difficulty CONTROLS — but proposed no operator and beat no fine-tuning or
prompting baseline. As an `analysis` case that is excellent; as a `method`
case it would be insufficient and the analysis REVIEW routes it back
(retry_execute / back_to_plan) as a blocking gap.

## M3 — What a `theory` contribution requires

The deliverable is one or more PROVEN mathematical results (a derived
formula, a bound, a theorem, an identity, a closed-form). Empirical
validation, if present, is secondary. Every result stated as established
must satisfy the Lean protocol below (M4–M6).

## M3b — Proof SUBSTANCE: a compiling proof is not yet a deep one

A Lean proof that CHECKS establishes only that the stated theorem is true —
it does NOT establish that the theorem is SUBSTANTIVE. The Lean protocol
(M4–M6) guards the *truth* of a claim; this rule guards its *depth*. They
are independent gates: a tautology compiles just as cleanly as a separation
theorem. M4 keeps the run honest; M3b keeps it from being **proof theater**
— a green Lean check dressing up a result a reviewer can see is true by
inspection of its statement.

**The criterion.** A Lean theorem counts as genuine theoretical depth ONLY
if it DERIVES a non-obvious consequence. It does NOT count as depth if it
merely (a) restates the empirical finding as a proof *premise* and then
applies arithmetic monotonicity (`Nat.mul_le_mul_left`, `le_trans`,
one-step `simp`/rewriting) to reach the conclusion, or (b) proves a
definitional tautology. The test:

> **If a competent reviewer could see the result is true by inspection of
> the statement, the Lean adds rigor-theater, not depth.** A SUBSTANTIVE
> proof does at least one of: (i) constructs a non-obvious
> witness/counterexample (an explicit object that establishes a regime is
> non-empty or a property is achievable), (ii) derives a consequence whose
> truth is NOT visible from the premises, or (iii) establishes a
> bound/impossibility that CONSTRAINS the empirical claim (rules out a
> regime the data would otherwise be free to occupy).

**The DEPTH layer: substantive-by-construction is not yet non-obvious
(elementary-theorem proof-theater).** There is a SECOND failure mode above
the assume-then-monotonicity one. A theorem can be substantive by the
criterion above — it really IS a construction or a bound, with free
variables, not an assumed premise — and STILL be ELEMENTARY: a
textbook/constant-table result a competent reviewer recognises immediately.
The classic instance is **XOR is not linearly separable**: a 4-point parity
table on which every linear functional is at chance yet a product feature
separates — a true *constructive separation* (so it passes (i) on paper),
but it is the canonical first-week example of the linear-separability
obstruction, proved by `ring`/`decide`/`norm_num` over a fixed constant
table. Constructing it derives nothing a reviewer would not see by
inspection. **Substantive-but-elementary is NOT a theory contribution**: a headline
theorem must be NON-OBVIOUS, not merely true-and-constructed. The test
sharpens to: *does the theorem GENERALIZE* (free variables; a
bound/consequence that holds for a CLASS; a witness whose existence is not
the textbook example) — or is it a CLOSED constant fact / a named textbook
example dressed in a constant table? A closed-constant-table fact closed by
a single algebraic-identity tactic, used as a *headline*, is elementary
proof-theater and must not earn full theory marks even though it compiles
and is technically "constructive."

The structural CONTRAST that keeps this from over-firing: a `decide` /
`native_decide` that VERIFIES a CONSTRUCTED witness the run's claim
depends on (the d5/d7 shape — the witness/bound IS the contribution, its
truth not visible from the statement) is NOT elementary; nor is a derived
bound over free variables (`omega`/`linarith`). Elementary proof-theater is
specifically the *closed algebraic identity over a tiny hand-written
constant table* (`ring`/`norm_num`/`rfl`) standing in as the headline.

### Worked example — d7 (trivial) vs d5 (substantive)

This contrast is the calibration anchor; apply it to any theory artifact.

- **d7 — TRIVIAL (the failure mode).** The headline theorem
  `plausibility_moves_at_least_binding` is, in full,
  `(alpha g_b g_p : Nat) (h : g_b ≤ g_p) : alpha * g_b ≤ alpha * g_p`,
  proved by `Nat.mul_le_mul_left alpha h` — i.e. "a ≤ b ⟹ α·a ≤ α·b",
  pure `Nat`-multiplication monotonicity. T2 (`∃ α, tol < α·m` from
  `m > 0`) is the same shape. These COMPILE, but the *entire* scientific
  content lives in the ASSUMED premise (`g_b ≤ g_p`, which is the
  empirical finding, measured — not derived), and the Lean adds nothing a
  reviewer could not see by reading the statement. The "theorem" is the
  empirical observation with an arithmetic hat on. This fails M3b and an
  expert rigor bar: it is a *formalized observation*, not a
  theorem.
- **d5 — SUBSTANTIVE (the bar).** `Metrics.lean` re-implements signed
  cosine²/dot/norm over exact `Rat`; `T1_T2_witness.lean` then CONSTRUCTS
  explicit img/txt/R matrices and proves CONSTRUCTIVE EXISTENCE of the
  "aligned clouds, misaligned axes" regime — a genuine separation result
  (the regime the empirical claim needs is provably non-empty, established
  by an exhibited witness, not assumed). The truth of this is NOT visible
  from the statement; you have to build the witness. This passes M3b.
- **fair-erasure-durability — SUBSTANTIVE-BUT-ELEMENTARY (the depth-layer
  failure mode, caught live).** The headline H4 theorem
  `linear_clean_not_nonlinear_clean` exhibits the XOR/parity 4-point witness
  (`p1..p4`) on which every linear functional has equal class means yet the
  product feature `g(x)=x₁x₂` separates with margin 1, all proved by
  `simp only [const-defs]; ring` / `norm_num` over the fixed table. It is
  *constructive* (so the assume-then-monotonicity heuristic and the M3b
  criterion (i) both pass it — `omicairn.lean_verify` stamped
  `substance_warnings: []`), but the construction is the textbook
  XOR-is-not-linearly-separable example: a reviewer sees it true by
  inspection. This is ELEMENTARY proof-theater, NOT a theory contribution — REVIEW
  recommends a non-obvious replacement or downgrading the headline. The
  separation between it and d5 is that d5's witness (Pythagorean-triple
  rational rotation forcing CKA=1 / kNN=1 yet small matched cosine, verified
  by `native_decide`) is NOT a named textbook object and its truth is not
  visible without building it.

### How M3b is enforced (judgment, not a syntactic gate)

"Trivial vs substantive" is not decidable by regex — you cannot grep for
"is this theorem deep." So M3b is enforced by JUDGMENT, in two places, and
is **advisory (a REVIEW judgment), not a hard pipeline block**:

1. **The analysis REVIEW judges substance, not presence**
   (`prompts/review.md`). If the headline Lean theorem merely assumes the
   empirical finding as a hypothesis and applies trivial arithmetic, the
   proof does not carry the contribution — REVIEW flags it as proof theater
   and recommends either a substantive theorem (witness/bound/impossibility)
   or honestly downgrading the "theorem" to a "formalized observation." A
   borderline-substantive proof does NOT halt the run; but a tautology must
   NOT count as a theory contribution. The SAME judgement applies to the
   ELEMENTARY case above: if the headline theorem is a closed/constant-table
   textbook result (the XOR-table shape) — even when it is technically
   constructive — REVIEW recommends a NON-OBVIOUS replacement (a
   witness/bound that generalizes or whose truth is not visible from the
   statement) or downgrading the headline.
2. **The formalization agent targets substance up front**
   (`agents/math-formalization.md`): it is instructed to aim at a
   witness/separation/bound/impossibility and to AVOID the
   assume-the-finding + arithmetic-monotonicity pattern from the start.

Two optional WARN-only heuristics in `lean_verify` (unified under
`lean_verify.substance_warnings`, surfaced in
`LeanVerdict.substance_warnings`) flag *syntactic shadows* — both are hints,
never verdicts; the real judgment is the analysis REVIEW above:
- `trivial_pattern_warnings` — the assume-the-finding + one-library-lemma
  monotonicity shadow (a relational-hypothesis premise matching the
  empirical claim, discharged by a single monotonicity/transitivity lemma).
- `elementary_theorem_warnings` — the DEPTH layer: every proof in the source
  closes by a closed-form algebraic-identity tactic (`ring`/`norm_num`/
  `rfl`) over what is structurally a small constant table, with NO
  constructed-witness check (`decide`/`native_decide`), derived bound
  (`omega`/`linarith`), induction, or named-lemma derivation. This is the
  shadow of elementary proof-theater (the XOR-table headline that slipped
  past the trivial-pattern gate). It is deliberately CONSERVATIVE — any
  witness-check or bound tactic disables it — so a legitimate d5/d7-style
  constructed-witness proof is never flagged; a false positive degrades to a
  reviewer prompt, never a block.

## M3c — Proof status: the honest THREE-state vocabulary (deferred proof)

A theory math claim need not be binary (proven-in-Lean vs conjecture). That
binary forces a DERIVED + EMPIRICALLY-VALIDATED result whose Lean proof is
merely deferred into the dishonest extreme "conjecture", understating real
work. OmiCairn recognises THREE `proof_status` states (record one per claim in
`<stage>/proof_obligations.json`; constants in `omicairn.constants.ProofStatus`):

- **`proven_lean`** — the claim's Lean 4 proof CHECKS (M4/M5: `compiled:
  true`, sorry-free) AND is SUBSTANTIVE (M3b). This is the ONLY status that
  may be reported as "proven / theorem (Lean-verified) / machine-checked".
- **`derived_validated_proof_pending`** — the claim is BOTH (a) formally
  DERIVED (a written derivation / proof sketch exists) AND (b) empirically
  VALIDATED (its prediction holds on data with CIs), but its machine-checked
  Lean proof is a PARALLEL obligation not yet closed. A theory case MAY
  finish carrying such a claim PROVIDED it is **labeled proof-pending in
  results.json / analysis.md** — "derived ... and validated empirically
  (CI ...); a machine-checked proof is in progress" — NEVER reported as
  "proven". The Lean proof is closed LATER by a `omicairn steer` deepening that
  adds the passing `.lean` and re-labels the claim `proven_lean` (no new
  pipeline stage — the deferred proof reuses the existing steering machinery).
- **`conjecture`** — neither proven nor (derived + empirically validated): a
  bare derivation with no empirical backing, or an unvalidated guess. Stated
  only as a labeled conjecture / empirical observation.

**This is MORE honest states, NOT a loophole.** A `derived_validated_proof_
pending` claim reported as proven is an OVER-CLAIM that REVIEW must route
back for correction (it reads `proof_obligations.json`). Only a passing
Lean verdict earns "proven". The empirical validation
strengthens a proof-pending claim but never substitutes for the eventual
Lean proof.

### Proof-obligation record (`proof_obligations.json`)

A JSON array; one entry per math claim, keys: `claim_id`, `statement`, `lean_file` (the attempted
`.lean`, or null), `lean_verdict_status` (`passing` | `failing` | `none`),
`empirical_validation_key` (the `results.json` key carrying the validation +
CI, or null), `proof_status` (one of the three above). EXECUTE emits it;
REVIEW reads it. Absent for a legacy / non-theory run (back-compat).

## M4 — Lean-verified math: the rule

When a OmiCairn run states a mathematical result as PROVEN/ESTABLISHED — a
derived formula, a bound, a theorem, an identity, a closed-form — it MUST
be formalized in Lean 4 and the proof MUST check (`lean`/`lake` exits clean
with no errors and no `sorry`) BEFORE the claim is reported as established.
This applies in ANY mode (an `analysis` or `method` case that derives a
bound is held to it too), not only `theory` cases. **Compiling is
necessary but not sufficient — the theorem must also be SUBSTANTIVE per
M3b; a tautology that checks is still proof theater.**

An unverified derivation is NOT stated as proven. Per M3c it carries one of
two honest labels: **`derived_validated_proof_pending`** when it is derived
AND empirically validated (Lean proof a parallel obligation — labeled
proof-pending in prose), else a labeled **"conjecture"** / **"empirical
observation"**. Phrases like "we prove", "it follows that", "the closed-form
is", "Lean-verified", or a stated bound asserted as established require a
passing Lean artifact (`proof_status: proven_lean`).

## M5 — Lean protocol (how to do it)

Use the `math-formalization` skill. For each claim:

1. Write the Lean 4 statement + proof to `<stage>/lean/<Claim>.lean`. Keep
   each claim in its own file; name the theorem meaningfully.
2. Run the verifier: `omicairn.lean_verify.verify_lean_file("<...>.lean")`. It
   returns a structured `LeanVerdict` (`compiled`, `theorems_checked`,
   `errors`, `timed_out`, `tool_missing`, `duration_s`).
3. Write the verdict to `<stage>/lean/<Claim>.verdict.json` (the skill does
   this). Both the `.lean` source and the verdict.json are AUDITABLE
   workspace artifacts — CROSS_CHECK and REVIEW read them.
4. Report the result as proven ONLY when `compiled: true`. Otherwise label it
   a conjecture/empirical observation and (if a `theory` case) treat the
   unproven claim as a stage failure to iterate on.

Keep proofs **Mathlib-free** when possible. The genuinely Mathlib-free
(Lean 4 *core*) tactics are `rfl`, `decide`, `omega` (linear arithmetic
over `Nat`/`Int`), and `simp` (with core lemmas only), plus literal
`Nat`/`Int`/`Rat` arithmetic dischargeable by `decide`/`rfl` and the core
`Nat.*`/`Int.*` lemmas (`Nat.mul_comm`, `Nat.left_distrib`, …). These cover
a great deal (arithmetic identities, simple algebraic rearrangements,
finite/linear bounds) and check in well under a second with NO project
setup. Use the bundled Mathlib-free Lake scaffold (`LeanScaffold/` at the
OmiCairn package root) as a home for `.lean` sources when you want a project
context.

> **`ring`, `linarith`, and `norm_num` are NOT core — they REQUIRE
> Mathlib.** This was empirically confirmed (Lean v4.30.0, in the
> `LeanScaffold` Lake env): `rfl`/`decide`/`omega`/`simp` compile cleanly
> with no import, while `ring`, `linarith`, and `norm_num` each fail with
> `error: unknown tactic` (the tactic is not in scope without
> `import Mathlib`). Because the default `LeanScaffold` has NO Mathlib
> dependency, a proof that reaches for `ring`/`linarith`/`norm_num` will
> FAIL to compile in the scaffold. Do NOT use them in a Mathlib-free proof.
> If a claim genuinely needs one of these, the proof needs Mathlib — see M6
> (add a Mathlib-gated Lake target / `lake exe cache get` once, OR downgrade
> the specific claim to a labeled conjecture). Do not "pretend `ring` is
> core."

## M6 — When Lean / Mathlib is unavailable

- If the Lean toolchain is absent on the host, `verify_lean_file` returns
  `tool_missing: true` (it does not crash). In that case you CANNOT state
  any derivation as proven — every math claim is a labeled conjecture until
  Lean is installed and the proof checks. Note the missing toolchain in
  `analysis.md` § Limitations. Lean install is a one-time local step
  (elan); proof-checking is CPU-only and runs in the orchestrator
  environment, never on a shared GPU host.
- If a proof genuinely needs Mathlib (advanced analysis/probability
  lemmas), it requires a one-time `lake exe cache get` in the project. If
  that is not available in budget, downgrade the specific claim to a
  conjecture and record what Mathlib lemma the full proof would need.
