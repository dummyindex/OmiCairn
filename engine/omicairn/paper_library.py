"""Shared on-disk LAB LITERATURE LIBRARY for OmiCairn workspaces.

Purpose
-------
Per-workspace `literature/` directories are populated independently by
`omicairn.literature.LiteratureFetcher`. Two workspaces that share any
foundational papers (common in a domain) therefore re-download those
PDFs and re-convert them to text. This module is the literature analogue
of the lab knowledge base: a single VISIBLE, cross-workspace library
that runs both CONTRIBUTE to and REUSE from, so a paper is downloaded
once and its PDF→markdown conversion happens once for the whole lab.

    <workspaces_root>/lab_library/     # the resolved default (see resolve_root)
    ├── papers.json                    # manifest: one entry per unique DOI/PMID
    ├── pdfs/<slug>.pdf                # content-addressed by DOI or PMID slug
    ├── abstracts/<slug>.txt
    ├── markdown/<slug>.md             # converted PDF full-text (pdftotext)
    └── provenance.jsonl               # append-only: {workspace, key, added_at, ...}

Location
--------
`resolve_root()` mirrors `omicairn.lab_knowledge.resolve_root` exactly
(highest precedence first): an explicit `--root`, the `$OMICAIRN_PAPER_LIB`
env var, else a VISIBLE `lab_library/` beside the run workspaces
(`<workspaces_root>/lab_library/`), with a `<cwd>/workspaces/lab_library/`
CLI fallback. The old hidden `~/.cache` location is retired.
`default_library_root()` is kept as a back-compat env-or-cwd wrapper.

Callers
-------
- `omicairn.literature.LiteratureFetcher` consults `get(doi)` before
  downloading and reuses the cached PDF + markdown; calls
  `add(paper, pdf_source=..., convert=True, ...)` after a fetch so the
  PDF and its converted markdown are shared with future runs.
- Agents may call `omicairn paperlib add` via Bash when WebSearch locates
  a new verifiable reference.
- The `omicairn paperlib` CLI exposes list/search/get/add/stats/convert.

Design
------
- Flat JSON + per-file PDFs + per-file markdown, no database. Scales fine
  at O(10k) papers. (Future transport — a DB or an MCP server — could
  front this same flat-file store; deliberately NOT built.)
- File-locked updates (lockfile at PAPER_LIB_LOCK) so two runs fetching
  concurrently cannot corrupt the manifest.
- Idempotent: `add` on an existing DOI merges new fields and appends a
  provenance record rather than overwriting.
- PDF→markdown conversion is best-effort via the `pdftotext` binary;
  it degrades to None (no markdown stored) when the binary is missing.
- Workspace `literature/` stays authoritative for the anti-fabrication
  contract; `link_into_workspace` just hardlinks library files into
  the workspace (falls back to copy on Windows / cross-device).
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any, Iterator

from omicairn.constants import (
    ENV_PAPER_LIB,
    PAPER_LIB_ABSTRACTS_DIR,
    PAPER_LIB_DIRNAME,
    PAPER_LIB_LOCK,
    PAPER_LIB_MANIFEST,
    PAPER_LIB_MARKDOWN_DIR,
    PAPER_LIB_PDFS_DIR,
    PAPER_LIB_PROVENANCE,
)
from omicairn.literature import Paper

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def resolve_root(
    explicit: str | pathlib.Path | None = None,
    workspaces_root: str | pathlib.Path | None = None,
) -> pathlib.Path:
    """Resolve the lab literature library directory.

    Precedence (highest first) — mirrors `lab_knowledge.resolve_root`:
      1. ``explicit`` — a path passed in directly (e.g. CLI ``--root``).
      2. ``$OMICAIRN_PAPER_LIB`` — the env override.
      3. ``<workspaces_root>/lab_library`` — the default: a VISIBLE
         folder beside the run workspaces. Callers pass the absolute
         workspaces root so this is cwd-independent during a run.
      4. ``<cwd>/workspaces/lab_library`` — last-resort default for the
         CLI when no workspace context is available.

    The directory is NOT created here (resolution is side-effect free);
    writers create it on demand via ``ensure_library`` / ``_locked``.
    """
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()
    override = os.environ.get(ENV_PAPER_LIB)
    if override:
        return pathlib.Path(override).expanduser().resolve()
    if workspaces_root is not None:
        return (pathlib.Path(workspaces_root) / PAPER_LIB_DIRNAME).resolve()
    return (pathlib.Path.cwd() / "workspaces" / PAPER_LIB_DIRNAME).resolve()


def default_library_root() -> pathlib.Path:
    """Backward-compatible parameterless resolver (env or cwd default).

    Honors $OMICAIRN_PAPER_LIB; otherwise falls back to the cwd default
    (``<cwd>/workspaces/lab_library``). Prefer ``resolve_root`` with an
    explicit ``workspaces_root`` from within a run.
    """
    return resolve_root()


def workspaces_root_for(start: str | pathlib.Path) -> pathlib.Path | None:
    """Resolve the *workspaces root* given any path inside a run workspace.

    Walks up from ``start`` to the workspace root (the dir with a ``STATE.md``,
    written by ``workspace.init``) and returns its parent — the dir holding
    ``lab_library/`` + ``lab_knowledge/``. Robust to where the literature dir
    sits: ``<ws>/literature`` (init) and the stage-nested
    ``<ws>/<NNN_stage>/literature`` (a subagent fetch with cwd in the stage)
    both resolve correctly, where a naive ``output_dir.parent.parent`` strands
    a per-workspace ``lab_library`` copy (audit 2026-06-10). ``None`` when no
    workspace ancestor exists (e.g. a bare tmp dir) so callers can fall back.
    """
    p = pathlib.Path(start).resolve()
    for anc in (p, *p.parents):
        if (anc / "STATE.md").is_file():
            return anc.parent
    return None


def link_or_copy(src: pathlib.Path, dst: pathlib.Path) -> str:
    """Hardlink ``src`` → ``dst`` (bytes exist once, shared inode); copy on
    failure (cross-device / Windows). Transparent to readers. Returns
    ``"linked"``/``"copied"``. No-op-safe: skips when ``dst`` already exists.
    """
    if dst.exists():
        return "linked" if dst.stat().st_nlink > 1 else "copied"
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
        return "linked"
    except (OSError, NotImplementedError):
        shutil.copy2(src, dst)
        return "copied"


def slug_for(paper: Paper | dict) -> str:
    """Content-addressable slug derived from DOI, then PMID, then title.

    The same paper always maps to the same slug regardless of where it
    was fetched from, so repeat fetches hit the cache.
    """
    doi = paper.doi if isinstance(paper, Paper) else paper.get("doi")
    pmid = paper.pmid if isinstance(paper, Paper) else paper.get("pmid")
    title = paper.title if isinstance(paper, Paper) else paper.get("title", "")
    if doi:
        return doi.replace("/", "_").replace(":", "_")
    if pmid:
        return f"pmid_{pmid}"
    # Title fallback — lowercase, spaces → underscores, strip non-alnum.
    cleaned = "".join(c if c.isalnum() or c == "_" else "_"
                      for c in title.lower().replace(" ", "_"))
    return f"title_{cleaned[:60]}"


def _key_for(paper: Paper | dict) -> str:
    """Dedup key — mirrors Paper.dedup_key but works on dicts too."""
    if isinstance(paper, Paper):
        return paper.dedup_key
    if paper.get("doi"):
        return f"doi:{paper['doi'].lower()}"
    if paper.get("pmid"):
        return f"pmid:{paper['pmid']}"
    title = paper.get("title") or ""
    return f"title:{title.lower()[:80]}"


# ---------------------------------------------------------------------------
# Locking — simple atomic-create lockfile, best-effort
# ---------------------------------------------------------------------------

@contextmanager
def _locked(root: pathlib.Path, timeout: float = 10.0) -> Iterator[None]:
    """Acquire a filesystem-level lock on the library. Not reentrant.

    Uses O_CREAT|O_EXCL so two processes can't both claim it. Times out
    with a warning rather than a hard error — the manifest writer is
    also atomic (temp file + rename), so even a contended write is
    recoverable. The lockfile is for the read-modify-write cycle.
    """
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / PAPER_LIB_LOCK
    start = time.monotonic()
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() - start > timeout:
                logger.warning(
                    "paper_library: lock held at %s for >%.0fs — "
                    "proceeding without lock (another run may clobber)",
                    lock_path, timeout,
                )
                fd = -1  # sentinel for "gave up"
                break
            time.sleep(0.1)
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
# Manifest I/O
# ---------------------------------------------------------------------------

def _read_manifest(root: pathlib.Path) -> dict[str, dict[str, Any]]:
    """Return the manifest as {dedup_key: paper_dict}. Missing → empty."""
    path = root / PAPER_LIB_MANIFEST
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    # Stored as a flat list of paper dicts; convert to keyed dict.
    return {_key_for(p): p for p in raw}


def _write_manifest(root: pathlib.Path, manifest: dict[str, dict[str, Any]]) -> None:
    """Atomic write: tmp file + rename."""
    path = root / PAPER_LIB_MANIFEST
    tmp = path.with_suffix(path.suffix + ".tmp")
    # Sort by key for deterministic diffs.
    papers = [manifest[k] for k in sorted(manifest.keys())]
    tmp.write_text(
        json.dumps(papers, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)


def _append_provenance(
    root: pathlib.Path,
    key: str,
    workspace: str,
    source: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append one JSONL line to provenance.jsonl."""
    record = {
        "key": key,
        "workspace": workspace,
        "source": source,
        "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if extra:
        record.update(extra)
    path = root / PAPER_LIB_PROVENANCE
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ensure_library(root: pathlib.Path | None = None) -> pathlib.Path:
    """Create the library dir layout if missing; return the root."""
    root = pathlib.Path(root) if root else default_library_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / PAPER_LIB_PDFS_DIR).mkdir(exist_ok=True)
    (root / PAPER_LIB_ABSTRACTS_DIR).mkdir(exist_ok=True)
    (root / PAPER_LIB_MARKDOWN_DIR).mkdir(exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# PDF → markdown conversion (best-effort, via the `pdftotext` binary)
# ---------------------------------------------------------------------------

def convert_pdf_to_markdown(pdf_path: pathlib.Path | str) -> str | None:
    """Convert a PDF to plain-text markdown via ``pdftotext -layout``.

    Returns the extracted text, or ``None`` on ANY failure: the binary
    is missing (``pymupdf``/``fitz`` is not installed in this env, so we
    shell out instead), the file is absent, the conversion errors, or it
    yields no text. Never raises — conversion is a best-effort enrichment,
    not a correctness requirement.
    """
    pdf_path = pathlib.Path(pdf_path)
    if not pdf_path.exists():
        return None
    try:
        proc = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), "-"],
            capture_output=True, timeout=120,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as e:
        logger.info("pdftotext unavailable / failed for %s: %s", pdf_path, e)
        return None
    if proc.returncode != 0:
        logger.info("pdftotext exited %d for %s", proc.returncode, pdf_path)
        return None
    text = proc.stdout.decode("utf-8", errors="replace").strip()
    return text or None


