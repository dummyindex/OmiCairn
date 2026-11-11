"""Math-paper review + formalization subsystem.

Reviews landmark/founding math papers (antiquity → modern) and uses the
local Lean/Mathlib toolchain to FORMALIZE + PROOF-CHECK their core
theorems, building a verified bridge from the math literature into the lab's
theorem store. The literature analogue of :mod:`omicairn.lab_theorems`: where
that module accumulates Lean-verified *lemmas*, this one accumulates
Lean-verified *claims attributed to source papers*, with provenance edges
``paper → claim → theorem``.

Design (see ``docs/design-docs/math-paper-review.md``):

- **Store** = two folded-JSONL op-logs (``papers.jsonl`` / ``claims.jsonl``)
  under ``lab_theorems/paper_review/store/``, folded on read (last write per
  key wins), guarded by an atomic-create lockfile — the exact discipline of
  :mod:`omicairn.lab_knowledge` / :mod:`omicairn.lab_theorems`.
- **Verification** REUSES :func:`omicairn.lean_verify.verify_lean_source`
  (compile) + :func:`omicairn.lean_verify.library_trivia_warnings` (the M3b
  substance gate). It does NOT reinvent proof checking.
- **The HARD BAR** for a ``verified`` claim: Lean accepts the proof
  sorry-free AND the proof is AXIOM-CLEAN (``#print axioms`` ⊆
  :data:`constants.ALLOWED_LEAN_AXIOMS`) AND it clears the substance gate.
  A compiled-but-not-axiom-clean (e.g. ``sorryAx``) proof is downgraded to
  ``formalized_stmt_only`` — honesty over coverage.
- **Linking**: a ``verified`` claim is linked into the shared
  :mod:`omicairn.lab_theorems` store via :func:`lab_theorems.add_theorem`, and a
  ``claim → theorem`` graph edge is recorded.

This is GENERAL research infrastructure — domain-agnostic, no hardcoded
field keywords (eras are time bands, not fields; the axiom set is a Lean
foundation, not a domain term).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import pathlib
import re
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

from .constants import (
    ALLOWED_LEAN_AXIOMS,
    CLAIM_EDGE_FORMALIZED_AS,
    CLAIM_NODE,
    ClaimVerdict,
    CurriculumLevel,
    ENV_PAPER_REVIEW,
    LAB_THEOREMS_AREA,
    PAPER_EDGE_PRESENTS,
    PAPER_ERA_ORDER,
    PAPER_NODE,
    PAPER_REVIEW_CLAIMS_FILE,
    PAPER_REVIEW_DIRNAME,
    PAPER_REVIEW_LEAN_SUBDIR,
    PAPER_REVIEW_LOCK,
    PAPER_REVIEW_PAPERS_FILE,
    PAPER_REVIEW_SUBAREA,
    PaperEra,
    THEOREM_NODE,
)
from . import lab_theorems
from . import lean_verify


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _slug(text: str) -> str:
    """Lowercase-hyphenated slug of a free-text fragment (claim id suffix)."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:48] or "claim"


# ---------------------------------------------------------------------------
# Location resolution (mirrors lab_theorems.resolve_root)
# ---------------------------------------------------------------------------

def _area_root() -> pathlib.Path:
    """The ``lab_theorems/`` area beside the repo (``.../engine/omicairn``
    → repo → ``lab_theorems``); paper-review data lives in a sub-area beneath
    it so it ships beside the theorem library it bridges into."""
    pkg = pathlib.Path(__file__).resolve().parent       # .../omicairn
    engine_dir = pkg.parent                                 # .../engine
    repo = engine_dir.parent                                # repo root
    return repo / LAB_THEOREMS_AREA / PAPER_REVIEW_SUBAREA


def resolve_root(explicit: str | pathlib.Path | None = None) -> pathlib.Path:
    """Resolve the paper-review STORE directory (where the op-logs live).

    Precedence: ``explicit`` (CLI ``--root``) → ``$OMICAIRN_PAPER_REVIEW`` →
    ``<repo>/lab_theorems/paper_review/store``.
    """
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()
    env = os.environ.get(ENV_PAPER_REVIEW, "").strip()
    if env:
        return pathlib.Path(env).expanduser().resolve()
    return (_area_root() / PAPER_REVIEW_DIRNAME).resolve()


