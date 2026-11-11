"""Tests for omicairn.paper_library — add/get/search/link_into_workspace."""
from __future__ import annotations

import json
import pathlib

import pytest

from omicairn import paper_library
from omicairn.constants import (
    PAPER_LIB_ABSTRACTS_DIR,
    PAPER_LIB_DIRNAME,
    PAPER_LIB_MANIFEST,
    PAPER_LIB_MARKDOWN_DIR,
    PAPER_LIB_PDFS_DIR,
    PAPER_LIB_PROVENANCE,
)
from omicairn.literature import Paper


def _mk_paper(**kw) -> Paper:
    defaults = {"doi": "10.1038/nature12113", "title": "Test Paper",
                "authors": "Smith J", "year": 2012, "abstract": "Abstract text."}
    defaults.update(kw)
    return Paper(**defaults)


def test_ensure_library_creates_layout(tmp_path: pathlib.Path) -> None:
    root = paper_library.ensure_library(tmp_path / "lib")
    assert root.is_dir()
    assert (root / PAPER_LIB_PDFS_DIR).is_dir()
    assert (root / PAPER_LIB_ABSTRACTS_DIR).is_dir()


def test_add_and_get_by_doi(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "lib"
    paper_library.add(_mk_paper(), root=root, workspace="ws-A")
    # Bare DOI
    hit = paper_library.get("10.1038/nature12113", root=root)
    assert hit is not None
    assert hit["title"] == "Test Paper"
    # Full key
    hit = paper_library.get("doi:10.1038/nature12113", root=root)
    assert hit is not None


def test_add_idempotent_merges_fields(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "lib"
    paper_library.add(_mk_paper(abstract=""), root=root, workspace="ws-A")
    # Second add with abstract fills in the gap without overwriting title.
    paper_library.add(
        _mk_paper(abstract="richer", title="Different Title Ignored"),
        root=root, workspace="ws-B",
    )
    hit = paper_library.get("10.1038/nature12113", root=root)
    assert hit["abstract"] == "richer"
    assert hit["title"] == "Test Paper"  # existing truthy value preserved


def test_add_copies_pdf_and_abstract(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "lib"
    src_pdf = tmp_path / "source.pdf"
    src_pdf.write_bytes(b"%PDF-1.4\nfake\n")
    src_abs = tmp_path / "source.txt"
    src_abs.write_text("abstract body", encoding="utf-8")

    paper_library.add(
        _mk_paper(), root=root,
        pdf_source=src_pdf, abstract_source=src_abs,
        workspace="ws-A", source="literature",
    )
    # Content-addressable location
    slug = "10.1038_nature12113"
    assert (root / PAPER_LIB_PDFS_DIR / f"{slug}.pdf").read_bytes() == b"%PDF-1.4\nfake\n"
    assert (root / PAPER_LIB_ABSTRACTS_DIR / f"{slug}.txt").read_text() == "abstract body"


def test_add_hardlinks_pdf_not_copies_on_same_fs(tmp_path: pathlib.Path) -> None:
    """REGRESSION (disk-redundancy audit): add() must REFERENCE the source
    PDF by hardlink (shared inode, st_nlink>=2) — NOT byte-copy it — so the
    shared library copy and the originating workspace copy occupy the bytes
    once. Previously `shutil.copy2` made every shared PDF an independent
    inode (~1.5-3 GB of dups across 22 workspaces)."""
    root = tmp_path / "lib"
    src_pdf = tmp_path / "source.pdf"
    src_pdf.write_bytes(b"%PDF-1.4\nshared-bytes\n")
    paper_library.add(_mk_paper(), root=root, pdf_source=src_pdf,
                      workspace="ws-A", convert=False)
    slug = "10.1038_nature12113"
    dst = root / PAPER_LIB_PDFS_DIR / f"{slug}.pdf"
    assert dst.read_bytes() == b"%PDF-1.4\nshared-bytes\n"
    assert dst.stat().st_ino == src_pdf.stat().st_ino  # one inode, not a copy
    assert src_pdf.stat().st_nlink >= 2


def test_add_hardlink_falls_back_to_copy_cross_device(
        tmp_path: pathlib.Path, monkeypatch) -> None:
    """When os.link fails (cross-device / Windows), add() still lands the PDF
    via the copy fallback — content correct, separate inode."""
    root = tmp_path / "lib"
    src_pdf = tmp_path / "source.pdf"
    src_pdf.write_bytes(b"%PDF-1.4\nxdev\n")
    monkeypatch.setattr(paper_library.os, "link",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("xdev")))
    paper_library.add(_mk_paper(), root=root, pdf_source=src_pdf,
                      workspace="ws-A", convert=False)
    dst = root / PAPER_LIB_PDFS_DIR / "10.1038_nature12113.pdf"
    assert dst.read_bytes() == b"%PDF-1.4\nxdev\n"
    assert dst.stat().st_ino != src_pdf.stat().st_ino  # copied, separate inode


def test_add_no_doi_arxiv_then_get_by_slug(tmp_path: pathlib.Path) -> None:
    """An arXiv paper (NO doi, NO pmid) is stored under its title slug and is
    findable via get_by_slug — the DOI-free identity path the reuse lookup
    uses. The dedup-key `get` cannot find it (no DOI/PMID), but get_by_slug
    matches on the slug both add and reuse compute."""
    root = tmp_path / "lib"
    arxiv = Paper(title="Representation Engineering: A Top-Down Approach",
                  authors="Zou", year=2023, abstract="abs")
    paper_library.add(arxiv, root=root, workspace="ws-A", convert=False)
    slug = paper_library.slug_for(arxiv)
    assert slug.startswith("title_")
    hit = paper_library.get_by_slug(slug, root=root)
    assert hit is not None
    assert hit["title"] == "Representation Engineering: A Top-Down Approach"
    # The DOI/PMID-keyed get genuinely cannot resolve a no-DOI paper.
    assert paper_library.get(slug, root=root) is None


def test_get_by_slug_returns_none_when_absent(tmp_path: pathlib.Path) -> None:
    assert paper_library.get_by_slug("title_nope", root=tmp_path / "lib") is None


def test_provenance_log_is_appended_per_add(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "lib"
    paper_library.add(_mk_paper(), root=root, workspace="ws-A", source="literature")
    paper_library.add(_mk_paper(), root=root, workspace="ws-B", source="citation_resolver")
    lines = (root / PAPER_LIB_PROVENANCE).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    recs = [json.loads(l) for l in lines]
    assert {r["workspace"] for r in recs} == {"ws-A", "ws-B"}
    assert {r["source"] for r in recs} == {"literature", "citation_resolver"}


def test_search_ranks_title_above_abstract(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "lib"
    paper_library.add(_mk_paper(
        doi="10.1/x", title="Pyroptosis in LUAD",
        abstract="unrelated"), root=root)
    paper_library.add(_mk_paper(
        doi="10.2/y", title="Other topic",
        abstract="mentions pyroptosis once"), root=root)
    hits = paper_library.search("pyroptosis", root=root)
    assert len(hits) == 2
    assert hits[0]["title"] == "Pyroptosis in LUAD"  # title hit wins


def test_stats_counts(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "lib"
    src_pdf = tmp_path / "s.pdf"
    src_pdf.write_bytes(b"fake")
    paper_library.add(_mk_paper(), root=root, pdf_source=src_pdf,
                      workspace="ws-A")
    paper_library.add(_mk_paper(doi="10.2/y", title="Other"), root=root,
                      workspace="ws-A")
    s = paper_library.stats(root=root)
    assert s["papers"] == 2
    assert s["pdfs"] == 1
    assert s["provenance_records"] == 2


def test_link_into_workspace_writes_filtered_manifest(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "lib"
    pdf = tmp_path / "s.pdf"
    pdf.write_bytes(b"PDF")
    paper_library.add(_mk_paper(), root=root, pdf_source=pdf, workspace="ws-A")
    paper_library.add(_mk_paper(doi="10.2/z", title="Unused"), root=root,
                      workspace="ws-A")

    ws_lit = tmp_path / "ws-B" / "literature"
    res = paper_library.link_into_workspace(
        ws_lit, keys=["doi:10.1038/nature12113"], root=root,
    )
    assert res["missing"] == 0
    # Filtered papers.json has only the one we asked for.
    manifest = json.loads((ws_lit / PAPER_LIB_MANIFEST).read_text())
    assert len(manifest) == 1
    assert manifest[0]["doi"] == "10.1038/nature12113"
    # PDF landed in the workspace literature dir.
    assert (ws_lit / PAPER_LIB_PDFS_DIR / "10.1038_nature12113.pdf").exists()


def test_env_var_overrides_default_root(tmp_path, monkeypatch) -> None:
    custom = tmp_path / "custom-lib"
    monkeypatch.setenv("OMICAIRN_PAPER_LIB", str(custom))
    root = paper_library.default_library_root()
    assert root == custom.resolve()


def test_slug_for_handles_no_doi_no_pmid(tmp_path: pathlib.Path) -> None:
    p = Paper(title="Hard Title, With/Slashes!")
    slug = paper_library.slug_for(p)
    assert slug.startswith("title_")
    assert "/" not in slug


def test_get_returns_none_when_missing(tmp_path: pathlib.Path) -> None:
    assert paper_library.get("10.1/absent", root=tmp_path / "lib") is None


# ---------------------------------------------------------------------------
# Tier 3.3 — shared lessons store
# ---------------------------------------------------------------------------

def test_shared_lessons_for_phase_returns_none_when_no_file(tmp_path, monkeypatch):
    """When the lib has no lessons file, render '(none)'."""
    monkeypatch.setenv("OMICAIRN_PAPER_LIB", str(tmp_path / "lib"))
    assert paper_library.shared_lessons_for_phase("execute") == "(none)"


def test_add_lesson_and_lookup_by_phase(tmp_path, monkeypatch):
    monkeypatch.setenv("OMICAIRN_PAPER_LIB", str(tmp_path / "lib"))
    paper_library.add_lesson({
        "id": "L01",
        "phase": "execute",
        "stage": "002_execute",
        "root_cause": "scRNA-seq RDS truncated at 50%",
        "fix": "Pre-fetch large objects from SCOPE in parallel",
    })
    rendered = paper_library.shared_lessons_for_phase("execute")
    assert "L01" in rendered
    assert "scRNA-seq" in rendered
    assert paper_library.shared_lessons_for_phase("review") == "(none)"


def test_add_lesson_dedupes_on_phase_and_root_cause(tmp_path, monkeypatch):
    monkeypatch.setenv("OMICAIRN_PAPER_LIB", str(tmp_path / "lib"))
    lesson = {
        "id": "L01",
        "phase": "execute",
        "stage": "002_execute",
        "root_cause": "same root cause",
        "fix": "fix",
    }
    paper_library.add_lesson(lesson)
    paper_library.add_lesson(lesson)
    # Only one entry should exist.
    path = paper_library._shared_lessons_path()
    lines = [l for l in path.read_text().splitlines() if l.strip()]
    assert len(lines) == 1


def test_promote_workspace_lessons_appends_only_new(tmp_path, monkeypatch):
    monkeypatch.setenv("OMICAIRN_PAPER_LIB", str(tmp_path / "lib"))
    ws = tmp_path / "ws"
    (ws / "knowledge").mkdir(parents=True)
    (ws / "knowledge" / "lessons.jsonl").write_text(
        json.dumps({"id": "L01", "phase": "execute",
                    "root_cause": "rc1", "fix": "f1"}) + "\n"
        + json.dumps({"id": "L02", "phase": "review",
                      "root_cause": "rc2", "fix": "f2"}) + "\n"
    )
    n = paper_library.promote_workspace_lessons(ws)
    assert n == 2
    # Re-promoting same workspace adds nothing new (dedup).
    n2 = paper_library.promote_workspace_lessons(ws)
    assert n2 == 2  # promote_workspace_lessons counts attempts; add_lesson dedupes
    # Verify only 2 unique lines persisted.
    path = paper_library._shared_lessons_path()
    lines = [l for l in path.read_text().splitlines() if l.strip()]
    assert len(lines) == 2


def test_add_lesson_rejects_missing_required_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("OMICAIRN_PAPER_LIB", str(tmp_path / "lib"))
    with pytest.raises(ValueError):
        paper_library.add_lesson({"phase": "execute"})  # no root_cause
    with pytest.raises(TypeError):
        paper_library.add_lesson("not a dict")


# ---------------------------------------------------------------------------
# resolve_root precedence (mirrors lab_knowledge.resolve_root)
# ---------------------------------------------------------------------------

def test_resolve_root_default_is_visible_not_dotcache(tmp_path, monkeypatch):
    """Default must NOT land in a hidden ~/.cache dir — it's a visible
    <workspaces_root>/lab_library folder."""
    monkeypatch.delenv("OMICAIRN_PAPER_LIB", raising=False)
    ws_root = tmp_path / "workspaces"
    root = paper_library.resolve_root(workspaces_root=ws_root)
    assert root == (ws_root / PAPER_LIB_DIRNAME).resolve()
    assert ".cache" not in str(root)
    assert not str(root).startswith(str(pathlib.Path.home() / ".cache"))


def test_resolve_root_cwd_fallback(tmp_path, monkeypatch):
    """No env, no workspaces_root → <cwd>/workspaces/lab_library."""
    monkeypatch.delenv("OMICAIRN_PAPER_LIB", raising=False)
    monkeypatch.chdir(tmp_path)
    root = paper_library.resolve_root()
    assert root == (tmp_path / "workspaces" / PAPER_LIB_DIRNAME).resolve()


def test_resolve_root_env_override(tmp_path, monkeypatch):
    custom = tmp_path / "env-lib"
    monkeypatch.setenv("OMICAIRN_PAPER_LIB", str(custom))
    # env beats workspaces_root default
    root = paper_library.resolve_root(workspaces_root=tmp_path / "ws")
    assert root == custom.resolve()


def test_resolve_root_explicit_wins_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OMICAIRN_PAPER_LIB", str(tmp_path / "env-lib"))
    explicit = tmp_path / "explicit-lib"
    root = paper_library.resolve_root(explicit=explicit,
                                      workspaces_root=tmp_path / "ws")
    assert root == explicit.resolve()


def test_resolve_root_is_side_effect_free(tmp_path, monkeypatch):
    """Resolution does not create the directory."""
    monkeypatch.delenv("OMICAIRN_PAPER_LIB", raising=False)
    root = paper_library.resolve_root(workspaces_root=tmp_path / "ws")
    assert not root.exists()


def test_ensure_library_creates_on_demand(tmp_path, monkeypatch):
    monkeypatch.delenv("OMICAIRN_PAPER_LIB", raising=False)
    root = paper_library.resolve_root(workspaces_root=tmp_path / "ws")
    assert not root.exists()
    created = paper_library.ensure_library(root)
    assert created.is_dir()
    assert (created / PAPER_LIB_MARKDOWN_DIR).is_dir()


# ---------------------------------------------------------------------------
# workspaces_root_for — robust workspaces-root resolution (audit 2026-06-10)
# ---------------------------------------------------------------------------

def test_workspaces_root_for_finds_state_md_workspace(tmp_path):
    """A literature dir directly under a STATE.md-bearing workspace resolves
    to the workspaces root (the workspace's parent)."""
    workspaces = tmp_path / "workspaces"
    ws = workspaces / "run-1"
    ws.mkdir(parents=True)
    (ws / "STATE.md").write_text("---\n---\n")
    assert paper_library.workspaces_root_for(ws / "literature") == workspaces


def test_workspaces_root_for_handles_stage_nested_literature(tmp_path):
    """REGRESSION: a stage-nested `<ws>/<NNN_stage>/literature` must STILL
    resolve to the workspaces root, not the workspace itself (the bug that
    stranded a per-workspace lab_library copy on d3)."""
    workspaces = tmp_path / "workspaces"
    ws = workspaces / "run-1"
    ws.mkdir(parents=True)
    (ws / "STATE.md").write_text("---\n---\n")
    nested = ws / "001_plan" / "literature"
    nested.mkdir(parents=True)
    assert paper_library.workspaces_root_for(nested) == workspaces


def test_workspaces_root_for_returns_none_without_workspace(tmp_path):
    """No STATE.md ancestor → None, so callers fall back to their guess."""
    bare = tmp_path / "a" / "b" / "literature"
    bare.mkdir(parents=True)
    assert paper_library.workspaces_root_for(bare) is None


# ---------------------------------------------------------------------------
# link_or_copy — hardlink (dedup) with copy fallback
# ---------------------------------------------------------------------------

def test_link_or_copy_hardlinks_on_same_fs(tmp_path):
    src = tmp_path / "src.pdf"
    src.write_bytes(b"%PDF-1.4\nx\n")
    dst = tmp_path / "sub" / "dst.pdf"
    assert paper_library.link_or_copy(src, dst) == "linked"
    assert dst.read_bytes() == b"%PDF-1.4\nx\n"
    assert dst.stat().st_ino == src.stat().st_ino  # one inode, deduped
    assert src.stat().st_nlink == 2


def test_link_or_copy_falls_back_to_copy_when_link_fails(tmp_path, monkeypatch):
    src = tmp_path / "src.pdf"
    src.write_bytes(b"BYTES")
    dst = tmp_path / "dst.pdf"
    monkeypatch.setattr(paper_library.os, "link",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("xdev")))
    assert paper_library.link_or_copy(src, dst) == "copied"
    assert dst.read_bytes() == b"BYTES"
    assert dst.stat().st_ino != src.stat().st_ino  # separate inode (copied)


def test_link_or_copy_is_noop_when_dst_exists(tmp_path):
    src = tmp_path / "src.pdf"
    src.write_bytes(b"NEW")
    dst = tmp_path / "dst.pdf"
    dst.write_bytes(b"OLD")
    # Must not overwrite an existing destination.
    paper_library.link_or_copy(src, dst)
    assert dst.read_bytes() == b"OLD"


# ---------------------------------------------------------------------------
# PDF → markdown conversion (pdftotext monkeypatched — never shell out)
# ---------------------------------------------------------------------------

class _FakeProc:
    def __init__(self, stdout=b"", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_convert_pdf_to_markdown_success(tmp_path, monkeypatch):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    def fake_run(cmd, capture_output=True, timeout=None):
        assert cmd[0] == "pdftotext"
        return _FakeProc(stdout="CONVERTED BODY".encode("utf-8"))

    monkeypatch.setattr(paper_library.subprocess, "run", fake_run)
    text = paper_library.convert_pdf_to_markdown(pdf)
    assert text == "CONVERTED BODY"


def test_convert_pdf_to_markdown_missing_binary_returns_none(tmp_path, monkeypatch):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    def fake_run(cmd, capture_output=True, timeout=None):
        raise FileNotFoundError("pdftotext: not found")

    monkeypatch.setattr(paper_library.subprocess, "run", fake_run)
    assert paper_library.convert_pdf_to_markdown(pdf) is None


def test_convert_pdf_to_markdown_missing_file_returns_none(tmp_path):
    assert paper_library.convert_pdf_to_markdown(tmp_path / "nope.pdf") is None


def test_convert_pdf_to_markdown_empty_output_returns_none(tmp_path, monkeypatch):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(paper_library.subprocess, "run",
                        lambda *a, **k: _FakeProc(stdout=b"   \n  "))
    assert paper_library.convert_pdf_to_markdown(pdf) is None


def test_add_stores_markdown_and_records_md_path(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    src_pdf = tmp_path / "source.pdf"
    src_pdf.write_bytes(b"%PDF-1.4\nfake\n")
    monkeypatch.setattr(paper_library.subprocess, "run",
                        lambda *a, **k: _FakeProc(stdout=b"FULL TEXT MD"))
    stored = paper_library.add(_mk_paper(), root=root, pdf_source=src_pdf,
                               workspace="ws-A", convert=True)
    slug = "10.1038_nature12113"
    md = root / PAPER_LIB_MARKDOWN_DIR / f"{slug}.md"
    assert md.read_text(encoding="utf-8") == "FULL TEXT MD"
    assert stored["md_path"] == f"{PAPER_LIB_MARKDOWN_DIR}/{slug}.md"


def test_add_convert_false_skips_markdown(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    src_pdf = tmp_path / "source.pdf"
    src_pdf.write_bytes(b"%PDF-1.4\nfake\n")
    called = {"n": 0}

    def fake_run(*a, **k):
        called["n"] += 1
        return _FakeProc(stdout=b"SHOULD NOT RUN")

    monkeypatch.setattr(paper_library.subprocess, "run", fake_run)
    paper_library.add(_mk_paper(), root=root, pdf_source=src_pdf,
                      convert=False)
    assert called["n"] == 0
    assert not (root / PAPER_LIB_MARKDOWN_DIR / "10.1038_nature12113.md").exists()


def test_add_no_markdown_when_conversion_fails_gracefully(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    src_pdf = tmp_path / "source.pdf"
    src_pdf.write_bytes(b"%PDF-1.4\nfake\n")
    monkeypatch.setattr(paper_library.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    stored = paper_library.add(_mk_paper(), root=root, pdf_source=src_pdf,
                               convert=True)
    assert "md_path" not in stored
    assert not (root / PAPER_LIB_MARKDOWN_DIR / "10.1038_nature12113.md").exists()


def test_get_markdown_roundtrip(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    src_pdf = tmp_path / "source.pdf"
    src_pdf.write_bytes(b"%PDF-1.4\nfake\n")
    monkeypatch.setattr(paper_library.subprocess, "run",
                        lambda *a, **k: _FakeProc(stdout=b"BODY TEXT"))
    paper_library.add(_mk_paper(), root=root, pdf_source=src_pdf, convert=True)
    # By bare DOI and by full key.
    assert paper_library.get_markdown("10.1038/nature12113", root=root) == "BODY TEXT"
    assert paper_library.get_markdown("doi:10.1038/nature12113", root=root) == "BODY TEXT"


def test_get_markdown_returns_none_when_absent(tmp_path):
    assert paper_library.get_markdown("10.1/absent", root=tmp_path / "lib") is None


def test_dedup_by_pmid(tmp_path):
    root = tmp_path / "lib"
    paper_library.add(Paper(pmid="999111", title="By PMID"), root=root)
    paper_library.add(Paper(pmid="999111", title="Dup ignored",
                            abstract="added"), root=root)
    hit = paper_library.get("999111", root=root)
    assert hit is not None
    assert hit["title"] == "By PMID"
    assert hit["abstract"] == "added"
    assert paper_library.stats(root=root)["papers"] == 1


def test_stats_counts_markdowns(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    src_pdf = tmp_path / "s.pdf"
    src_pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(paper_library.subprocess, "run",
                        lambda *a, **k: _FakeProc(stdout=b"md text"))
    paper_library.add(_mk_paper(), root=root, pdf_source=src_pdf, convert=True)
    s = paper_library.stats(root=root)
    assert s["markdowns"] == 1


# ---------------------------------------------------------------------------
# CLI smoke (--root + stats + convert) via cli.main() + monkeypatched argv
# ---------------------------------------------------------------------------

def _run_cli(monkeypatch, argv: list[str]) -> int:
    from omicairn import cli
    monkeypatch.setattr("sys.argv", ["omicairn", *argv])
    return cli.main()


def test_cli_paperlib_stats_with_root(tmp_path, monkeypatch, capsys):
    root = tmp_path / "lib"
    paper_library.add(_mk_paper(), root=root, workspace="ws-A")
    rc = _run_cli(monkeypatch, ["paperlib", "stats", "--root", str(root)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "papers: 1" in out
    assert "markdowns: 0" in out
    assert str(root.resolve()) in out


def test_cli_paperlib_convert_all_with_root(tmp_path, monkeypatch, capsys):
    root = tmp_path / "lib"
    src_pdf = tmp_path / "s.pdf"
    src_pdf.write_bytes(b"%PDF-1.4\n")
    # Add WITHOUT markdown (convert=False), then convert via CLI.
    paper_library.add(_mk_paper(), root=root, pdf_source=src_pdf, convert=False)
    monkeypatch.setattr(paper_library.subprocess, "run",
                        lambda *a, **k: _FakeProc(stdout=b"converted via cli"))
    rc = _run_cli(monkeypatch,
                  ["paperlib", "convert", "--all", "--root", str(root)])
    assert rc == 0
    assert "converted: 1" in capsys.readouterr().out
    assert paper_library.get_markdown("10.1038/nature12113", root=root) \
        == "converted via cli"


def test_cli_paperlib_convert_requires_target_or_all(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    paper_library.ensure_library(root)
    rc = _run_cli(monkeypatch, ["paperlib", "convert", "--root", str(root)])
    assert rc == 2
