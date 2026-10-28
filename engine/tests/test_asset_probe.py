"""Tests for asset_probe.py.

Network is NEVER hit in these tests — we drive every probe through
unittest.mock to keep them hermetic. The probe module's URL extraction,
result schema, JSON write path, and rendering are all exercised
without leaving the local host.
"""
from __future__ import annotations

import json
import pathlib
import tempfile
from unittest import mock

import pytest

from omicairn import asset_probe


# ---------------------------------------------------------------------------
# URL extraction
# ---------------------------------------------------------------------------

def test_extract_urls_finds_http_https_and_dois():
    text = (
        "See https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-15667\n"
        "and https://zenodo.org/records/17225179 — full citation is\n"
        "10.1038/s41592-025-02927-5. Also http://example.com/data.tsv.\n"
    )
    urls = asset_probe._extract_urls(text)
    assert "https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-15667" in urls
    assert "https://zenodo.org/records/17225179" in urls
    assert "http://example.com/data.tsv" in urls
    assert "https://doi.org/10.1038/s41592-025-02927-5" in urls


def test_extract_urls_dedupes():
    text = "https://example.com/a https://example.com/a https://example.com/b"
    urls = asset_probe._extract_urls(text)
    assert urls.count("https://example.com/a") == 1
    assert "https://example.com/b" in urls


def test_extract_urls_strips_trailing_punctuation():
    text = "see https://example.com/path?query=1 (more)."
    urls = asset_probe._extract_urls(text)
    assert "https://example.com/path?query=1" in urls


def test_extract_urls_finds_bare_accessions():
    """Each repository accession family resolves to its canonical
    landing page, even when the accession sits in prose with no
    explicit URL (the parkinson-tcell-foothold smoke-run bug — a
    RESEARCH.md referencing 4 GSE accessions but only the 3
    surrounding text URLs were probed).
    """
    text = (
        "Datasets: GSE178265 (Kamath SN), GSM1234567 (one sample), "
        "BioProject PRJNA834801 (Wallen 2022 fecal shotgun), "
        "with SRR1234567 / SRX9876543 individual runs. "
        "ArrayExpress E-MTAB-15667. PRIDE PXD046355. "
        "MassIVE MSV000087654. Synapse syn12345678. "
        "dbGaP study phs002819.v1.p1. "
        "CELLxGENE collection d5d0df8f-4eee-49d8-a221-a288f50a1590."
    )
    urls = asset_probe._extract_urls(text)
    assert "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE178265" in urls
    assert "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM1234567" in urls
    assert "https://www.ncbi.nlm.nih.gov/bioproject/PRJNA834801" in urls
    assert "https://www.ncbi.nlm.nih.gov/sra/?term=SRR1234567" in urls
    assert "https://www.ncbi.nlm.nih.gov/sra/?term=SRX9876543" in urls
    assert (
        "https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-15667"
        in urls
    )
    assert "https://www.ebi.ac.uk/pride/archive/projects/PXD046355" in urls
    assert (
        "https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession=MSV000087654"
        in urls
    )
    assert "https://www.synapse.org/Synapse:syn12345678" in urls
    assert (
        "https://www.ncbi.nlm.nih.gov/projects/gap/cgi-bin/study.cgi"
        "?study_id=phs002819.v1.p1" in urls
    )
    assert (
        "https://cellxgene.cziscience.com/collections/"
        "d5d0df8f-4eee-49d8-a221-a288f50a1590" in urls
    )


def test_extract_urls_cellxgene_uuid_url_form():
    """The literal cellxgene.cziscience.com collection URL is also
    recognised even when the keyword is inside the URL host itself."""
    text = (
        "See cellxgene.cziscience.com/collections/"
        "d5d0df8f-4eee-49d8-a221-a288f50a1590 for the public copy."
    )
    urls = asset_probe._extract_urls(text)
    assert any("d5d0df8f-4eee-49d8-a221-a288f50a1590" in u for u in urls)


def test_extract_urls_uuid_without_cellxgene_keyword_ignored():
    """Bare UUIDs without a nearby cellxgene/cziscience keyword must
    NOT be promoted to a CELLxGENE collection URL — too many false
    positives (artefact session IDs, random hashes).
    """
    text = (
        "Internal session token: d5d0df8f-4eee-49d8-a221-a288f50a1590. "
        "(No data-repository context.)"
    )
    urls = asset_probe._extract_urls(text)
    assert not any("cellxgene" in u.lower() for u in urls)