def lean_dir(root: pathlib.Path | None = None) -> pathlib.Path:
    """Directory holding the ``.lean`` sources (sibling of the store)."""
    r = resolve_root() if root is None else pathlib.Path(root)
    return (r.parent / PAPER_REVIEW_LEAN_SUBDIR).resolve()


_MATHLIB_WORKDIR_ENV = "OMICAIRN_MATHLIB_PROJECT_ROOT"


def mathlib_workdir() -> Optional[pathlib.Path]:
    """Locate the reusable Mathlib Lake project (``.lean-mathlib-omicairn``) and
    return a per-subsystem scratch subdir INSIDE it (so Mathlib resolves), or
    ``None`` if no built project is found.

    The Mathlib checkout is a heavy, GITIGNORED dir that lives at the MAIN
    repo root — it is NOT copied into a git worktree. So we cannot assume it
    sits a fixed number of parents above the store. Resolution order:

      1. ``$OMICAIRN_MATHLIB_PROJECT_ROOT`` — an explicit project dir override.
      2. a ``.lean-mathlib-omicairn`` (with a ``lakefile.toml``) found by walking
         up from the store root AND from the omicairn package — covering both the
         in-repo and the worktree case (where it resolves to the main checkout
         via the package's real path).

    Returns the project's ``omicairn_paper_review`` subdir (created on demand) so
    the verifier writes its temp ``.lean`` beside Mathlib. A ``None`` return
    means "no toolchain project" — the caller's verdict will be honest
    (``failed``/``formalized_stmt_only``), never a fabricated ``verified``.
    """
    env = os.environ.get(_MATHLIB_WORKDIR_ENV, "").strip()
    if env:
        proj = pathlib.Path(env).expanduser().resolve()
        if (proj / "lakefile.toml").exists():
            sub = proj / "omicairn_paper_review"
            sub.mkdir(parents=True, exist_ok=True)
            return sub
    # Search upward from the store root and from the package's real location.
    seeds = [resolve_root(), pathlib.Path(__file__).resolve().parent]
    seen: set[pathlib.Path] = set()
    for seed in seeds:
        for d in (seed, *seed.parents):
            if d in seen:
                continue
            seen.add(d)
            cand = d / ".lean-mathlib-omicairn"
            if (cand / "lakefile.toml").exists():
                sub = cand / "omicairn_paper_review"
                sub.mkdir(parents=True, exist_ok=True)
                return sub
    return None


def _papers_path(root: pathlib.Path | None = None) -> pathlib.Path:
    return (resolve_root() if root is None else pathlib.Path(root)) / PAPER_REVIEW_PAPERS_FILE


def _claims_path(root: pathlib.Path | None = None) -> pathlib.Path:
    return (resolve_root() if root is None else pathlib.Path(root)) / PAPER_REVIEW_CLAIMS_FILE


@contextmanager
def _locked(root: pathlib.Path, timeout: float = 10.0) -> Iterator[None]:
    """Atomic-create lockfile around a read-append cycle (best-effort)."""
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / PAPER_REVIEW_LOCK
    start = time.monotonic()
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() - start > timeout:
                fd = -1
                break
            time.sleep(0.05)
    if fd is not None and fd >= 0:
        try:
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        except OSError:
            pass
    try:
        yield
    finally:
        if fd is not None and fd >= 0:
            os.close(fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class PaperClaim:
    """One reviewable claim of a source paper + its formalization verdict."""
    claim_id: str
    paper_id: str
    informal_statement: str = ""
    informal_proof: str = ""
    lean_statement: str = ""
    lean_proof: str = ""
    verdict: str = ClaimVerdict.DEFERRED
    axioms: list[str] = dataclasses.field(default_factory=list)
    axiom_clean: bool = False
    linked_theorem: str = ""
    lean_file: str = ""
    substance_warnings: list[str] = dataclasses.field(default_factory=list)
    theorems_checked: list[str] = dataclasses.field(default_factory=list)
    lean_verdict: dict[str, Any] = dataclasses.field(default_factory=dict)
    defer_reason: str = ""
    curriculum_level: str = CurriculumLevel.UNDERGRAD
    added_at: str = ""
    verified_at: str = ""

    @property
    def key(self) -> str:
        return self.claim_id

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PaperClaim":
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in fields})


