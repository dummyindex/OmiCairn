"""Cross-run HUMAN-FEEDBACK store — the lab's durable annotation memory.

Every explicit human steering message, free-form note, correction,
approval, rejection, or rating is a piece of expensive supervision: a
human looked at what OmiCairn produced and said "do this instead", "this
analysis is wrong", "reject this result". Today that signal is scattered and
ephemeral:

  - ``omicairn steer`` writes ``knowledge/steering_plan.json`` + appends
    ``knowledge/steering_log.jsonl`` — but PER-WORKSPACE only, and a
    finished run's plan is renamed ``.consumed.json``.
  - ``omicairn note`` writes ``knowledge/user_notes.md`` — injected into the
    next phase via ``{{user_notes}}`` and then (for ``--once`` notes)
    moved to ``user_notes_consumed.md``. The signal is consumed in-prompt
    and never aggregated.

There is no cross-run view ("show me every analysis correction the lab has
ever issued", "every review rejection", "every steer on run d7"), the
records aren't TYPED, and they aren't LINKED to the specific stage /
artifact the human was reacting to.

This module closes that gap — it is to human supervision what
``lab_knowledge`` is to the lab's compounding scientific memory. It is
**additive**: the per-workspace ``steering_log.jsonl`` and
``user_notes.md`` are left exactly as-is; we ALSO append a typed, linked
record here so the annotation is durable and queryable across the fleet.

Schema (one JSON object per line; folded on read, last write per ``id``
wins)::

    {
      "id":             "fb_20260611T120000Z_3f1c9a",  # unique, time-sortable
      "ts":             "2026-06-11T12:00:00+00:00",
      "schema_version": 1,
      "kind":           "steer" | "note" | "correction" |
                        "approval" | "rejection" | "rating",
      "text":           "<the human's words>",
      "workspace":      "ddr-blca-20260430-043813",   # run dir name
      "run_topic_slug": "ddr-blca",
      "stage_dir":      "017_execute",         # optional — stage targeted/issued-at
      "target_artifact":"analysis.md",    # optional — what the human reacted to
      "prior_decision": "PROCEED",           # optional — verdict being overridden
      "rating":          4,                  # optional — for kind=rating (int)
      "tags":           ["analysis", "overclaim"],
      "omicairn_commit":    "07f911f",           # provenance: code state
      "omicairn_commit_full":"07f911fa400e...",
      "recorded_at":    "2026-06-11T12:00:00+00:00"  # provenance datetime
    }

The link fields (``workspace`` + ``stage_dir`` + ``target_artifact``) and
the ``kind`` are the load-bearing part: they turn a pile of free text into
a queryable supervision dataset that later OmiCairn improvements can mine
(prompt tuning, eval sets, reward signal, drift detection).

Storage mirrors ``lab_knowledge`` (and the rest of OmiCairn's flat-file
discipline): a single append-only JSONL op-log, an atomic-create lockfile
guarding the read-append cycle, and a VISIBLE ``human_feedback/`` folder
beside the run workspaces, resolved by :func:`resolve_root` exactly as the
lab KB resolves its root.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import secrets
import time
from contextlib import contextmanager
from typing import Any, Iterator

from .constants import (
    ENV_HUMAN_FEEDBACK,
    HUMAN_FEEDBACK_DIRNAME,
    HUMAN_FEEDBACK_FILE,
    HUMAN_FEEDBACK_INDEX_FILE,
    HUMAN_FEEDBACK_LOCK,
    HUMAN_FEEDBACK_SCHEMA_VERSION,
)

# The typed kinds a human-feedback record may carry. Open, but enumerated
# so a CLI / caller typo (`kind="rejct"`) fails loudly rather than silently
# polluting later "all rejections" queries. ``steer`` and ``note`` are the
# auto-wired capture points; the rest are issued explicitly by a human (or a
# future review UI) when they correct / approve / reject / rate an artifact.
KINDS: frozenset[str] = frozenset(
    {"steer", "note", "correction", "approval", "rejection", "rating"}
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    """A unique, time-sortable record id: ``fb_<utc-compact>_<rand>``."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"fb_{stamp}_{secrets.token_hex(3)}"