def get_markdown(
    key: str,
    root: pathlib.Path | None = None,
) -> str | None:
    """Return the converted markdown full-text for a paper, or None.

    ``key`` accepts the same forms as ``get`` (bare/keyed DOI or PMID) and
    also a raw library slug (useful for title-only IDEATE sources that
    carry no DOI/PMID). Resolution order:
      1. manifest entry → its slug
      2. ``key`` treated as a bare DOI/PMID → derived slug
      3. ``key`` treated literally as the slug
    """
    root = ensure_library(root)
    md_dir = root / PAPER_LIB_MARKDOWN_DIR
    candidate_slugs: list[str] = []
    entry = get(key, root=root)
    if entry is not None:
        candidate_slugs.append(slug_for(entry))
    bare = key.split(":", 1)[1] if key.startswith(("doi:", "pmid:")) else key
    candidate_slugs.append(
        slug_for({"doi": bare} if ("/" in bare or bare.startswith("10."))
                 else {"pmid": bare}))
    candidate_slugs.append(key)  # raw-slug fallback
    for slug in candidate_slugs:
        md_path = md_dir / f"{slug}.md"
        if md_path.exists():
            try:
                return md_path.read_text(encoding="utf-8")
            except OSError:
                return None
    return None


def get(
    key: str,
    root: pathlib.Path | None = None,
) -> dict[str, Any] | None:
    """Look up a paper by DOI, PMID, or full dedup key.

    Accepts any of:
      - "10.1038/nature12113"  (bare DOI)
      - "doi:10.1038/nature12113"  (full key)
      - "12345678"  (bare PMID)
      - "pmid:12345678"  (full key)
    """
    root = ensure_library(root)
    manifest = _read_manifest(root)
    # Try each normalisation.
    candidates = [key]
    if not key.startswith(("doi:", "pmid:", "title:")):
        if "/" in key or key.startswith("10."):
            candidates.append(f"doi:{key.lower()}")
        elif key.isdigit():
            candidates.append(f"pmid:{key}")
    for c in candidates:
        if c in manifest:
            return manifest[c]
    return None