@dataclasses.dataclass
class MathPaper:
    """A landmark source paper under review (the bibliographic shell)."""
    paper_id: str
    title: str = ""
    author: str = ""
    year: str = ""
    era: str = PaperEra.MODERN
    citation: str = ""
    claim_ids: list[str] = dataclasses.field(default_factory=list)
    added_at: str = ""

    @property
    def key(self) -> str:
        return self.paper_id

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MathPaper":
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in fields})


# ---------------------------------------------------------------------------
# Store I/O (two append-only op-logs, folded on read)
# ---------------------------------------------------------------------------

def load_papers(root: pathlib.Path | None = None) -> dict[str, MathPaper]:
    path = _papers_path(root)
    out: dict[str, MathPaper] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("paper_id"):
            out[d["paper_id"]] = MathPaper.from_dict(d)
    return out


def load_claims(root: pathlib.Path | None = None) -> dict[str, PaperClaim]:
    path = _claims_path(root)
    out: dict[str, PaperClaim] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("claim_id"):
            out[d["claim_id"]] = PaperClaim.from_dict(d)
    return out


def claims_for(paper_id: str, root: pathlib.Path | None = None) -> list[PaperClaim]:
    """All claims belonging to a paper, in stored order (folded)."""
    return [c for c in load_claims(root).values() if c.paper_id == paper_id]


def get_paper(paper_id: str, root: pathlib.Path | None = None) -> Optional[MathPaper]:
    return load_papers(root).get(paper_id)


def get_claim(claim_id: str, root: pathlib.Path | None = None) -> Optional[PaperClaim]:
    return load_claims(root).get(claim_id)


def put_paper(paper: MathPaper, root: pathlib.Path | None = None) -> MathPaper:
    r = resolve_root() if root is None else pathlib.Path(root)
    if not paper.added_at:
        paper.added_at = _now()
    with _locked(r):
        path = _papers_path(r)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(paper.to_dict(), ensure_ascii=False) + "\n")
    return paper


def put_claim(claim: PaperClaim, root: pathlib.Path | None = None) -> PaperClaim:
    r = resolve_root() if root is None else pathlib.Path(root)
    if not claim.added_at:
        claim.added_at = _now()
    with _locked(r):
        path = _claims_path(r)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(claim.to_dict(), ensure_ascii=False) + "\n")
    return claim


def add_paper(
    paper_id: str, *, title: str = "", author: str = "", year: str = "",
    era: str = PaperEra.MODERN, citation: str = "",
    root: pathlib.Path | None = None,
) -> MathPaper:
    """Register a paper shell (idempotent re-add merges claim_ids via fold)."""
    existing = get_paper(paper_id, root=root)
    claim_ids = existing.claim_ids if existing else []
    paper = MathPaper(
        paper_id=paper_id, title=title, author=author, year=year, era=era,
        citation=citation, claim_ids=list(claim_ids),
    )
    return put_paper(paper, root=root)


# ---------------------------------------------------------------------------
# Axiom-clean check — the HARD BAR (re-verified by the Lean toolchain)
# ---------------------------------------------------------------------------

_AXIOM_LINE_RE = re.compile(
    r"depends on axioms:\s*\[([^\]]*)\]", re.MULTILINE
)
_NO_AXIOM_RE = re.compile(
    r"(?:does not depend on any axioms|trusts (?:no|0) axioms)", re.IGNORECASE
)


def parse_axioms(lean_output: str) -> list[str]:
    """Parse the axiom set from a ``#print axioms`` Lean output.

    PURE — fixture-testable without Lean. Recognises both the
    ``'decl' depends on axioms: [a, b, c]`` line and the
    ``does not depend on any axioms`` form (returns ``[]`` for the latter).
    """
    out = lean_output or ""
    if _NO_AXIOM_RE.search(out):
        return []
    axs: list[str] = []
    seen: set[str] = set()
    for m in _AXIOM_LINE_RE.finditer(out):
        for tok in m.group(1).split(","):
            tok = tok.strip()
            if tok and tok not in seen:
                seen.add(tok)
                axs.append(tok)
    return axs


def axioms_clean(axioms: list[str]) -> bool:
    """True iff every axiom is in the allowed classical-kernel set (an EMPTY
    set — a fully axiom-free proof — is trivially clean)."""
    return all(a in ALLOWED_LEAN_AXIOMS for a in axioms)


