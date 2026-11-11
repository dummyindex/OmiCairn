"""Per-workspace knowledge graph — the durable product of a OmiCairn run.

A workspace accumulates a graph of research knowledge: goals,
hypotheses, findings, result pointers, dataset pointers, and metadata,
linked by provenance edges (tests / supports / refutes / derived_from /
raises / cites). Run summaries (review notes, the negative-results
record, the {{knowledge_graph_projection}} prompt block) are projections
of a slice of this graph, not the product itself (core belief 11). See
`docs/design-docs/dynamic-hypotheses.md` for the full spec.

Storage
-------
`<ws>/knowledge/graph.jsonl` — an append-only op-log. Each line is one
upsert::

    {"op": "upsert_node", "node": {...}}
    {"op": "upsert_edge", "edge": {...}}

`load_graph` folds the log into current state (last write per id wins).
This matches the JSONL discipline already used by `events.jsonl`,
`provenance.jsonl`, and `shared_lessons.jsonl` — flat files, no
database (ARCHITECTURE.md "no database"; the codebase prefers
grep/diff-able stores).

Honesty rule (mechanical)
-------------------------
A hypothesis may only enter `supported`/`refuted` if at least one
incoming `supports`/`refutes` edge originates from a node whose
`evidence_paths` resolve on disk under the workspace. This is the
graph-level enforcement of belief 10 (honest science) and is checked
here in `set_hypothesis_status`, not left to prompt wording.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
import pathlib
import time
from contextlib import contextmanager
from typing import Any, Iterator

from .constants import (
    EDGE_RELS,
    EdgeRel,
    GraphUpdateVerdict,
    HYPOTHESIS_STATUSES_REQUIRING_EVIDENCE,
    HYPOTHESIS_TRANSITIONS,
    HypothesisOrigin,
    HypothesisStatus,
    KG_GRAPH_FILE,
    KG_LOCK_FILE,
    KNOWLEDGE_DIR,
    NODE_TYPES,
    NodeType,
    PhaseName,
)


class IllegalTransitionError(ValueError):
    """A hypothesis status change that HYPOTHESIS_TRANSITIONS forbids
    (e.g. supported -> inconclusive). Distinct from HonestyRuleError so
    callers can tell "this is a state conflict" from "this lacks
    evidence" — they warrant different operator messages."""