def get_by_slug(
    slug: str,
    root: pathlib.Path | None = None,
) -> dict[str, Any] | None:
    """Look up a manifest entry by its content-addressable ``slug_for`` slug.

    This is the DOI-FREE identity path: ``add`` stores every paper's PDF at
    ``pdfs/<slug>.pdf`` where ``slug = slug_for(paper)``, and the reuse path
    computes the SAME ``slug_for`` for the paper it wants. arXiv papers carry
    no DOI/PMID, so the dedup-key-based ``get`` can never find them; matching
    on the slug both sides already agree on (the unified key) does. Returns
    the first manifest entry whose ``slug_for`` equals ``slug``, else None.
    """
    root = ensure_library(root)
    manifest = _read_manifest(root)
    for entry in manifest.values():
        if slug_for(entry) == slug:
            return entry
    return None


def add(
    paper: Paper | dict,
    root: pathlib.Path | None = None,
    pdf_source: pathlib.Path | None = None,
    abstract_source: pathlib.Path | None = None,
    workspace: str = "unknown",
    source: str = "manual",
    extra_provenance: dict[str, Any] | None = None,
    convert: bool = True,
) -> dict[str, Any]:
    """Persist a paper into the library. Idempotent on dedup key.

    - If the paper already exists, new non-empty fields are merged in
      (existing values win unless the existing value is falsy).
    - If `pdf_source` / `abstract_source` are given and the library
      doesn't already have them, the files are copied in.
    - When ``convert`` (default) and a PDF is present without library
      markdown yet, the PDF is converted to markdown via ``pdftotext``
      and stored at ``markdown/<slug>.md`` (best-effort — missing binary
      or conversion failure just skips it; ``md_path`` is recorded in the
      manifest only when markdown actually exists).
    - A provenance record is appended unconditionally.

    Returns the stored dict (new or merged).
    """
    root = ensure_library(root)
    paper_dict = asdict(paper) if isinstance(paper, Paper) else dict(paper)
    key = _key_for(paper_dict)
    slug = slug_for(paper_dict)

    with _locked(root):
        manifest = _read_manifest(root)
        existing = manifest.get(key)
        if existing is None:
            manifest[key] = paper_dict
            merged = paper_dict
        else:
            merged = dict(existing)
            for k, v in paper_dict.items():
                # Prefer existing truthy values; only fill in gaps.
                if not merged.get(k) and v:
                    merged[k] = v
            manifest[key] = merged

        # Reference the source PDF into the library by HARDLINK (bytes exist
        # once on disk, shared inode with the originating workspace copy);
        # link_or_copy falls back to copy cross-device and is a no-op when the
        # library file already exists (idempotent, never clobbers).
        if pdf_source and pdf_source.exists():
            dst = root / PAPER_LIB_PDFS_DIR / f"{slug}.pdf"
            link_or_copy(pdf_source, dst)
            merged["oa_pdf_path"] = f"{PAPER_LIB_PDFS_DIR}/{slug}.pdf"

        # Same for the abstract text.
        if abstract_source and abstract_source.exists():
            dst = root / PAPER_LIB_ABSTRACTS_DIR / f"{slug}.txt"
            link_or_copy(abstract_source, dst)

        # Convert the library PDF to markdown once and share it. Only
        # generate if we have a PDF in the library and no markdown yet.
        lib_pdf = root / PAPER_LIB_PDFS_DIR / f"{slug}.pdf"
        md_dst = root / PAPER_LIB_MARKDOWN_DIR / f"{slug}.md"
        if convert and lib_pdf.exists() and not md_dst.exists():
            text = convert_pdf_to_markdown(lib_pdf)
            if text:
                md_dst.parent.mkdir(parents=True, exist_ok=True)
                md_dst.write_text(text, encoding="utf-8")
        if md_dst.exists():
            merged["md_path"] = f"{PAPER_LIB_MARKDOWN_DIR}/{slug}.md"

        _write_manifest(root, manifest)
        _append_provenance(
            root, key=key, workspace=workspace,
            source=source, extra=extra_provenance,
        )
    return merged


