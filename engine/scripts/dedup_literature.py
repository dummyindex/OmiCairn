#!/usr/bin/env python3
"""One-time, idempotent maintenance: dedup per-workspace literature PDFs.

Replaces byte-identical per-workspace ``literature/`` files with **hardlinks**
to the canonical central ``lab_library/`` so each PDF/abstract exists once on
disk. It also reports any spurious per-workspace ``lab_library/`` full copies
(the audit-2026-06-10 bug, fixed for future runs in ``literature.py``).

This is the *cleanup-the-existing-mess* companion to the code fix: the code
fix stops NEW runs from copying; this reclaims space already duplicated. It is
intentionally conservative.

SAFETY CONTRACT (all enforced):
  * **Dry-run by default.** Nothing is modified without ``--apply``.
  * **Never touches a LIVE run.** A workspace whose ``.run.lock`` holds an
    alive PID is skipped entirely (uses ``omicairn.lock.inspect``, the same
    liveness check the orchestrator uses).
  * **Never deletes the central store.** Central files are only ever the
    *target* of a hardlink; they are never unlinked or modified.
  * **Only replaces verified duplicates.** A workspace file is replaced only
    when a central file with the same slug exists, is byte-identical, is on
    the same filesystem (hardlinks require it), and is not already the same
    inode. Replacement is atomic (link to a temp name, then ``os.replace``).
  * **Idempotent.** Re-running finds nothing left to do (already-hardlinked
    files are skipped via the same-inode check).

It does NOT delete the abandoned duplicate workspaces or any ``lab_library``
copy — deletion is the owner's call. It only converts duplicate *files* to
hardlinks and reports what it sees.

Usage:
    python scripts/dedup_literature.py [WORKSPACES_ROOT] [--apply] [--quiet]

    WORKSPACES_ROOT  defaults to ./workspaces (or $OMICAIRN_WORKSPACES_ROOT).
    The central library is resolved as $OMICAIRN_PAPER_LIB or
    <WORKSPACES_ROOT>/lab_library.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

# Make the sibling ``omicairn`` package importable when run from the repo root.
_REPO = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from omicairn import lock  # noqa: E402

_PAPER_SUBDIRS = ("pdfs", "abstracts")
_RUN_LOCK = ".run.lock"
_LIB_DIRNAME = "lab_library"
_KB_DIRNAME = "lab_knowledge"


def _same_filesystem(a: pathlib.Path, b: pathlib.Path) -> bool:
    try:
        return a.stat().st_dev == b.stat().st_dev
    except OSError:
        return False


def _files_identical(a: pathlib.Path, b: pathlib.Path) -> bool:
    """True iff a and b have identical bytes (size check first)."""
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
    except OSError:
        return False
    try:
        with a.open("rb") as fa, b.open("rb") as fb:
            while True:
                ba, bb = fa.read(1 << 20), fb.read(1 << 20)
                if ba != bb:
                    return False
                if not ba:
                    return True
    except OSError:
        return False


def _relink(dup: pathlib.Path, central: pathlib.Path) -> bool:
    """Atomically replace ``dup`` with a hardlink to ``central``.

    Link to a temp name in dup's dir, then os.replace over dup. On any
    failure the original dup is left intact. Returns True on success.
    """
    tmp = dup.with_suffix(dup.suffix + ".dedup.tmp")
    try:
        if tmp.exists():
            tmp.unlink()
        os.link(central, tmp)
        os.replace(tmp, dup)  # atomic on POSIX; replaces the duplicate
        return True
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


class Stats:
    def __init__(self) -> None:
        self.workspaces_scanned = 0
        self.workspaces_skipped_live = 0
        self.already_linked = 0
        self.relinkable = 0
        self.relinked = 0
        self.bytes_reclaimable = 0
        self.per_ws_lib_copies: list[tuple[pathlib.Path, int]] = []


def _dir_size(p: pathlib.Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _iter_literature_dirs(ws: pathlib.Path):
    """Yield every ``literature/`` dir under a workspace: the top-level one and
    any stage-nested ``<NNN_stage>/literature`` (the layout that produced the
    duplicates)."""
    top = ws / "literature"
    if top.is_dir():
        yield top
    for stage in sorted(ws.glob("*/literature")):
        if stage.is_dir():
            yield stage


def process_workspace(ws: pathlib.Path, central: pathlib.Path,
                      stats: Stats, apply: bool, log) -> None:
    # LIVE-run guard: never touch a workspace with an alive lock holder.
    st = lock.inspect(ws / _RUN_LOCK)
    if st.holder_alive:
        stats.workspaces_skipped_live += 1
        log(f"  SKIP (live run, PID {st.holder_pid}): {ws.name}")
        return
    stats.workspaces_scanned += 1

    # Report a spurious per-workspace lab_library copy (do NOT delete it).
    ws_lib = ws / _LIB_DIRNAME
    if ws_lib.is_dir():
        size = _dir_size(ws_lib)
        stats.per_ws_lib_copies.append((ws_lib, size))

    for lit in _iter_literature_dirs(ws):
        for sub in _PAPER_SUBDIRS:
            d = lit / sub
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*")):
                if not f.is_file():
                    continue
                central_f = central / sub / f.name
                if not central_f.is_file():
                    continue
                # Already the same inode → nothing to do (idempotent).
                if f.stat().st_ino == central_f.stat().st_ino:
                    stats.already_linked += 1
                    continue
                if not _same_filesystem(f, central_f):
                    continue
                if not _files_identical(f, central_f):
                    continue
                stats.relinkable += 1
                stats.bytes_reclaimable += f.stat().st_size
                rel = f.relative_to(ws.parent)
                if apply:
                    if _relink(f, central_f):
                        stats.relinked += 1
                        log(f"  relinked: {rel}")
                    else:
                        log(f"  FAILED to relink (left intact): {rel}")
                else:
                    log(f"  would relink: {rel}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    default_root = os.environ.get("OMICAIRN_WORKSPACES_ROOT") or "workspaces"
    ap.add_argument("workspaces_root", nargs="?", default=default_root,
                    help="workspaces dir (default: ./workspaces)")
    ap.add_argument("--apply", action="store_true",
                    help="actually relink (default: dry-run, report only)")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress per-file lines; print summary only")
    args = ap.parse_args(argv)

    ws_root = pathlib.Path(args.workspaces_root).expanduser().resolve()
    if not ws_root.is_dir():
        print(f"ERROR: workspaces root not found: {ws_root}", file=sys.stderr)
        return 2
    central = pathlib.Path(
        os.environ.get("OMICAIRN_PAPER_LIB") or (ws_root / _LIB_DIRNAME)
    ).expanduser().resolve()
    if not central.is_dir():
        print(f"ERROR: central lab_library not found: {central}",
              file=sys.stderr)
        return 2

    def log(msg: str) -> None:
        if not args.quiet:
            print(msg)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"dedup_literature [{mode}]")
    print(f"  workspaces root: {ws_root}")
    print(f"  central library: {central}")

    stats = Stats()
    for child in sorted(ws_root.iterdir()):
        if not child.is_dir():
            continue
        if child.name in (_LIB_DIRNAME, _KB_DIRNAME):
            continue  # never recurse into the shared stores themselves
        process_workspace(child, central, stats, args.apply, log)

    print("\nsummary")
    print(f"  workspaces scanned:        {stats.workspaces_scanned}")
    print(f"  workspaces skipped (live): {stats.workspaces_skipped_live}")
    print(f"  files already hardlinked:  {stats.already_linked}")
    verb = "relinked" if args.apply else "relinkable"
    n = stats.relinked if args.apply else stats.relinkable
    print(f"  files {verb}:           {n}")
    print(f"  bytes reclaimable:         "
          f"{stats.bytes_reclaimable / 1024 / 1024:.1f} MB")
    if stats.per_ws_lib_copies:
        print("\n  spurious per-workspace lab_library/ copies "
              "(reported only — deletion is the owner's call):")
        for p, size in stats.per_ws_lib_copies:
            print(f"    {p}  ({size / 1024 / 1024:.1f} MB)")
    if not args.apply and (stats.relinkable or stats.per_ws_lib_copies):
        print("\n  (dry-run) re-run with --apply to convert duplicates to "
              "hardlinks. Per-workspace lab_library copies are NOT removed "
              "by this tool.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