def test_extract_urls_zenodo_doi_already_handled():
    """Zenodo DOIs (10.5281/zenodo.NNN) flow through the existing DOI
    handler — verified explicitly so future refactors don't drop the
    case while assuming a dedicated zenodo pattern is missing.
    """
    text = "Archived at 10.5281/zenodo.10729767 in the supplement."
    urls = asset_probe._extract_urls(text)
    assert "https://doi.org/10.5281/zenodo.10729767" in urls


# ---------------------------------------------------------------------------
# Accession-slug heuristic
# ---------------------------------------------------------------------------

def test_accession_slug_picks_longest_alnum_with_digit():
    assert asset_probe._accession_slug_from_url(
        "https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-15667"
    ) == "E-MTAB-15667"
    assert asset_probe._accession_slug_from_url(
        "https://zenodo.org/records/17225179"
    ) == "17225179"


def test_accession_slug_falls_back_to_host():
    # Path has no segment with a digit → returns netloc.
    assert asset_probe._accession_slug_from_url(
        "https://example.com/about/contact/"
    ) == "example.com"


# ---------------------------------------------------------------------------
# allow_web=False short-circuit
# ---------------------------------------------------------------------------

def test_probe_skipped_no_web_when_allow_web_false():
    with tempfile.TemporaryDirectory() as t:
        p = pathlib.Path(t) / "RESEARCH.md"
        p.write_text(
            "https://example.com/a\nhttps://example.com/b\n"
        )
        results = asset_probe.probe_research_md(p, allow_web=False)
        assert {r.status for r in results} == {"skipped_no_web"}
        assert {r.url for r in results} == {
            "https://example.com/a", "https://example.com/b",
        }


# ---------------------------------------------------------------------------
# Probe result classes (mocked network)
# ---------------------------------------------------------------------------

def _stub_response(status: int, content_length: str | None = None):
    class _R:
        def __init__(self):
            self.status = status
            self.headers = {}
            if content_length is not None:
                self.headers["Content-Length"] = content_length
            self.headers["Content-Type"] = "text/plain"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return _R()


def test_probe_one_ok_via_head():
    with mock.patch.object(
        asset_probe.urllib.request, "urlopen",
        return_value=_stub_response(200, "12345"),
    ):
        r = asset_probe._probe_one("https://example.com/x")
    assert r.status == "ok"
    assert r.http_status == 200
    assert r.size_bytes == 12345


def test_probe_one_missing_on_404():
    err = asset_probe.urllib.error.HTTPError(
        "https://example.com/x", 404, "Not Found", {}, None,
    )
    with mock.patch.object(
        asset_probe.urllib.request, "urlopen", side_effect=err,
    ):
        r = asset_probe._probe_one("https://example.com/x")
    assert r.status == "missing"
    assert r.http_status == 404


def test_probe_one_unreachable_on_5xx():
    err = asset_probe.urllib.error.HTTPError(
        "https://example.com/x", 503, "Service Unavailable", {}, None,
    )
    with mock.patch.object(
        asset_probe.urllib.request, "urlopen", side_effect=err,
    ):
        r = asset_probe._probe_one("https://example.com/x")
    assert r.status == "unreachable"
    assert r.http_status == 503


def test_probe_one_unreachable_on_url_error():
    with mock.patch.object(
        asset_probe.urllib.request, "urlopen",
        side_effect=asset_probe.urllib.error.URLError("dns failed"),
    ):
        r = asset_probe._probe_one("https://example.com/x")
    assert r.status == "unreachable"
    assert r.http_status is None


def test_probe_one_falls_back_to_ranged_get_on_405():
    err = asset_probe.urllib.error.HTTPError(
        "https://example.com/x", 405, "Method Not Allowed", {}, None,
    )
    side_effects = [err, _stub_response(206, "1")]

    def _stub(req, **kw):
        rv = side_effects.pop(0)
        if isinstance(rv, asset_probe.urllib.error.HTTPError):
            raise rv
        return rv

    with mock.patch.object(asset_probe.urllib.request, "urlopen", side_effect=_stub):
        r = asset_probe._probe_one("https://example.com/x")
    assert r.status == "ok"
    assert r.http_status == 206


# ---------------------------------------------------------------------------
# write_probe_json
# ---------------------------------------------------------------------------

