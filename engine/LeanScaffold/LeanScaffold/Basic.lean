/-
  LeanScaffold.Basic — worked examples of the kind of Mathlib-free
  mathematical claim a OmiCairn run can verify with CORE tactics only. These
  demonstrate, by construction, that the verifier checks real proofs in a
  Lake-project context. A OmiCairn run adds its own claim modules alongside
  this one (one theorem per claim) per the math-formalization skill.

  Tactics / lemmas used here are all Lean 4 CORE (NO Mathlib): `rfl`,
  `decide`, `omega` (linear arithmetic over Nat/Int), and core `Nat.*`
  lemmas (`Nat.mul_comm`, `Nat.left_distrib`, `Nat.mul_zero`). Mathlib
  tactics like `ring` / `nlinarith` are deliberately NOT used — they
  require a Mathlib build (`lake exe cache get`), see rules/method_depth.md
  M6. Core tactics cover arithmetic identities, distributivity, and finite
  / linear bounds, which is a large fraction of the closed-forms and bounds
  a run's analysis states.
-/

namespace LeanScaffold

/-- A trivial arithmetic identity (the canonical sanity proof). -/
theorem two_plus_two : 2 + 2 = 4 := by rfl

/-- A linear-arithmetic fact discharged by `omega`. -/
theorem succ_pos (n : Nat) : 0 < n + 1 := by omega

/-- Commutativity of multiplication via the core lemma (the shape of an
    algebraic-identity claim). -/
theorem mul_comm_nat (a b : Nat) : a * b = b * a := Nat.mul_comm a b

/-- Left-distributivity via the core lemma (a closed-form rearrangement). -/
theorem left_distrib_nat (a b c : Nat) : a * (b + c) = a * b + a * c :=
  Nat.left_distrib a b c

/-- A finite/decidable arithmetic fact, discharged by `decide`. -/
theorem mod_seven_two : (7 : Nat) % 2 = 1 := by decide

/-- A non-negativity BOUND (the shape of a "the quantity is bounded below
    by 0" claim, e.g. a count/variance lower bound). For `Nat` this is
    immediate from `omega`. -/
theorem nat_sq_nonneg (a : Nat) : 0 ≤ a * a := by omega

end LeanScaffold
