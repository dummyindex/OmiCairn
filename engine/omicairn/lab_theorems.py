"""Lab-level theorem library + virtual-mathematician substrate.

(Named ``lab_theorems`` to parallel ``lab_knowledge`` / ``lab_library`` and
to avoid colliding with Lean's own Mathlib dependency.)

OmiCairn theory phases (theory-case runs already emit Lean theorems)
should not be limited to the base LLM's pretrained math or web-grabbed
"knowledge" — a fact is only as good as a proof that *compiles*. This module
is the lab's compounding, MACHINE-VALIDATED theorem library: a collection of
theorems whose Lean source has been checked sorry-free by the LOCAL toolchain
(:mod:`omicairn.lean_verify`), wired into a prerequisite dependency graph and
ordered by a curriculum band (elementary → PhD).

Storage mirrors the lab-KB discipline exactly (no database): a single
append-only JSONL op-log (``theorems.jsonl``), folded to current state on
read, guarded by an atomic-create lockfile. Each entry is a
:class:`TheoremEntry`:

    name           canonical theorem name (the Lean declaration name)
    statement      human/LaTeX-ish statement of the theorem
    lean_source    the Lean program text (statement + proof)
    lean_file      tracked path of the ``.lean`` source (relative to the area)
    status         verified | unverified | refuted  (TheoremStatus)
    compiled       bool — did the LOCAL Lean toolchain accept it?
    sorry_free     bool — compiled AND no `sorry`/`admit` placeholder
    theorems_checked   declaration names Lean parsed out of the source
    substance_warnings the M3b proof-substance hints (REUSED verbatim from
                       lean_verify.substance_warnings — we do NOT reinvent
                       the substance check)
    prerequisites  names of library theorems this one DEPENDS ON (graph edges)
    curriculum_level   one of constants.CurriculumLevel
    tactics        the Lean tactics the proof uses (parsed, for the graph)
    citation       where the theorem comes from (course / textbook / source)
    verdict        the full LeanVerdict.to_dict() — the audit record
    added_at / verified_at  ISO timestamps

The VALIDATION pipeline (:func:`validate_entry`) is the load-bearing
contract: it calls :func:`omicairn.lean_verify.verify_lean_file` against the
reusable Mathlib Lake project, records the real verdict, and REUSES the
existing proof-substance machinery rather than trusting LLM text. An entry
only becomes ``verified`` if Lean accepted it sorry-free.

The VIRTUAL MATHEMATICIAN (:class:`VirtualMathematician`) is the agent loop:
state a theorem → retrieve relevant prerequisites from the graph → draft a
Lean proof (via a pluggable drafter; the default is a retrieval/template
drafter, an LLM drafter is the production hook) → compile → repair-loop →
on a clean sorry-free compile, CONTRIBUTE the theorem back to the library.
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
    CURRICULUM_ORDER,
    CurriculumLevel,
    ENV_LAB_THEOREMS,
    LEAN_VERDICT_SUFFIX,
    LAB_THEOREMS_AREA,
    LAB_THEOREMS_DIRNAME,
    LAB_THEOREMS_FILE,
    LAB_THEOREMS_LEAN_SUBDIR,
    LAB_THEOREMS_LOCK,
    THEOREM_EDGE_DEPENDS_ON,
    THEOREM_NODE,
    TheoremStatus,
)
from . import lean_verify


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Location resolution (mirrors lab_knowledge.resolve_root)
# ---------------------------------------------------------------------------

def _area_root() -> pathlib.Path:
    """The tracked ``lab_theorems/`` area beside the repo (two levels up from the
    ``omicairn`` package: ``.../engine/omicairn`` → repo → ``lab_theorems``)."""
    pkg = pathlib.Path(__file__).resolve().parent           # .../omicairn
    engine_dir = pkg.parent                                     # .../engine
    repo = engine_dir.parent                                    # repo root
    return repo / LAB_THEOREMS_AREA


def resolve_root(explicit: str | pathlib.Path | None = None) -> pathlib.Path:
    """Resolve the theorem-library STORE directory (where theorems.jsonl lives).

    Precedence (highest first):
      1. ``explicit`` — a path passed in directly (CLI ``--root``).
      2. ``$OMICAIRN_LAB_THEOREMS`` — env override.
      3. ``<repo>/lab_theorems/store`` — the default tracked location.
    """
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()
    env = os.environ.get(ENV_LAB_THEOREMS, "").strip()
    if env:
        return pathlib.Path(env).expanduser().resolve()
    return (_area_root() / LAB_THEOREMS_DIRNAME).resolve()


def lean_dir(root: pathlib.Path | None = None) -> pathlib.Path:
    """The directory holding the ``.lean`` sources for the library.

    Default: the ``lean/`` sibling of the store (``lab_theorems/lean``). When a
    non-default store ``root`` is given, the lean dir is its sibling so a
    relocated library keeps its sources together.
    """
    r = resolve_root() if root is None else pathlib.Path(root)
    return (r.parent / LAB_THEOREMS_LEAN_SUBDIR).resolve()


def _store_path(root: pathlib.Path | None = None) -> pathlib.Path:
    return (resolve_root() if root is None else pathlib.Path(root)) / LAB_THEOREMS_FILE


@contextmanager
def _locked(root: pathlib.Path, timeout: float = 10.0) -> Iterator[None]:
    """Atomic-create lockfile around the read-append cycle (best-effort:
    proceeds after timeout — a single-line append is atomic at the OS
    level)."""
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / LAB_THEOREMS_LOCK
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
# Tactic / declaration parsing (for the knowledge graph)
# ---------------------------------------------------------------------------

# A conservative, domain-agnostic roster of Lean tactic tokens we surface for
# the graph "tactics used" attribute. This is NOT a substance judgment (that
# is lean_verify.substance_warnings); it is descriptive metadata for the
# dependency/curriculum graph. Kept generic — these are Lean built-ins /
# Mathlib staples, not domain keywords.
_TACTIC_TOKENS: frozenset[str] = frozenset({
    "nlinarith", "linarith", "ring", "ring_nf", "field_simp", "norm_num",
    "simp", "simpa", "omega", "positivity", "decide", "native_decide",
    "induction", "cases", "rfl", "rw", "exact", "refine", "apply", "intro",
    "constructor", "calc", "linear_combination", "gcongr", "polyrith",
})


def parse_tactics(source: str) -> list[str]:
    """Best-effort list of Lean tactic tokens appearing in ``source``.

    Word-boundary token match against a generic Lean-tactic roster. Order is
    first-appearance; duplicates collapsed. Descriptive metadata only."""
    found: list[str] = []
    seen: set[str] = set()
    for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", source):
        if tok in _TACTIC_TOKENS and tok not in seen:
            seen.add(tok)
            found.append(tok)
    return found


def has_sorry(source: str) -> bool:
    """True iff the proof uses a real `sorry`/`admit` placeholder in CODE.

    A `sorry` makes a Lean file COMPILE while proving nothing — exactly the
    failure mode this library exists to prevent. The verifier's `compiled`
    flag does not catch it (Lean emits a warning, not an error), so we check
    the source directly and fold it into ``sorry_free``.

    COMMENT-AWARE (2026-06-23 fix): the previous bare
    ``\\bsorry\\b`` scan matched the word "sorry" inside a Lean comment or
    docstring (``-- no sorry needed``, ``/-- proves X without sorry -/``),
    which WRONGLY demoted a genuinely sorry-free theorem to UNVERIFIED. We now
    delegate to :func:`omicairn.lean_verify.source_uses_sorry`, which strips Lean
    comments before matching — keying off CODE only. The compiler diagnostic
    (`declaration uses 'sorry'`, parsed by lean_verify) remains the
    authoritative signal when a verdict is available."""
    return lean_verify.source_uses_sorry(source)


# ---------------------------------------------------------------------------
# TheoremEntry — the library record
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class TheoremEntry:
    name: str
    statement: str
    lean_source: str = ""
    lean_file: str = ""
    status: str = TheoremStatus.UNVERIFIED
    compiled: bool = False
    sorry_free: bool = False
    theorems_checked: list[str] = dataclasses.field(default_factory=list)
    substance_warnings: list[str] = dataclasses.field(default_factory=list)
    prerequisites: list[str] = dataclasses.field(default_factory=list)
    curriculum_level: str = CurriculumLevel.UNDERGRAD
    tactics: list[str] = dataclasses.field(default_factory=list)
    citation: str = ""
    verdict: dict[str, Any] = dataclasses.field(default_factory=dict)
    added_at: str = ""
    verified_at: str = ""

    # Identity in the store is the canonical theorem name (a re-add of the
    # same name OVERWRITES — last write wins, the op-log fold semantics).
    @property
    def key(self) -> str:
        return self.name

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TheoremEntry":
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in fields})


# ---------------------------------------------------------------------------
# Store I/O (append-only op-log, folded on read) — mirrors lab_knowledge
# ---------------------------------------------------------------------------

def load(root: pathlib.Path | None = None) -> dict[str, TheoremEntry]:
    """Fold the op-log into {name: TheoremEntry}. Last write per name wins."""
    path = _store_path(root)
    out: dict[str, TheoremEntry] = {}
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
        if isinstance(d, dict) and d.get("name"):
            out[d["name"]] = TheoremEntry.from_dict(d)
    return out


def _append(root: pathlib.Path, entry: TheoremEntry) -> None:
    path = _store_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")


def get(name: str, root: pathlib.Path | None = None) -> Optional[TheoremEntry]:
    return load(root).get(name)


def put(entry: TheoremEntry, root: pathlib.Path | None = None) -> TheoremEntry:
    """Append an entry to the store (op-log). Stamps ``added_at`` if unset."""
    r = resolve_root() if root is None else pathlib.Path(root)
    if not entry.added_at:
        entry.added_at = _now()
    with _locked(r):
        _append(r, entry)
    return entry


# ---------------------------------------------------------------------------
# Validation pipeline — the load-bearing contract (REUSES lean_verify)
# ---------------------------------------------------------------------------

def _verdict_path(entry: TheoremEntry, root: pathlib.Path | None = None) -> pathlib.Path:
    """Where this entry's audit verdict.json is written (beside the store,
    in a ``verdicts/`` subdir, named by theorem). Mirrors the
    `<stage>/lean/<Claim>.verdict.json` convention."""
    r = resolve_root() if root is None else pathlib.Path(root)
    vd = r / "verdicts"
    vd.mkdir(parents=True, exist_ok=True)
    return vd / f"{entry.name}{LEAN_VERDICT_SUFFIX}"


def validate_entry(
    entry: TheoremEntry,
    *,
    workdir: str | pathlib.Path | None = None,
    root: pathlib.Path | None = None,
    timeout_s: int | None = None,
    write_verdict: bool = True,
) -> TheoremEntry:
    """Compile the entry's Lean source against the local toolchain and record
    the REAL verdict, REUSING :mod:`omicairn.lean_verify`.

    This is where "validate, don't trust" lives. ``workdir`` should be inside
    the reusable Mathlib Lake project (``.lean-mathlib-omicairn``) so Mathlib deps
    resolve; pass it explicitly for Mathlib-backed proofs. The entry's
    ``compiled`` / ``sorry_free`` / ``status`` / ``substance_warnings`` /
    ``theorems_checked`` / ``verdict`` fields are populated from the
    :class:`omicairn.lean_verify.LeanVerdict`. An entry only becomes
    ``verified`` when Lean accepts it sorry-free.

    Does NOT mutate the store — caller decides whether to :func:`put` the
    validated entry (contribution is a separate, explicit step).
    """
    source = entry.lean_source
    if not source and entry.lean_file:
        try:
            source = pathlib.Path(entry.lean_file).read_text()
            entry.lean_source = source
        except OSError:
            source = ""

    verdict = lean_verify.verify_lean_source(
        source, workdir=workdir, name=entry.name, timeout_s=timeout_s,
    )
    entry.compiled = verdict.compiled
    entry.theorems_checked = verdict.theorems_checked
    # Substance signal = the M3b warnings (verbatim from the verifier)
    # PLUS the math-LIBRARY trivia check, which catches the tautology closers
    # (`trivial`/`decide`/`native_decide`/`rfl`) the M3b layers deliberately
    # treat as legit (the d5/d7 witness-check shape). Deduped, paper hints
    # first. This is the SUBSTANCE GATE: a compiling-but-trivial proof carries
    # a warning and is demoted, never silently first-class.
    trivia = lean_verify.library_trivia_warnings(source)
    seen_w: set[str] = set()
    merged_w: list[str] = []
    for w in (*verdict.substance_warnings, *trivia):
        if w not in seen_w:
            seen_w.add(w)
            merged_w.append(w)
    entry.substance_warnings = merged_w
    entry.verdict = verdict.to_dict()
    entry.tactics = parse_tactics(source)
    sorry_present = has_sorry(source)
    entry.sorry_free = verdict.compiled and not sorry_present
    if verdict.tool_missing:
        # Toolchain absent — keep honest UNVERIFIED, do not fabricate verified.
        entry.status = TheoremStatus.UNVERIFIED
    elif entry.sorry_free and trivia:
        # Compiles sorry-free but the proof is a content-free tautology closer
        # with no named-lemma derivation — record it, but as a SECOND-class
        # `verified_trivial`, NOT a first-class library theorem (WARN, not a
        # hard reject: an honest short proof never trips `trivia`).
        entry.status = TheoremStatus.VERIFIED_TRIVIAL
        entry.verified_at = _now()
    elif entry.sorry_free:
        entry.status = TheoremStatus.VERIFIED
        entry.verified_at = _now()
    elif verdict.compiled and sorry_present:
        # Compiles only because of a placeholder — NOT verified.
        entry.status = TheoremStatus.UNVERIFIED
    else:
        entry.status = TheoremStatus.REFUTED

    if write_verdict:
        try:
            vp = _verdict_path(entry, root=root)
            vp.write_text(json.dumps(verdict.to_dict(), indent=2))
        except OSError:
            pass
    return entry


def add_theorem(
    name: str,
    statement: str,
    lean_source: str,
    *,
    prerequisites: list[str] | None = None,
    curriculum_level: str = CurriculumLevel.UNDERGRAD,
    citation: str = "",
    lean_file: str = "",
    workdir: str | pathlib.Path | None = None,
    root: pathlib.Path | None = None,
    require_verified: bool = True,
    timeout_s: int | None = None,
) -> TheoremEntry:
    """Validate a theorem and (optionally) contribute it to the library.

    Compiles via :func:`validate_entry`. When ``require_verified`` (the
    default), an entry that did NOT compile sorry-free is NOT written to the
    store — instead the validated entry is returned with its honest
    ``status`` (``unverified``/``refuted``) so the caller can see why. This is
    the discipline: a small clean validated core beats a large unvalidated
    list. Pass ``require_verified=False`` to record an honest placeholder
    (e.g. a known-hard theorem you want tracked as ``unverified``).
    """
    entry = TheoremEntry(
        name=name, statement=statement, lean_source=lean_source,
        lean_file=lean_file, prerequisites=list(prerequisites or []),
        curriculum_level=curriculum_level, citation=citation,
    )
    entry = validate_entry(
        entry, workdir=workdir, root=root, timeout_s=timeout_s,
    )
    # `verified_trivial` is NOT a first-class verified theorem — under the
    # default `require_verified` it is returned (with its substance warning)
    # but NOT contributed, exactly like an unverified entry. Pass
    # `require_verified=False` to record it as a flagged placeholder.
    if require_verified and entry.status != TheoremStatus.VERIFIED:
        return entry
    return put(entry, root=root)


# ---------------------------------------------------------------------------
# Query / stats
# ---------------------------------------------------------------------------

def query(
    *,
    text: str | None = None,
    level: str | None = None,
    status: str | None = None,
    limit: int = 100,
    root: pathlib.Path | None = None,
) -> list[TheoremEntry]:
    """List/search library entries, optionally filtered by free text (over
    name/statement/citation), curriculum level, or status. Returned in
    curriculum order then name."""
    entries = list(load(root).values())
    if level is not None:
        entries = [e for e in entries if e.curriculum_level == level]
    if status is not None:
        entries = [e for e in entries if e.status == status]
    if text:
        t = text.lower()
        entries = [
            e for e in entries
            if t in e.name.lower() or t in e.statement.lower()
            or t in (e.citation or "").lower()
        ]
    order = {lvl: i for i, lvl in enumerate(CURRICULUM_ORDER)}
    entries.sort(key=lambda e: (order.get(e.curriculum_level, 99), e.name))
    return entries[:limit]


def stats(root: pathlib.Path | None = None) -> dict[str, Any]:
    entries = load(root)
    by_level: dict[str, int] = {}
    by_status: dict[str, int] = {}
    n_warnings = 0
    for e in entries.values():
        by_level[e.curriculum_level] = by_level.get(e.curriculum_level, 0) + 1
        by_status[e.status] = by_status.get(e.status, 0) + 1
        if e.substance_warnings:
            n_warnings += 1
    return {
        "total": len(entries),
        "verified": by_status.get(TheoremStatus.VERIFIED, 0),
        "by_level": by_level,
        "by_status": by_status,
        "with_substance_warnings": n_warnings,
    }


# ---------------------------------------------------------------------------
# Knowledge graph — the prerequisite DAG (reuses knowledge_graph Node/Edge)
# ---------------------------------------------------------------------------

def build_graph(root: pathlib.Path | None = None) -> "kg.Graph":
    """Project the library into a :class:`omicairn.knowledge_graph.Graph`.

    Each theorem is a node (``type=THEOREM_NODE``); each prerequisite is
    a ``depends_on`` edge (theorem → prerequisite). This reuses the SAME
    Node/Edge/Graph types the run-level knowledge graph uses, so the math
    library is a first-class citizen of the lab graph model. Tactics +
    curriculum level + status ride along in ``Node.extra``/``tags``.
    """
    from . import knowledge_graph as kg
    g = kg.Graph()
    entries = load(root)
    for e in entries.values():
        nid = f"{THEOREM_NODE}:{e.name}"
        g.nodes[nid] = kg.Node(
            id=nid, type=THEOREM_NODE, title=e.name,
            body=e.statement, status=e.status,
            tags=[e.curriculum_level, *e.tactics],
            extra={
                "citation": e.citation, "compiled": e.compiled,
                "sorry_free": e.sorry_free,
                "substance_warnings": e.substance_warnings,
                "lean_file": e.lean_file,
            },
        )
    for e in entries.values():
        src = f"{THEOREM_NODE}:{e.name}"
        for pre in e.prerequisites:
            dst = f"{THEOREM_NODE}:{pre}"
            edge = kg.Edge(src=src, rel=THEOREM_EDGE_DEPENDS_ON, dst=dst)
            g.edges[edge.id] = edge
    return g


def prerequisites_closure(
    name: str, root: pathlib.Path | None = None, _seen: set[str] | None = None
) -> list[str]:
    """Transitive prerequisite closure of a theorem (topological-ish: deeper
    prereqs first). Cycle-safe. Used by the virtual mathematician to assemble
    the lemmas a new proof can lean on."""
    entries = load(root)
    seen = _seen if _seen is not None else set()
    out: list[str] = []
    e = entries.get(name)
    if e is None:
        return out
    for pre in e.prerequisites:
        if pre in seen:
            continue
        seen.add(pre)
        out.extend(prerequisites_closure(pre, root=root, _seen=seen))
        out.append(pre)
    return out


def _find_cycle(entries: dict[str, TheoremEntry]) -> list[str] | None:
    """Return ONE depends_on cycle as a path (``[a, b, …, a]``) if the prereq
    DAG has one, else None. A cyclic prereq graph is an invariant violation: a
    theorem cannot (transitively) be its own prerequisite, and
    :func:`prerequisites_closure` would silently produce a non-topological
    'closure' over it. DFS with a recursion stack (3-colour)."""
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in entries}

    def dfs(node: str, path: list[str]) -> list[str] | None:
        color[node] = GREY
        path.append(node)
        e = entries.get(node)
        for pre in (e.prerequisites if e else []):
            if pre not in entries:
                continue  # missing prereq handled separately
            if color[pre] == GREY:
                # Back-edge → cycle; return the path from `pre` round to `pre`.
                return path[path.index(pre):] + [pre]
            if color[pre] == WHITE:
                found = dfs(pre, path)
                if found is not None:
                    return found
        path.pop()
        color[node] = BLACK
        return None

    for n in entries:
        if color[n] == WHITE:
            found = dfs(n, [])
            if found is not None:
                return found
    return None


def check_curriculum_monotonic(root: pathlib.Path | None = None) -> list[str]:
    """Return violations of the curriculum/dependency invariants. Empty list =
    clean. A validator the library scheduler can gate on. Checks:

      * NO ``depends_on`` CYCLE (a theorem cannot be its own transitive
        prerequisite — would break topological closure);
      * every prerequisite is PRESENT (not a dangling name);
      * every prerequisite is VERIFIED (compiled sorry-free) — a theorem may
        not be treated as resting on an UNVERIFIED / refuted / trivial prereq;
      * NO STRICTLY-HIGHER-band prerequisite (curriculum monotonicity).
    """
    entries = load(root)
    order = {lvl: i for i, lvl in enumerate(CURRICULUM_ORDER)}
    violations: list[str] = []
    # (a) cycle check first — a cyclic graph poisons every other invariant.
    cycle = _find_cycle(entries)
    if cycle is not None:
        violations.append("depends_on cycle: " + " -> ".join(cycle))
    for e in entries.values():
        my = order.get(e.curriculum_level, 99)
        for pre in e.prerequisites:
            pe = entries.get(pre)
            if pe is None:
                violations.append(f"{e.name} depends on missing '{pre}'")
                continue
            # (b) prereq must itself be VERIFIED before a dependent rests on it.
            if pe.status != TheoremStatus.VERIFIED:
                violations.append(
                    f"{e.name} depends on UNVERIFIED '{pre}' "
                    f"(status={pe.status})"
                )
            if order.get(pe.curriculum_level, 99) > my:
                violations.append(
                    f"{e.name} ({e.curriculum_level}) depends on higher-band "
                    f"'{pre}' ({pe.curriculum_level})"
                )
    return violations


def render_availability_md(root: pathlib.Path | None = None,
                           max_list: int = 60) -> str:
    """Compact AVAILABILITY listing for the EXECUTE/theory prompt token
    ({{lab_theorems}}): the verified theorems a run can RETRIEVE and import as
    proof scaffolding, plus the CLI seam to fetch the full compiled Lean. This
    is the read side of the EXECUTE-retrieval seam — a static listing the agent
    sees, with a `omicairn labtheorems retrieve` instruction so it can pull the
    actual sources on demand (headless-robust: a plain CLI, no MCP/runtime
    dep). Returns a sentinel when the library is empty/absent so the token is
    always substitutable."""
    try:
        entries = load(root)
    except OSError:
        entries = {}
    verified = [e for e in entries.values()
                if e.status == TheoremStatus.VERIFIED]
    if not verified:
        return "(no lab theorem library available)"
    order = {lvl: i for i, lvl in enumerate(CURRICULUM_ORDER)}
    verified.sort(key=lambda e: (order.get(e.curriculum_level, 99), e.name))
    lines = [
        f"The lab theorem library has **{len(verified)} Lean-verified "
        f"(sorry-free) theorems** you may build on instead of cold-proving. "
        f"Before formalizing a claim, RETRIEVE relevant verified lemmas with:",
        "",
        '    omicairn labtheorems retrieve "<your goal / keywords>"',
        "",
        "which returns the matching theorems WITH their compiled Lean source "
        "to import as scaffolding. Available theorems (name — statement):",
        "",
    ]
    for e in verified[:max_list]:
        lines.append(f"- `{e.name}` [{e.curriculum_level}] — {e.statement}")
    if len(verified) > max_list:
        lines.append(f"- … and {len(verified) - max_list} more "
                     f"(`omicairn labtheorems list`).")
    return "\n".join(lines)


def render_index_md(root: pathlib.Path | None = None) -> str:
    """A human-readable index of the library, grouped by curriculum band."""
    entries = load(root)
    order = {lvl: i for i, lvl in enumerate(CURRICULUM_ORDER)}
    by_level: dict[str, list[TheoremEntry]] = {}
    for e in entries.values():
        by_level.setdefault(e.curriculum_level, []).append(e)
    lines = ["# Lab Theorem Library — Index", "",
             f"_{len(entries)} theorems; "
             f"{sum(1 for e in entries.values() if e.status == TheoremStatus.VERIFIED)} "
             f"Lean-verified (sorry-free, local toolchain)._", ""]
    for lvl in sorted(by_level, key=lambda l: order.get(l, 99)):
        lines.append(f"## {lvl}")
        for e in sorted(by_level[lvl], key=lambda x: x.name):
            mark = {TheoremStatus.VERIFIED: "[OK]",
                    TheoremStatus.VERIFIED_TRIVIAL: "[~~]",
                    TheoremStatus.UNVERIFIED: "[??]",
                    TheoremStatus.REFUTED: "[XX]"}.get(e.status, "[??]")
            deps = f"  deps: {', '.join(e.prerequisites)}" if e.prerequisites else ""
            lines.append(f"- {mark} **{e.name}** — {e.statement}{deps}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Virtual mathematician — the agent loop
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ProofAttempt:
    """One drafter→compile iteration of the virtual mathematician."""
    iteration: int
    lean_source: str
    compiled: bool
    sorry_free: bool
    errors: list[str] = dataclasses.field(default_factory=list)
    substance_warnings: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class ProofResult:
    """Outcome of a virtual-mathematician run on one target theorem."""
    name: str
    statement: str
    proved: bool
    contributed: bool
    attempts: list[ProofAttempt] = dataclasses.field(default_factory=list)
    entry: Optional[TheoremEntry] = None
    retrieved_prereqs: list[str] = dataclasses.field(default_factory=list)
    note: str = ""


# A Drafter takes (target_name, statement, retrieved_lemmas, prev_attempt,
# prev_errors) and returns a candidate Lean source string. The default drafter
# is retrieval/template-based and offline; an LLM drafter (claude_runner) is
# the production hook — pluggable so the loop is testable without tokens.
Drafter = Callable[[str, str, list["TheoremEntry"], Optional[str], list[str]], str]


class VirtualMathematician:
    """Agentic prove-and-contribute loop over the math library.

    Loop: state a theorem → retrieve relevant prerequisites from the graph →
    draft a Lean proof → compile (local toolchain) → on failure, feed the
    errors back to the drafter and repair (bounded iterations) → on a clean
    sorry-free compile, CONTRIBUTE the theorem to the library (graph edges
    to the retrieved prereqs included).

    The drafter is injected: the default :meth:`_template_drafter` is a
    retrieval/template baseline (no LLM, so the loop is exercisable offline
    and in CI); production passes an LLM-backed drafter.
    """

    def __init__(
        self,
        drafter: Drafter | None = None,
        *,
        workdir: str | pathlib.Path | None = None,
        root: pathlib.Path | None = None,
        max_iters: int = 3,
        timeout_s: int | None = None,
    ) -> None:
        self.drafter = drafter or self._template_drafter
        self.workdir = workdir
        self.root = root
        self.max_iters = max_iters
        self.timeout_s = timeout_s

    # ---- retrieval -------------------------------------------------------
    # Generic, content-free tokens that must not drive lexical retrieval
    # (every statement mentions variables/quantifiers). Domain-agnostic: these
    # are math STOPWORDS, not field keywords.
    _RETRIEVAL_STOPWORDS: frozenset[str] = frozenset({
        "for", "the", "of", "is", "and", "as", "to", "a", "an", "on", "in",
        "form", "two", "variable", "some",
    })

    def retrieve(self, statement: str, hint_prereqs: list[str] | None = None,
                 k: int = 5, min_overlap: int = 2) -> list[TheoremEntry]:
        """Retrieve library lemmas relevant to a target statement.

        Explicit ``hint_prereqs`` (their transitive closure) come first; then
        a cheap lexical overlap over statements/names fills the rest, but ONLY
        for entries clearing ``min_overlap`` CONTENT tokens (stopwords
        excluded) — so an unrelated lemma is not dragged in by an incidental
        shared word. Returns verified entries only — the mathematician builds
        on PROVEN ground."""
        entries = load(self.root)
        chosen: list[TheoremEntry] = []
        seen: set[str] = set()
        for pre in (hint_prereqs or []):
            for name in (*prerequisites_closure(pre, root=self.root), pre):
                e = entries.get(name)
                if e and e.status == TheoremStatus.VERIFIED and name not in seen:
                    seen.add(name)
                    chosen.append(e)
        toks = {t for t in re.findall(r"[a-z]+", statement.lower())
                if t not in self._RETRIEVAL_STOPWORDS}
        scored: list[tuple[int, TheoremEntry]] = []
        for e in entries.values():
            if e.name in seen or e.status != TheoremStatus.VERIFIED:
                continue
            etoks = {t for t in re.findall(r"[a-z]+",
                                           (e.name + " " + e.statement).lower())
                     if t not in self._RETRIEVAL_STOPWORDS}
            scored.append((len(toks & etoks), e))
        scored.sort(key=lambda x: -x[0])
        for score, e in scored:
            if len(chosen) >= k:
                break
            if score >= min_overlap:
                chosen.append(e)
        return chosen

    # ---- default offline drafter ----------------------------------------
    @staticmethod
    def _template_drafter(
        name: str, statement: str, lemmas: list["TheoremEntry"],
        prev: Optional[str], errors: list[str],
    ) -> str:
        """Retrieval/template baseline drafter (no LLM).

        Emits a Mathlib-importing skeleton that tries the general-purpose
        nonlinear-arithmetic closers, seeded with square-nonneg hints — enough
        to discharge the seeded inequality family end-to-end offline. This is
        the BASELINE; an LLM drafter replaces it in production. If a previous
        attempt failed, it escalates the tactic on the repair pass."""
        header = "import Mathlib\n\n"
        # Escalate across repair iterations.
        if prev is None:
            tac = "nlinarith [sq_nonneg (a - b), sq_nonneg (a + b)]"
        elif any("nlinarith" in e or "linarith" in e for e in errors) or prev:
            tac = ("nlinarith [sq_nonneg (a - b), sq_nonneg (a + b), "
                   "sq_nonneg a, sq_nonneg b, mul_self_nonneg (a - b)]")
        else:
            tac = "nlinarith [sq_nonneg (a - b)]"
        return f"{header}theorem {name} {statement} := by\n  {tac}\n"

    # ---- the loop --------------------------------------------------------
    def prove(
        self,
        name: str,
        statement: str,
        *,
        hint_prereqs: list[str] | None = None,
        curriculum_level: str = CurriculumLevel.UNDERGRAD,
        citation: str = "",
        contribute: bool = True,
    ) -> ProofResult:
        """Attempt to prove ``name : statement`` and (optionally) contribute.

        ``statement`` is the Lean binder+goal that follows the theorem name,
        e.g. ``"(a b : ℝ) : 0 ≤ (a - b) ^ 2"`` — the drafter wraps it in a
        full declaration. Returns a :class:`ProofResult` with the full attempt
        trail and, on success, the contributed entry.
        """
        retrieved = self.retrieve(statement, hint_prereqs=hint_prereqs)
        result = ProofResult(
            name=name, statement=statement, proved=False, contributed=False,
            retrieved_prereqs=[e.name for e in retrieved],
        )
        prev_src: Optional[str] = None
        prev_errors: list[str] = []
        for i in range(1, self.max_iters + 1):
            src = self.drafter(name, statement, retrieved, prev_src, prev_errors)
            verdict = lean_verify.verify_lean_source(
                src, workdir=self.workdir, name=name, timeout_s=self.timeout_s,
            )
            sorry_free = verdict.compiled and not has_sorry(src)
            result.attempts.append(ProofAttempt(
                iteration=i, lean_source=src, compiled=verdict.compiled,
                sorry_free=sorry_free, errors=verdict.errors,
                substance_warnings=verdict.substance_warnings,
            ))
            if verdict.tool_missing:
                result.note = "lean toolchain not found — cannot validate"
                return result
            if sorry_free:
                result.proved = True
                # SUBSTANCE GATE for the auto-contribution path: a sorry-free
                # compile is necessary but not sufficient. A content-free
                # tautology closer (`trivial`/`decide`/…) is demoted to
                # `verified_trivial` and NOT contributed as a first-class
                # theorem — otherwise the auto-curriculum loop would fill the
                # library with `compiled`-but-trivial table facts (the exact
                # proof-theater the M3b/W18 gates guard against).
                trivia = lean_verify.library_trivia_warnings(src)
                merged_w = list(dict.fromkeys(
                    [*verdict.substance_warnings, *trivia]))
                is_trivial = bool(trivia)
                entry = TheoremEntry(
                    name=name, statement=statement, lean_source=src,
                    compiled=True, sorry_free=True,
                    theorems_checked=verdict.theorems_checked,
                    substance_warnings=merged_w,
                    prerequisites=[e.name for e in retrieved],
                    curriculum_level=curriculum_level, citation=citation,
                    tactics=parse_tactics(src), verdict=verdict.to_dict(),
                    status=(TheoremStatus.VERIFIED_TRIVIAL if is_trivial
                            else TheoremStatus.VERIFIED),
                    verified_at=_now(),
                )
                result.entry = entry
                if is_trivial:
                    # Flagged, never auto-contributed (WARN, not reject): the
                    # caller / reviewer can still see it on result.entry.
                    result.note = (f"proved in {i} iteration(s) but TRIVIAL "
                                   f"(content-free closer); flagged "
                                   f"verified_trivial, NOT contributed")
                elif contribute:
                    put(entry, root=self.root)
                    result.contributed = True
                    result.note = (f"proved in {i} iteration(s); contributed "
                                   f"to library")
                else:
                    result.note = f"proved in {i} iteration(s) (not contributed)"
                return result
            prev_src = src
            prev_errors = verdict.errors
        result.note = (f"failed after {self.max_iters} iterations; last "
                       f"errors: {prev_errors[:2]}")
        return result
