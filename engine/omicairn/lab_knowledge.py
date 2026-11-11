"""Cross-workspace LAB knowledge base — the lab's compounding memory.

Each OmiCairn workspace owns a per-run knowledge graph (`knowledge/graph.jsonl`).
That makes a single run's knowledge durable, but every new run still
starts blank — IDEATE re-proposes directions the lab already pursued, and
SCOPE can't see what's been supported, refuted, or which datasets the lab
holds. This module closes that gap (core belief 11, across workspaces):

  - `distill_workspace(ws_root, uri_base=...)` reads a finished
    workspace's graph and DISTILLS its durable items — supported/refuted/
    inconclusive hypotheses, findings, dataset pointers, and adopted
    research directions — into an append-only, URI-indexed lab store.
  - Each entry carries an `evidence_uris` list and a `source_uri`
    pointing back to WHERE the result lives: `file://` paths locally, or
    `s3://<bucket>/<prefix>/...` when an `uri_base` is supplied. This is
    the "index to previously-run results".

    NOTE: `uri_base` only RECORDS the URI string — OmiCairn does not upload
    anything. Storage stays LOCAL; if you want an s3:// index to resolve,
    you archive the workspace to S3 yourself. An actual uploader is a
    future bonus, deliberately NOT implemented.
  - `render_lab_knowledge_md()` surfaces it into IDEATE/SCOPE prompts so
    a new run sees the accumulated body and builds on it.

Storage mirrors the existing flat-file discipline (no database): a single
append-only JSONL op-log, folded to current state on read; an atomic-create
lockfile guards the read-append cycle. Location is resolved by
`resolve_root()` (highest precedence first): an explicit path (CLI
`--root`), the `$OMICAIRN_LAB_KB` env var, or — by default — a VISIBLE
`lab_knowledge/` folder beside the run workspaces
(`<workspaces_root>/lab_knowledge/`), created on demand during a run. The
old hidden `~/.cache` location is gone.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import time
import urllib.parse
from contextlib import contextmanager
from typing import Any, Iterator

from .constants import (
    EdgeRel,
    HypothesisOrigin,
    HypothesisStatus,
    LAB_KB_DIRNAME,
    LAB_KB_FILE,
    LAB_KB_INDEX_FILE,
    LAB_KB_LOCK,
    ENV_LAB_KB,
    NodeType,
    SERENDIPITY_DIRECTION_ORIGIN,
)
from . import knowledge_graph as kg


_DISTILLED_STATUSES = frozenset({
    HypothesisStatus.SUPPORTED, HypothesisStatus.REFUTED,
    HypothesisStatus.INCONCLUSIVE,
})

# Surprise ratings that count as "high-surprise" for serendipity promotion
# (creativity audit F1). A supported on-the-fly hypothesis is promotable if
# it is cheap-to-test OR carries one of these surprise ratings. Conservative:
# only an explicit "high" qualifies on the surprise axis (medium/low/unrated
# do not), so promotion stays selective.
_SURPRISE_PROMOTABLE = frozenset({"high"})


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def resolve_root(
    explicit: str | pathlib.Path | None = None,
    workspaces_root: str | pathlib.Path | None = None,
) -> pathlib.Path:
    """Resolve the lab knowledge base directory.

    Precedence (highest first):
      1. ``explicit`` — a path passed in directly (e.g. CLI ``--root``).
      2. ``$OMICAIRN_LAB_KB`` — the env override.
      3. ``<workspaces_root>/lab_knowledge`` — the default: a VISIBLE
         folder beside the run workspaces. The orchestrator passes the
         absolute workspaces root (``ws.root.parent``) so this is
         cwd-independent during a run.
      4. ``<cwd>/workspaces/lab_knowledge`` — last-resort default for the
         CLI when no workspace context is available (matches the
         conventional layout when invoked from the repo root).

    The directory is NOT created here (resolution is side-effect free);
    writers create it on demand via ``_locked`` / ``_append``.
    """
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()
    override = os.environ.get(ENV_LAB_KB)
    if override:
        return pathlib.Path(override).expanduser().resolve()
    if workspaces_root is not None:
        return (pathlib.Path(workspaces_root) / LAB_KB_DIRNAME).resolve()
    return (pathlib.Path.cwd() / "workspaces" / LAB_KB_DIRNAME).resolve()


def default_root() -> pathlib.Path:
    """Backward-compatible parameterless resolver (env or cwd default)."""
    return resolve_root()


def _store_path(root: pathlib.Path | None = None) -> pathlib.Path:
    return (root or default_root()) / LAB_KB_FILE


@contextmanager
def _locked(
    root: pathlib.Path, timeout: float = 10.0, strict: bool = False
) -> Iterator[None]:
    """Hold the lab-KB write lock for the read-append (or rewrite) cycle.

    Atomic-create (``O_CREAT | O_EXCL``) sentinel, PID-tagged on
    acquisition so a peer / compaction can tell a LIVE holder from a
    crashed one (``omicairn.lock.inspect``). ``strict=False`` (distill's
    default) proceeds after ``timeout`` so a stale lock can't wedge the
    pipeline; ``strict=True`` (compaction) RAISES ``TimeoutError`` instead
    — a rewrite must never interleave with a concurrent writer.
    """
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / LAB_KB_LOCK
    start = time.monotonic()
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() - start > timeout:
                if strict:
                    raise TimeoutError(
                        f"lab-KB lock {lock_path} held by another writer; "
                        f"refusing after {timeout}s")
                fd = -1
                break
            time.sleep(0.05)
    if fd is not None and fd >= 0:
        # Tag the lock with our PID so a peer (or a compaction safety
        # check) can distinguish a LIVE holder from a crashed one.
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
# URI helpers — the index back to where a result lives
# ---------------------------------------------------------------------------

def _path_to_uri(rel_path: str, ws_root: pathlib.Path, uri_base: str | None) -> str:
    """Map a workspace-relative artifact path to a locating URI.

    Default → a local `file://` URI under the workspace. When `uri_base`
    is given (e.g. ``s3://bucket/omicairn/<run>``) the path is appended to it,
    so distilling an archived/uploaded run indexes S3 URIs instead.

    Pass-through: an evidence_path that is ALREADY an absolute URI (it has
    a URL scheme, e.g. the ``https://``/``s3://`` location of a probed
    dataset asset) is a locator in its own right — return it verbatim
    rather than re-rooting it under the workspace. Detected structurally
    via ``urlsplit().scheme`` (no string matching).
    """
    if urllib.parse.urlsplit(rel_path).scheme:
        return rel_path
    rel = rel_path.lstrip("/")
    if uri_base:
        return uri_base.rstrip("/") + "/" + rel
    return (ws_root / rel).resolve().as_uri()


def _entry_key(kind: str, title: str, workspace: str) -> str:
    return f"{kind}:{kg._slug(title)}:{workspace}"


# Fields that are volatile per-distill and carry NO knowledge — they must be
# excluded when deciding whether a re-distilled entry is "the same knowledge"
# as the one already folded in the op-log. `distilled_at` is `_now()` stamped
# on EVERY distill, so comparing raw dicts would make every entry look changed
# and defeat the delta-append. Everything else (kind/workspace/topic_slug/
# title/status/origin/summary/evidence_uris/source_uri/key) is the meaningful
# content that `_fold`/`load` and all consumers read, so it stays in the key.
_VOLATILE_ENTRY_FIELDS = frozenset({"distilled_at"})


def _entry_identity(entry: dict[str, Any]) -> dict[str, Any]:
    """The knowledge-bearing projection of an entry (volatile fields dropped).

    Two entries are "semantically identical" iff their identities are equal.
    Used by the distill delta-append to skip re-writing unchanged knowledge.
    """
    return {k: v for k, v in entry.items() if k not in _VOLATILE_ENTRY_FIELDS}


# ---------------------------------------------------------------------------
# Store I/O (append-only op-log, folded on read)
# ---------------------------------------------------------------------------

def load(root: pathlib.Path | None = None) -> dict[str, dict[str, Any]]:
    """Fold the op-log into {key: entry}. Last write per key wins."""
    path = _store_path(root)
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(e, dict) and e.get("key"):
            out[e["key"]] = e
    return out


def _append(root: pathlib.Path, entries: list[dict[str, Any]]) -> None:
    path = _store_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _count_raw_lines(path: pathlib.Path) -> int:
    """Count non-blank JSON lines in the op-log (the raw, pre-fold size)."""
    if not path.exists():
        return 0
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            n += 1
    return n


def compact(
    root: pathlib.Path | None = None,
    apply: bool = False,
    stamp: str | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Fold-and-rewrite the append-only op-log to one line per unique key.

    ``distill_workspace`` re-appends ALL of a workspace's entries on every
    distill, so the file grows O(stages x keys) while ``load()`` folds it
    back to the current entries on read. This reclaims that redundancy
    WITHOUT changing what consumers see: it rewrites the file to the folded
    current state (latest ``distilled_at`` per key, exactly what ``load()``
    returns — no divergent reimplementation of the fold).

    Safety rails (maintenance tool, NOT a live mutation):
      - ``apply=False`` (DEFAULT): dry run — report raw->folded counts +
        redundancy, change NOTHING.
      - ``apply=True`` REFUSES if a LIVE pid holds the lab-KB lock (a
        concurrent distill); acquires the SAME ``_locked`` (strict) lock so
        a distill can't interleave; writes a timestamped ``.bak``; rewrites
        via temp+fsync+``os.replace`` (atomic); then re-``load()``s and
        asserts the folded state is IDENTICAL — on any divergence it
        RESTORES from ``.bak`` and raises.
      - already-compact (raw <= folded) is a no-op.

    Returns ``{raw_lines, folded_keys, redundancy, already_compact,
    applied, backup_path}`` (redundancy = raw/folded; 1.0 == compact).
    """
    from . import lock as _lock

    root = (root or default_root())
    path = _store_path(root)
    lock_path = root / LAB_KB_LOCK

    # Fold via the SAME logic consumers use — never a divergent reimpl.
    folded_before = load(root)
    raw_lines = _count_raw_lines(path)
    folded_keys = len(folded_before)
    redundancy = (raw_lines / folded_keys) if folded_keys else 1.0
    already_compact = raw_lines <= folded_keys

    report: dict[str, Any] = {
        "root": str(root), "store": str(path), "raw_lines": raw_lines,
        "folded_keys": folded_keys, "redundancy": round(redundancy, 2),
        "already_compact": already_compact, "applied": False,
        "backup_path": None,
    }
    if not apply or already_compact:
        return report

    # MANDATORY safety rail: refuse if a LIVE pid holds the lab-KB lock (a
    # concurrent distill / another compaction). PID-tagged on acquisition, so
    # a LIVE holder is detectable; a crashed holder must NOT block us forever.
    held = _lock.inspect(lock_path)
    if held.holder_alive:
        raise RuntimeError(
            f"lab-KB lock {lock_path} held by LIVE pid {held.holder_pid}; "
            f"a OmiCairn run is actively writing. Refusing to compact. Re-run "
            f"--apply when the fleet is quiet.")
    # Holder (if any) is dead/empty → a crashed writer's stale lock. The
    # plain _locked uses O_EXCL on file EXISTENCE, so a stale lock would wedge
    # strict acquisition; reclaim it (force_clear refuses a live holder, so
    # this can never steal an active lock).
    if lock_path.exists():
        _lock.force_clear(lock_path)

    stamp = stamp or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with _locked(root, timeout=timeout, strict=True):
        # Snapshot the pre-compaction op-log so a bad rewrite is recoverable.
        backup = path.with_name(f"{LAB_KB_FILE}.{stamp}.bak")
        backup.write_bytes(path.read_bytes() if path.exists() else b"")
        report["backup_path"] = str(backup)

        # Atomic rewrite: one folded line per key -> temp -> fsync -> replace.
        tmp = path.with_name(f"{LAB_KB_FILE}.{stamp}.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for e in folded_before.values():
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(path))

        # Verify-after: re-fold and assert the folded state is IDENTICAL.
        # This is the load-bearing correctness guard — compaction MUST NOT
        # change what consumers see. On ANY divergence, restore + raise.
        folded_after = load(root)
        if folded_after != folded_before:
            path.write_bytes(backup.read_bytes())  # restore the op-log
            raise RuntimeError(
                "compaction changed the folded state (keys or values "
                f"diverged: {len(folded_before)} -> {len(folded_after)}); "
                f"restored op-log from {backup}. No data lost.")
        # Keep the index in sync with the rewritten store.
        _write_index(root, folded_after)

    report["applied"] = True
    report["raw_lines_after"] = _count_raw_lines(path)
    return report


# ---------------------------------------------------------------------------
# Distillation
# ---------------------------------------------------------------------------

def distill_workspace(
    ws_root: pathlib.Path,
    topic_slug: str = "",
    uri_base: str | None = None,
    root: pathlib.Path | None = None,
) -> dict[str, int]:
    """Distill a workspace's knowledge graph into the lab store.

    Pulls: resolved hypotheses (supported/refuted/inconclusive) with their
    supporting/refuting evidence URIs; findings; dataset_ref nodes; and
    adopted IDEATE directions. Re-distilling the same workspace updates its
    entries in place (dedup key includes the workspace), while other
    workspaces' same-title items coexist — so the lab can see "supported
    in run A, refuted in run B". Returns counts by kind.
    """
    ws_root = pathlib.Path(ws_root)
    g = kg.load_graph(ws_root)
    ws_name = ws_root.name
    source_uri = _path_to_uri("", ws_root, uri_base).rstrip("/")
    root = (root or default_root())

    def _evidence_uris(node_id: str) -> list[str]:
        uris: list[str] = []
        for rel in (EdgeRel.SUPPORTS, EdgeRel.REFUTES, EdgeRel.TESTS):
            for e in g.edges_to(node_id, rel=rel) + g.edges_from(node_id, rel=rel):
                src = g.nodes.get(e.src) or g.nodes.get(e.dst)
                if src is None:
                    continue
                for p in src.evidence_paths:
                    uris.append(_path_to_uri(p, ws_root, uri_base))
        return sorted(set(uris))

    entries: list[dict[str, Any]] = []
    counts = {"hypotheses": 0, "findings": 0, "datasets": 0, "directions": 0}

    for h in g.nodes_of_type(NodeType.HYPOTHESIS):
        if h.status not in _DISTILLED_STATUSES:
            continue
        entries.append({
            "kind": "hypothesis", "workspace": ws_name, "topic_slug": topic_slug,
            "title": h.title, "status": h.status,
            "origin": h.origin, "summary": h.body,
            "evidence_uris": _evidence_uris(h.id),
            "source_uri": source_uri, "distilled_at": _now(),
            "key": _entry_key("hypothesis", h.title, ws_name),
        })
        counts["hypotheses"] += 1

    for f in g.nodes_of_type(NodeType.FINDING):
        entries.append({
            "kind": "finding", "workspace": ws_name, "topic_slug": topic_slug,
            "title": f.title, "summary": f.body,
            "evidence_uris": [_path_to_uri(p, ws_root, uri_base)
                              for p in f.evidence_paths],
            "source_uri": source_uri, "distilled_at": _now(),
            "key": _entry_key("finding", f.title, ws_name),
        })
        counts["findings"] += 1

    for ds in g.nodes_of_type(NodeType.DATASET_REF):
        entries.append({
            "kind": "dataset", "workspace": ws_name, "topic_slug": topic_slug,
            "title": ds.title, "summary": ds.body,
            "evidence_uris": [_path_to_uri(p, ws_root, uri_base)
                              for p in ds.evidence_paths],
            "source_uri": source_uri, "distilled_at": _now(),
            "key": _entry_key("dataset", ds.title, ws_name),
        })
        counts["datasets"] += 1

    for goal in g.nodes_of_type(NodeType.GOAL):
        if goal.extra.get("kind") != "direction":
            continue
        # Distill BOTH adopted and proposed IDEATE directions: an
        # ideate-only run's value IS its proposals, and a future ideate
        # should know what the lab already brainstormed (adopted = don't
        # duplicate; proposed = already considered, may extend).
        entries.append({
            "kind": "direction", "workspace": ws_name, "topic_slug": topic_slug,
            "title": goal.extra.get("label", goal.title),
            "status": "adopted" if goal.extra.get("adopted") else "proposed",
            "summary": goal.extra.get("rationale", ""),
            "evidence_uris": [], "source_uri": source_uri,
            "distilled_at": _now(),
            "key": _entry_key("direction", goal.title, ws_name),
        })
        counts["directions"] += 1

    if entries:
        with _locked(root):
            # Delta-append (root fix for the op-log's ~14x bloat): fold the
            # CURRENT op-log and only append entries that are NEW or whose
            # knowledge CHANGED. Re-distilling unchanged knowledge writes
            # nothing. This is behaviour-preserving on the READ path — the
            # skipped lines are exact duplicates (same key, same identity)
            # that `load()`/`_fold` would collapse anyway, so the folded
            # {key: entry} state is identical to the old append-all path. The
            # fold-before-append runs INSIDE the held lock, so a concurrent
            # distill cannot race the decision. `compact` is retained as a
            # backstop for pre-existing bloat.
            existing = load(root)
            to_write: list[dict[str, Any]] = []
            for e in entries:
                prior = existing.get(e["key"])
                if prior is not None and _entry_identity(prior) == _entry_identity(e):
                    continue  # unchanged knowledge — folds away, skip the write
                existing[e["key"]] = e
                to_write.append(e)
            if to_write:
                _append(root, to_write)
            _write_index(root, existing)
    return counts


# ---------------------------------------------------------------------------
# Serendipity promotion (creativity audit F1)
# ---------------------------------------------------------------------------

def _is_promotable(node: kg.Node) -> bool:
    """True iff a knowledge-graph hypothesis node is a promotable
    serendipitous discovery (creativity audit F1).

    ALL of:
      - origin == on_the_fly  (only serendipitous signal, never a
        pre-registered hypothesis — those are already the run's headline);
      - status == supported   (and because the graph's honesty rule already
        gated entry to `supported` via an on-disk-resolvable supports edge,
        this transitively guarantees real evidence — we do NOT re-check it);
      - high-surprise OR cheap-to-test  (read from extra; fail-CLOSED — a
        node with neither signal, e.g. a legacy node, is NOT promoted).
    """
    if node.type != NodeType.HYPOTHESIS:
        return False
    if node.origin != HypothesisOrigin.ON_THE_FLY:
        return False
    if node.status != HypothesisStatus.SUPPORTED:
        return False
    cheap = bool(node.extra.get("cheap_to_test", False))
    surprising = str(node.extra.get("surprise", "")).lower() in _SURPRISE_PROMOTABLE
    return cheap or surprising


def promote_serendipitous_directions(
    ws_root: pathlib.Path,
    topic_slug: str = "",
    uri_base: str | None = None,
    root: pathlib.Path | None = None,
) -> dict[str, int]:
    """Promote supported, surprising/cheap-to-test ON-THE-FLY hypotheses
    into PROPOSED (un-adopted) ideated directions in the cross-run lab KB
    (creativity audit F1 — the serendipity-promotion loop).

    This is the missing EDGE the audit identified: a within-run discovery
    that today is captured but caged (sorted last, post-hoc-tagged, trapped
    under the pre-registered goal) earns a SEAT AT IDEATE. The promoted
    entry is shaped like `distill_workspace`'s direction entries
    (kind=="direction", status=="proposed") so the standing continuous-loop
    cron, `omicairn ideate`, and `render_lab_knowledge_md` pick it up as a
    candidate WITHOUT any cron/IDEATE code change.

    RIGOR PRESERVED: the entry is NEVER adopted/auto-launched — a human or
    the cron must `omicairn ideate --adopt` + start a new workspace, which then
    walks the FULL SCOPE->PLAN pre-registration + every W-gate. We add an
    intake valve UPSTREAM of the gates; we relax nothing. We also touch
    neither the per-run graph, the Decision flow, the gates, nor the
    on-the-fly cap — this is a pure READ of the folded graph + an
    append-only WRITE to the lab store.

    Idempotent (delta-append on a stable key) and best-effort. Returns
    ``{"promoted": n, "candidates": n}``.
    """
    ws_root = pathlib.Path(ws_root)
    g = kg.load_graph(ws_root)
    ws_name = ws_root.name
    source_uri = _path_to_uri("", ws_root, uri_base).rstrip("/")
    root = (root or default_root())

    def _evidence_uris(node_id: str) -> list[str]:
        uris: list[str] = []
        for rel in (EdgeRel.SUPPORTS, EdgeRel.TESTS):
            for e in g.edges_to(node_id, rel=rel) + g.edges_from(node_id, rel=rel):
                src = g.nodes.get(e.src) or g.nodes.get(e.dst)
                if src is None:
                    continue
                for p in src.evidence_paths:
                    uris.append(_path_to_uri(p, ws_root, uri_base))
        return sorted(set(uris))

    entries: list[dict[str, Any]] = []
    candidates = 0
    for h in g.nodes_of_type(NodeType.HYPOTHESIS):
        if not _is_promotable(h):
            continue
        candidates += 1
        cheap = bool(h.extra.get("cheap_to_test", False))
        surprise = str(h.extra.get("surprise", "") or "unrated")
        rationale = (
            f"Promoted from a supported on-the-fly discovery in run "
            f"{ws_name} (surprise={surprise}, cheap_to_test={cheap}). "
            f"Earns a seat at IDEATE: a new run must still pre-register + "
            f"pass every gate before this can become a headline."
        )
        # Title-prefix the statement so a promoted direction reads as a
        # forward-looking research question, distinct from the original
        # hypothesis phrasing in the run's graph projection.
        title = h.title
        entries.append({
            "kind": "direction", "workspace": ws_name, "topic_slug": topic_slug,
            "title": title,
            "status": "proposed",          # NEVER auto-adopted
            "origin": SERENDIPITY_DIRECTION_ORIGIN,
            "summary": rationale,
            "evidence_uris": _evidence_uris(h.id),
            "source_uri": source_uri, "distilled_at": _now(),
            "key": _entry_key("direction", title, ws_name),
        })

    if entries:
        with _locked(root):
            existing = load(root)
            to_write: list[dict[str, Any]] = []
            for e in entries:
                prior = existing.get(e["key"])
                if prior is not None and _entry_identity(prior) == _entry_identity(e):
                    continue
                existing[e["key"]] = e
                to_write.append(e)
            if to_write:
                _append(root, to_write)
            _write_index(root, existing)
    return {"promoted": len(entries), "candidates": candidates}


# ---------------------------------------------------------------------------
# Query + render
# ---------------------------------------------------------------------------

def query(
    text: str = "",
    kind: str | None = None,
    status: str | None = None,
    root: pathlib.Path | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    q = (text or "").lower().strip()
    out = []
    for e in load(root).values():
        if kind is not None and e.get("kind") != kind:
            continue
        if status is not None and e.get("status") != status:
            continue
        if q and q not in (e.get("title", "") + " " + e.get("summary", "")).lower():
            continue
        out.append(e)
    out.sort(key=lambda e: e.get("distilled_at", ""), reverse=True)
    return out[:limit]


def stats(root: pathlib.Path | None = None) -> dict[str, Any]:
    entries = list(load(root).values())
    by_kind: dict[str, int] = {}
    for e in entries:
        by_kind[e.get("kind", "?")] = by_kind.get(e.get("kind", "?"), 0) + 1
    workspaces = {e.get("workspace") for e in entries}
    return {"entries": len(entries), "by_kind": by_kind,
            "workspaces": len(workspaces)}


def render_lab_knowledge_md(root: pathlib.Path | None = None,
                            max_items: int = 40) -> str:
    """Compact markdown of prior lab knowledge for IDEATE/SCOPE prompts."""
    entries = list(load(root).values())
    if not entries:
        return "(no prior lab knowledge — this is the first run)"
    hyps = [e for e in entries if e["kind"] == "hypothesis"]
    dirs = [e for e in entries if e["kind"] == "direction"]
    dsets = [e for e in entries if e["kind"] == "dataset"]
    lines = [
        "_Distilled from prior runs across the lab — build on it; do not "
        "re-propose what is already supported/refuted, and reuse known "
        "datasets._", ""]
    if hyps:
        lines.append("### Hypotheses already resolved")
        for e in sorted(hyps, key=lambda e: e.get("status", ""))[:max_items]:
            lines.append(
                f"- **[{e.get('status')}]** {e['title']}  "
                f"_(run {e['workspace']})_")
    if dirs:
        lines.append("")
        lines.append("### Directions already explored (adopted = don't duplicate)")
        for e in sorted(dirs, key=lambda e: e.get("status", ""))[:max_items]:
            lines.append(
                f"- **[{e.get('status', 'proposed')}]** {e['title']}  "
                f"_(run {e['workspace']})_")
    if dsets:
        lines.append("")
        lines.append("### Datasets the lab already holds")
        seen = set()
        for e in dsets:
            t = e["title"]
            if t in seen:
                continue
            seen.add(t)
            uri = (e.get("evidence_uris") or [""])[0]
            lines.append(f"- {t}{(' — ' + uri) if uri else ''}")
    return "\n".join(lines)


def _write_index(root: pathlib.Path, entries: dict[str, dict[str, Any]]) -> None:
    """Human-readable URI index of the lab knowledge base."""
    rows = ["# Lab knowledge index", "",
            f"_{len(entries)} distilled items; updated {_now()}._", "",
            "| kind | status | title | run | evidence URI |",
            "|------|--------|-------|-----|--------------|"]
    for e in sorted(entries.values(), key=lambda e: (e.get("kind", ""),
                                                      e.get("workspace", ""))):
        uri = (e.get("evidence_uris") or [e.get("source_uri", "")])[0]
        title = e.get("title", "").replace("|", "\\|")[:80]
        rows.append(f"| {e.get('kind')} | {e.get('status', '')} | {title} | "
                    f"{e.get('workspace', '')} | {uri} |")
    (root / LAB_KB_INDEX_FILE).write_text("\n".join(rows), encoding="utf-8")