# ---------------------------------------------------------------------------
# Location resolution (identical precedence to lab_knowledge.resolve_root)
# ---------------------------------------------------------------------------

def resolve_root(
    explicit: str | pathlib.Path | None = None,
    workspaces_root: str | pathlib.Path | None = None,
) -> pathlib.Path:
    """Resolve the human-feedback store directory.

    Precedence (highest first) — mirrors ``lab_knowledge.resolve_root`` so
    the two cross-run stores live side by side under the same
    ``<workspaces_root>/``:

      1. ``explicit`` — a path passed in directly (e.g. CLI ``--root``).
      2. ``$OMICAIRN_HUMAN_FEEDBACK`` — the env override.
      3. ``<workspaces_root>/human_feedback`` — the default: a VISIBLE
         folder beside the run workspaces (and beside ``lab_knowledge/``).
         Callers pass the absolute workspaces root (``ws.root.parent``) so
         this is cwd-independent during a run.
      4. ``<cwd>/workspaces/human_feedback`` — last-resort CLI default when
         no workspace context is available.

    Resolution is side-effect free; writers create the dir on demand.
    """
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()
    override = os.environ.get(ENV_HUMAN_FEEDBACK)
    if override:
        return pathlib.Path(override).expanduser().resolve()
    if workspaces_root is not None:
        return (pathlib.Path(workspaces_root) / HUMAN_FEEDBACK_DIRNAME).resolve()
    return (pathlib.Path.cwd() / "workspaces" / HUMAN_FEEDBACK_DIRNAME).resolve()


def default_root() -> pathlib.Path:
    """Backward-compatible parameterless resolver (env or cwd default)."""
    return resolve_root()


def _store_path(root: pathlib.Path | None = None) -> pathlib.Path:
    return (root or default_root()) / HUMAN_FEEDBACK_FILE


@contextmanager
def _locked(root: pathlib.Path, timeout: float = 10.0) -> Iterator[None]:
    """Hold the feedback-store write lock for the read-append cycle.

    Atomic-create (``O_CREAT | O_EXCL``) sentinel, PID-tagged on acquisition
    (so ``omicairn.lock.inspect`` can tell a LIVE holder from a crashed one).
    Proceeds after ``timeout`` so a stale lock from a crashed writer can't
    wedge a capture — feedback recording is best-effort and must never block
    a human's ``omicairn steer`` / ``omicairn note``. Identical shape to
    ``lab_knowledge._locked`` (non-strict mode).
    """
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / HUMAN_FEEDBACK_LOCK
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
# Store I/O (append-only op-log, folded on read)
# ---------------------------------------------------------------------------

def load(root: pathlib.Path | None = None) -> dict[str, dict[str, Any]]:
    """Fold the op-log into {id: record}. Last write per id wins.

    A record without an ``id`` is skipped (defensive against a hand-edited
    or truncated line); malformed JSON lines are tolerated and skipped, the
    same robustness contract ``lab_knowledge.load`` keeps.
    """
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
        if isinstance(e, dict) and e.get("id"):
            out[e["id"]] = e
    return out


def _append(root: pathlib.Path, records: list[dict[str, Any]]) -> None:
    path = _store_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for e in records:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Recording (the capture API)
# ---------------------------------------------------------------------------