def _decl_name(lean_source: str) -> Optional[str]:
    """The first theorem/lemma/def name in the source (the decl to axiom-check)."""
    names = lean_verify.parse_theorem_names(lean_source)
    return names[0] if names else None


# ---------------------------------------------------------------------------
# Formalize + verify one claim — the load-bearing contract
# ---------------------------------------------------------------------------

def review_claim(
    claim: PaperClaim,
    *,
    workdir: str | pathlib.Path | None = None,
    root: pathlib.Path | None = None,
    timeout_s: int | None = None,
    link_theorem: bool = True,
    lab_root: pathlib.Path | None = None,
) -> PaperClaim:
    """Compile + axiom-check + substance-gate a claim's Lean proof and assign
    its VERDICT, REUSING :mod:`omicairn.lean_verify`. Does NOT mutate the store —
    the caller decides whether to :func:`put_claim`.

    A ``deferred`` claim (no ``lean_proof``) is returned untouched (honest
    placeholder). Otherwise:

    * proof does not compile → ``failed``;
    * compiles sorry-free, axiom-clean, substance-gate pass → ``verified``
      (and, when ``link_theorem``, linked into the lab_theorems store);
    * compiles but NOT axiom-clean (e.g. ``sorryAx``), or a sorry hole, or
      a substance-gate trip → ``formalized_stmt_only`` (we have the statement,
      not a clean proof).
    """
    if claim.verdict == ClaimVerdict.DEFERRED and not claim.lean_proof:
        return claim
    source = claim.lean_proof
    if not source:
        # Statement-only (no proof body offered): try to compile the
        # statement; success → formalized_stmt_only, else failed.
        source = claim.lean_statement
        verdict = lean_verify.verify_lean_source(
            source, workdir=workdir, name=claim.claim_id.replace("::", "_"),
            timeout_s=timeout_s,
        )
        claim.lean_verdict = verdict.to_dict()
        claim.theorems_checked = verdict.theorems_checked
        claim.verdict = (ClaimVerdict.FORMALIZED_STMT_ONLY if verdict.compiled
                         else ClaimVerdict.FAILED)
        return claim

    decl = _decl_name(source)
    # Append a `#print axioms` for the FIRST declaration so the verdict carries
    # the axiom dependency set the HARD BAR checks. (`#print axioms` emits a
    # message, not an error — it never affects `compiled`.)
    check_source = source
    if decl is not None:
        check_source = f"{source.rstrip()}\n#print axioms {decl}\n"

    verdict = lean_verify.verify_lean_source(
        check_source, workdir=workdir,
        name=claim.claim_id.replace("::", "_"), timeout_s=timeout_s,
    )
    claim.lean_verdict = verdict.to_dict()
    claim.theorems_checked = verdict.theorems_checked
    sorry_present = lab_theorems.has_sorry(source)
    trivia = lean_verify.library_trivia_warnings(source)
    claim.substance_warnings = list(dict.fromkeys(
        [*verdict.substance_warnings, *trivia]))
    claim.axioms = parse_axioms(verdict.stdout)
    claim.axiom_clean = axioms_clean(claim.axioms)

    if verdict.tool_missing:
        # Toolchain absent — cannot validate; keep honest (statement-only if a
        # statement exists, else failed). Never fabricate `verified`.
        claim.verdict = (ClaimVerdict.FORMALIZED_STMT_ONLY
                         if claim.lean_statement else ClaimVerdict.FAILED)
        return claim
    if not verdict.compiled:
        claim.verdict = ClaimVerdict.FAILED
        return claim
    if sorry_present or not claim.axiom_clean or trivia:
        # Compiles, but not a CLEAN proof: a sorry hole, a non-kernel axiom
        # dependency, or content-free tautology theater → statement only.
        claim.verdict = ClaimVerdict.FORMALIZED_STMT_ONLY
        return claim

    # The HARD BAR cleared: sorry-free + axiom-clean + substance pass.
    claim.verdict = ClaimVerdict.VERIFIED
    claim.verified_at = _now()
    if link_theorem and decl is not None:
        try:
            entry = lab_theorems.add_theorem(
                decl, claim.informal_statement or claim.lean_statement, source,
                curriculum_level=claim.curriculum_level,
                citation=f"paper:{claim.paper_id}",
                workdir=workdir, root=lab_root, require_verified=True,
                timeout_s=timeout_s,
            )
            if entry.status == lab_theorems.TheoremStatus.VERIFIED:
                claim.linked_theorem = decl
        except Exception:
            # Linking is best-effort; a link failure never un-verifies the
            # claim (its OWN verdict is independently established above).
            pass
    return claim


