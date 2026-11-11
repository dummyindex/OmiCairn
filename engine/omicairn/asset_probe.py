"""Asset-probe pre-flight.

Inspects URLs / accessions named in ``RESEARCH.md`` BEFORE the SCOPE
phase runs, so PLAN can pre-register hypotheses that respect what is
actually fetchable. The morphogen-organoid run burned five EXECUTE
retries discovering one missing asset at a time; this probe converts
those discoveries into a single up-front audit.

Output artifact: ``<workspace>/asset_probe.json`` with one row per
asset found in RESEARCH.md, keyed by canonical URL.

Schema (one entry per asset)::

    {
      "url": "<canonical http(s) URL>",
      "accession": "<slug or accession id, best-effort>",
      "status": "ok | missing | unreachable | not_probed | skipped_no_web",
      "http_status": <int or null>,
      "size_bytes": <int or null>,           // from Content-Length
      "content_type": "<str or null>",
      "checked_at": "<ISO8601 timestamp>",
      "error": "<str or empty>"
    }

The probe is intentionally domain-agnostic: it scans
``RESEARCH.md.research_question`` and the literal text of the file
for ``http://`` / ``https://`` URLs and DOI patterns, and emits one
probe row per unique URL discovered. PLAN reads the JSON via the
``{{asset_probe_summary}}`` token and weighs hypotheses against the
``status`` field.

Network discipline
------------------
- Uses HEAD then falls back to a GET with a 1-byte Range request when
  HEAD is rejected (some hosts only respond to GET with Range).
- Per-URL timeout: 15s connect, 15s read.
- Total wall time bounded by ``max_total_seconds`` (default 90s).
- When ``allow_web=False`` is set in RESEARCH.md, every asset is
  recorded with status ``skipped_no_web`` and probe completes
  immediately with no network calls.
- Idempotent: writes to a ``.tmp`` sibling and renames atomically so
  a partial probe does not poison the file.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import http.client
import json
import logging
import pathlib
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Final, Iterable

logger = logging.getLogger(__name__)


class ProbeStatus:
    """Canonical asset-probe status strings (no scattered literals).

    These are the values written into ``asset_probe.json``'s per-asset
    ``status`` field. Consumers (CROSS_CHECK W9, the lab-KB dataset-ref
    seeder) match against these constants rather than re-typing the
    literal — one source of truth for "did the run actually resolve this
    asset".
    """

    OK: Final[str] = "ok"                      # HTTP 2xx — reachable/resolved
    MISSING: Final[str] = "missing"            # HTTP 4xx
    UNREACHABLE: Final[str] = "unreachable"    # DNS / conn / 5xx / timeout
    NOT_PROBED: Final[str] = "not_probed"      # budget exceeded
    SKIPPED_NO_WEB: Final[str] = "skipped_no_web"  # allow_web=False

    # Statuses that mean a hypothesis depending on the asset is blocked.
    BLOCKED: Final[frozenset[str]] = frozenset({MISSING, UNREACHABLE})
    # Render order for the summary table (worst-first).
    RENDER_ORDER: Final[tuple[str, ...]] = (
        MISSING, UNREACHABLE, SKIPPED_NO_WEB, NOT_PROBED, OK,
    )


_URL_PATTERN = re.compile(
    r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=\-%]+",
    re.IGNORECASE,
)
# Plain DOI without scheme (10.1038/...). We resolve via doi.org.
_DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/[^\s\)\]\>\"',]+", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Bare-accession patterns
# ---------------------------------------------------------------------------
#
# RESEARCH.md authors very often reference public-data assets by their
# bare accession ID rather than as a full URL (e.g. "GSE178265" or
# "PXD046355" sitting in prose). The URL/DOI patterns above miss
# those; this list resolves each accession family to its canonical
# landing page so the probe can include them in `asset_probe.json`.
#
# Policy note (the design principles "no hardcoded domain terms"): this list is
# *infrastructure*, not biomedical domain knowledge. Each entry is a
# public-data REPOSITORY (NCBI, EBI, PRIDE, MassIVE, Synapse, dbGaP,
# CELLxGENE, Zenodo). Adding a new repository here is the same kind
# of change as adding a new HTTP host to a network library — it is
# domain-agnostic by construction. Do NOT add patterns that encode
# disease names, gene symbols, or method tokens.
#
# Each tuple is (compiled regex, URL template). The regex must use a
# single named or positional group capturing the canonical accession
# string; the URL template gets ``.format(acc=<matched-string>)``.
_BARE_ACCESSION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # NCBI GEO series and samples.
    (re.compile(r"\bGSE\d+\b"),
     "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={acc}"),
    (re.compile(r"\bGSM\d+\b"),
     "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={acc}"),
    # NCBI BioProject (PRJNA / PRJEB / PRJEA / PRJNB ...).
    (re.compile(r"\bPRJ[NE][AB]\d+\b"),
     "https://www.ncbi.nlm.nih.gov/bioproject/{acc}"),
    # NCBI SRA: SRR (runs) and SRX (experiments).
    (re.compile(r"\bSR[RX]\d+\b"),
     "https://www.ncbi.nlm.nih.gov/sra/?term={acc}"),
    # EBI ArrayExpress / BioStudies (E-MTAB-*, E-GEOD-*, E-PROT-* ...).
    (re.compile(r"\bE-[A-Z]{4}-\d+\b"),
     "https://www.ebi.ac.uk/biostudies/arrayexpress/studies/{acc}"),
    # PRIDE proteomics archive.
    (re.compile(r"\bPXD\d{6}\b"),
     "https://www.ebi.ac.uk/pride/archive/projects/{acc}"),
    # MassIVE proteomics archive.
    (re.compile(r"\bMSV\d{9}\b"),
     "https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession={acc}"),
    # Synapse data commons.
    (re.compile(r"\bsyn\d+\b"),
     "https://www.synapse.org/Synapse:{acc}"),
    # dbGaP study accessions (e.g. phs001234.v1.p1).
    (re.compile(r"\bphs\d+\.v\d+\.p\d+\b"),
     "https://www.ncbi.nlm.nih.gov/projects/gap/cgi-bin/study.cgi?study_id={acc}"),
)

# CELLxGENE collection UUIDs (8-4-4-4-12 hex). Bare UUIDs are too
# generic to match unconditionally (a random hex blob can match), so we
# accept a UUID only when it is:
#   (a) embedded in the literal CELLxGENE collection URL, OR
#   (b) within ~80 characters of "cellxgene" or "cziscience" in the
#       surrounding text (case-insensitive).
_CELLXGENE_URL_PATTERN: re.Pattern[str] = re.compile(
    r"cellxgene\.cziscience\.com/collections/"
    r"([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})",
    re.IGNORECASE,
)
_UUID_PATTERN: re.Pattern[str] = re.compile(
    r"\b([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\b",
    re.IGNORECASE,
)
_CELLXGENE_KEYWORD_PATTERN: re.Pattern[str] = re.compile(
    r"(cellxgene|cziscience)", re.IGNORECASE,
)
_CELLXGENE_NEIGHBOURHOOD_CHARS: int = 80
_CELLXGENE_COLLECTION_URL_TEMPLATE: str = (
    "https://cellxgene.cziscience.com/collections/{acc}"
)

# Hosts we never probe — common patterns that are noise (reference URLs in
# CLAUDE.md, deprecated mirrors, etc.). Add to this list as patterns
# emerge; for now empty.
_HOST_BLOCKLIST: frozenset[str] = frozenset()

# Canonical workspace-relative name of the probe artifact. One source of
# truth for the filename so readers/writers never re-type the literal.
PROBE_FILENAME: Final[str] = "asset_probe.json"

_DEFAULT_PER_URL_TIMEOUT_S: float = 15.0
_DEFAULT_TOTAL_BUDGET_S: float = 90.0
_USER_AGENT: str = "OmiCairn/asset-probe (+https://github.com/anthropics/claude-code)"


@dataclasses.dataclass
class ProbeResult:
    url: str
    accession: str
    status: str
    http_status: int | None
    size_bytes: int | None
    content_type: str | None
    checked_at: str
    error: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _accession_slug_from_url(url: str) -> str:
    """Best-effort accession/slug extractor.

    Heuristics: take the longest path segment that looks like an
    accession (alphanumeric + hyphens, ≥4 chars, contains a digit). If
    none qualify, return the host. Always returns SOMETHING non-empty.
    """
    try:
        p = urllib.parse.urlparse(url)
    except Exception:
        return url
    parts = [s for s in p.path.split("/") if s]
    candidates = [
        s for s in parts
        if len(s) >= 4 and any(c.isdigit() for c in s) and re.fullmatch(r"[A-Za-z0-9_.\-]+", s)
    ]
    if candidates:
        return max(candidates, key=len)
    return p.netloc or url


# Number of trailing URL path segments that constitute a recognizable
# dataset / repository identity (``<org>/<name>``). HuggingFace dataset URLs
# (``/datasets/<org>/<name>``), HF model URLs (``/<org>/<name>``) and GitHub
# repo URLs (``/<org>/<repo>``) all share this owner/name shape, so a single
# structural rule covers them without per-host special-casing.
_IDENTITY_PATH_SEGMENTS: Final[int] = 2
# Leading path segments that are routing prefixes, not part of the identity
# (e.g. HuggingFace ``/datasets/<org>/<name>``). Dropped before taking the
# trailing ``<org>/<name>`` so the title is the dataset itself, not "datasets".
_PATH_ROUTING_PREFIXES: Final[frozenset[str]] = frozenset({"datasets", "models"})


def _dataset_identity(accession: str, url: str) -> str:
    """Canonical, human-meaningful identity for a probed asset.

    The probe's ``accession`` is the preferred title *when it is a real
    name* — but ``_accession_slug_from_url`` falls back to the bare host
    (``huggingface.co`` / ``github.com``) whenever no path segment looks
    like a classic accession id, which is exactly the common HF/GitHub case
    (``facebook/winoground`` has no digit). A bare-host title is useless and,
    worse, collapses every distinct dataset from that host into one bullet in
    the IDEATE/SCOPE lab-knowledge surface (it dedups by title).

    This derives a distinct, recognizable title structurally (``urlsplit``,
    no regex string-soup):

    * If ``accession`` is a real name (not equal to the URL host), keep it.
    * Otherwise take the trailing ``<org>/<name>`` of the URL path, after
      dropping routing prefixes like ``datasets`` / ``models`` — e.g.
      ``https://huggingface.co/datasets/facebook/winoground`` → ``facebook/winoground``,
      ``https://github.com/chanzuckerberg/cellxgene-census`` → ``chanzuckerberg/cellxgene-census``.
    * If the path has only one meaningful segment, use it alone.
    * Fall back to ``host/<path>`` (full path, not bare host) and finally to
      the raw accession/url so the result is always non-empty and distinct.
    """
    acc = (accession or "").strip()
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        parts = None

    host = (parts.netloc if parts else "").lower()
    # A non-host accession is already a usable, distinct name — keep it.
    if acc and acc.lower() != host:
        return acc

    if parts is not None:
        segs = [s for s in parts.path.split("/") if s]
        # Drop leading routing prefixes (datasets/, models/) so the identity
        # is the asset itself, not the route to it.
        while segs and segs[0].lower() in _PATH_ROUTING_PREFIXES:
            segs = segs[1:]
        if segs:
            identity = "/".join(segs[-_IDENTITY_PATH_SEGMENTS:])
            if identity:
                return identity
        # No path identity (e.g. bare host root) — host + path beats bare host.
        if host:
            return f"{host}{parts.path}".rstrip("/") or host

    return acc or url


def _extract_urls(text: str) -> list[str]:
    """Extract unique http(s) URLs, DOI shorthand, and bare accession
    tokens (GEO, SRA, BioProject, PRIDE, MassIVE, Synapse, dbGaP,
    ArrayExpress/BioStudies, CELLxGENE collections) from a text blob.

    DOIs are resolved to ``https://doi.org/<doi>``. Bare accessions are
    resolved to the canonical landing page for their repository (see
    ``_BARE_ACCESSION_PATTERNS``).
    """
    seen: set[str] = set()
    out: list[str] = []

    def _add(u: str) -> None:
        if u in seen:
            return
        seen.add(u)
        out.append(u)

    for m in _URL_PATTERN.finditer(text):
        u = m.group(0).rstrip(".,;:)]'\"")
        # Skip pure host blocklist matches (none currently).
        try:
            host = urllib.parse.urlparse(u).netloc.lower()
        except Exception:
            host = ""
        if host in _HOST_BLOCKLIST:
            continue
        _add(u)
    for m in _DOI_PATTERN.finditer(text):
        doi = m.group(0).rstrip(".,;:)]'\"")
        _add(f"https://doi.org/{doi}")

    # Bare-accession patterns. Each match is resolved to its repository
    # landing page via the (regex, template) tuple list above.
    for pat, template in _BARE_ACCESSION_PATTERNS:
        for m in pat.finditer(text):
            acc = m.group(0)
            _add(template.format(acc=acc))

    # CELLxGENE collection UUIDs. We accept a UUID in one of two ways:
    #   (1) it appears inside the literal cellxgene.cziscience.com URL
    #       (caught here by the dedicated regex — the surrounding URL
    #       may already have been captured above, but we still emit
    #       the canonical collection landing page to be sure);
    #   (2) it appears within ~80 chars of a "cellxgene" / "cziscience"
    #       keyword, allowing prose references like
    #       "CELLxGENE collection d5d0df8f-..." to be picked up.
    for m in _CELLXGENE_URL_PATTERN.finditer(text):
        _add(_CELLXGENE_COLLECTION_URL_TEMPLATE.format(acc=m.group(1)))
    for m in _UUID_PATTERN.finditer(text):
        uuid = m.group(1)
        lo = max(0, m.start() - _CELLXGENE_NEIGHBOURHOOD_CHARS)
        hi = min(len(text), m.end() + _CELLXGENE_NEIGHBOURHOOD_CHARS)
        if _CELLXGENE_KEYWORD_PATTERN.search(text[lo:hi]):
            _add(_CELLXGENE_COLLECTION_URL_TEMPLATE.format(acc=uuid))

    return out


def _probe_one(
    url: str,
    per_url_timeout_s: float = _DEFAULT_PER_URL_TIMEOUT_S,
) -> ProbeResult:
    """HEAD-first probe of a single URL.

    Returns a ProbeResult with status one of:
    - ``ok``           : HTTP 2xx on HEAD or ranged GET
    - ``missing``      : HTTP 4xx
    - ``unreachable``  : DNS / connection / 5xx / timeout
    """
    t = dt.datetime.now(dt.timezone.utc).isoformat()
    accession = _accession_slug_from_url(url)
    try:
        req = urllib.request.Request(
            url,
            method="HEAD",
            headers={"User-Agent": _USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=per_url_timeout_s) as r:
            return ProbeResult(
                url=url,
                accession=accession,
                status=ProbeStatus.OK,
                http_status=r.status,
                size_bytes=_int_or_none(r.headers.get("Content-Length")),
                content_type=r.headers.get("Content-Type"),
                checked_at=t,
                error="",
            )
    except urllib.error.HTTPError as e:
        # Some servers reject HEAD with 405; retry as ranged GET.
        if e.code in (405, 403):
            return _probe_one_ranged_get(url, per_url_timeout_s, accession, t)
        status = (ProbeStatus.MISSING if 400 <= e.code < 500
                  else ProbeStatus.UNREACHABLE)
        return ProbeResult(
            url=url, accession=accession, status=status,
            http_status=e.code, size_bytes=None, content_type=None,
            checked_at=t, error=str(e)[:200],
        )
    except (urllib.error.URLError, socket.timeout, http.client.HTTPException, OSError) as e:
        return ProbeResult(
            url=url, accession=accession, status=ProbeStatus.UNREACHABLE,
            http_status=None, size_bytes=None, content_type=None,
            checked_at=t, error=str(e)[:200],
        )


def _probe_one_ranged_get(
    url: str,
    per_url_timeout_s: float,
    accession: str,
    t: str,
) -> ProbeResult:
    """Fallback for hosts that reject HEAD: GET with Range: bytes=0-0."""
    try:
        req = urllib.request.Request(
            url,
            method="GET",
            headers={
                "User-Agent": _USER_AGENT,
                "Range": "bytes=0-0",
            },
        )
        with urllib.request.urlopen(req, timeout=per_url_timeout_s) as r:
            return ProbeResult(
                url=url, accession=accession, status=ProbeStatus.OK,
                http_status=r.status,
                size_bytes=_int_or_none(r.headers.get("Content-Length")),
                content_type=r.headers.get("Content-Type"),
                checked_at=t, error="",
            )
    except urllib.error.HTTPError as e:
        status = (ProbeStatus.MISSING if 400 <= e.code < 500
                  else ProbeStatus.UNREACHABLE)
        return ProbeResult(
            url=url, accession=accession, status=status,
            http_status=e.code, size_bytes=None, content_type=None,
            checked_at=t, error=str(e)[:200],
        )
    except (urllib.error.URLError, socket.timeout, http.client.HTTPException, OSError) as e:
        return ProbeResult(
            url=url, accession=accession, status=ProbeStatus.UNREACHABLE,
            http_status=None, size_bytes=None, content_type=None,
            checked_at=t, error=str(e)[:200],
        )


def _int_or_none(s: str | None) -> int | None:
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def probe_research_md(
    research_md_path: pathlib.Path,
    *,
    allow_web: bool = True,
    max_total_seconds: float = _DEFAULT_TOTAL_BUDGET_S,
    per_url_timeout_s: float = _DEFAULT_PER_URL_TIMEOUT_S,
) -> list[ProbeResult]:
    """Scan RESEARCH.md, extract URLs/DOIs, probe each within budget.

    When ``allow_web`` is False, every URL is recorded with status
    ``skipped_no_web`` immediately — no network calls are issued.
    """
    text = pathlib.Path(research_md_path).read_text(errors="replace")
    urls = _extract_urls(text)
    t0 = time.time()

    results: list[ProbeResult] = []
    if not allow_web:
        ts = dt.datetime.now(dt.timezone.utc).isoformat()
        for u in urls:
            results.append(ProbeResult(
                url=u, accession=_accession_slug_from_url(u),
                status=ProbeStatus.SKIPPED_NO_WEB,
                http_status=None, size_bytes=None, content_type=None,
                checked_at=ts, error="",
            ))
        return results

    for u in urls:
        if time.time() - t0 > max_total_seconds:
            ts = dt.datetime.now(dt.timezone.utc).isoformat()
            results.append(ProbeResult(
                url=u, accession=_accession_slug_from_url(u),
                status=ProbeStatus.NOT_PROBED,
                http_status=None, size_bytes=None, content_type=None,
                checked_at=ts, error="probe budget exceeded",
            ))
            continue
        results.append(_probe_one(u, per_url_timeout_s=per_url_timeout_s))
    return results


def write_probe_json(
    workspace_root: pathlib.Path,
    results: Iterable[ProbeResult],
) -> pathlib.Path:
    """Write asset_probe.json atomically into the workspace root."""
    target = pathlib.Path(workspace_root) / PROBE_FILENAME
    tmp = target.with_suffix(".json.tmp")
    payload = {
        "version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "assets": [r.to_dict() for r in results],
    }
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(target)
    return target


def load_probe_results(workspace_root: pathlib.Path) -> list[ProbeResult]:
    """Read ``asset_probe.json`` back into typed ``ProbeResult`` rows.

    Structured inverse of ``write_probe_json``. Returns ``[]`` when the
    artifact is absent or unparseable (a probe is best-effort pre-flight,
    never load-bearing). Unknown keys in an asset row are dropped so a
    schema bump can't crash a reader.
    """
    path = pathlib.Path(workspace_root) / PROBE_FILENAME
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    fields = {f.name for f in dataclasses.fields(ProbeResult)}
    out: list[ProbeResult] = []
    for entry in (payload.get("assets") or []):
        if not isinstance(entry, dict):
            continue
        out.append(ProbeResult(**{
            "url": entry.get("url", ""),
            "accession": entry.get("accession", ""),
            "status": entry.get("status", ""),
            "http_status": entry.get("http_status"),
            "size_bytes": entry.get("size_bytes"),
            "content_type": entry.get("content_type"),
            "checked_at": entry.get("checked_at", ""),
            "error": entry.get("error", ""),
            **{k: v for k, v in entry.items() if k in fields and k not in {
                "url", "accession", "status", "http_status", "size_bytes",
                "content_type", "checked_at", "error"}},
        }))
    return out


def seed_dataset_refs(workspace_root: pathlib.Path) -> int:
    """Seed one ``DATASET_REF`` node per RESOLVED asset into the run's KG.

    This closes the lab-knowledge dataset-sharing gap: the lab KB's
    dataset channel was wired end-to-end (``lab_knowledge.distill_workspace``
    pulls ``DATASET_REF`` nodes; IDEATE/SCOPE render them) but NOTHING in
    the normal pipeline ever created such nodes — so it always rendered
    empty. The asset probe already resolved, at workspace init, exactly
    which named assets (datasets, models, code repos by accession/URL) the
    run can actually reach; this turns each ``ok`` probe row into a
    ``DATASET_REF`` node so distill can share it ("the lab already holds X
    at <url>") with sibling runs.

    Only ``ProbeStatus.OK`` rows are seeded — a ``missing`` / ``unreachable``
    asset is NOT something the lab "holds". Idempotent: the node id is a
    stable slug (``dataset:<ws>:<identity>``), so re-seeding upserts in
    place rather than duplicating. Reuses the exact ``DATASET_REF`` node
    shape that ``project_descriptor`` already uses for external descriptors.

    Both the node ``title`` and the id key are the human-meaningful dataset
    identity derived by ``_dataset_identity`` (e.g. ``facebook/winoground``)
    rather than the bare probe ``accession`` (which is often just the host,
    e.g. ``huggingface.co``). This is what makes distinct datasets render as
    distinct bullets in IDEATE/SCOPE instead of collapsing by host — a bare
    host is not a unique key. For a real-name accession the identity equals
    the accession, so the id slug (``dataset:<ws>:<accession>``) and re-seed
    idempotency are byte-identical to before this fix.

    Returns the number of nodes upserted (0 when the probe is absent /
    empty / had no resolved assets).
    """
    from . import knowledge_graph as kg  # local import: avoid cycle at import time

    ws_root = pathlib.Path(workspace_root)
    resolved = [r for r in load_probe_results(ws_root)
                if r.status == ProbeStatus.OK]
    if not resolved:
        return 0
    ws_name = ws_root.name
    seeded = 0
    seen_ids: set[str] = set()
    for r in resolved:
        accession = (r.accession or "").strip()
        # The title IS the per-dataset identity, and so is the id key — a bare
        # host accession ("huggingface.co") is NOT a unique key (it collides
        # across every dataset from that host), so keying the id off the
        # derived identity is what makes both the id AND the title distinct.
        # For a real accession the identity == accession, so the id slug
        # (``dataset:<ws>:<accession>``) is byte-identical to before this fix.
        title = _dataset_identity(accession, r.url or "")
        if not title:
            continue
        node_id = kg._slug(f"dataset:{ws_name}:{title}")
        if node_id in seen_ids:
            continue  # de-dup within this seed pass (probe may repeat a dataset)
        seen_ids.add(node_id)
        body = (f"{r.content_type}" if r.content_type else "").strip()
        kg.upsert_node(ws_root, kg.Node(
            id=node_id,
            type=kg.NodeType.DATASET_REF,
            title=title,
            body=body,
            origin="omicairn",
            source_phase="asset_probe",
            evidence_paths=[r.url] if r.url else [],
            tags=["asset_probe"],
            extra={"source_project": ws_name, "location": r.url,
                   "size_bytes": r.size_bytes},
        ))
        seeded += 1
    return seeded


def render_summary(results: Iterable[ProbeResult]) -> str:
    """Render a SCOPE-prompt-friendly Markdown summary of probe results.

    The orchestrator passes this via the ``{{asset_probe_summary}}``
    token; SCOPE quotes it verbatim into ``data_profile.md`` so PLAN
    sees it.
    """
    rows = list(results)
    if not rows:
        return "_(no URLs/DOIs detected in RESEARCH.md)_"
    by_status: dict[str, list[ProbeResult]] = {}
    for r in rows:
        by_status.setdefault(r.status, []).append(r)
    lines: list[str] = []
    lines.append("| status | accession | url | http | size |")
    lines.append("|---|---|---|---|---|")
    for status in ProbeStatus.RENDER_ORDER:
        for r in by_status.get(status, []):
            size = "" if r.size_bytes is None else f"{r.size_bytes:,}"
            http_s = "" if r.http_status is None else str(r.http_status)
            lines.append(
                f"| {r.status} | {r.accession} | {r.url} | {http_s} | {size} |"
            )
    counts = {k: len(v) for k, v in by_status.items()}
    lines.append("")
    lines.append(
        "**Counts:** "
        + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    )
    if "missing" in counts or "unreachable" in counts:
        lines.append("")
        lines.append(
            "**PLAN MUST acknowledge every `missing` / `unreachable` "
            "asset** above with either a `fallback_if_blocked` "
            "subhypothesis or an explicit defer-to-Limitations entry. "
            "An unacknowledged missing asset is a W9 ERROR."
        )
    return "\n".join(lines)