def record(
    kind: str,
    text: str,
    *,
    workspace: str = "",
    run_topic_slug: str = "",
    stage_dir: str | None = None,
    target_artifact: str | None = None,
    prior_decision: str | None = None,
    rating: int | None = None,
    tags: list[str] | None = None,
    root: pathlib.Path | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one typed, linked human-feedback record to the store.

    ``kind`` MUST be one of :data:`KINDS` (a typo fails loudly so it can't
    poison later typed queries). ``text`` is the human's verbatim words.
    The link fields (``workspace`` / ``stage_dir`` / ``target_artifact``)
    are what make a record reusable later — keep them populated whenever the
    caller knows them. Per-entry provenance (OmiCairn commit + datetime) is
    captured here unless the caller passes a pre-built ``provenance`` dict
    (lets a wiring point reuse a snapshot it already took).

    Returns the written record. Lock-guarded append; folded on read.
    """
    kind = (kind or "").strip()
    if kind not in KINDS:
        raise ValueError(
            f"unknown feedback kind {kind!r}; must be one of {sorted(KINDS)}"
        )
    text = (text or "").strip()
    if not text:
        raise ValueError("feedback text is empty; pass the human's message")

    prov = provenance if provenance is not None else _capture_provenance()
    rec: dict[str, Any] = {
        "id": _new_id(),
        "ts": _now(),
        "schema_version": HUMAN_FEEDBACK_SCHEMA_VERSION,
        "kind": kind,
        "text": text,
        "workspace": workspace or "",
        "run_topic_slug": run_topic_slug or "",
        "stage_dir": stage_dir,
        "target_artifact": target_artifact,
        "prior_decision": prior_decision,
        "tags": list(tags or []),
        # Provenance: which OmiCairn code state + when (mirrors provenance.py).
        "omicairn_commit": prov.get("git_commit_short", "unknown"),
        "omicairn_commit_full": prov.get("git_commit", "unknown"),
        "recorded_at": prov.get("recorded_at") or _now(),
    }
    if rating is not None:
        rec["rating"] = int(rating)

    root = root or default_root()
    with _locked(root):
        _append(root, [rec])
        _write_index(root, load(root))
    return rec


def _capture_provenance() -> dict[str, Any]:
    """Best-effort OmiCairn code-state + datetime snapshot for a record.

    Reuses the repo's provenance helper (``provenance.capture_omicairn_version``)
    so the commit is the SYSTEM's commit (resolved from the package), never
    the workspace's, and the field semantics match ``provenance.jsonl``.
    Falls back to a minimal stamp if provenance capture raises — recording a
    human's words must never fail on a git hiccup.
    """
    try:
        from . import provenance

        return provenance.capture_omicairn_version()
    except Exception:
        return {
            "git_commit": "unknown",
            "git_commit_short": "unknown",
            "recorded_at": _now(),
        }


def record_from_workspace(
    ws_root: pathlib.Path,
    kind: str,
    text: str,
    *,
    stage_dir: str | None = None,
    target_artifact: str | None = None,
    prior_decision: str | None = None,
    rating: int | None = None,
    tags: list[str] | None = None,
    root: pathlib.Path | None = None,
) -> dict[str, Any] | None:
    """Convenience wrapper used by the CLI wiring (``cmd_steer`` / ``cmd_note``).

    Auto-derives the cross-run link fields from the workspace:
      - ``workspace``       = the workspace directory name (the run id),
      - ``run_topic_slug``  = parsed from RESEARCH.md (falls back to the
                              slug embedded in the dir name),
      - ``stage_dir``       = the caller's value, or — if omitted — the
                              CURRENT (latest) stage dir on disk, i.e. the
                              stage the human was looking at when they
                              steered/noted,
    and resolves the store root beside the workspace's ``workspaces_root``
    (``<ws>.parent/human_feedback/``) exactly like ``omicairn run`` /
    ``lab_knowledge`` resolve theirs.

    Best-effort + non-fatal: any failure (store IO, lock, slug parse)
    returns ``None`` and is swallowed by the caller, because this is an
    ADDITIVE durability mirror — it must never break the primary
    steering_log / user_notes write path. Returns the record on success.
    """
    ws_root = pathlib.Path(ws_root)
    try:
        store_root = root or resolve_root(workspaces_root=ws_root.parent)
        slug = _topic_slug_for(ws_root)
        if stage_dir is None:
            stage_dir = _current_stage_dir(ws_root)
        return record(
            kind,
            text,
            workspace=ws_root.name,
            run_topic_slug=slug,
            stage_dir=stage_dir,
            target_artifact=target_artifact,
            prior_decision=prior_decision,
            rating=rating,
            tags=tags,
            root=store_root,
        )
    except Exception:
        return None


def _topic_slug_for(ws_root: pathlib.Path) -> str:
    """Best-effort topic slug: RESEARCH.md is authoritative; else the dir
    name with its trailing ``-<timestamp>`` stripped."""
    try:
        from . import workspace as _ws

        return _ws.load_workspace(ws_root).research_md.topic_slug
    except Exception:
        # Dir names are "<slug>-YYYYMMDD-HHMMSS"; drop the two trailing
        # timestamp segments if present.
        name = ws_root.name
        parts = name.rsplit("-", 2)
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            return parts[0]
        return name


def _current_stage_dir(ws_root: pathlib.Path) -> str | None:
    """The latest (highest-index) numbered stage dir name, or None.

    This is the stage the human was reacting to when they issued the
    steer/note — the link that lets a later query say "every steer on the
    EXECUTE stage of run d7". Uses ``stage.list_stages`` so only valid
    ``NNN_<phase>`` dirs count (knowledge/, logs/, literature/ are ignored).
    """
    try:
        from . import stage as _stage

        stages = _stage.list_stages(ws_root)
        if stages:
            return stages[-1].rel_path
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Query + stats + export
# ---------------------------------------------------------------------------

def query(
    text: str = "",
    kind: str | None = None,
    workspace: str | None = None,
    tag: str | None = None,
    root: pathlib.Path | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return matching records, newest first.

    Filters compose (AND): ``kind`` exact, ``workspace`` exact, ``tag``
    membership, and ``text`` substring over the record's ``text``. The typed
    + linked filters are the whole point — "all analysis corrections", "all
    rejections on run d7" — so they're first-class, not a text grep.
    """
    q = (text or "").lower().strip()
    out: list[dict[str, Any]] = []
    for e in load(root).values():
        if kind is not None and e.get("kind") != kind:
            continue
        if workspace is not None and e.get("workspace") != workspace:
            continue
        if tag is not None and tag not in (e.get("tags") or []):
            continue
        if q and q not in (e.get("text", "") or "").lower():
            continue
        out.append(e)
    out.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return out[:limit]


def stats(root: pathlib.Path | None = None) -> dict[str, Any]:
    """Aggregate counts: total, by kind, distinct workspaces."""
    records = list(load(root).values())
    by_kind: dict[str, int] = {}
    for e in records:
        by_kind[e.get("kind", "?")] = by_kind.get(e.get("kind", "?"), 0) + 1
    workspaces = {e.get("workspace") for e in records if e.get("workspace")}
    return {
        "records": len(records),
        "by_kind": by_kind,
        "workspaces": len(workspaces),
    }


def export(
    out_path: pathlib.Path,
    kind: str | None = None,
    workspace: str | None = None,
    root: pathlib.Path | None = None,
) -> int:
    """Write the (optionally filtered) folded records to ``out_path`` as a
    JSON array, newest first. Returns the number of records written.

    The folded, deduped, sorted view is the shape a downstream consumer
    (eval-set builder, prompt-tuning corpus, drift dashboard) wants — not
    the raw op-log. ``--limit`` is intentionally absent here: an export is
    the whole dataset.
    """
    records = query(kind=kind, workspace=workspace, root=root, limit=10**9)
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(records)


def _write_index(root: pathlib.Path, records: dict[str, dict[str, Any]]) -> None:
    """Human-readable markdown index of the feedback store (one-way view).

    A glanceable companion to the JSONL — never parsed back (project rule:
    JSON is canonical, markdown is a human view). Newest first.
    """
    ordered = sorted(records.values(), key=lambda e: e.get("ts", ""), reverse=True)
    rows = [
        "# Human feedback index", "",
        f"_{len(ordered)} records; updated {_now()}._", "",
        "| ts | kind | run | stage | artifact | text |",
        "|----|------|-----|-------|----------|------|",
    ]
    for e in ordered:
        text = (e.get("text", "") or "").replace("|", "\\|").replace("\n", " ")
        if len(text) > 70:
            text = text[:67] + "..."
        rows.append(
            f"| {e.get('ts', '')} | {e.get('kind', '')} | "
            f"{e.get('workspace', '') or '-'} | {e.get('stage_dir') or '-'} | "
            f"{e.get('target_artifact') or '-'} | {text} |"
        )
    (root / HUMAN_FEEDBACK_INDEX_FILE).write_text("\n".join(rows), encoding="utf-8")