# ---------------------------------------------------------------------------
# The Reviewer loop — analogous to lab_theorems.VirtualMathematician
# ---------------------------------------------------------------------------

# A Drafter takes (paper, informal_statement, informal_proof) and returns a
# (lean_statement, lean_proof) pair. The default is a no-op manual drafter
# (claims arrive pre-formalized from the seed); an LLM drafter (claude -p) is
# the production hook — pluggable so the loop is testable without tokens.
Drafter = Callable[["MathPaper", str, str], tuple[str, str]]


@dataclasses.dataclass
class ReviewResult:
    """Outcome of reviewing one paper."""
    paper_id: str
    claims: list[PaperClaim] = dataclasses.field(default_factory=list)
    verified: int = 0
    formalized_stmt_only: int = 0
    failed: int = 0
    deferred: int = 0
    note: str = ""


class Reviewer:
    """Drives ingest → formalize → verify → record for a paper's claims.

    The drafter is injected: the default :meth:`_manual_drafter` passes a
    claim's pre-supplied ``lean_statement`` / ``lean_proof`` through unchanged
    (the seed-paper path); production passes an LLM-backed drafter that turns
    an informal statement into Lean.
    """

    def __init__(
        self,
        drafter: Drafter | None = None,
        *,
        workdir: str | pathlib.Path | None = None,
        root: pathlib.Path | None = None,
        lab_root: pathlib.Path | None = None,
        timeout_s: int | None = None,
        link_theorems: bool = True,
    ) -> None:
        self.drafter = drafter or self._manual_drafter
        self.workdir = workdir
        self.root = root
        self.lab_root = lab_root
        self.timeout_s = timeout_s
        self.link_theorems = link_theorems

    @staticmethod
    def _manual_drafter(paper: "MathPaper", informal_stmt: str,
                        informal_proof: str) -> tuple[str, str]:
        """No-op drafter: claims carry their own pre-formalized Lean (seed
        path). Returns empty strings so :meth:`review_paper` keeps whatever the
        claim already has."""
        return "", ""

    def review_paper(
        self, paper_id: str, claims: list[PaperClaim] | None = None,
        *, persist: bool = True,
    ) -> ReviewResult:
        """Review all of a paper's claims (or a supplied list), recording each.

        For each claim: if it has no Lean yet, ask the drafter; then
        :func:`review_claim` to compile/axiom-check/substance-gate and assign
        the verdict; then (when ``persist``) :func:`put_claim` it and register
        it on the paper's ``claim_ids``.
        """
        result = ReviewResult(paper_id=paper_id)
        the_claims = claims if claims is not None else claims_for(
            paper_id, root=self.root)
        paper = get_paper(paper_id, root=self.root) or MathPaper(paper_id=paper_id)
        recorded_ids: list[str] = list(paper.claim_ids)
        for claim in the_claims:
            if not claim.lean_proof and not claim.lean_statement:
                lst, lpf = self.drafter(
                    paper, claim.informal_statement, claim.informal_proof)
                claim.lean_statement = claim.lean_statement or lst
                claim.lean_proof = claim.lean_proof or lpf
            claim = review_claim(
                claim, workdir=self.workdir, root=self.root,
                timeout_s=self.timeout_s, link_theorem=self.link_theorems,
                lab_root=self.lab_root,
            )
            if persist:
                put_claim(claim, root=self.root)
                if claim.claim_id not in recorded_ids:
                    recorded_ids.append(claim.claim_id)
            result.claims.append(claim)
            if claim.verdict == ClaimVerdict.VERIFIED:
                result.verified += 1
            elif claim.verdict == ClaimVerdict.FORMALIZED_STMT_ONLY:
                result.formalized_stmt_only += 1
            elif claim.verdict == ClaimVerdict.FAILED:
                result.failed += 1
            else:
                result.deferred += 1
        if persist and recorded_ids != paper.claim_ids:
            paper.claim_ids = recorded_ids
            put_paper(paper, root=self.root)
        result.note = (
            f"{result.verified} verified, "
            f"{result.formalized_stmt_only} stmt-only, "
            f"{result.failed} failed, {result.deferred} deferred")
        return result


