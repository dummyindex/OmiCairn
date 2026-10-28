"""Literature retrieval engine for OmiCairn.

Fetches academic papers from PubMed, Semantic Scholar, CrossRef,
Unpaywall, and Europe PMC. Domain-agnostic: adapts API selection
and search strategy based on RESEARCH.md's domain field.

Usable as:
  - Library:  from omicairn.literature import LiteratureFetcher
  - CLI:      python -m omicairn.literature --research-md ... --output-dir ...
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import logging
import pathlib
import re
import socket
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from typing import Any
from urllib.parse import quote, urlparse

import requests

from omicairn.constants import (
    ENV_ENABLE_SSRF_GUARD,
    PAPER_LIB_ABSTRACTS_DIR,
    PAPER_LIB_MARKDOWN_DIR,
    PAPER_LIB_PDFS_DIR,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_FILE_BYTES = 100 * 1024 * 1024       # 100 MB per file
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB total

PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
S2_BASE = "https://api.semanticscholar.org/graph/v1"
CROSSREF_BASE = "https://api.crossref.org/v1"
UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
EPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"

CONTACT_EMAIL = "omicairn@research-tool.local"

# Rate-limit sleeps (seconds)
PUBMED_DELAY = 0.34   # 3 req/sec
S2_DELAY = 3.0        # Semantic Scholar rate-limits aggressively on shared pool
CROSSREF_DELAY = 0.1  # polite pool, generous
UNPAYWALL_DELAY = 0.1
EPMC_DELAY = 1.0





# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Paper:
    pmid: str | None = None
    doi: str | None = None
    title: str = ""
    authors: str = ""
    year: int | None = None
    journal: str = ""
    volume: str = ""
    pages: str = ""
    abstract: str = ""
    citation_count: int = 0
    is_open_access: bool = False
    oa_pdf_url: str | None = None
    oa_pdf_path: str | None = None
    fulltext_path: str | None = None
    paywalled: bool = False
    keywords: list[str] = field(default_factory=list)
    source_api: str = ""
    relevance_query: str = ""
    domain_tags: list[str] = field(default_factory=list)

    @property
    def dedup_key(self) -> str:
        if self.doi:
            return f"doi:{self.doi.lower()}"
        if self.pmid:
            return f"pmid:{self.pmid}"
        return f"title:{self.title.lower()[:80]}"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = (
            f"OmiCairn/0.1 (mailto:{CONTACT_EMAIL})"
        )
    return _session


def _get_json(url: str, params: dict | None = None,
              delay: float = 0.0, retries: int = 3) -> dict | list | None:
    sess = _get_session()
    for attempt in range(retries):
        try:
            if delay > 0:
                time.sleep(delay)
            resp = sess.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = min(2 ** (attempt + 1), 30)
                logger.warning("Rate-limited on %s, waiting %ds", url, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.warning("Request failed (attempt %d/%d): %s — %s",
                           attempt + 1, retries, url, e)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None


def _get_xml(url: str, params: dict | None = None,
             delay: float = 0.0, retries: int = 3) -> ET.Element | None:
    sess = _get_session()
    for attempt in range(retries):
        try:
            if delay > 0:
                time.sleep(delay)
            resp = sess.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                time.sleep(min(2 ** (attempt + 1), 30))
                continue
            resp.raise_for_status()
            return ET.fromstring(resp.content)
        except (requests.RequestException, ET.ParseError) as e:
            logger.warning("XML request failed (attempt %d/%d): %s — %s",
                           attempt + 1, retries, url, e)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None


def _ssrf_guard_enabled() -> bool:
    """The SSRF download guard is OFF BY DEFAULT; enable it by setting
    OMICAIRN_ENABLE_SSRF_GUARD truthy. Turn it on for untrusted/cloud
    deployments where an LLM-surveyed URL could point the server-side
    fetch at an internal/metadata endpoint."""
    import os
    return os.environ.get(ENV_ENABLE_SSRF_GUARD, "").strip().lower() \
        in ("1", "true", "yes", "on")


def _host_is_blocked(hostname: str) -> bool:
    """True if `hostname` resolves to any private / loopback / link-local /
    reserved address — the SSRF block. A public URL that 302-redirects to
    e.g. 169.254.169.254 (cloud metadata) or localhost is the threat;
    source URLs here are LLM-surveyed (IDEATE bridge) and could be induced
    by prompt injection in a fetched page. No-op (returns False) unless
    the guard is enabled via OMICAIRN_ENABLE_SSRF_GUARD."""
    if not _ssrf_guard_enabled():
        return False
    if not hostname:
        return True
    try:
        infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, UnicodeError, OSError):
        # Unresolvable → NOT blocked: requests will fail to connect on its
        # own (no SSRF reach), and refusing here would wrongly reject a
        # transiently-unresolvable real host. The block is specifically
        # for hosts that DO resolve to private/loopback/metadata IPs.
        return False
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return True
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return True
    return False


def _url_is_fetchable(url: str) -> bool:
    """Scheme allowlist (http/https only) + SSRF host block. Active only
    when the guard is enabled via OMICAIRN_ENABLE_SSRF_GUARD (off by
    default → any url passes here)."""
    if not _ssrf_guard_enabled():
        return bool(url)
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return not _host_is_blocked(parsed.hostname or "")


_ARXIV_ABS_RE = re.compile(
    r"^https?://(?:www\.)?arxiv\.org/abs/(?P<id>[^?#/]+)", re.IGNORECASE)


def _pdf_url_for(url: str) -> str:
    """Normalize a known abstract/landing URL to a direct-PDF URL so the
    downloader saves a real PDF, not an HTML landing page.

    Currently handles arXiv abstract pages (``arxiv.org/abs/2401.00001`` →
    ``arxiv.org/pdf/2401.00001``). This is provenance-agnostic plumbing,
    not a domain keyword list (design principles): it keys on URL shape only.
    Unknown URLs pass through unchanged."""
    if not url:
        return url
    m = _ARXIV_ABS_RE.match(url)
    if m:
        return f"https://arxiv.org/pdf/{m.group('id')}"
    return url


def _download_file(url: str, dest: pathlib.Path,
                   max_bytes: int = MAX_FILE_BYTES) -> bool:
    # SSRF guard on the INITIAL url (scheme + private-IP block).
    if not _url_is_fetchable(url):
        logger.warning("Refusing to download (blocked scheme/host): %s", url)
        return False
    sess = _get_session()
    try:
        with sess.get(url, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            # Post-redirect re-validation: if a redirect landed us on a
            # private/loopback host, do not persist the (possibly internal)
            # body. Fall back to the (already pre-checked) request URL when
            # the response object doesn't expose a final url.
            final_url = getattr(resp, "url", None) or url
            final_host = urlparse(final_url).hostname or ""
            if _host_is_blocked(final_host):
                logger.warning("Refusing download after redirect to blocked "
                               "host: %s -> %s", url, resp.url)
                return False
            cl = resp.headers.get("content-length")
            content_length = int(cl) if cl and cl.isdigit() else 0
            if content_length > max_bytes:
                logger.info("Skipping download %s (%.1f MB > limit)",
                            url, content_length / 1024 / 1024)
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    written += len(chunk)
                    if written > max_bytes:
                        logger.info("Download exceeded limit, truncating: %s", url)
                        break
                    f.write(chunk)
            return True
    except requests.RequestException as e:
        logger.warning("Download failed: %s — %s", url, e)
        return False


# ---------------------------------------------------------------------------
# API clients
# ---------------------------------------------------------------------------

def pubmed_search(query: str, max_results: int = 20) -> list[str]:
    """Search PubMed, return list of PMIDs."""
    data = _get_json(
        f"{PUBMED_BASE}/esearch.fcgi",
        params={"db": "pubmed", "term": query, "retmax": max_results,
                "retmode": "json", "sort": "relevance"},
        delay=PUBMED_DELAY,
    )
    if not data:
        return []
    return data.get("esearchresult", {}).get("idlist", [])


def pubmed_fetch(pmids: list[str]) -> list[Paper]:
    """Fetch paper metadata + abstracts from PubMed for a list of PMIDs."""
    if not pmids:
        return []
    papers = []
    # Fetch in batches of 20
    for i in range(0, len(pmids), 20):
        batch = pmids[i:i + 20]
        root = _get_xml(
            f"{PUBMED_BASE}/efetch.fcgi",
            params={"db": "pubmed", "id": ",".join(batch),
                    "retmode": "xml", "rettype": "abstract"},
            delay=PUBMED_DELAY,
        )
        if root is None:
            continue
        for article in root.iter("PubmedArticle"):
            p = _parse_pubmed_article(article)
            if p:
                papers.append(p)
    return papers


def _parse_pubmed_article(article: ET.Element) -> Paper | None:
    """Parse a single PubmedArticle XML element into a Paper."""
    try:
        medline = article.find("MedlineCitation")
        if medline is None:
            return None
        pmid_el = medline.find("PMID")
        pmid = pmid_el.text if pmid_el is not None else None

        art = medline.find("Article")
        if art is None:
            return None

        title_el = art.find("ArticleTitle")
        title = title_el.text or "" if title_el is not None else ""

        # Abstract
        abstract_parts = []
        abstract_el = art.find("Abstract")
        if abstract_el is not None:
            for text_el in abstract_el.findall("AbstractText"):
                label = text_el.get("Label", "")
                txt = "".join(text_el.itertext()).strip()
                if label:
                    abstract_parts.append(f"{label}: {txt}")
                else:
                    abstract_parts.append(txt)
        abstract = " ".join(abstract_parts)

        # Authors
        author_list = art.find("AuthorList")
        authors_parts = []
        if author_list is not None:
            for author in author_list.findall("Author"):
                last = author.findtext("LastName", "")
                init = author.findtext("Initials", "")
                if last:
                    authors_parts.append(f"{last} {init}".strip())
        authors = ", ".join(authors_parts[:5])
        if len(authors_parts) > 5:
            authors += " et al."

        # Journal
        journal_el = art.find("Journal")
        journal = ""
        volume = ""
        year = None
        if journal_el is not None:
            journal = journal_el.findtext("Title", "") or journal_el.findtext("ISOAbbreviation", "")
            ji = journal_el.find("JournalIssue")
            if ji is not None:
                volume = ji.findtext("Volume", "")
                pd = ji.find("PubDate")
                if pd is not None:
                    y = pd.findtext("Year")
                    if y and y.isdigit():
                        year = int(y)

        # Pages
        pages = art.findtext("Pagination/MedlinePgn", "")

        # DOI
        doi = None
        for eid in art.findall("ELocationID"):
            if eid.get("EIdType") == "doi":
                doi = eid.text
        if doi is None:
            article_ids = article.find("PubmedData/ArticleIdList")
            if article_ids is not None:
                for aid in article_ids.findall("ArticleId"):
                    if aid.get("IdType") == "doi":
                        doi = aid.text
                        break

        # MeSH terms
        keywords = []
        mesh_list = medline.find("MeshHeadingList")
        if mesh_list is not None:
            for mh in mesh_list.findall("MeshHeading"):
                desc = mh.findtext("DescriptorName", "")
                if desc:
                    keywords.append(desc)

        return Paper(
            pmid=pmid, doi=doi, title=title, authors=authors,
            year=year, journal=journal, volume=volume, pages=pages,
            abstract=abstract, keywords=keywords[:10],
            source_api="pubmed",
        )
    except (AttributeError, TypeError, ValueError) as e:
        logger.warning("Failed to parse PubMed article: %s", e)
        return None


def semantic_scholar_search(query: str, max_results: int = 20) -> list[Paper]:
    """Search Semantic Scholar, return Paper objects."""
    data = _get_json(
        f"{S2_BASE}/paper/search",
        params={
            "query": query,
            "limit": min(max_results, 100),
            "fields": "paperId,title,abstract,authors,year,venue,"
                      "citationCount,isOpenAccess,openAccessPdf,"
                      "externalIds",
        },
        delay=S2_DELAY,
    )
    if not data or "data" not in data:
        return []

    papers = []
    for item in data["data"]:
        ext_ids = item.get("externalIds") or {}
        oa_pdf = item.get("openAccessPdf") or {}
        authors_list = item.get("authors") or []
        auth_str = ", ".join(a.get("name", "") for a in authors_list[:5])
        if len(authors_list) > 5:
            auth_str += " et al."

        p = Paper(
            pmid=ext_ids.get("PubMed"),
            doi=ext_ids.get("DOI"),
            title=item.get("title", ""),
            authors=auth_str,
            year=item.get("year"),
            journal=item.get("venue", ""),
            abstract=item.get("abstract") or "",
            citation_count=item.get("citationCount", 0),
            is_open_access=item.get("isOpenAccess", False),
            oa_pdf_url=oa_pdf.get("url"),
            source_api="semantic_scholar",
            relevance_query=query,
        )
        papers.append(p)
    return papers


def crossref_enrich(paper: Paper) -> Paper:
    """Enrich a Paper with CrossRef metadata (journal, volume, pages)."""
    if not paper.doi:
        return paper
    data = _get_json(
        f"{CROSSREF_BASE}/works/{quote(paper.doi, safe='')}",
        delay=CROSSREF_DELAY,
    )
    if not data or "message" not in data:
        return paper
    msg = data["message"]
    if not paper.journal:
        container = msg.get("container-title", [])
        paper.journal = container[0] if container else ""
    if not paper.volume:
        paper.volume = msg.get("volume", "")
    if not paper.pages:
        paper.pages = msg.get("page", "")
    if not paper.authors:
        auth_list = msg.get("author", [])
        parts = []
        for a in auth_list[:5]:
            name = f"{a.get('family', '')} {a.get('given', '')[:1]}".strip()
            if name:
                parts.append(name)
        if len(auth_list) > 5:
            parts.append("et al.")
        paper.authors = ", ".join(parts)
    if not paper.year:
        issued = msg.get("issued", {}).get("date-parts", [[]])
        if issued and issued[0]:
            paper.year = issued[0][0]
    paper.source_api += "+crossref" if paper.source_api else "crossref"
    return paper


def unpaywall_check(paper: Paper) -> Paper:
    """Check Unpaywall for OA PDF availability."""
    if not paper.doi:
        paper.paywalled = True  # can't check without DOI
        return paper
    data = _get_json(
        f"{UNPAYWALL_BASE}/{quote(paper.doi, safe='')}",
        params={"email": CONTACT_EMAIL},
        delay=UNPAYWALL_DELAY,
    )
    if not data:
        return paper
    paper.is_open_access = data.get("is_oa", False)
    best_loc = data.get("best_oa_location") or {}
    pdf_url = best_loc.get("url_for_pdf") or best_loc.get("url")
    if pdf_url:
        paper.oa_pdf_url = pdf_url
    paper.paywalled = not paper.is_open_access
    return paper


def epmc_fulltext(paper: Paper, output_dir: pathlib.Path) -> Paper:
    """Try to get full text from Europe PMC for OA papers."""
    if paper.paywalled:
        return paper
    # Try by DOI first, then PMID
    query = f"DOI:{paper.doi}" if paper.doi else f"EXT_ID:{paper.pmid}"
    data = _get_json(
        f"{EPMC_BASE}/search",
        params={"query": query, "resultType": "core", "format": "json"},
        delay=EPMC_DELAY,
    )
    if not data:
        return paper
    results = data.get("resultList", {}).get("result", [])
    if not results:
        return paper
    result = results[0]
    pmcid = result.get("pmcid")
    if not pmcid:
        return paper
    if result.get("isOpenAccess") != "Y":
        return paper

    # Fetch full text XML
    sess = _get_session()
    try:
        time.sleep(EPMC_DELAY)
        resp = sess.get(f"{EPMC_BASE}/{pmcid}/fullTextXML", timeout=30)
        if resp.status_code == 200:
            ft_path = output_dir / "fulltext" / f"{pmcid}.xml"
            ft_path.parent.mkdir(parents=True, exist_ok=True)
            ft_path.write_bytes(resp.content)
            paper.fulltext_path = f"fulltext/{pmcid}.xml"
    except requests.RequestException as e:
        logger.warning("Europe PMC fulltext fetch failed for %s: %s", pmcid, e)
    return paper


# ---------------------------------------------------------------------------
# Query generation
# ---------------------------------------------------------------------------

# Only filter English function words (articles, prepositions, etc.). One
# canonical frozenset, reused by both the key-term extractor and the slug
# anchor heuristic.
_FUNCTION_WORDS = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would could should may might can shall of in to for with on "
    "at from by about as into through during before after above below "
    "between under and but or nor not so if than that this these those "
    "it its we our they their which who whom what when where how whether "
    "does using also".split()
)

# Structural / boilerplate words of a RESEARCH.md brief — especially a
# DISCOVERY-mode (IDEATE) brief, whose research_question is a PURPOSE
# statement framed with section-header prose ("RESEARCH PURPOSE (wave 2)
# — ... PROPOSE 6-10 RUNNABLE directions ...") AND whose YAML frontmatter /
# comment scaffolding ("research_mode: DISCOVERY", "domain:", "topic_slug:",
# "do NOT re-propose ... ADOPTED ... compute-FEASIBLE") can bleed into the
# extracted research_question depending on how the brief is parsed/passed.
# These are the scaffolding of the brief, NOT search keywords: splicing
# them into a query ("interp worldmodel DISCOVERY IDEATE FEASIBLE") returns
# 0 hits and then the domain-agnostic fallbacks contaminate the corpus
# (audit 2026-06-14, topic_slug interp-worldmodel-mm-rai). ONE canonical
# frozenset; matched case-insensitively against extracted key_terms /
# entities. Kept deliberately narrow: a word qualifies ONLY if it is a
# OmiCairn-process / brief-structure word a reviewer would NEVER search for —
# never a domain/method/result term. The acid test for adding a word here:
# "would a reviewer type this into Semantic Scholar to find the paper?" If
# no, it belongs here.
_BOILERPLATE_TERMS = frozenset(
    # Brief section-header / PURPOSE-statement scaffolding.
    "research purpose wave verified constraints constraint direction "
    "directions propose proposed proposes runnable survey landscape "
    "candidate candidates illustrative exhaustive intersection broad "
    "explicitly wants centered concrete level contribution goal goals "
    "approach approaches towards toward via thread threads spine core "
    "tension stretch problem promise program target establishes "
    # DISCOVERY / IDEATE research-mode + YAML-frontmatter structural
    # vocabulary (research_mode: DISCOVERY, the IDEATE-mode comment block,
    # the FEASIBLE / RUNNABLE / NON-DUPLICATE framing, the ADOPTED-direction
    # machinery). These describe the *process*, not the science — a reviewer
    # never searches for "DISCOVERY IDEATE FEASIBLE mode domain".
    "discovery ideate feasible feasibility adopted adopt mode domain slug "
    "topic nonduplicate duplicate registration preregistration "
    "preregisters registers registered scope plan execute writeup ideation "
    "brainstorm brief slate "
    # Spelled-out small numbers ("propose 6-10 ... at the intersection of
    # FOUR threads") are brief scaffolding, never search keywords.
    "one two three four five six seven eight nine ten".split()
)


def _prose_words(text: str) -> list[str]:
    """Lower-cased alphanumeric word tokens of `text`, in order."""
    return [w.lower() for w in re.findall(r'[A-Za-z0-9]+', text)]


def _prose_word_form(piece: str, prose_set: set[str], prose: list[str]) -> str | None:
    """Return the prose word a `piece` represents: itself if it is a prose
    word, or the prose word it is a (>= 4-char) prefix of ("model" →
    "models"). None if `piece` is not a real prose word — this is what keeps
    a meaningless glued suffix ("ness" in "fairness") from being accepted."""
    if piece in prose_set:
        return piece
    if len(piece) >= 4:
        for w in prose:
            if (w not in _BOILERPLATE_TERMS and w.startswith(piece)
                    and len(w) > len(piece)):
                return w
    return None


def _unglue_to_prose_words(token: str, prose: list[str]) -> list[str] | None:
    """Split a glued compound into >= 2 REAL prose words, greedily peeling
    the longest prose-word prefix off the front ("worldmodel" → ["world",
    "models"]). Every piece must be a genuine prose word (see
    :func:`_prose_word_form`); returns None if the token cannot be fully
    consumed by real words, so "fairness" (no clean 2-word split) and
    "interpretability" are left intact for the verbatim path."""
    # Exclude boilerplate prose words from the split vocabulary: a brief
    # that contains "research_mode: DISCOVERY" puts "mode" in the prose, and
    # "mode" is a 4-char prefix of "model" — letting it peel would split
    # "worldmodel" into "world"+"mode"+"l" and fail. Boilerplate is never a
    # legitimate constituent of a real compound.
    prose_set = {w for w in prose if w not in _BOILERPLATE_TERMS}
    rest = token
    out: list[str] = []
    while rest:
        matched = False
        # Longest leading prose word first, so "world"+"models" wins over a
        # shorter accidental prefix. The prefix must be strictly SHORTER than
        # what's left (a candidate equal to the whole remainder is the
        # no-split case — "worldmodel"/"interpretability" matching itself —
        # and must not be accepted as a single "word").
        for w in sorted(prose_set, key=len, reverse=True):
            if len(w) >= 4 and len(w) < len(rest) and rest.startswith(w):
                out.append(w)
                rest = rest[len(w):]
                matched = True
                break
        if not matched:
            # Tail may be a prefix-form of a prose word ("model" → "models").
            form = _prose_word_form(rest, prose_set, prose)
            if form and out:
                out.append(form)
                rest = ""
                break
            return None
    return out if len(out) >= 2 else None


def _expand_to_prose_word(token: str, prose: list[str]) -> str | None:
    """Map a slug/domain `token` to the real, searchable word the author
    actually used, using ONLY the prose as evidence (no hardcoded keyword
    list — design principles). Returns None when the token is un-searchable soup.

    A topic slug / domain string is built from abbreviations and glued
    compounds that match nothing on Semantic Scholar / PubMed: a slug
    ``interp-steer-fair-mm`` is ``interp steer fair mm`` and a domain
    ``..._worldmodel_..._responsible_ai`` glues ``worldmodel`` and ``ai``.
    "interp"/"steer"/"fair" are PREFIXES of real words the author wrote
    ("interpretability", "steering", "fairness"); "worldmodel" is two prose
    words glued ("world model"); "mm"/"rai"/"ai" are pure abbreviations with
    no prose expansion. Resolution order (PREFER the fuller word — a slug
    abbreviation often ALSO appears verbatim in the prose as a slug-fragment,
    e.g. "interp" inside "MECH-INTERP" or the workspace name "example-study-
    interp", so verbatim presence alone does NOT make a token searchable):

      1. token in _BOILERPLATE_TERMS  → None (process/structure word).
      2. token is a strict PREFIX (>= 4 chars) of a LONGER prose word  →
         that fuller word ("interp" → "interpretability", "steer" →
         "steering", "fair" → "fairness"). Checked BEFORE verbatim so an
         abbreviation never wins over its own expansion.
      3. token is a glued compound (>= 8 chars) splitting into >= 2 prose
         words at a prefix boundary  → the spaced form ("worldmodel" →
         "world model").
      4. token (>= 4 chars) appears verbatim in prose  → keep verbatim, any
         length >= 4 ("multimodal", "calibration"). Only a token that is a
         strict abbreviation of a LONGER word was rewritten at step 2, so
         what reaches here verbatim is a genuine standalone word.
      5. token is a medium-length (4-7 char) token ABSENT from prose  →
         keep it. This preserves genuine searchable acronyms / symbols the
         author abbreviated and never spelled out (e.g. "SAE","VLM",
         "LASSO") — dropping them would needlessly narrow the net.
      6. otherwise (a <= 3-char abbreviation — "mm","rai","ai","gd" — even
         if it appears verbatim in prose as an abbreviation, OR an
         un-splittable >= 8-char glued compound)  → None. The user's acid
         test forbids the <= 3-char slug tails ("mm","rai") as bare tokens,
         and a 2-3 char string is too ambiguous to search on usefully; a
         real short concept reaches the query through the key-terms /
         entity extractors, not the slug anchor.
    """
    tl = token.lower()
    if tl in _BOILERPLATE_TERMS:
        return None
    # PREFER the fuller prose word over the bare token, even when the token
    # also appears verbatim (>= 4 chars so a 2-3 char stub can't over-match).
    # Never expand to a boilerplate word.
    if len(tl) >= 4:
        for w in prose:
            if (w != tl and w not in _BOILERPLATE_TERMS
                    and w.startswith(tl) and len(w) > len(tl)):
                return w
    # Glued compound: split into a sequence of REAL prose words
    # ("worldmodel" → "world" + "models"). Both the head and the remainder
    # must each be a genuine prose word (verbatim or a prefix of one), so we
    # never invent a split or keep a meaningless suffix ("fairness" must NOT
    # become "fair" + "ness"). Attempted even when the whole token appears
    # verbatim, because a glued compound often ALSO appears verbatim as a
    # slug echo in the brief's own frontmatter ("topic_slug: interp-
    # worldmodel-..."); a real single word ("interpretability","fairness")
    # simply won't split into two prose words here.
    if len(tl) >= 8:
        split = _unglue_to_prose_words(tl, prose)
        if split:
            return " ".join(split)
    # Verbatim real word the author typed → keep, but only >= 4 chars (a
    # 2-3 char verbatim hit is an abbreviation like "mm"/"MM", not a word).
    if len(tl) >= 4 and tl in prose:
        return token
    # Medium-length acronym absent from prose → keep ("LUAD","SAE","VLM").
    # A <= 3-char abbreviation or an un-splittable long glued compound is
    # dropped as un-searchable soup.
    if 4 <= len(tl) <= 7:
        return token
    return None


def _resolve_tokens(raw_tokens: list[str], research_question: str) -> list[str]:
    """Rewrite each raw slug/domain token to its searchable prose form
    (:func:`_expand_to_prose_word`), drop the un-resolvable ones, and
    de-duplicate while preserving order. A multi-word expansion ("world
    model") contributes both words, still de-duped against the rest."""
    prose = _prose_words(research_question)
    out: list[str] = []
    seen: set[str] = set()
    for t in raw_tokens:
        resolved = _expand_to_prose_word(t, prose)
        if not resolved:
            continue
        for w in resolved.split():
            if w.lower() not in seen:
                out.append(w)
                seen.add(w.lower())
    return out


def _slug_query_anchor(slug_terms: list[str], research_question: str) -> list[str]:
    """Return the slug tokens REWRITTEN to the real, searchable words the
    author used (see :func:`_expand_to_prose_word`).

    The topic slug is a FILESYSTEM identifier, not a query: a slug like
    ``interp-worldmodel-mm-rai`` tokenizes to ``interp worldmodel mm rai``
    — abbreviations ("mm","rai"), prefixes ("interp"), and glued compounds
    ("worldmodel") that match nothing on Semantic Scholar / PubMed,
    returning 0 hits and triggering the contaminating fallbacks (audit
    2026-06-14). Each token is resolved against the research_question prose:
    a verbatim hit is kept, a prefix is expanded to its full word, a glued
    compound is un-glued, and an un-resolvable abbreviation is dropped. The
    raw slug tokens ("interp"/"worldmodel"/"mm"/"rai") therefore NEVER reach
    a query as bare words. Purely data-driven: the gate is the prose itself,
    not a hardcoded keyword list.

    Returns ``[]`` when no token resolves — the caller falls back to the
    (cleaned) domain phrase rather than re-emit the raw slug, so an
    all-abbreviation slug yields an on-topic query, not soup.
    """
    return _resolve_tokens(slug_terms, research_question)


def _clean_domain_phrase(domain: str, research_question: str) -> str:
    """Render the `domain` field into a searchable phrase, dropping its
    abbreviation / glued-compound junk.

    A domain like ``ml_interpretability_worldmodel_multimodal_responsible_ai``
    underscore-splits to real words ("interpretability","multimodal",
    "responsible") PLUS un-searchable junk: a glued compound ("worldmodel")
    and bare abbreviations ("ml","ai"). Emitting them verbatim was the
    residual leak (audit 2026-06-14): "worldmodel" and "ai" appeared as bare
    query tokens. Unlike the filesystem slug, the domain is CURATED prose, so
    its real words are trusted on their own merits (>= 4 chars, not
    boilerplate) WITHOUT requiring a prose hit — a short RQ legitimately may
    not repeat every domain word. Per token:

      - boilerplate / <= 3-char abbreviation ("ml","ai","rai")  → drop.
      - >= 8-char token that UN-GLUES into >= 2 prose words ("worldmodel" →
        "world"+"model")  → use the un-glued split. A long token that does
        NOT un-glue is a real single word ("multimodal","interpretability")
        and is kept verbatim — we only un-glue, never drop, a long word, so
        a curated domain term is never lost.
      - otherwise (a real >= 4-char domain word)  → keep verbatim.

    De-dups, preserves order. Falls back to the raw underscore-spaced domain
    only if NOTHING survives (so a degenerate all-abbreviation domain still
    anchors the recency / negative-result queries instead of going empty)."""
    prose = _prose_words(research_question)
    out: list[str] = []
    seen: set[str] = set()

    def _emit(word: str) -> None:
        if word.lower() not in seen:
            out.append(word)
            seen.add(word.lower())

    for t in domain.replace("_", " ").replace("-", " ").split():
        tl = t.lower()
        if tl in _BOILERPLATE_TERMS or len(tl) <= 3:
            continue
        # Only a token that demonstrably un-glues into >= 2 prose words is
        # treated as a glued compound; a long word that doesn't un-glue
        # ("multimodal","interpretability") is a real term, kept verbatim
        # below. Attempted even for a verbatim hit, since "worldmodel" can
        # appear verbatim as a slug echo in the brief's own frontmatter.
        if len(tl) >= 8:
            unglued = _expand_to_prose_word(t, prose)
            if unglued and " " in unglued:
                for w in unglued.split():
                    _emit(w)
                continue
        _emit(t)
    return " ".join(out) if out else domain.replace("_", " ")


def generate_queries(research_question: str, domain: str,
                     topic_slug: str,
                     extra_terms: list[str] | None = None) -> list[str]:
    """Generate search queries from RESEARCH.md fields.

    Purely data-driven: extracts terms from the research question text,
    topic slug, and optional extra_terms. No hardcoded domain or method
    keywords — works for any field of research.

    The slug is treated as a WEAK anchor: its tokens are used only when
    they are real searchable words (see :func:`_slug_query_anchor`), so an
    abbreviation slug like ``interp-worldmodel-mm-rai`` does not splice
    junk tokens ("mm", "rai", "worldmodel") into the primary query. Brief
    structural words ("RESEARCH PURPOSE wave VERIFIED ...") are filtered
    out of the key terms via :data:`_BOILERPLATE_TERMS`. The contaminating
    domain-agnostic "negative result" angle is anchored to the domain (and
    dropped when there is no informative domain).
    """
    # Split on whitespace AND hyphens so a glued compound like
    # "interpretability-VERIFIED" or "Mechanism-grounded" contributes its
    # real word ("interpretability", "Mechanism") without dragging the
    # boilerplate/function half ("VERIFIED", "grounded") into the query.
    words = re.findall(r'[A-Za-z0-9]+', research_question)
    key_terms = [
        w for w in words
        if w.lower() not in _FUNCTION_WORDS
        and w.lower() not in _BOILERPLATE_TERMS
        and len(w) > 2
    ]

    # Uppercase abbreviations are likely named entities (acronyms, symbols)
    # — but a brief written in shouting prose ("RESEARCH PURPOSE VERIFIED
    # MULTIMODAL WORLD MODELS") emits boilerplate as ALL-CAPS too, so the
    # boilerplate filter applies here as well.
    entities = [
        e for e in re.findall(r'\b[A-Z][A-Z0-9]{2,}\b', research_question)
        if e.lower() not in _BOILERPLATE_TERMS
    ]

    slug_terms = topic_slug.replace("-", " ").replace("_", " ").split()
    # `slug_set` filters the RAW slug tokens out of key_terms/extras so an
    # abbreviation the anchor already rewrote is not double-counted.
    slug_set = {s.lower() for s in slug_terms}
    # Real searchable anchor: slug tokens REWRITTEN to the prose words they
    # abbreviate ("interp"→"interpretability", "worldmodel"→"world model"),
    # un-resolvable abbreviations ("mm"/"rai") dropped — NEVER the raw
    # filesystem slug (see _slug_query_anchor / _expand_to_prose_word).
    anchor = _slug_query_anchor(slug_terms, research_question)
    domain_informative = bool(domain) and domain.lower() not in ("general", "")
    # Domain phrase is rewritten the same way ("worldmodel"→"world model",
    # bare "ai"/"ml" dropped) so glued/abbreviated domain tokens don't leak
    # as bare query words.
    domain_phrase = (_clean_domain_phrase(domain, research_question)
                     if domain_informative else "")
    # All-abbreviation slug (anchor empty)? Fall back to the CLEANED domain
    # phrase as the anchor — never the raw slug — so every query stays
    # on-topic instead of emitting "interp worldmodel mm rai".
    if not anchor and domain_phrase:
        anchor = domain_phrase.split()

    queries = []

    # Q1: searchable anchor (already on-topic — rewritten slug, or cleaned
    # domain phrase as fallback) + a few real key terms from the research
    # question.
    primary_bits = list(anchor)
    extras = [t for t in key_terms[:8] if t.lower() not in slug_set][:3]
    primary = " ".join(primary_bits)
    if extras:
        primary += " " + " ".join(extras)
    queries.append(primary.strip())

    # Q2: named entities not already in slug
    unique_entities = [e for e in entities if e.lower() not in slug_set][:4]
    if unique_entities:
        queries.append(" ".join(anchor[:2] + unique_entities))

    # Q3: slug anchor + domain (if informative)
    if domain_phrase:
        queries.append(" ".join(anchor[:2]) + " " + domain_phrase)

    # Q4: negative/replication angle — ANCHORED to the domain, like Q5. A
    # bare "<slug> replication failure OR negative result" is domain-
    # agnostic and matches generic "negative results" methodology papers
    # across all of science (biomedical "Errata"/"Correction"/replication
    # junk contaminated ml_* corpora — audit 2026-06-14). Without an
    # informative domain there is nothing to keep it on-topic, so the angle
    # is dropped rather than emitted unanchored.
    if domain_phrase:
        queries.append(" ".join(anchor) + " " + domain_phrase
                       + " replication failure OR negative result")

    # Q5: recent work. Anchor to the domain when informative — a bare
    # "<slug> 2023 OR 2024 OR 2025" with a generic slug (common words like
    # "safety", "gap", "direction") matches large volumes of recent
    # off-topic literature (e.g. biomedical papers polluted the shared
    # library for ml_* cases — audit 2026-06-10). Folding in the domain
    # keeps the recency filter on-topic. Still fully data-driven (the
    # domain comes from RESEARCH.md, not a hardcoded keyword list).
    _recent = " ".join(anchor)
    if domain_phrase:
        _recent += " " + domain_phrase
    queries.append(_recent + " 2023 OR 2024 OR 2025")

    # Q6: broader context — key terms from research question WITHOUT the
    # topic slug anchor.  This catches background/mechanism papers (reviews,
    # seminal papers) that scoping and interpretation need but that
    # don't mention the narrow topic slug.
    non_slug_terms = [t for t in key_terms if t.lower() not in slug_set]
    if len(non_slug_terms) >= 3:
        queries.append(" ".join(non_slug_terms[:5]) + " review OR mechanism")

    # Q7: first anchor term only + domain context — wider net than Q3 which
    # uses first two anchor terms.  Example: "pyroptosis cancer" instead of
    # "pyroptosis luad cancer genomics".
    if len(anchor) >= 2 and domain_phrase:
        queries.append(anchor[0] + " " + domain_phrase + " review")

    # Append any caller-supplied extra terms as an additional query
    if extra_terms:
        queries.append(" ".join(anchor[:2] + extra_terms[:4]))

    # De-duplicate repeated words within each query (the anchor and the
    # domain phrase / key terms now share rewritten words, e.g. both expand
    # "interp"→"interpretability", producing "interpretability ...
    # interpretability") and drop any query that collapsed to empty.
    return [dq for q in queries if (dq := _dedup_query_words(q))][:7]


def _dedup_query_words(query: str) -> str:
    """Collapse repeated words in a query while preserving order and the
    search operators. Case-insensitive on word identity; the FIRST casing
    seen wins. ``OR`` / standalone year tokens are kept verbatim (they are
    boolean syntax, legitimately repeatable). Returns '' for empty input."""
    out: list[str] = []
    seen: set[str] = set()
    for tok in query.split():
        if tok == "OR" or tok.isdigit():
            out.append(tok)
            continue
        key = tok.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tok)
    # De-dup can strip the operand of an "X OR Y" tail (e.g. "review OR
    # mechanism" → "review OR" when "mechanism" appeared earlier). Drop a
    # dangling/leading OR so the query stays well-formed.
    while out and out[0] == "OR":
        out.pop(0)
    while out and out[-1] == "OR":
        out.pop()
    return " ".join(out).strip()


# ---------------------------------------------------------------------------
# Main fetcher
# ---------------------------------------------------------------------------

@dataclass
class FetchStats:
    queries_run: int = 0
    pubmed_hits: int = 0
    s2_hits: int = 0
    papers_after_dedup: int = 0
    crossref_enriched: int = 0
    unpaywall_checked: int = 0
    oa_papers: int = 0
    paywalled_papers: int = 0
    pdfs_downloaded: int = 0
    fulltexts_downloaded: int = 0
    abstracts_saved: int = 0
    total_pdf_bytes: int = 0
    library_synced: int = 0
    errors: list[str] = field(default_factory=list)


class LiteratureFetcher:
    # Soft default corpus size. This is a TARGET/CEILING the ranker trims
    # to, NOT a requirement: a thorough biomedical-computational study
    # (2026 norms) engages the literature comprehensively where it exists, so
    # we gather a generous pool for PLAN/EXECUTE to draw on. Where a
    # topic genuinely has few papers, fewer are kept and that is fine — the
    # number is never asserted as a floor anywhere downstream.
    DEFAULT_MAX_PAPERS: int = 60

    def __init__(
        self,
        output_dir: pathlib.Path,
        max_papers: int = DEFAULT_MAX_PAPERS,
        max_total_bytes: int = MAX_TOTAL_BYTES,
        max_file_bytes: int = MAX_FILE_BYTES,
        domain: str = "general",
        use_library: bool = True,
        library_root: pathlib.Path | None = None,
    ):
        self.output_dir = pathlib.Path(output_dir)
        self.max_papers = max_papers
        self.max_total_bytes = max_total_bytes
        self.max_file_bytes = max_file_bytes
        self.domain = domain
        self.stats = FetchStats()
        self._total_downloaded = 0
        # Opt-out hook for tests: `use_library=False` keeps the fetch
        # self-contained to output_dir (no interaction with the
        # on-disk shared library). Library caching is cheap on hit
        # and costs one manifest read on miss, so default ON.
        self.use_library = use_library
        # Resolve the shared library root once so contribute + reuse hit
        # the SAME visible store. output_dir is `<ws>/literature`, so the
        # workspaces root is its grandparent. An explicit `library_root`
        # (or $OMICAIRN_PAPER_LIB) wins via resolve_root precedence.
        self.library_root = library_root

    def _resolve_library_root(self) -> "pathlib.Path":
        from omicairn import paper_library
        # Find the workspaces root by walking up to the STATE.md-bearing
        # workspace (robust whether output_dir is `<ws>/literature` or a
        # stage-nested `<ws>/<NNN_stage>/literature`); fall back to the old
        # grandparent guess for non-workspace dirs (e.g. tests).
        ws_root = paper_library.workspaces_root_for(self.output_dir) \
            or self.output_dir.parent.parent
        return paper_library.resolve_root(
            explicit=self.library_root,
            workspaces_root=ws_root,
        )

    def _copy_cached_markdown(self, p: "Paper", slug: str,
                              lib_root: "pathlib.Path") -> None:
        """Copy the shared converted markdown for `p` into the run's
        `literature/markdown/<slug>.md` (best-effort). Conversion already
        happened once in the library, so the run just reuses the text."""
        try:
            from omicairn import paper_library
            text = paper_library.get_markdown(p.doi or slug, root=lib_root)
            if not text:
                return
            md_dst = self.output_dir / PAPER_LIB_MARKDOWN_DIR / f"{slug}.md"
            md_dst.parent.mkdir(parents=True, exist_ok=True)
            md_dst.write_text(text, encoding="utf-8")
        except Exception as e:  # noqa: BLE001 — best-effort enrichment
            logger.info("markdown reuse skipped for %s: %s", slug, e)

    def search_and_fetch(
        self,
        queries: list[str],
        research_question: str = "",
    ) -> list[Paper]:
        """Run the full search-enrich-download pipeline."""
        # Ensure output dirs exist (incl. markdown/ for shared full-text).
        for subdir in (PAPER_LIB_ABSTRACTS_DIR, PAPER_LIB_PDFS_DIR,
                       PAPER_LIB_MARKDOWN_DIR, "fulltext"):
            (self.output_dir / subdir).mkdir(parents=True, exist_ok=True)

        search_log: list[dict[str, Any]] = []

        # Phase 1: Search
        all_papers: dict[str, Paper] = {}

        for query in queries:
            self.stats.queries_run += 1
            ts_start = time.time()
            pm_count = 0

            # PubMed — returns results for biomedical queries; returns
            # empty for non-biomedical ones. No hardcoded domain check.
            pmids = pubmed_search(query, max_results=20)
            pm_count = len(pmids)
            self.stats.pubmed_hits += pm_count
            pm_papers = pubmed_fetch(pmids)
            for p in pm_papers:
                p.relevance_query = query
                key = p.dedup_key
                if key not in all_papers:
                    all_papers[key] = p

            # Semantic Scholar — covers all academic domains
            s2_papers = semantic_scholar_search(query, max_results=20)
            self.stats.s2_hits += len(s2_papers)
            for p in s2_papers:
                key = p.dedup_key
                if key not in all_papers:
                    all_papers[key] = p
                else:
                    # Merge: prefer PubMed abstract, S2 citation count
                    existing = all_papers[key]
                    if not existing.abstract and p.abstract:
                        existing.abstract = p.abstract
                    if p.citation_count > existing.citation_count:
                        existing.citation_count = p.citation_count
                    if p.is_open_access and not existing.is_open_access:
                        existing.is_open_access = p.is_open_access
                        existing.oa_pdf_url = p.oa_pdf_url

            search_log.append({
                "query": query,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "pubmed_results": pm_count,
                "s2_results": len(s2_papers),
                "wall_seconds": round(time.time() - ts_start, 1),
            })

        self.stats.papers_after_dedup = len(all_papers)
        logger.info("Search complete: %d unique papers from %d queries",
                     len(all_papers), len(queries))

        # Fail LOUDLY on a total miss. Every query returning 0 results is the
        # signal that the query build produced garbage (e.g. an abbreviation
        # slug + brief-boilerplate terms — audit 2026-06-14). We do NOT fall
        # back to broad domain-agnostic queries that would fill the corpus
        # with off-domain papers + pollute the shared library; better an
        # empty/small on-topic set than 50 off-topic papers. Surface a clear
        # warning in stats.errors (→ search_log.json) and the log so init
        # output and downstream phases see it.
        if queries and not all_papers:
            msg = (
                f"literature search returned 0 results for all "
                f"{len(queries)} queries — check topic terms; NOT falling "
                f"back to domain-agnostic queries. Queries tried: "
                f"{queries!r}"
            )
            logger.warning(msg)
            self.stats.errors.append(msg)

        # Phase 2: Rank and trim
        papers = list(all_papers.values())
        current_year = time.gmtime().tm_year

        def _rank_score(p: Paper) -> float:
            recency = max(0.5, 1.0 - 0.05 * (current_year - (p.year or 2000)))
            return p.citation_count * recency

        papers.sort(key=_rank_score, reverse=True)
        papers = papers[:self.max_papers]

        # Phase 3: Enrich with CrossRef
        for p in papers:
            if p.doi:
                crossref_enrich(p)
                self.stats.crossref_enriched += 1

        # Phase 4: Check Unpaywall for OA status
        for p in papers:
            unpaywall_check(p)
            self.stats.unpaywall_checked += 1
            if p.is_open_access:
                self.stats.oa_papers += 1
            else:
                self.stats.paywalled_papers += 1

        # Phase 5: Download OA PDFs — consult the shared library first so
        # we don't re-download papers already cached from prior runs.
        lib_root = self._resolve_library_root() if self.use_library else None
        for p in papers:
            if not p.oa_pdf_url:
                continue
            if self._total_downloaded >= self.max_total_bytes:
                logger.info("Total download limit reached (%.1f MB)",
                            self._total_downloaded / 1024 / 1024)
                break
            from omicairn import paper_library
            slug = paper_library.slug_for(p)
            pdf_path = self.output_dir / PAPER_LIB_PDFS_DIR / f"{slug}.pdf"

            # Library cache lookup — REFERENCE the library PDF (hardlink,
            # falling back to copy cross-device) instead of re-downloading,
            # so the bytes exist once on disk. Silent no-op when use_library
            # is off or the paper is not in the library yet. Also pull the
            # shared converted markdown so the run reuses it (conversion
            # happens once, in the library).
            #
            # Identity is the SLUG both sides already agree on (slug_for):
            # DOI-bearing papers resolve via the dedup-key `get(p.doi)`, but
            # arXiv papers carry NO DOI and so were never found here — they
            # re-downloaded every run AND duplicated the bytes. Fall back to
            # the slug-keyed `get_by_slug` (the unified key) so a no-DOI paper
            # already in the shared lib is hardlink-reused, not re-fetched.
            if self.use_library and not pdf_path.exists():
                cached = (paper_library.get(p.doi, root=lib_root) if p.doi
                          else None) \
                    or paper_library.get_by_slug(slug, root=lib_root)
                if cached and cached.get("oa_pdf_path"):
                    lib_pdf = lib_root / cached["oa_pdf_path"]
                    if lib_pdf.exists():
                        paper_library.link_or_copy(lib_pdf, pdf_path)
                        p.oa_pdf_path = f"{PAPER_LIB_PDFS_DIR}/{slug}.pdf"
                        self.stats.pdfs_downloaded += 1
                        self.stats.total_pdf_bytes += pdf_path.stat().st_size
                        self._copy_cached_markdown(p, slug, lib_root)
                        logger.info("paper_library cache hit: %s", p.doi or slug)
                        continue

            if _download_file(p.oa_pdf_url, pdf_path, self.max_file_bytes):
                actual_size = pdf_path.stat().st_size
                self._total_downloaded += actual_size
                self.stats.total_pdf_bytes += actual_size
                self.stats.pdfs_downloaded += 1
                p.oa_pdf_path = f"{PAPER_LIB_PDFS_DIR}/{slug}.pdf"

        # Phase 6: Europe PMC full text for OA papers
        for p in papers:
            if p.is_open_access and not p.fulltext_path:
                epmc_fulltext(p, self.output_dir)
                if p.fulltext_path:
                    self.stats.fulltexts_downloaded += 1

        # Phase 7: Save abstracts
        for p in papers:
            if not p.abstract:
                continue
            from omicairn import paper_library
            slug = paper_library.slug_for(p)
            abs_path = self.output_dir / PAPER_LIB_ABSTRACTS_DIR / f"{slug}.txt"
            abs_path.write_text(
                f"Title: {p.title}\n"
                f"Authors: {p.authors}\n"
                f"Year: {p.year}\n"
                f"Journal: {p.journal}\n"
                f"DOI: {p.doi or 'N/A'}\n"
                f"PMID: {p.pmid or 'N/A'}\n\n"
                f"{p.abstract}\n",
                encoding="utf-8",
            )
            self.stats.abstracts_saved += 1

        # Phase 8: Write papers.json
        papers_data = [asdict(p) for p in papers]
        # Remove internal score
        for pd in papers_data:
            pd.pop("_score", None)
        papers_json = self.output_dir / "papers.json"
        papers_json.write_text(
            json.dumps(papers_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Phase 9: Write search log
        search_log_path = self.output_dir / "search_log.json"
        search_log_path.write_text(
            json.dumps({
                "queries": search_log,
                "stats": asdict(self.stats),
                "domain": self.domain,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Phase 10: Write README summary
        readme = self.output_dir / "README.md"
        # Surface a total-miss warning at the TOP of the summary so the init
        # output and a human skim both see it immediately, rather than the
        # fetch looking "successful" with a silently-empty corpus.
        warn_block = ""
        if self.stats.errors:
            warn_block = (
                "> **WARNING — literature fetch issue(s):**\n"
                + "".join(f"> - {e}\n" for e in self.stats.errors)
                + "\n"
            )
        readme.write_text(
            f"# Literature Fetch Summary\n\n"
            f"{warn_block}"
            f"- **Domain:** {self.domain}\n"
            f"- **Queries run:** {self.stats.queries_run}\n"
            f"- **Papers found:** {self.stats.papers_after_dedup}\n"
            f"- **Papers kept (top {self.max_papers}):** {len(papers)}\n"
            f"- **Abstracts saved:** {self.stats.abstracts_saved}\n"
            f"- **OA papers:** {self.stats.oa_papers}\n"
            f"- **Paywalled papers:** {self.stats.paywalled_papers}\n"
            f"- **PDFs downloaded:** {self.stats.pdfs_downloaded} "
            f"({self.stats.total_pdf_bytes / 1024 / 1024:.1f} MB)\n"
            f"- **Full texts (Europe PMC):** {self.stats.fulltexts_downloaded}\n"
            f"- **CrossRef enriched:** {self.stats.crossref_enriched}\n\n"
            f"See `papers.json` for full metadata, `abstracts/` for text, "
            f"`pdfs/` for OA PDFs.\n",
            encoding="utf-8",
        )

        # Phase 11: Write LITERATURE_INDEX.md — a human-readable, citation-
        # ready manifest of what's in this folder. Distinct from README.md
        # (which is a stats summary): this is the table the user, and the
        # PLAN/EXECUTE phases, scan when deciding which paper backs which claim.
        index_path = self.output_dir / "LITERATURE_INDEX.md"
        index_path.write_text(
            render_literature_index(papers, domain=self.domain),
            encoding="utf-8",
        )

        # Phase 12: Sync into the shared paper library so future workspaces
        # hit the cache instead of re-downloading (and re-converting) these
        # papers. CONTRIBUTE with convert=True so the PDF→markdown happens
        # once here and is shared. Best-effort: library problems
        # (permissions, full disk) must not fail the fetch.
        if self.use_library:
            try:
                from omicairn import paper_library
                contrib_root = lib_root if lib_root is not None \
                    else self._resolve_library_root()
                ws_tag = self.output_dir.parent.name
                for p in papers:
                    slug = paper_library.slug_for(p)
                    pdf_src = (self.output_dir / PAPER_LIB_PDFS_DIR / f"{slug}.pdf"
                               if p.oa_pdf_path else None)
                    abs_src = (self.output_dir / PAPER_LIB_ABSTRACTS_DIR / f"{slug}.txt"
                               if p.abstract else None)
                    # `add` is a no-op on duplicate keys aside from provenance.
                    paper_library.add(
                        p, root=contrib_root,
                        pdf_source=pdf_src if pdf_src and pdf_src.exists() else None,
                        abstract_source=abs_src if abs_src and abs_src.exists() else None,
                        workspace=ws_tag,
                        source="literature_fetcher",
                        extra_provenance={"query": p.relevance_query},
                        convert=True,
                    )
                    # Pull the freshly-converted markdown back into the run.
                    if p.oa_pdf_path:
                        self._copy_cached_markdown(p, slug, contrib_root)
                self.stats.library_synced = len(papers)
            except Exception as e:  # noqa: BLE001 — defensive at side-effect boundary
                logger.warning("paper_library sync skipped: %s", e)

        logger.info("Literature fetch complete: %d papers, %d PDFs, %d abstracts",
                     len(papers), self.stats.pdfs_downloaded, self.stats.abstracts_saved)
        return papers


# ---------------------------------------------------------------------------
# Human-readable index renderer (LITERATURE_INDEX.md)
# ---------------------------------------------------------------------------

def _format_authors_short(authors: "str | list | None", max_names: int = 3) -> str:
    """Return "Smith J, Doe J, Lee K, et al." given a comma/semi-separated
    authors string OR a list of author-name strings.

    `authors` is LLM-authored data (papers.json), so it can legitimately arrive as a JSON list rather than a
    delimited string — normalise both shapes here rather than assuming a
    string (a list-valued `authors` crashed a literature
    sync with `re.split(... , list)` → TypeError; see the development notes
    2026-06-24-literature-sync-list-authors)."""
    if not authors:
        return ""
    if isinstance(authors, (list, tuple)):
        parts = [str(a).strip() for a in authors if str(a).strip()]
        if len(parts) <= max_names:
            return ", ".join(parts)
        return ", ".join(parts[:max_names]) + ", et al."
    parts = [a.strip() for a in re.split(r"[;,]", str(authors)) if a.strip()]
    if len(parts) <= max_names:
        return ", ".join(parts)
    return ", ".join(parts[:max_names]) + ", et al."


def _format_reference_entry(p: "Paper | dict") -> str:
    """One-line Vancouver-style reference for the table. Accepts either
    a Paper dataclass or a plain dict (so CLI users can re-render from
    papers.json without re-instantiating Paper)."""
    if isinstance(p, dict):
        title = p.get("title") or ""
        authors = p.get("authors") or ""
        year = p.get("year")
        journal = p.get("journal") or ""
        volume = p.get("volume") or ""
        pages = p.get("pages") or ""
        doi = p.get("doi") or ""
    else:
        title = p.title
        authors = p.authors
        year = p.year
        journal = p.journal
        volume = p.volume
        pages = p.pages
        doi = p.doi or ""
    short = _format_authors_short(authors)
    tail_bits: list[str] = []
    if journal:
        tail_bits.append(journal)
    if year:
        tail_bits.append(str(year))
    tail = "; ".join(tail_bits)
    if volume:
        tail += f";{volume}"
    if pages:
        tail += f":{pages}"
    cite = ". ".join(b for b in (short, title, tail) if b)
    if doi:
        cite += f". doi:{doi}"
    return cite.rstrip(". ") + "."


def render_literature_index(
    papers: "list[Paper] | list[dict]",
    domain: str | None = None,
) -> str:
    """Return the body of ``literature/LITERATURE_INDEX.md``.

    The manifest is intentionally compact so the user can scan the
    whole fetched corpus on one screen: one table row per paper
    showing citation status (OA / abstract-only), a short reference
    line, and paths to the abstract/PDF artifacts on disk. Phase
    agents can read papers.json programmatically; humans read this.
    """
    pkg: list[dict] = []
    for p in papers:
        if isinstance(p, dict):
            pkg.append(p)
        else:
            pkg.append(asdict(p))

    total = len(pkg)
    oa = sum(1 for d in pkg if d.get("is_open_access"))
    paywalled = sum(1 for d in pkg if d.get("paywalled"))
    with_pdf = sum(1 for d in pkg if d.get("oa_pdf_path"))
    with_fulltext = sum(1 for d in pkg if d.get("fulltext_path"))

    lines: list[str] = []
    lines.append("# Literature Index")
    lines.append("")
    if domain:
        lines.append(f"Domain: `{domain}`")
    lines.append(
        f"Papers: {total}  |  OA: {oa}  |  Paywalled: {paywalled}  |  "
        f"PDFs on disk: {with_pdf}  |  Full-text XML: {with_fulltext}"
    )
    lines.append("")
    lines.append(
        "This file is a human-readable manifest of papers fetched for "
        "this workspace. The pipeline phases consume `papers.json`; "
        "this file is the one you skim."
    )
    lines.append("")
    lines.append("## Entries")
    lines.append("")
    lines.append(
        "| # | Access | Reference | Abstract | PDF / Full-text |"
    )
    lines.append(
        "|---|--------|-----------|----------|-----------------|"
    )
    for i, d in enumerate(pkg, start=1):
        if d.get("is_open_access") and d.get("oa_pdf_path"):
            access = "OA+PDF"
        elif d.get("is_open_access"):
            access = "OA"
        elif d.get("paywalled"):
            access = "PAYWALL"
        else:
            access = "ABS"
        ref = _format_reference_entry(d).replace("|", r"\|")
        pmid = d.get("pmid") or ""
        doi = d.get("doi") or ""
        abs_stub = ""
        if pmid:
            abs_stub = f"abstracts/pmid_{pmid}.txt"
        elif doi:
            safe = re.sub(r"[^A-Za-z0-9]+", "_", doi).strip("_")
            abs_stub = f"abstracts/doi_{safe}.txt"
        pdf_stub = d.get("oa_pdf_path") or d.get("fulltext_path") or "—"
        lines.append(
            f"| {i} | {access} | {ref} | {abs_stub or '—'} | {pdf_stub} |"
        )
    lines.append("")
    lines.append("## Legend")
    lines.append("")
    lines.append("- `OA+PDF` — open access with PDF downloaded locally.")
    lines.append("- `OA` — open access, abstract only (no PDF fetched).")
    lines.append("- `PAYWALL` — paywalled; cite abstract only.")
    lines.append("- `ABS` — abstract-only record (e.g., PubMed without OA).")
    lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# High-level entry point (called from workspace.py or CLI)
# ---------------------------------------------------------------------------

def fetch(
    research_md: Any,
    output_dir: pathlib.Path,
    max_papers: int = LiteratureFetcher.DEFAULT_MAX_PAPERS,
    max_pdfs: int | None = None,  # None = no limit (governed by total bytes)
    timeout_seconds: int = 300,
) -> list[Paper]:
    """Fetch literature for a RESEARCH.md. Returns list of Paper objects.

    This is the main entry point used by workspace.init() and the CLI.
    """
    queries = generate_queries(
        research_question=research_md.research_question,
        domain=research_md.domain,
        topic_slug=research_md.topic_slug,
    )
    logger.info("Generated %d search queries for topic=%s domain=%s",
                len(queries), research_md.topic_slug, research_md.domain)

    fetcher = LiteratureFetcher(
        output_dir=output_dir,
        max_papers=max_papers,
        domain=research_md.domain,
    )
    return fetcher.search_and_fetch(
        queries=queries,
        research_question=research_md.research_question,
    )


# ---------------------------------------------------------------------------
# IDEATE → literature/ bridge
# ---------------------------------------------------------------------------

def export_sources_to_literature(
    output_dir: pathlib.Path,
    sources: list[dict],
    domain: str = "",
    download: bool = False,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    max_download_count: int = 200,
    use_library: bool = True,
    library_root: pathlib.Path | None = None,
) -> dict:
    """Bridge IDEATE-surveyed `source` nodes into the workspace's
    ``literature/`` store so the downstream pipeline (PLAN literature
    review) inherits IDEATE's bibliography.

    `sources` are dicts shaped like IDEATE source nodes:
    ``{"title","summary","source_type","url","author","year"}``. Each is
    mapped to a Paper, MERGED into ``literature/papers.json`` (dedup by
    title/doi — never clobbers an existing fetched corpus), the abstract
    written to ``abstracts/<slug>.txt``, and — when ``download`` — the
    URL best-effort fetched to ``pdfs/<slug>.pdf`` (failures are skipped,
    exactly like the normal fetcher's 403 handling). LITERATURE_INDEX.md
    is regenerated. Idempotent: safe to call after every IDEATE pass and
    again on --adopt.

    When ``use_library`` (default), any newly-downloaded PDFs are also
    CONTRIBUTED to the shared lab library (with PDF→markdown conversion),
    and the shared converted markdown for cited papers is PULLED back into
    ``literature/markdown/<slug>.md``. Both are best-effort — a library
    problem never fails the bridge.

    Returns counts: {added, merged_existing, downloaded, abstracts,
    contributed, markdown_pulled}.
    """
    output_dir = pathlib.Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / PAPER_LIB_ABSTRACTS_DIR).mkdir(exist_ok=True)
    (output_dir / PAPER_LIB_PDFS_DIR).mkdir(exist_ok=True)
    (output_dir / PAPER_LIB_MARKDOWN_DIR).mkdir(exist_ok=True)
    from omicairn import paper_library

    # Load any existing papers.json (real fetch may have populated it).
    papers_json = output_dir / "papers.json"
    existing: list[dict] = []
    if papers_json.exists():
        try:
            existing = json.loads(papers_json.read_text(encoding="utf-8")) or []
        except (OSError, json.JSONDecodeError):
            existing = []
    by_key = {Paper(**{k: v for k, v in p.items()
                       if k in Paper.__dataclass_fields__}).dedup_key: p
              for p in existing if isinstance(p, dict)}

    counts = {"added": 0, "merged_existing": len(existing),
              "downloaded": 0, "abstracts": 0, "download_capped": False,
              "contributed": 0, "markdown_pulled": 0}
    used_slugs: dict[str, str] = {}   # slug -> dedup_key, to break collisions
    # dedup_key -> (Paper, slug) for papers that got a downloaded PDF this
    # call, used to contribute them to the shared library afterwards.
    downloaded_papers: list[tuple[Paper, str]] = []
    total_bytes = 0

    def _attempt_pdf(p: Paper, slug: str) -> bool:
        """Best-effort PDF download for one paper. Mutates `p.oa_pdf_path`
        / `p.paywalled`, bumps counts/total_bytes, and records the paper
        for shared-library contribution on success. Returns True iff a PDF
        landed. Bounded by an aggregate byte cap + count cap so a large
        survey can't exhaust disk."""
        nonlocal total_bytes
        if not (download and p.oa_pdf_url):
            return False
        if counts["downloaded"] >= max_download_count or total_bytes >= max_total_bytes:
            counts["download_capped"] = True  # hit the byte/count ceiling
            return False
        pdf_path = output_dir / PAPER_LIB_PDFS_DIR / f"{slug}.pdf"
        if _download_file(_pdf_url_for(p.oa_pdf_url), pdf_path, max_file_bytes):
            p.oa_pdf_path = f"{PAPER_LIB_PDFS_DIR}/{slug}.pdf"
            counts["downloaded"] += 1
            downloaded_papers.append((p, slug))
            try:
                total_bytes += pdf_path.stat().st_size
            except OSError:
                pass
            return True
        p.paywalled = True  # URL didn't yield a file (paywall/HTML)
        return False

    def _slug_for(p: Paper) -> str:
        slug = paper_library.slug_for(p)
        # De-collision: slug_for truncates title to 60 chars but dedup_key
        # uses 80, so two distinct entries can share a slug and silently
        # overwrite each other's abstract/PDF. Suffix a short hash of the
        # dedup_key when a *different* entry already claimed this slug.
        if used_slugs.get(slug, p.dedup_key) != p.dedup_key:
            slug = f"{slug}_{hashlib.sha1(p.dedup_key.encode()).hexdigest()[:8]}"
        used_slugs[slug] = p.dedup_key
        return slug

    for s in sources:
        if not isinstance(s, dict) or not s.get("title"):
            continue
        st = str(s.get("source_type") or "other")
        yr = s.get("year")
        p = Paper(
            title=str(s.get("title")),
            authors=str(s.get("author") or ""),
            year=int(yr) if isinstance(yr, int) or
            (isinstance(yr, str) and yr.isdigit()) else None,
            abstract=str(s.get("summary") or ""),
            oa_pdf_url=(str(s["url"]) if s.get("url") else None),
            is_open_access=bool(s.get("url")),
            source_api=f"ideate:{st}",
            domain_tags=[st],
        )
        if p.dedup_key in by_key:
            # Already present (e.g. a prior metadata-only pass wrote it, or
            # a real fetch did). Don't clobber the richer entry — but DO
            # top up a MISSING PDF when this is a download pass. Without
            # this, a metadata-first pass (download=False) followed by a
            # download pass (download=True) would skip every entry here and
            # never fetch a single PDF — the IDEATE→literature seam bug
            # where a 161-source survey landed 0 PDFs (see TD / 2026-06-09).
            if not download:
                continue
            existing = by_key[p.dedup_key]
            existing_pdf = existing.get("oa_pdf_path")
            pdf_on_disk = bool(existing_pdf) and (output_dir / existing_pdf).exists()
            if pdf_on_disk or not existing.get("oa_pdf_url"):
                continue  # already has a PDF, or no URL to fetch one from
            # Carry the existing entry's richer fields onto the download
            # Paper so a successful fetch updates the SAME entry in place.
            p.oa_pdf_url = existing.get("oa_pdf_url") or p.oa_pdf_url
            slug = _slug_for(p)
            if _attempt_pdf(p, slug):
                existing["oa_pdf_path"] = p.oa_pdf_path
                existing["paywalled"] = False
            elif p.paywalled:
                existing["paywalled"] = True
            continue
        slug = _slug_for(p)
        # Write the abstract/summary text.
        if p.abstract:
            (output_dir / PAPER_LIB_ABSTRACTS_DIR / f"{slug}.txt").write_text(
                p.abstract, encoding="utf-8")
            counts["abstracts"] += 1
        _attempt_pdf(p, slug)
        by_key[p.dedup_key] = asdict(p)
        counts["added"] += 1

    merged = list(by_key.values())
    papers_json.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    (output_dir / "LITERATURE_INDEX.md").write_text(
        render_literature_index(merged, domain=domain), encoding="utf-8")

    # Contribute newly-downloaded PDFs to the shared lab library (with
    # PDF→markdown conversion) and pull the shared markdown for the cited
    # papers back into this run's literature/markdown/. Best-effort: the
    # bridge must never fail the run over a library hiccup.
    if use_library:
        try:
            ws_root = paper_library.workspaces_root_for(output_dir) \
                or output_dir.parent.parent
            lib_root = paper_library.resolve_root(
                explicit=library_root,
                workspaces_root=ws_root,
            )
            ws_tag = output_dir.parent.name
            for p, slug in downloaded_papers:
                pdf_src = output_dir / PAPER_LIB_PDFS_DIR / f"{slug}.pdf"
                abs_src = output_dir / PAPER_LIB_ABSTRACTS_DIR / f"{slug}.txt"
                paper_library.add(
                    p, root=lib_root,
                    pdf_source=pdf_src if pdf_src.exists() else None,
                    abstract_source=abs_src if abs_src.exists() else None,
                    workspace=ws_tag, source="ideate_bridge", convert=True,
                )
                counts["contributed"] += 1
                # Pull the freshly-converted markdown for this contributed
                # paper. IDEATE sources are often title-only (no DOI/PMID),
                # so resolve the markdown by its on-disk slug.
                text = paper_library.get_markdown(slug, root=lib_root)
                if text:
                    md_dst = output_dir / PAPER_LIB_MARKDOWN_DIR / f"{slug}.md"
                    if not md_dst.exists():
                        md_dst.write_text(text, encoding="utf-8")
                        counts["markdown_pulled"] += 1
            # Also pull shared markdown for cited papers carrying a library
            # key (e.g. a real fetch populated papers.json with DOIs).
            for entry in merged:
                key = entry.get("doi") or entry.get("pmid")
                if not key:
                    continue
                slug = paper_library.slug_for(entry)
                md_dst = output_dir / PAPER_LIB_MARKDOWN_DIR / f"{slug}.md"
                if md_dst.exists():
                    continue
                text = paper_library.get_markdown(str(key), root=lib_root)
                if text:
                    md_dst.write_text(text, encoding="utf-8")
                    counts["markdown_pulled"] += 1
        except Exception as e:  # noqa: BLE001 — best-effort side-effect
            logger.warning("paper_library bridge sync skipped: %s", e)
    return counts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="omicairn-literature",
        description="Fetch academic papers for a OmiCairn workspace",
    )
    parser.add_argument("--research-md", required=True,
                        help="Path to RESEARCH.md")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory for literature/")
    parser.add_argument("--max-papers", type=int,
                        default=LiteratureFetcher.DEFAULT_MAX_PAPERS)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    from . import research_md as rmd_mod
    rmd = rmd_mod.load(args.research_md)
    output_dir = pathlib.Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    papers = fetch(rmd, output_dir, max_papers=args.max_papers)
    print(f"Done: {len(papers)} papers fetched to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