def test_write_probe_json_atomic_and_parseable():
    with tempfile.TemporaryDirectory() as t:
        ws = pathlib.Path(t)
        results = [
            asset_probe.ProbeResult(
                url="https://example.com/a", accession="a",
                status="ok", http_status=200, size_bytes=100,
                content_type="text/plain", checked_at="2026-04-29T00:00:00Z",
                error="",
            ),
            asset_probe.ProbeResult(
                url="https://example.com/b", accession="b",
                status="missing", http_status=404, size_bytes=None,
                content_type=None, checked_at="2026-04-29T00:00:01Z",
                error="404 Not Found",
            ),
        ]
        out = asset_probe.write_probe_json(ws, results)
        assert out.name == "asset_probe.json"
        payload = json.loads(out.read_text())
        assert payload["version"] == 1
        assert len(payload["assets"]) == 2
        assert payload["assets"][0]["status"] == "ok"
        assert payload["assets"][1]["status"] == "missing"


# ---------------------------------------------------------------------------
# render_summary
# ---------------------------------------------------------------------------

def test_render_summary_groups_status_and_warns_on_missing():
    results = [
        asset_probe.ProbeResult(
            url="https://example.com/a", accession="a", status="ok",
            http_status=200, size_bytes=100, content_type=None,
            checked_at="t", error="",
        ),
        asset_probe.ProbeResult(
            url="https://example.com/b", accession="b", status="missing",
            http_status=404, size_bytes=None, content_type=None,
            checked_at="t", error="",
        ),
    ]
    rendered = asset_probe.render_summary(results)
    assert "missing" in rendered
    assert "ok" in rendered
    assert "PLAN MUST acknowledge" in rendered


def test_render_summary_empty():
    assert "no URLs" in asset_probe.render_summary([])


# ---------------------------------------------------------------------------
# Round-trip reader + DATASET_REF seeding (the lab-KB dataset-channel producer)
# ---------------------------------------------------------------------------

from omicairn import knowledge_graph as kg  # noqa: E402
from omicairn.constants import NodeType  # noqa: E402


def _write_probe(ws: pathlib.Path, assets: list[dict]) -> None:
    results = [asset_probe.ProbeResult(**a) for a in assets]
    asset_probe.write_probe_json(ws, results)


def _ok(accession: str, url: str, content_type: str = "text/html",
        size: int | None = 123) -> dict:
    return dict(url=url, accession=accession, status=asset_probe.ProbeStatus.OK,
                http_status=200, size_bytes=size, content_type=content_type,
                checked_at="t", error="")


def test_load_probe_results_round_trips(tmp_path):
    ws = tmp_path / "run-a"
    ws.mkdir()
    _write_probe(ws, [_ok("winoground", "https://hf.co/datasets/facebook/winoground")])
    rows = asset_probe.load_probe_results(ws)
    assert len(rows) == 1
    assert rows[0].accession == "winoground"
    assert rows[0].status == asset_probe.ProbeStatus.OK


def test_load_probe_results_missing_file_is_empty(tmp_path):
    assert asset_probe.load_probe_results(tmp_path / "nope") == []


def test_seed_dataset_refs_creates_dataset_ref_node(tmp_path):
    """A resolved (ok) probe row becomes a DATASET_REF node in the run KG —
    the previously-missing producer for the lab-KB dataset channel."""
    ws = tmp_path / "run-a"
    ws.mkdir()
    _write_probe(ws, [_ok("cellxgene-census", "https://github.com/chanzuckerberg/cellxgene-census")])
    n = asset_probe.seed_dataset_refs(ws)
    assert n == 1
    g = kg.load_graph(ws)
    dsets = g.nodes_of_type(NodeType.DATASET_REF)
    assert len(dsets) == 1
    assert dsets[0].title == "cellxgene-census"
    assert dsets[0].evidence_paths == ["https://github.com/chanzuckerberg/cellxgene-census"]
    assert dsets[0].extra["location"] == "https://github.com/chanzuckerberg/cellxgene-census"
    assert dsets[0].source_phase == "asset_probe"


def test_seed_dataset_refs_only_resolved_assets(tmp_path):
    """missing / unreachable assets are NOT something the lab holds — skip."""
    ws = tmp_path / "run-a"
    ws.mkdir()
    _write_probe(ws, [
        _ok("good", "https://hf.co/datasets/good"),
        dict(url="https://hf.co/datasets/gone", accession="gone",
             status=asset_probe.ProbeStatus.MISSING, http_status=404,
             size_bytes=None, content_type=None, checked_at="t", error="404"),
        dict(url="https://down.example/x", accession="down",
             status=asset_probe.ProbeStatus.UNREACHABLE, http_status=None,
             size_bytes=None, content_type=None, checked_at="t", error="timeout"),
    ])
    assert asset_probe.seed_dataset_refs(ws) == 1
    titles = {d.title for d in kg.load_graph(ws).nodes_of_type(NodeType.DATASET_REF)}
    assert titles == {"good"}


