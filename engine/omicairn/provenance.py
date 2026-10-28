"""Run-level reproducibility provenance.

For every research round we record the OmiCairn SYSTEM version (git
commit + dirty flag), package version, Python version, and UTC
datetime so a result can be reproduced (or at least attributed to an
exact code state) later.

Why per-INVOCATION, not once-at-init
------------------------------------
A OmiCairn run is resumed across many ``omicairn run`` invocations, and in
this project the CODE is frequently edited / merged between resumes.
The commit that produced stage 014 may differ from the one that
produced stage 003. A single ``omicairn_version`` field stamped at
``omicairn init`` would therefore LIE about most stages. Instead every
workspace-touching entry point (``init`` / ``run`` /
``ideate``) calls :func:`record_invocation`, which appends one line
to an append-only ``<ws>/provenance.jsonl`` and refreshes a
``omicairn_version`` block in STATE.md (the latest snapshot, for
``omicairn status`` at-a-glance).

Why resolve the repo from the PACKAGE, not the workspace cwd
------------------------------------------------------------
The workspace is a different directory from the OmiCairn source tree
(and is often gitignored, or not a git repo at all). We resolve the
``engine`` repo from ``omicairn.__file__`` so the recorded commit
is the SYSTEM's commit, never the workspace's.

Robustness
----------
Everything is best-effort and MUST NOT fail a run. Handles: OmiCairn
installed as a pip wheel with no git repo (falls back to
``importlib.metadata`` / ``__version__`` / "unknown"); a missing git
binary; a dirty working tree (records ``dirty=True`` plus a short
``diff_hash`` to disambiguate); and detached HEAD (branch="DETACHED").

Schema (one JSON object per line in ``provenance.jsonl``)::

    {
      "version": 1,
      "command": "run",
      "recorded_at": "2026-06-07T12:00:00+00:00",
      "git_commit": "58f1701c0e...full-sha...",
      "git_commit_short": "58f1701",
      "git_branch": "feat/parkinson-smoke-fixes",
      "git_dirty": false,
      "git_diff_hash": null,
      "omicairn_version": "0.1.0",
      "python_version": "3.11.15",
      "repo_root": "/Users/.../engine"
    }
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.metadata
import json
import pathlib
import platform
import subprocess
from typing import Any

from .constants import (
    CODE_PROVENANCE_SCHEMA_VERSION,
    CODE_PROVENANCE_STAMP,
    CodeProvenanceKey,
    CodeProvenanceMode,
    PROVENANCE_LOG,
    PROVENANCE_SCHEMA_VERSION,
)


_UNKNOWN = "unknown"


def _repo_root() -> pathlib.Path:
    """The engine source repo, resolved from this package file.

    ``omicairn/provenance.py`` -> ``omicairn/`` -> repo root (parent of the
    package dir). NOT the workspace cwd.
    """
    return pathlib.Path(__file__).resolve().parent.parent


def _git(args: list[str], cwd: pathlib.Path) -> str | None:
    """Run a git command, returning stripped stdout or None on any error.

    Swallows a missing git binary, non-git dirs, and non-zero exits —
    provenance capture must never raise.
    """
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def _package_version() -> str:
    """Best-effort OmiCairn package version via importlib.metadata / __version__."""
    for dist in ("omicairn", "omicairn"):
        try:
            return importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            continue
        except Exception:
            break
    try:
        from . import __version__  # type: ignore

        if __version__:
            return str(__version__)
    except Exception:
        pass
    return _UNKNOWN


def capture_omicairn_version() -> dict[str, Any]:
    """Snapshot the OmiCairn system's version + the UTC datetime.

    Returns a flat dict (see module docstring schema). All fields are
    best-effort: git fields fall back to "unknown"/None when no repo /
    no git binary; the package version falls back to "unknown".
    """
    repo = _repo_root()
    commit = _git(["rev-parse", "HEAD"], repo)
    commit_short = _git(["rev-parse", "--short", "HEAD"], repo)

    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
    if branch == "HEAD":
        # Detached HEAD — rev-parse --abbrev-ref returns the literal
        # "HEAD", which is ambiguous; record it explicitly.
        branch = "DETACHED"

    # Dirty-tree detection: `git status --porcelain` is empty iff the
    # working tree + index are clean. When dirty we also hash the diff
    # so two dirty captures at the same commit are distinguishable.
    dirty: bool | None = None
    diff_hash: str | None = None
    porcelain = _git(["status", "--porcelain"], repo)
    if porcelain is not None:
        dirty = bool(porcelain.strip())
        if dirty:
            # Hash both tracked diff and the porcelain (the latter also
            # reflects untracked files, which a bare `git diff` misses).
            diff = _git(["diff", "HEAD"], repo) or ""
            blob = (diff + "\n" + porcelain).encode("utf-8", "replace")
            diff_hash = hashlib.sha256(blob).hexdigest()[:12]

    return {
        "version": PROVENANCE_SCHEMA_VERSION,
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "git_commit": commit or _UNKNOWN,
        "git_commit_short": commit_short or _UNKNOWN,
        "git_branch": branch or _UNKNOWN,
        "git_dirty": dirty,
        "git_diff_hash": diff_hash,
        "omicairn_version": _package_version(),
        "python_version": platform.python_version(),
        "repo_root": str(repo),
    }


def _format_state_stamp(entry: dict[str, Any]) -> str:
    """One-line human-readable reproducibility stamp for STATE.md /
    ``omicairn status``."""
    short = entry.get("git_commit_short", _UNKNOWN)
    branch = entry.get("git_branch", _UNKNOWN)
    dirty = entry.get("git_dirty")
    if dirty is True:
        dirty_str = f"dirty({entry.get('git_diff_hash') or '?'})"
    elif dirty is False:
        dirty_str = "clean"
    else:
        dirty_str = "unknown"
    return (
        f"commit {short} on {branch} ({dirty_str}); "
        f"pkg {entry.get('omicairn_version', _UNKNOWN)}; "
        f"py {entry.get('python_version', _UNKNOWN)}; "
        f"at {entry.get('recorded_at', '?')}"
    )


def record_invocation(
    ws_root: pathlib.Path, command: str
) -> dict[str, Any]:
    """Capture the OmiCairn version and append it to ``<ws>/provenance.jsonl``.

    One entry per call (per-invocation semantics). Creates the file if
    absent. Also refreshes the ``omicairn_version`` block in STATE.md (the
    LATEST snapshot, so ``omicairn status`` can show it). Best-effort —
    swallows all IO/state errors so it can never fail a run.

    Returns the captured entry (for callers / tests). ``command`` is a
    short tag: "init" / "run" / "ideate".
    """
    entry = capture_omicairn_version()
    entry["command"] = command
    ws_root = pathlib.Path(ws_root)
    try:
        ws_root.mkdir(parents=True, exist_ok=True)
        log = ws_root / PROVENANCE_LOG
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        return entry
    _stamp_state(ws_root, entry)
    return entry


def _stamp_state(ws_root: pathlib.Path, entry: dict[str, Any]) -> None:
    """Refresh the latest ``omicairn_version`` snapshot in STATE.md.

    Stores the structured snapshot under a ``omicairn_version`` key so
    ``omicairn status`` can render it, distinct from the existing
    ``started_at`` / ``updated_at`` (which track the RUN's wall-clock
    lifecycle, not the CODE version). Best-effort: a malformed or
    missing STATE.md is left untouched.
    """
    from . import state as state_mod

    state_path = ws_root / "STATE.md"
    if not state_path.exists():
        return
    try:
        s = state_mod.read(state_path)
    except (ValueError, OSError):
        return
    s["omicairn_version"] = {
        "command": entry.get("command"),
        "git_commit_short": entry.get("git_commit_short"),
        "git_commit": entry.get("git_commit"),
        "git_branch": entry.get("git_branch"),
        "git_dirty": entry.get("git_dirty"),
        "git_diff_hash": entry.get("git_diff_hash"),
        "omicairn_version": entry.get("omicairn_version"),
        "python_version": entry.get("python_version"),
        "recorded_at": entry.get("recorded_at"),
    }
    try:
        state_mod.write(state_path, s)
    except (OSError, ValueError):
        return


def read_latest(ws_root: pathlib.Path) -> dict[str, Any] | None:
    """Return the last entry in ``<ws>/provenance.jsonl``, or None.

    Used by ``omicairn status``. Tolerates a missing file and trailing
    blank / malformed lines.
    """
    log = pathlib.Path(ws_root) / PROVENANCE_LOG
    if not log.exists():
        return None
    last: dict[str, Any] | None = None
    try:
        for line in log.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                last = obj
    except OSError:
        return None
    return last


def write_stage_stamp(
    stage_dir: pathlib.Path, mode: str | None = None
) -> dict[str, Any] | None:
    """Stamp the live OmiCairn engine version into a stage dir (per-stage
    code provenance, phase 1 — additive).

    Writes ``<stage_dir>/code_provenance.json`` recording the engine git
    commit (+ dirty flag + diff_hash) that was live when the stage was
    created. This closes the stage->SHA attribution gap that the
    per-INVOCATION ``provenance.jsonl`` leaves open: a run resumed across
    many invocations at different commits otherwise cannot map a single
    stage back to the exact engine state that produced it.

    Stages are produced exactly once and never overwritten (see
    ``stage`` module docstring), so this is written once at stage creation
    and never needs refreshing — a re-attempt is a NEW numbered stage.

    ``mode`` is the resolved ``code_provenance.mode`` for the run
    (defaults to :attr:`CodeProvenanceMode.DEFAULT`). Phase 1 records the
    mode and the engine snapshot; the EXPERIMENT_CODE manifest / engine
    diff are reserved for the phase-2 ``git_pinned`` increment.

    Best-effort: swallows all IO errors so it can never fail a stage.
    Returns the written stamp dict (for callers/tests), or ``None`` on a
    write error.
    """
    stage_dir = pathlib.Path(stage_dir)
    resolved_mode = mode or CodeProvenanceMode.DEFAULT
    if resolved_mode not in CodeProvenanceMode.ALL:
        resolved_mode = CodeProvenanceMode.DEFAULT
    engine = capture_omicairn_version()
    stamp: dict[str, Any] = {
        CodeProvenanceKey.SCHEMA_VERSION: CODE_PROVENANCE_SCHEMA_VERSION,
        CodeProvenanceKey.MODE: resolved_mode,
        CodeProvenanceKey.ENGINE: engine,
        CodeProvenanceKey.RECORDED_AT: engine.get("recorded_at"),
    }
    try:
        stage_dir.mkdir(parents=True, exist_ok=True)
        out = stage_dir / CODE_PROVENANCE_STAMP
        out.write_text(
            json.dumps(stamp, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return None
    return stamp