def search(
    query: str,
    root: pathlib.Path | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Case-insensitive substring search over title + abstract + authors.

    Simple scoring: title hit weighted 3x, abstract 1x, authors 2x.
    For a 10k-paper library this is fast enough; if it ever gets slow,
    swap in a whoosh/BM25 index behind the same signature.
    """
    root = ensure_library(root)
    manifest = _read_manifest(root)
    q = query.lower().strip()
    if not q:
        return list(manifest.values())[:limit]
    scored: list[tuple[float, dict[str, Any]]] = []
    for p in manifest.values():
        score = 0.0
        title = (p.get("title") or "").lower()
        abstract = (p.get("abstract") or "").lower()
        authors = (p.get("authors") or "").lower()
        if q in title:
            score += 3.0
        if q in abstract:
            score += 1.0
        if q in authors:
            score += 2.0
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [p for _, p in scored[:limit]]


def stats(root: pathlib.Path | None = None) -> dict[str, int]:
    """Summary counts. Useful for `omicairn paperlib stats` CLI."""
    root = ensure_library(root)
    manifest = _read_manifest(root)
    pdfs_dir = root / PAPER_LIB_PDFS_DIR
    abs_dir = root / PAPER_LIB_ABSTRACTS_DIR
    md_dir = root / PAPER_LIB_MARKDOWN_DIR
    prov = root / PAPER_LIB_PROVENANCE
    return {
        "papers": len(manifest),
        "pdfs": len(list(pdfs_dir.glob("*.pdf"))) if pdfs_dir.exists() else 0,
        "abstracts": len(list(abs_dir.glob("*.txt"))) if abs_dir.exists() else 0,
        "markdowns": len(list(md_dir.glob("*.md"))) if md_dir.exists() else 0,
        "provenance_records": (
            sum(1 for _ in prov.open("r", encoding="utf-8"))
            if prov.exists() else 0
        ),
    }


def link_into_workspace(
    ws_literature_dir: pathlib.Path,
    keys: list[str],
    root: pathlib.Path | None = None,
) -> dict[str, int]:
    """Hardlink (or copy) library assets for `keys` into a workspace's
    literature/ folder, and write its filtered papers.json.

    Returns {linked, copied, missing} counts.

    The workspace gets its own papers.json (a filtered slice of the
    library manifest) so the anti-fabrication citation pipeline still
    reads a self-contained file. PDFs and abstracts are hardlinked
    when possible — same bytes on disk, no duplication.
    """
    root = ensure_library(root)
    ws_literature_dir = pathlib.Path(ws_literature_dir)
    ws_literature_dir.mkdir(parents=True, exist_ok=True)
    (ws_literature_dir / PAPER_LIB_PDFS_DIR).mkdir(exist_ok=True)
    (ws_literature_dir / PAPER_LIB_ABSTRACTS_DIR).mkdir(exist_ok=True)

    manifest = _read_manifest(root)
    selected: list[dict[str, Any]] = []
    linked = copied = missing = 0
    for key in keys:
        paper = manifest.get(key) or get(key, root=root)
        if paper is None:
            missing += 1
            continue
        slug = slug_for(paper)
        for sub in (PAPER_LIB_PDFS_DIR, PAPER_LIB_ABSTRACTS_DIR):
            ext = ".pdf" if sub == PAPER_LIB_PDFS_DIR else ".txt"
            src = root / sub / f"{slug}{ext}"
            if not src.exists():
                continue
            dst = ws_literature_dir / sub / f"{slug}{ext}"
            if dst.exists():
                continue
            if link_or_copy(src, dst) == "linked":
                linked += 1
            else:
                copied += 1
        selected.append(paper)

    # Write the filtered manifest.
    (ws_literature_dir / PAPER_LIB_MANIFEST).write_text(
        json.dumps(selected, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"linked": linked, "copied": copied, "missing": missing}


# ---------------------------------------------------------------------------
# Cross-workspace shared lessons (Tier 3.3)
# ---------------------------------------------------------------------------

_SHARED_LESSONS_FILE: str = "shared_lessons.jsonl"


def _shared_lessons_path() -> pathlib.Path:
    return default_library_root() / _SHARED_LESSONS_FILE


def add_lesson(lesson: dict) -> None:
    """Append a lesson to the shared cross-workspace lessons file.

    The lesson dict should follow the per-workspace lessons.jsonl
    convention: ``{id, phase, stage, root_cause, fix}``. We add an
    ``added_at`` ISO timestamp on write. Duplicate detection is
    minimal (exact root_cause string-match against existing lessons).
    """
    import datetime as dt
    if not isinstance(lesson, dict):
        raise TypeError("lesson must be a dict")
    if "phase" not in lesson or "root_cause" not in lesson:
        raise ValueError("lesson must carry at least `phase` and `root_cause`")
    lib_root = default_library_root()
    lib_root.mkdir(parents=True, exist_ok=True)
    path = _shared_lessons_path()
    # De-dupe on (phase, root_cause) exact match.
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    existing = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (existing.get("phase") == lesson.get("phase")
                        and existing.get("root_cause") == lesson.get("root_cause")):
                    return  # duplicate
        except OSError:
            pass
    with _locked(lib_root):
        with path.open("a", encoding="utf-8") as f:
            payload = dict(lesson)
            payload.setdefault(
                "added_at", dt.datetime.now(dt.timezone.utc).isoformat()
            )
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def shared_lessons_for_phase(phase: str, max_lessons: int = 5) -> str:
    """Return up to `max_lessons` shared lessons for a given phase,
    rendered as a markdown bullet list. Returns "(none)" when no
    lessons match (or the file is missing / unparseable).

    Used by orchestrator._shared_lessons_summary to populate the
    ``{{shared_lessons_for_phase}}`` token.
    """
    path = _shared_lessons_path()
    if not path.exists():
        return "(none)"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "(none)"
    matched: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("phase") == phase:
            matched.append(entry)
    if not matched:
        return "(none)"
    matched = matched[-max_lessons:]
    out = []
    for entry in matched:
        lid = entry.get("id", "L?")
        cause = (entry.get("root_cause") or "").strip().replace("\n", " ")
        fix = (entry.get("fix") or "").strip().replace("\n", " ")
        out.append(f"- **{lid}** — {cause[:280]}  ·  *fix:* {fix[:280]}")
    return "\n".join(out)


def promote_workspace_lessons(workspace_root: pathlib.Path) -> int:
    """Walk a workspace's ``knowledge/lessons.jsonl`` and promote each
    entry to the shared store via ``add_lesson``. Returns the number
    of lessons promoted (excludes duplicates already present)."""
    p = pathlib.Path(workspace_root) / "knowledge" / "lessons.jsonl"
    if not p.exists():
        return 0
    promoted = 0
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            add_lesson(entry)
            promoted += 1
        except (TypeError, ValueError):
            continue
    return promoted