def test_seed_dataset_refs_is_idempotent(tmp_path):
    """Re-seeding upserts by stable slug — no duplicate nodes."""
    ws = tmp_path / "run-a"
    ws.mkdir()
    _write_probe(ws, [_ok("winoground", "https://hf.co/datasets/facebook/winoground")])
    asset_probe.seed_dataset_refs(ws)
    asset_probe.seed_dataset_refs(ws)  # re-seed
    dsets = kg.load_graph(ws).nodes_of_type(NodeType.DATASET_REF)
    assert len(dsets) == 1


def test_seed_dataset_refs_no_probe_is_noop(tmp_path):
    """A run that resolved no assets seeds nothing — no spurious nodes."""
    ws = tmp_path / "run-empty"
    ws.mkdir()
    assert asset_probe.seed_dataset_refs(ws) == 0
    assert kg.load_graph(ws).nodes_of_type(NodeType.DATASET_REF) == []


# ---------------------------------------------------------------------------
# T-1 fix: DATASET_REF titles must be the human-meaningful dataset identity,
# not the bare host (which collapses distinct datasets to one bullet in the
# IDEATE/SCOPE lab-knowledge surface, which dedups by title).
# ---------------------------------------------------------------------------

def test_dataset_identity_hf_dataset_url_drops_host_and_routing_prefix():
    # accession is the bare host (the _accession_slug_from_url fallback for a
    # name with no digit) → derive <org>/<name> from the URL path.
    assert asset_probe._dataset_identity(
        "huggingface.co",
        "https://huggingface.co/datasets/facebook/winoground",
    ) == "facebook/winoground"


def test_dataset_identity_github_url_gives_org_repo():
    assert asset_probe._dataset_identity(
        "github.com",
        "https://github.com/chanzuckerberg/cellxgene-census",
    ) == "chanzuckerberg/cellxgene-census"


def test_dataset_identity_keeps_real_accession_unchanged():
    # A real accession (not the host) is already a usable distinct name.
    assert asset_probe._dataset_identity(
        "GSE178265", "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE178265",
    ) == "GSE178265"
    # No URL at all → keep the plain accession (no crash).
    assert asset_probe._dataset_identity("WinoBias", "") == "WinoBias"


def test_dataset_identity_bare_host_root_falls_back_to_path_not_host():
    # No path identity beyond the host → still beats a bare host by keeping
    # the (here empty) path; degenerates to host only when truly nothing else.
    assert asset_probe._dataset_identity(
        "example.com", "https://example.com/",
    ) == "example.com"


def test_seed_dataset_refs_derives_distinct_titles_from_bare_host_accession(tmp_path):
    """Two HF datasets whose accession degraded to the bare host get TWO
    distinct, meaningful titles (the bug: they used to both be 'huggingface.co')."""
    ws = tmp_path / "run-a"
    ws.mkdir()
    _write_probe(ws, [
        _ok("huggingface.co", "https://huggingface.co/datasets/Intel/SocialCounterfactuals"),
        _ok("huggingface.co", "https://huggingface.co/datasets/facebook/winoground"),
    ])
    assert asset_probe.seed_dataset_refs(ws) == 2
    titles = {d.title for d in kg.load_graph(ws).nodes_of_type(NodeType.DATASET_REF)}
    assert titles == {"Intel/SocialCounterfactuals", "facebook/winoground"}
    assert "huggingface.co" not in titles


def test_seed_dataset_refs_node_shape_unchanged_for_real_accession(tmp_path):
    """Regression: for a real-name accession the node is byte-identical to
    before the fix (same kind, id slug, uri, evidence) — only the rendered
    title surface improves for the broken bare-host case."""
    ws = tmp_path / "run-a"
    ws.mkdir()
    url = "https://github.com/chanzuckerberg/cellxgene-census"
    _write_probe(ws, [_ok("cellxgene-census", url)])
    assert asset_probe.seed_dataset_refs(ws) == 1
    (node,) = kg.load_graph(ws).nodes_of_type(NodeType.DATASET_REF)
    assert node.type == NodeType.DATASET_REF
    assert node.title == "cellxgene-census"                 # real accession kept
    assert node.id == kg._slug("dataset:run-a:cellxgene-census")  # id keys off accession
    assert node.evidence_paths == [url]
    assert node.extra["location"] == url