# ---------------------------------------------------------------------------
# Query / stats / graph
# ---------------------------------------------------------------------------

def stats(root: pathlib.Path | None = None) -> dict[str, Any]:
    papers = load_papers(root)
    claims = load_claims(root)
    by_era: dict[str, int] = {}
    by_verdict: dict[str, int] = {}
    n_axiom_clean = 0
    n_linked = 0
    for p in papers.values():
        by_era[p.era] = by_era.get(p.era, 0) + 1
    for c in claims.values():
        by_verdict[c.verdict] = by_verdict.get(c.verdict, 0) + 1
        if c.axiom_clean:
            n_axiom_clean += 1
        if c.linked_theorem:
            n_linked += 1
    return {
        "papers": len(papers),
        "claims": len(claims),
        "verified": by_verdict.get(ClaimVerdict.VERIFIED, 0),
        "axiom_clean": n_axiom_clean,
        "linked_into_lab_theorems": n_linked,
        "by_era": by_era,
        "by_verdict": by_verdict,
    }


def build_graph(root: pathlib.Path | None = None) -> "Any":
    """Project the index into a :class:`omicairn.knowledge_graph.Graph`:
    ``paper --presents--> claim --formalized_as--> theorem``."""
    from . import knowledge_graph as kg
    g = kg.Graph()
    papers = load_papers(root)
    claims = load_claims(root)
    for p in papers.values():
        nid = f"{PAPER_NODE}:{p.paper_id}"
        g.nodes[nid] = kg.Node(
            id=nid, type=PAPER_NODE, title=p.title or p.paper_id,
            body=p.citation, tags=[p.era],
            extra={"author": p.author, "year": p.year},
        )
    for c in claims.values():
        cid = f"{CLAIM_NODE}:{c.claim_id}"
        g.nodes[cid] = kg.Node(
            id=cid, type=CLAIM_NODE, title=c.claim_id, body=c.informal_statement,
            status=c.verdict,
            extra={"axiom_clean": c.axiom_clean, "axioms": c.axioms,
                   "linked_theorem": c.linked_theorem},
        )
        pid = f"{PAPER_NODE}:{c.paper_id}"
        e1 = kg.Edge(src=pid, rel=PAPER_EDGE_PRESENTS, dst=cid)
        g.edges[e1.id] = e1
        if c.linked_theorem:
            tid = f"{THEOREM_NODE}:{c.linked_theorem}"
            e2 = kg.Edge(src=cid, rel=CLAIM_EDGE_FORMALIZED_AS, dst=tid)
            g.edges[e2.id] = e2
    return g


def render_index_md(root: pathlib.Path | None = None) -> str:
    """Human-readable index, grouped by era, paper → claim verdicts."""
    papers = load_papers(root)
    claims = load_claims(root)
    order = {e: i for i, e in enumerate(PAPER_ERA_ORDER)}
    by_era: dict[str, list[MathPaper]] = {}
    for p in papers.values():
        by_era.setdefault(p.era, []).append(p)
    s = stats(root)
    lines = ["# Math Paper Review — Index", "",
             f"_{s['papers']} papers, {s['claims']} claims; "
             f"{s['verified']} Lean-verified (sorry-free, axiom-clean)._", ""]
    mark = {ClaimVerdict.VERIFIED: "[OK]",
            ClaimVerdict.FORMALIZED_STMT_ONLY: "[~~]",
            ClaimVerdict.FAILED: "[XX]",
            ClaimVerdict.DEFERRED: "[..]"}
    for era in sorted(by_era, key=lambda e: order.get(e, 99)):
        lines.append(f"## {era}")
        for p in sorted(by_era[era], key=lambda x: x.paper_id):
            lines.append(f"- **{p.title or p.paper_id}** — {p.author} ({p.year})")
            for c in claims_for(p.paper_id, root=root):
                m = mark.get(c.verdict, "[..]")
                extra = ""
                if c.verdict == ClaimVerdict.VERIFIED:
                    extra = f"  axioms={c.axioms}"
                elif c.verdict == ClaimVerdict.DEFERRED and c.defer_reason:
                    extra = f"  (deferred: {c.defer_reason})"
                lines.append(f"  - {m} {c.informal_statement}{extra}")
        lines.append("")
    return "\n".join(lines)