class HonestyRuleError(ValueError):
    """A supported/refuted transition refused because no incoming
    supports/refutes edge resolves to real on-disk evidence (belief 10)."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _slug(text: str, salt: str = "") -> str:
    """Short stable slug from text (+optional salt) for node ids."""
    h = hashlib.sha1((text.strip().lower() + "|" + salt).encode("utf-8"))
    return h.hexdigest()[:12]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Node:
    id: str
    type: str
    title: str
    body: str = ""
    status: str = ""
    origin: str = HypothesisOrigin.MANUAL
    source_phase: str = ""
    source_stage: str = ""
    evidence_paths: list[str] = dataclasses.field(default_factory=list)
    tags: list[str] = dataclasses.field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Node":
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in fields})


@dataclasses.dataclass
class Edge:
    src: str
    rel: str
    dst: str
    weight: float = 1.0
    source_phase: str = ""
    created_at: str = ""
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.src}--{self.rel}-->{self.dst}"

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Edge":
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in fields})


@dataclasses.dataclass
class Graph:
    """In-memory folded view of the op-log."""
    nodes: dict[str, Node] = dataclasses.field(default_factory=dict)
    edges: dict[str, Edge] = dataclasses.field(default_factory=dict)

    def nodes_of_type(self, node_type: str) -> list[Node]:
        return [n for n in self.nodes.values() if n.type == node_type]

    def edges_from(self, node_id: str, rel: str | None = None) -> list[Edge]:
        return [
            e for e in self.edges.values()
            if e.src == node_id and (rel is None or e.rel == rel)
        ]

    def edges_to(self, node_id: str, rel: str | None = None) -> list[Edge]:
        return [
            e for e in self.edges.values()
            if e.dst == node_id and (rel is None or e.rel == rel)
        ]


# ---------------------------------------------------------------------------
# Id helpers — stable ids so re-runs dedup instead of duplicating
# ---------------------------------------------------------------------------

def goal_id(question: str, explicit_id: str | None = None) -> str:
    if explicit_id:
        return f"{NodeType.GOAL}:{explicit_id}"
    return f"{NodeType.GOAL}:{_slug(question)}"


def hypothesis_id(statement: str, goal: str = "") -> str:
    return f"{NodeType.HYPOTHESIS}:{_slug(statement, salt=goal)}"


def result_ref_id(results_key: str) -> str:
    return f"{NodeType.RESULT_REF}:{_slug(results_key)}"


def finding_id(title: str) -> str:
    return f"{NodeType.FINDING}:{_slug(title)}"


# ---------------------------------------------------------------------------
# Paths + locking
# ---------------------------------------------------------------------------

def graph_path(ws_root: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(ws_root) / KNOWLEDGE_DIR / KG_GRAPH_FILE


@contextmanager
def _locked(ws_root: pathlib.Path, timeout: float = 10.0) -> Iterator[None]:
    """Atomic-create lockfile around the read-append cycle. Best-effort:
    on timeout it proceeds with a warning (each append is a single line
    write, so a contended append is still atomic at the OS level)."""
    kdir = pathlib.Path(ws_root) / KNOWLEDGE_DIR
    kdir.mkdir(parents=True, exist_ok=True)
    lock_path = kdir / KG_LOCK_FILE
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
# Op-log I/O
# ---------------------------------------------------------------------------

def _append_op(ws_root: pathlib.Path, op: dict[str, Any]) -> None:
    path = graph_path(ws_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(op, ensure_ascii=False) + "\n")


def load_graph(ws_root: pathlib.Path) -> Graph:
    """Fold the op-log into current state. Last upsert per id wins.

    Unknown/malformed lines are skipped rather than raising — the log is
    append-only and a partially-written final line (crash mid-write)
    must not brick the whole graph.
    """
    path = graph_path(ws_root)
    g = Graph()
    if not path.exists():
        return g
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            op = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = op.get("op")
        if kind == "upsert_node" and isinstance(op.get("node"), dict):
            node = Node.from_dict(op["node"])
            g.nodes[node.id] = node
        elif kind == "upsert_edge" and isinstance(op.get("edge"), dict):
            edge = Edge.from_dict(op["edge"])
            g.edges[edge.id] = edge
    return g


# ---------------------------------------------------------------------------
# Public API — mutation
# ---------------------------------------------------------------------------

def upsert_node(ws_root: pathlib.Path, node: Node) -> Node:
    """Insert or update a node. Merge-friendly: an existing node's
    created_at is preserved; updated_at is refreshed."""
    if node.type not in NODE_TYPES:
        raise ValueError(
            f"unknown node type {node.type!r}; expected one of {sorted(NODE_TYPES)}"
        )
    with _locked(ws_root):
        existing = load_graph(ws_root).nodes.get(node.id)
        now = _now()
        if existing is not None:
            node.created_at = existing.created_at or node.created_at or now
        else:
            node.created_at = node.created_at or now
        node.updated_at = now
        _append_op(ws_root, {"op": "upsert_node", "node": node.to_dict()})
    return node


def upsert_edge(ws_root: pathlib.Path, edge: Edge) -> Edge:
    if edge.rel not in EDGE_RELS:
        raise ValueError(
            f"unknown edge rel {edge.rel!r}; expected one of {sorted(EDGE_RELS)}"
        )
    edge.created_at = edge.created_at or _now()
    with _locked(ws_root):
        _append_op(ws_root, {"op": "upsert_edge", "edge": edge.to_dict()})
    return edge


def add_goal(
    ws_root: pathlib.Path,
    question: str,
    explicit_id: str | None = None,
    source_phase: str = "",
    origin: str = HypothesisOrigin.PREREGISTERED,
) -> Node:
    node = Node(
        id=goal_id(question, explicit_id),
        type=NodeType.GOAL,
        title=question,
        origin=origin,
        source_phase=source_phase,
    )
    return upsert_node(ws_root, node)


def add_hypothesis(
    ws_root: pathlib.Path,
    statement: str,
    goal_node_id: str,
    origin: str = HypothesisOrigin.PREREGISTERED,
    source_phase: str = "",
    source_stage: str = "",
    raised_by_finding: str | None = None,
    cheap_to_test: bool | None = None,
    surprise: str | None = None,
) -> Node:
    """Add a hypothesis node under a goal.

    On-the-fly hypotheses (origin=on_the_fly) are auto-flagged
    posthoc=True in extra so multiple-testing accounting can separate
    confirmatory from exploratory. If `raised_by_finding` is given, a
    `raises` edge finding→hypothesis is also written (the on-the-fly
    provenance path).

    `cheap_to_test` / `surprise` are OPTIONAL promotion signals (creativity
    audit F1). When provided they are recorded in `extra` so the post-run
    serendipity-promotion step can decide whether a supported on-the-fly
    hypothesis is worth promoting to a new ideated direction. Both default
    to None (omitted from extra) — every existing caller stays byte-
    identical. They are PURELY descriptive metadata: they never affect the
    cap, the goal attachment, the posthoc flag, the status, or any edge.
    """
    posthoc = origin == HypothesisOrigin.ON_THE_FLY
    extra: dict[str, Any] = {"goal_id": goal_node_id, "posthoc": posthoc}
    if cheap_to_test is not None:
        extra["cheap_to_test"] = bool(cheap_to_test)
    if surprise is not None:
        extra["surprise"] = str(surprise)
    node = Node(
        id=hypothesis_id(statement, goal_node_id),
        type=NodeType.HYPOTHESIS,
        title=statement,
        status=HypothesisStatus.PROPOSED,
        origin=origin,
        source_phase=source_phase,
        source_stage=source_stage,
        extra=extra,
    )
    upsert_node(ws_root, node)
    upsert_edge(ws_root, Edge(
        src=node.id, rel=EdgeRel.DERIVED_FROM, dst=goal_node_id,
        source_phase=source_phase,
    ))
    if raised_by_finding:
        upsert_edge(ws_root, Edge(
            src=raised_by_finding, rel=EdgeRel.RAISES, dst=node.id,
            source_phase=source_phase,
        ))
    return node


def add_finding(
    ws_root: pathlib.Path,
    title: str,
    body: str,
    evidence_paths: list[str],
    source_phase: str = "",
    source_stage: str = "",
    tags: list[str] | None = None,
) -> Node:
    node = Node(
        id=finding_id(title),
        type=NodeType.FINDING,
        title=title,
        body=body,
        evidence_paths=list(evidence_paths),
        source_phase=source_phase,
        source_stage=source_stage,
        tags=list(tags or []),
    )
    return upsert_node(ws_root, node)


def _evidence_resolves(ws_root: pathlib.Path, node: Node) -> bool:
    """True iff at least one of the node's evidence_paths points to a
    real, non-empty file *inside* the workspace.

    This is the teeth behind the honesty rule (belief 10): a hypothesis
    can only be marked supported/refuted when backed by such a file. The
    agent fully controls evidence_paths, so a bare `path.exists()` check
    is exploitable — it would accept the stage directory itself (always
    exists), a 0-byte placeholder, or an absolute path like
    `/etc/hostname`, licensing a `supported` verdict with no real result.
    We therefore require each accepted path to be:

      1. resolvable to a regular FILE (not a directory),
      2. NON-EMPTY (> 0 bytes),
      3. contained UNDER the workspace root (rejects absolute paths
         outside the workspace and `..` traversal escapes),
      4. located inside a numbered STAGE directory (``NNN_<phase>/...``)
         — i.e. a stage OUTPUT, not a workspace INPUT. Without (4) the
         agent could cite RESEARCH.md, omicairn_config.yaml, or an input
         data file (all real, non-empty, in-workspace) as "evidence" and
         move a hypothesis to supported with zero computation (audit H3).

    What this still cannot catch: a file that exists, is non-empty, and
    lives in a stage dir but whose *contents* don't actually support the
    claim. That is genuinely beyond a filesystem check — the cross_check
    layers and the analysis REVIEW verify content. This function closes the structural bypasses, not the
    semantic one.
    """
    try:
        root = pathlib.Path(ws_root).resolve()
    except (OSError, RuntimeError):
        return False
    for p in node.evidence_paths:
        cand = pathlib.Path(p)
        if not cand.is_absolute():
            cand = root / p
        try:
            resolved = cand.resolve()
        except (OSError, RuntimeError):
            continue
        # Must live under the workspace root (reject abs escapes + `..`).
        try:
            rel = resolved.relative_to(root)
        except ValueError:
            continue
        # Must live inside a numbered stage dir (a stage OUTPUT, not a
        # workspace input like RESEARCH.md / omicairn_config.yaml / data/).
        parts = rel.parts
        if len(parts) < 2 or not _is_stage_dir_name(parts[0]):
            continue
        # Must be a real, non-empty file.
        try:
            if resolved.is_file() and resolved.stat().st_size > 0:
                return True
        except OSError:
            continue
    return False


def _is_stage_dir_name(name: str) -> bool:
    """True if `name` is a numbered stage directory (``NNN_<something>``).
    Accepts any phase suffix (incl. standalone ideate/free), since the
    point is 'this is a stage OUTPUT directory', not 'a pipeline phase'."""
    from .constants import STAGE_INDEX_SEP, STAGE_INDEX_WIDTH
    if len(name) <= STAGE_INDEX_WIDTH:
        return False
    return (name[:STAGE_INDEX_WIDTH].isdigit()
            and name[STAGE_INDEX_WIDTH] == STAGE_INDEX_SEP)


def set_hypothesis_status(
    ws_root: pathlib.Path,
    hyp_id: str,
    new_status: str,
    superseded_by: str | None = None,
) -> Node:
    """Transition a hypothesis to a new lifecycle status.

    Enforces two rules:
      1. The transition must be legal per HYPOTHESIS_TRANSITIONS.
      2. supported/refuted require at least one incoming supports/refutes
         edge from a node whose evidence_paths resolve on disk (the
         mechanical honesty rule). A hypothesis cannot be declared
         supported on vibes.
    """
    g = load_graph(ws_root)
    node = g.nodes.get(hyp_id)
    if node is None:
        raise KeyError(f"no hypothesis node {hyp_id!r} in graph")
    if node.type != NodeType.HYPOTHESIS:
        raise ValueError(f"node {hyp_id!r} is a {node.type}, not a hypothesis")

    cur = node.status or HypothesisStatus.PROPOSED
    allowed = HYPOTHESIS_TRANSITIONS.get(cur, frozenset())
    if new_status != cur and new_status not in allowed:
        raise IllegalTransitionError(
            f"illegal hypothesis transition {cur!r} -> {new_status!r}; "
            f"allowed: {sorted(allowed)}"
        )

    if new_status in HYPOTHESIS_STATUSES_REQUIRING_EVIDENCE:
        want_rel = (
            EdgeRel.SUPPORTS if new_status == HypothesisStatus.SUPPORTED
            else EdgeRel.REFUTES
        )
        supporting = [
            e for e in g.edges_to(hyp_id, rel=want_rel)
        ]
        ok = any(
            (src := g.nodes.get(e.src)) is not None
            and _evidence_resolves(ws_root, src)
            for e in supporting
        )
        if not ok:
            raise HonestyRuleError(
                f"cannot set {hyp_id!r} to {new_status!r}: needs at least one "
                f"incoming {want_rel!r} edge from a node with a real, "
                f"non-empty, in-workspace evidence file (honesty rule). Add "
                f"the evidence node + edge first."
            )

    node.status = new_status
    if superseded_by:
        node.extra["superseded_by"] = superseded_by
    return upsert_node(ws_root, node)


# ---------------------------------------------------------------------------
# Public API — query + traversal
# ---------------------------------------------------------------------------

def get_node(ws_root: pathlib.Path, node_id: str) -> Node | None:
    return load_graph(ws_root).nodes.get(node_id)


def query_nodes(
    ws_root: pathlib.Path,
    node_type: str | None = None,
    status: str | None = None,
    origin: str | None = None,
    text: str | None = None,
) -> list[Node]:
    g = load_graph(ws_root)
    q = (text or "").lower().strip()
    out: list[Node] = []
    for n in g.nodes.values():
        if node_type is not None and n.type != node_type:
            continue
        if status is not None and n.status != status:
            continue
        if origin is not None and n.origin != origin:
            continue
        if q and q not in (n.title + " " + n.body).lower():
            continue
        out.append(n)
    out.sort(key=lambda n: n.created_at)
    return out


def hypotheses_for_goal(ws_root: pathlib.Path, goal_node_id: str) -> list[Node]:
    g = load_graph(ws_root)
    return [
        n for n in g.nodes.values()
        if n.type == NodeType.HYPOTHESIS
        and n.extra.get("goal_id") == goal_node_id
    ]


def ancestry(ws_root: pathlib.Path, node_id: str, rel: str = EdgeRel.DERIVED_FROM,
             max_depth: int = 50) -> list[str]:
    """Walk `rel` edges outward from node_id, returning the chain of dst
    ids in order. Cycle- and depth-guarded."""
    g = load_graph(ws_root)
    seen: set[str] = set()
    chain: list[str] = []
    cur = node_id
    for _ in range(max_depth):
        nexts = g.edges_from(cur, rel=rel)
        if not nexts:
            break
        dst = nexts[0].dst
        if dst in seen:
            break
        seen.add(dst)
        chain.append(dst)
        cur = dst
    return chain


def _norm_goal_node_id(goal_ref: str) -> str:
    return goal_ref if goal_ref.startswith(f"{NodeType.GOAL}:") else f"{NodeType.GOAL}:{goal_ref}"


def seed_goals(
    ws_root: pathlib.Path,
    goals: list[dict[str, Any]],
    source_phase: str = PhaseName.SCOPE,
) -> dict[str, int]:
    """Seed the graph with goal + pre-registered hypothesis nodes.

    Idempotent (stable node ids), so calling it at the start of every
    run is safe. `goals` is a list of dicts shaped like
    ``{"id": "g1", "question": "...", "hypotheses": ["H1...", ...]}``
    (the orchestrator converts research_md.Goal objects to this shape so
    knowledge_graph stays free of an upstream import).

    Returns {"goals": n, "hypotheses": n}.
    """
    n_goals = n_hyps = 0
    for g in goals:
        gid = str(g.get("id") or "g1")
        question = str(g.get("question") or "")
        node = Node(
            id=_norm_goal_node_id(gid),
            type=NodeType.GOAL,
            title=question,
            origin=HypothesisOrigin.PREREGISTERED,
            source_phase=source_phase,
        )
        upsert_node(ws_root, node)
        n_goals += 1
        for stmt in (g.get("hypotheses") or []):
            # CRITICAL: skip a hypothesis that already exists. seed_goals
            # runs at the START OF EVERY `omicairn run` (resume included), and
            # add_hypothesis always writes status=proposed — so re-seeding
            # would clobber a hypothesis a prior run advanced to
            # supported/refuted back to proposed, corrupting the durable
            # graph (belief 11) and the projection rendered from it. Mirrors the same guard in ingest_graph_updates step 3.
            if get_node(ws_root, hypothesis_id(str(stmt), node.id)) is not None:
                continue
            add_hypothesis(
                ws_root, str(stmt), node.id,
                origin=HypothesisOrigin.PREREGISTERED,
                source_phase=source_phase,
            )
            n_hyps += 1
    return {"goals": n_goals, "hypotheses": n_hyps}


def ingest_graph_updates(
    ws_root: pathlib.Path,
    updates: dict[str, Any],
    source_phase: str,
    source_stage: str,
    max_on_the_fly: int = 5,
) -> dict[str, Any]:
    """Fold a structured ``graph_updates.json`` payload (written by the
    EXECUTE/REVIEW agent) into the graph.

    This is the "assemble the knowledge base on the run" path (core
    belief 12): a single EXECUTE pass can register many result_refs,
    findings, and *on-the-fly* hypotheses, instead of testing one
    pre-registered hypothesis and discarding the rest of the signal.

    Payload shape (all keys optional)::

        {
          "result_refs": [
            {"key": "auc_cohortA", "title": "AUC=0.81", "value": 0.81,
             "evidence_paths": ["002_execute/results.json"],
             "hypothesis": "H1: X is prognostic", "goal_id": "g1",
             "verdict": <GraphUpdateVerdict: supports|refutes|inconclusive>}
          ],
          "findings": [
            {"title": "...", "body": "...",
             "evidence_paths": ["002_execute/results.json"], "tags": []}
          ],
          "on_the_fly_hypotheses": [
            {"statement": "...", "goal_id": "g1",
             "raised_by_finding": "<finding title>",
             "cheap_to_test": true}
          ]
        }

    The on_the_fly list is truncated to `max_on_the_fly`. Status
    advancement to supported/refuted goes through `set_hypothesis_status`,
    so the on-disk-evidence honesty rule is enforced — a result_ref must
    carry a resolvable evidence_path for its hypothesis to move.

    Returns counts + a `warnings` list (malformed entries are skipped,
    not fatal).
    """
    counts = {
        "result_refs": 0, "findings": 0, "on_the_fly": 0,
        "edges": 0, "status_advanced": 0,
    }
    warnings: list[str] = []
    finding_title_to_id: dict[str, str] = {}
    # Single global budget for on-the-fly hypotheses across BOTH the
    # explicit list and the auto-from-result_ref path. max_on_the_fly=0
    # (set by the orchestrator when allow_on_the_fly_hypotheses is false)
    # therefore suppresses every on-the-fly registration — this is the
    # lever that distinguishes the single-H control from dynamic-Hs.
    otf_used = 0

    # 1. Findings first (so on-the-fly raises edges can reference them).
    for i, f in enumerate(updates.get("findings", []) or []):
        if not isinstance(f, dict) or not f.get("title"):
            warnings.append(f"findings[{i}]: missing title; skipped")
            continue
        node = add_finding(
            ws_root, str(f["title"]), str(f.get("body", "")),
            evidence_paths=[str(p) for p in (f.get("evidence_paths") or [])],
            source_phase=source_phase, source_stage=source_stage,
            tags=[str(t) for t in (f.get("tags") or [])],
        )
        finding_title_to_id[str(f["title"])] = node.id
        counts["findings"] += 1

    # 2. Result refs + their tests/supports/refutes edges + status moves.
    for i, r in enumerate(updates.get("result_refs", []) or []):
        if not isinstance(r, dict) or not r.get("key"):
            warnings.append(f"result_refs[{i}]: missing key; skipped")
            continue
        ev = [str(p) for p in (r.get("evidence_paths") or [])]
        rnode = Node(
            id=result_ref_id(str(r["key"])),
            type=NodeType.RESULT_REF,
            title=str(r.get("title") or r["key"]),
            body=json.dumps(r.get("value")) if "value" in r else "",
            evidence_paths=ev,
            source_phase=source_phase, source_stage=source_stage,
        )
        upsert_node(ws_root, rnode)
        counts["result_refs"] += 1

        stmt = r.get("hypothesis")
        if not stmt:
            continue
        goal_node = _norm_goal_node_id(str(r.get("goal_id") or "g1"))
        hid = hypothesis_id(str(stmt), goal_node)
        if get_node(ws_root, hid) is None:
            # The result references a hypothesis we haven't seen — treat
            # it as on-the-fly so the signal isn't lost. Subject to the
            # on-the-fly budget; when exhausted (or 0), drop the linkage
            # so the single-H control never accrues exploratory edges.
            if otf_used >= max_on_the_fly:
                warnings.append(
                    f"result_refs[{i}] ({r['key']}): references unseen "
                    f"hypothesis but on-the-fly budget exhausted; "
                    f"result_ref kept, hypothesis linkage dropped"
                )
                continue
            add_hypothesis(
                ws_root, str(stmt), goal_node,
                origin=HypothesisOrigin.ON_THE_FLY,
                source_phase=source_phase, source_stage=source_stage,
            )
            otf_used += 1
            counts["on_the_fly"] += 1
        verdict = str(r.get("verdict") or "").lower()
        rel = (
            EdgeRel.SUPPORTS if verdict == GraphUpdateVerdict.SUPPORTS
            else EdgeRel.REFUTES if verdict == GraphUpdateVerdict.REFUTES
            else None
        )
        upsert_edge(ws_root, Edge(src=hid, rel=EdgeRel.TESTS, dst=rnode.id,
                                  source_phase=source_phase))
        counts["edges"] += 1
        if rel is not None:
            upsert_edge(ws_root, Edge(src=rnode.id, rel=rel, dst=hid,
                                      source_phase=source_phase))
            counts["edges"] += 1
        # Advance lifecycle: proposed -> testing -> supported/refuted.
        cur = get_node(ws_root, hid)
        if cur is not None and cur.status == HypothesisStatus.PROPOSED:
            set_hypothesis_status(ws_root, hid, HypothesisStatus.TESTING)
        target = (
            HypothesisStatus.SUPPORTED if verdict == GraphUpdateVerdict.SUPPORTS
            else HypothesisStatus.REFUTED if verdict == GraphUpdateVerdict.REFUTES
            else HypothesisStatus.INCONCLUSIVE if verdict == GraphUpdateVerdict.INCONCLUSIVE
            else None
        )
        if target is not None:
            try:
                set_hypothesis_status(ws_root, hid, target)
                counts["status_advanced"] += 1
            except HonestyRuleError as e:
                # No resolvable on-disk evidence — keep the hypothesis in
                # `testing` and record the honesty refusal.
                warnings.append(f"result_refs[{i}] ({r['key']}) honesty: {e}")
            except IllegalTransitionError as e:
                # A real state conflict (e.g. a prior stage already
                # resolved this hypothesis to a terminal/other status).
                # Distinct from an honesty failure — label it accurately
                # so the operator doesn't chase a phantom evidence gap.
                warnings.append(
                    f"result_refs[{i}] ({r['key']}) transition conflict: {e}"
                )

    # 3. On-the-fly hypotheses (share the global otf budget with the
    #    auto-from-result_ref path in step 2).
    otf = updates.get("on_the_fly_hypotheses", []) or []
    n_requested = sum(
        1 for h in otf if isinstance(h, dict) and h.get("statement")
    )
    for i, h in enumerate(otf):
        if not isinstance(h, dict) or not h.get("statement"):
            warnings.append(f"on_the_fly_hypotheses[{i}]: missing statement; skipped")
            continue
        if otf_used >= max_on_the_fly:
            warnings.append(
                f"on_the_fly_hypotheses: {n_requested} requested, capped at "
                f"{max_on_the_fly}; extras dropped"
            )
            break
        goal_node = _norm_goal_node_id(str(h.get("goal_id") or "g1"))
        raised_by = h.get("raised_by_finding")
        raised_id = finding_title_to_id.get(str(raised_by)) if raised_by else None
        existing_id = hypothesis_id(str(h["statement"]), goal_node)
        if get_node(ws_root, existing_id) is not None:
            # Already registered — typically because a result_ref in
            # step 2 introduced and advanced this same hypothesis
            # (supported/refuted). Re-running add_hypothesis here would
            # reset its status back to `proposed` and clobber that
            # advancement. Instead, only add the `raises` provenance
            # edge (if a finding raised it) and move on; don't recount
            # against the budget (step 2 already did).
            if raised_id is not None:
                upsert_edge(ws_root, Edge(
                    src=raised_id, rel=EdgeRel.RAISES, dst=existing_id,
                    source_phase=source_phase,
                ))
            continue
        # Thread the OPTIONAL promotion signals (creativity audit F1) from
        # the payload onto the node's extra, so the post-run serendipity-
        # promotion step can read them. Absent fields stay None → omitted
        # from extra (back-compat). Purely descriptive; does not touch the
        # cap, the budget accounting, or the lifecycle.
        ctt = h.get("cheap_to_test")
        srp = h.get("surprise")
        add_hypothesis(
            ws_root, str(h["statement"]), goal_node,
            origin=HypothesisOrigin.ON_THE_FLY,
            source_phase=source_phase, source_stage=source_stage,
            raised_by_finding=raised_id,
            cheap_to_test=bool(ctt) if ctt is not None else None,
            surprise=str(srp) if srp is not None else None,
        )
        otf_used += 1
        counts["on_the_fly"] += 1

    counts["warnings"] = warnings
    return counts


def stats(ws_root: pathlib.Path) -> dict[str, Any]:
    """Summary counts for `omicairn` CLI / comparison harness."""
    g = load_graph(ws_root)
    by_type: dict[str, int] = {}
    for n in g.nodes.values():
        by_type[n.type] = by_type.get(n.type, 0) + 1
    hyps = g.nodes_of_type(NodeType.HYPOTHESIS)
    by_status: dict[str, int] = {}
    for h in hyps:
        s = h.status or HypothesisStatus.PROPOSED
        by_status[s] = by_status.get(s, 0) + 1
    n_onthefly = sum(
        1 for h in hyps if h.origin == HypothesisOrigin.ON_THE_FLY
    )
    n_tested = sum(
        1 for h in hyps if g.edges_from(h.id, rel=EdgeRel.TESTS)
    )
    by_rel: dict[str, int] = {}
    for e in g.edges.values():
        by_rel[e.rel] = by_rel.get(e.rel, 0) + 1
    return {
        "nodes": len(g.nodes),
        "edges": len(g.edges),
        "nodes_by_type": by_type,
        "hypotheses_by_status": by_status,
        "hypotheses_on_the_fly": n_onthefly,
        "hypotheses_tested": n_tested,
        "edges_by_rel": by_rel,
    }


# ---------------------------------------------------------------------------
# Graph projection — the read side of the knowledge graph
# ---------------------------------------------------------------------------

def paper_projection(ws_root: pathlib.Path) -> dict[str, Any]:
    """Return the structured slice of the graph that summarises a run's
    goals, hypotheses and evidence (core belief 11).

    Shape::

        {
          "goals": [
            {"id": "goal:g1", "question": "...",
             "hypotheses": [
               {"id": "...", "statement": "...", "status": "supported",
                "origin": "preregistered", "posthoc": false,
                "evidence": [
                  {"result_ref": "result_ref:auc", "title": "AUC=0.81",
                   "rel": "supports",
                   "evidence_paths": ["002_execute/results.json"]}
                ]}
             ]}
          ],
          "unaffiliated_findings": [ {finding dicts not tied to a goal} ],
          "counts": {"supported": n, "refuted": n, "inconclusive": n,
                     "testing": n, "proposed": n,
                     "on_the_fly": n, "preregistered": n}
        }

    Hypotheses are ordered so the strongest results lead: supported,
    refuted, inconclusive, testing, proposed. Within a status,
    pre-registered before on-the-fly (confirmatory results lead the
    listing; exploratory follow), so a reader sees resolved hypotheses
    and their effect sizes first.
    """
    g = load_graph(ws_root)
    _status_rank = {
        HypothesisStatus.SUPPORTED: 0,
        HypothesisStatus.REFUTED: 1,
        HypothesisStatus.INCONCLUSIVE: 2,
        HypothesisStatus.TESTING: 3,
        HypothesisStatus.PROPOSED: 4,
        HypothesisStatus.SUPERSEDED: 5,
    }

    def _hyp_evidence(hyp_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for rel in (EdgeRel.SUPPORTS, EdgeRel.REFUTES):
            for e in g.edges_to(hyp_id, rel=rel):
                src = g.nodes.get(e.src)
                if src is None:
                    continue
                out.append({
                    "result_ref": src.id,
                    "title": src.title,
                    "rel": rel,
                    "evidence_paths": list(src.evidence_paths),
                })
        return out

    def _hyp_dict(h: Node) -> dict[str, Any]:
        return {
            "id": h.id,
            "statement": h.title,
            "status": h.status or HypothesisStatus.PROPOSED,
            "origin": h.origin,
            "posthoc": bool(h.extra.get("posthoc", False)),
            "evidence": _hyp_evidence(h.id),
        }

    def _hyp_sort_key(h: Node):
        origin_rank = 0 if h.origin != HypothesisOrigin.ON_THE_FLY else 1
        return (_status_rank.get(h.status or HypothesisStatus.PROPOSED, 9),
                origin_rank, h.created_at)

    goals_out: list[dict[str, Any]] = []
    for goal in sorted(g.nodes_of_type(NodeType.GOAL), key=lambda n: n.created_at):
        # Skip un-adopted IDEATE directions: they are proposed brainstorm
        # candidates (kind=="direction", adopted=False), NOT run goals.
        # Leaking them here injected rejected directions into the
        # projection as goals (audit H1). Adopted directions set
        # adopted=True (adopt_direction) and correctly remain.
        if goal.extra.get("kind") == "direction" and not goal.extra.get("adopted"):
            continue
        hyps = [
            n for n in g.nodes.values()
            if n.type == NodeType.HYPOTHESIS
            and n.extra.get("goal_id") == goal.id
        ]
        hyps.sort(key=_hyp_sort_key)
        goals_out.append({
            "id": goal.id,
            "question": goal.title,
            "hypotheses": [_hyp_dict(h) for h in hyps],
        })

    # Findings not reachable from any hypothesis via a raises edge are
    # "unaffiliated" — still worth surfacing (a signal nobody turned into
    # a hypothesis yet). A finding is affiliated if it raises >=1 hyp.
    affiliated: set[str] = {
        e.src for e in g.edges.values() if e.rel == EdgeRel.RAISES
    }
    unaffiliated = [
        {"id": f.id, "title": f.title, "body": f.body,
         "evidence_paths": list(f.evidence_paths)}
        for f in g.nodes_of_type(NodeType.FINDING)
        if f.id not in affiliated
    ]

    all_hyps = g.nodes_of_type(NodeType.HYPOTHESIS)
    counts = {
        "supported": sum(1 for h in all_hyps if h.status == HypothesisStatus.SUPPORTED),
        "refuted": sum(1 for h in all_hyps if h.status == HypothesisStatus.REFUTED),
        "inconclusive": sum(1 for h in all_hyps if h.status == HypothesisStatus.INCONCLUSIVE),
        "testing": sum(1 for h in all_hyps if h.status == HypothesisStatus.TESTING),
        "proposed": sum(1 for h in all_hyps if (h.status or HypothesisStatus.PROPOSED) == HypothesisStatus.PROPOSED),
        "on_the_fly": sum(1 for h in all_hyps if h.origin == HypothesisOrigin.ON_THE_FLY),
        "preregistered": sum(1 for h in all_hyps if h.origin == HypothesisOrigin.PREREGISTERED),
    }
    return {"goals": goals_out, "unaffiliated_findings": unaffiliated,
            "counts": counts}


def render_paper_projection_md(ws_root: pathlib.Path) -> str:
    """Render `paper_projection` as a markdown block for the phase prompts
    ({{knowledge_graph_projection}} token). Returns "(knowledge graph is
    empty)" when there are no goals, so the token is always substitutable.

    Each resolved hypothesis is rendered with its status, a post-hoc tag
    when exploratory, and its evidence file paths — so agents can point at
    the on-disk evidence and the human can see confirmatory vs. exploratory
    at a glance.
    """
    proj = paper_projection(ws_root)
    if not proj["goals"]:
        return "(knowledge graph is empty)"
    c = proj["counts"]
    lines: list[str] = [
        f"_Graph summary: {c['supported']} supported, {c['refuted']} refuted, "
        f"{c['inconclusive']} inconclusive, {c['testing']} testing, "
        f"{c['proposed']} proposed · {c['preregistered']} pre-registered, "
        f"{c['on_the_fly']} on-the-fly (post-hoc)._",
        "",
    ]
    for goal in proj["goals"]:
        lines.append(f"### Goal {goal['id'].split(':', 1)[-1]}: {goal['question']}")
        if not goal["hypotheses"]:
            lines.append("- (no hypotheses recorded)")
            continue
        for h in goal["hypotheses"]:
            tag = " _(post-hoc/exploratory)_" if h["posthoc"] else ""
            lines.append(f"- **[{h['status']}]** {h['statement']}{tag}")
            for ev in h["evidence"]:
                paths = ", ".join(ev["evidence_paths"]) or "(no path)"
                lines.append(
                    f"    - {ev['rel']}: {ev['title']} — `{paths}`"
                )
    if proj["unaffiliated_findings"]:
        lines.append("")
        lines.append("### Unaffiliated findings (signal not yet tied to a hypothesis)")
        for f in proj["unaffiliated_findings"]:
            paths = ", ".join(f["evidence_paths"]) or "(no path)"
            lines.append(f"- {f['title']} — `{paths}`")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# IDEATE — sources + proposed research directions (the brainstorm stage)
# ---------------------------------------------------------------------------

def source_id(title: str) -> str:
    return f"{NodeType.SOURCE}:{_slug(title)}"


def add_source(
    ws_root: pathlib.Path,
    title: str,
    source_type: str = "other",
    url: str = "",
    author: str = "",
    year: int | None = None,
    summary: str = "",
    evidence_paths: list[str] | None = None,
    source_phase: str = PhaseName.IDEATE,
    source_stage: str = "",
    verified: bool = False,
    verification_note: str = "",
    credibility: str = "",
) -> Node:
    """Add an external information source node (paper / blog / stated
    belief). `source_type` is one of SOURCE_TYPES (validated leniently:
    unknown values fall back to 'other' so a typo doesn't lose the node).

    `verified` / `verification_note` / `credibility` record the IDEATE
    anti-fabrication check (was the source actually resolved + matched on
    the web, and how reliable is it) so downstream stages and the
    bibliography bridge can distrust unverified/low-credibility sources.
    """
    from .constants import SOURCE_TYPES, SourceType
    st = source_type if source_type in SOURCE_TYPES else SourceType.OTHER
    node = Node(
        id=source_id(title),
        type=NodeType.SOURCE,
        title=title,
        body=summary,
        source_phase=source_phase,
        source_stage=source_stage,
        evidence_paths=list(evidence_paths or []),
        extra={
            "source_type": st,
            "url": url,
            "author": author,
            "year": year,
            "verified": bool(verified),
            "verification_note": verification_note,
            "credibility": credibility or "unrated",
        },
    )
    return upsert_node(ws_root, node)


def ingest_ideation(
    ws_root: pathlib.Path,
    payload: dict[str, Any],
    source_phase: str = PhaseName.IDEATE,
    source_stage: str = "",
) -> dict[str, Any]:
    """Fold an IDEATE `directions.json` payload into the graph.

    A proposed research DIRECTION is stored as a `goal` node with
    origin='ideated' and extra.kind='direction' (status not yet active —
    extra.adopted=False). Its seed hypotheses live in
    extra.seed_hypotheses as plain strings until the direction is
    adopted (avoids polluting the hypothesis space with un-chosen
    candidates). Each direction `cites` the source nodes that motivated
    it, so every proposal is traceable to its provenance.

    Payload (all keys optional)::

        {
          "sources": [
            {"title","source_type","url","author","year","summary",
             "evidence_paths"}
          ],
          "directions": [
            {"id","title","question","rationale","hypotheses":[...],
             "cites":[<source title>...],
             "novelty":"high|medium|low","feasibility":"..."}
          ]
        }

    Returns counts + warnings (malformed entries skipped, not fatal).
    """
    counts = {"sources": 0, "directions": 0, "cite_edges": 0}
    warnings: list[str] = []
    title_to_source_id: dict[str, str] = {}

    for i, s in enumerate(payload.get("sources", []) or []):
        if not isinstance(s, dict) or not s.get("title"):
            warnings.append(f"sources[{i}]: missing title; skipped")
            continue
        yr = s.get("year")
        node = add_source(
            ws_root, str(s["title"]),
            source_type=str(s.get("source_type") or "other"),
            url=str(s.get("url") or ""), author=str(s.get("author") or ""),
            year=int(yr) if isinstance(yr, (int, float)) or
            (isinstance(yr, str) and yr.isdigit()) else None,
            summary=str(s.get("summary") or ""),
            evidence_paths=[str(p) for p in (s.get("evidence_paths") or [])],
            source_phase=source_phase, source_stage=source_stage,
            verified=bool(s.get("verified", False)),
            verification_note=str(s.get("verification_note") or ""),
            credibility=str(s.get("credibility") or "unrated"),
        )
        title_to_source_id[str(s["title"])] = node.id
        counts["sources"] += 1

    for i, d in enumerate(payload.get("directions", []) or []):
        if not isinstance(d, dict) or not (d.get("question") or d.get("title")):
            warnings.append(f"directions[{i}]: missing question/title; skipped")
            continue
        did = str(d.get("id") or "")
        question = str(d.get("question") or d.get("title"))
        gid = goal_id(question, explicit_id=did or None)
        seeds = [str(h) for h in (d.get("hypotheses") or [])]
        node = Node(
            id=gid, type=NodeType.GOAL,
            title=question,
            body=str(d.get("rationale") or ""),
            origin=HypothesisOrigin.ON_THE_FLY,  # ideated, not pre-registered
            source_phase=source_phase, source_stage=source_stage,
            extra={
                "kind": "direction",
                "label": str(d.get("title") or question)[:120],
                "rationale": str(d.get("rationale") or ""),
                "novelty": str(d.get("novelty") or "unrated"),
                "feasibility": str(d.get("feasibility") or "unrated"),
                "seed_hypotheses": seeds,
                "adopted": False,
            },
        )
        upsert_node(ws_root, node)
        counts["directions"] += 1
        for ct in (d.get("cites") or []):
            sid = title_to_source_id.get(str(ct)) or source_id(str(ct))
            if get_node(ws_root, sid) is None:
                warnings.append(
                    f"directions[{i}] cites unknown source {ct!r}; "
                    f"edge written but source node absent"
                )
            upsert_edge(ws_root, Edge(
                src=gid, rel=EdgeRel.CITES, dst=sid, source_phase=source_phase,
            ))
            counts["cite_edges"] += 1

    counts["warnings"] = warnings
    return counts


def proposed_directions(ws_root: pathlib.Path) -> list[dict[str, Any]]:
    """Return ideated directions (goal nodes with extra.kind=='direction'),
    each with its sources (resolved title+type), seed hypotheses, ratings,
    and adopted flag. Ordered: un-adopted first, then by novelty."""
    g = load_graph(ws_root)
    _nov = {"high": 0, "medium": 1, "low": 2, "unrated": 3}
    out: list[dict[str, Any]] = []
    for n in g.nodes_of_type(NodeType.GOAL):
        if n.extra.get("kind") != "direction":
            continue
        cites = []
        for e in g.edges_from(n.id, rel=EdgeRel.CITES):
            s = g.nodes.get(e.dst)
            cites.append({
                "id": e.dst,
                "title": s.title if s else e.dst,
                "source_type": (s.extra.get("source_type") if s else "unknown"),
            })
        out.append({
            "id": n.id,
            "label": n.extra.get("label", n.title),
            "question": n.title,
            "rationale": n.extra.get("rationale", ""),
            "novelty": n.extra.get("novelty", "unrated"),
            "feasibility": n.extra.get("feasibility", "unrated"),
            "seed_hypotheses": list(n.extra.get("seed_hypotheses", [])),
            "adopted": bool(n.extra.get("adopted", False)),
            "sources": cites,
        })
    out.sort(key=lambda d: (d["adopted"], _nov.get(d["novelty"], 3)))
    return out


def adopt_direction(ws_root: pathlib.Path, direction_id: str) -> Node:
    """Promote a proposed direction to an active goal: mark it adopted and
    materialize its seed hypotheses as real (pre-registered) hypothesis
    nodes under it. Returns the goal node. Idempotent."""
    g = load_graph(ws_root)
    # Accept a bare id or a full goal:<...> id.
    node = g.nodes.get(direction_id) or g.nodes.get(f"{NodeType.GOAL}:{direction_id}")
    if node is None:
        raise KeyError(f"no direction {direction_id!r} in graph")
    if node.extra.get("kind") != "direction":
        raise ValueError(f"node {node.id!r} is not an ideated direction")
    node.extra["adopted"] = True
    upsert_node(ws_root, node)
    for stmt in node.extra.get("seed_hypotheses", []):
        if get_node(ws_root, hypothesis_id(str(stmt), node.id)) is None:
            add_hypothesis(
                ws_root, str(stmt), node.id,
                origin=HypothesisOrigin.PREREGISTERED,
                source_phase=PhaseName.IDEATE,
            )
    return node


def render_directions_md(ws_root: pathlib.Path) -> str:
    """Human-readable proposed-directions brief for the IDEATE CLI and for
    injection into SCOPE. Returns a sentinel when none exist."""
    dirs = proposed_directions(ws_root)
    if not dirs:
        return "(no ideated directions yet)"
    lines: list[str] = []
    for d in dirs:
        flag = " ✓ADOPTED" if d["adopted"] else ""
        short_id = d["id"].split(":", 1)[-1]
        lines.append(
            f"### [{short_id}]{flag} {d['label']}"
        )
        lines.append(f"- **Question:** {d['question']}")
        if d["rationale"]:
            lines.append(f"- **Why:** {d['rationale']}")
        lines.append(
            f"- **Novelty:** {d['novelty']}  ·  **Feasibility:** {d['feasibility']}"
        )
        if d["seed_hypotheses"]:
            lines.append("- **Seed hypotheses:**")
            for h in d["seed_hypotheses"]:
                lines.append(f"    - {h}")
        if d["sources"]:
            srcs = ", ".join(
                f"{s['title']} ({s['source_type']})" for s in d["sources"]
            )
            lines.append(f"- **Grounded in:** {srcs}")
        lines.append("")
    return "\n".join(lines).rstrip()
