"""Unit tests for omicairn.literature — pure, no-network tests.

These tests exercise the deterministic, domain-agnostic parts of the
literature module: query generation, paper deduplication, and the
FetchStats/Paper dataclasses. No HTTP. If a test here hits the network
we have a regression.
"""
from __future__ import annotations

import json
import re

import pytest

from omicairn import literature
from omicairn.literature import (
    FetchStats,
    LiteratureFetcher,
    Paper,
    generate_queries,
    render_literature_index,
)


# ---------------------------------------------------------------------------
# Paper.dedup_key
# ---------------------------------------------------------------------------

def test_paper_dedup_key_prefers_doi():
    p = Paper(pmid="12345", doi="10.1038/nature12345", title="X")
    assert p.dedup_key == "doi:10.1038/nature12345"


def test_paper_dedup_key_falls_back_to_pmid():
    p = Paper(pmid="12345", doi=None, title="X")
    assert p.dedup_key == "pmid:12345"


def test_paper_dedup_key_falls_back_to_title():
    p = Paper(title="A fairly specific paper title about a topic")
    assert p.dedup_key.startswith("title:")
    assert "fairly specific paper" in p.dedup_key


def test_paper_dedup_key_case_insensitive_on_doi():
    a = Paper(doi="10.1038/NATURE12345")
    b = Paper(doi="10.1038/nature12345")
    assert a.dedup_key == b.dedup_key


# ---------------------------------------------------------------------------
# generate_queries — domain-agnostic, deterministic
# ---------------------------------------------------------------------------

def test_generate_queries_returns_at_most_seven():
    qs = generate_queries(
        research_question="Does X predict Y in Z?",
        domain="general",
        topic_slug="x-predicts-y",
    )
    assert 1 <= len(qs) <= 7


def test_generate_queries_starts_with_slug_based_primary():
    qs = generate_queries(
        research_question="How does pyroptosis affect lung cancer prognosis?",
        domain="oncology",
        topic_slug="pyroptosis-luad",
    )
    # Q1 must at least contain the slug terms.
    assert "pyroptosis" in qs[0].lower()
    assert "luad" in qs[0].lower()


def test_generate_queries_includes_negative_replication_angle():
    """With an informative domain the negative/replication angle is emitted
    (and domain-anchored — see the wave-2 contamination tests). With a
    'general' domain it is dropped instead, so this test now supplies a real
    domain (audit 2026-06-14: the unanchored angle was a contamination
    source)."""
    qs = generate_queries(
        research_question="Can we predict X from Y using LASSO?",
        domain="statistics_machine_learning",
        topic_slug="lasso-xy",
    )
    joined = " ".join(qs).lower()
    assert "replication" in joined or "negative" in joined


def test_generate_queries_includes_recent_year_filter():
    qs = generate_queries(
        research_question="Does gradient descent converge for non-convex losses?",
        domain="machine_learning",
        topic_slug="gd-nonconvex",
    )
    joined = " ".join(qs)
    # Recent-year query uses literal year tokens.
    assert "2024" in joined or "2025" in joined or "2023" in joined


def test_generate_queries_recent_query_is_domain_anchored():
    """Regression (audit 2026-06-10): the recent-work query must fold in
    the domain so a generic slug ('safety', 'gap', 'direction') + bare
    years does not pull off-topic recent literature (biomedical papers
    polluted the shared library for ml_* cases). The year+domain must
    co-occur in one query."""
    qs = generate_queries(
        research_question="Do unsafe-concept directions align across modalities?",
        domain="ml_multimodal_safety_interpretability",
        topic_slug="example-study-d5",
    )
    year_qs = [q for q in qs if ("2023" in q or "2024" in q or "2025" in q)]
    assert year_qs, "expected a recent-year query"
    assert any("multimodal" in q.lower() for q in year_qs), (
        f"recent-year query is not domain-anchored: {year_qs}"
    )


def test_generate_queries_recent_query_unanchored_when_domain_general():
    """When domain is general/empty, the recent query stays slug-only
    (no 'general' leak) — preserves back-compat for unlabelled cases."""
    qs = generate_queries(
        research_question="What predicts Y?", domain="general",
        topic_slug="feature-y",
    )
    year_qs = [q for q in qs if ("2023" in q or "2024" in q or "2025" in q)]
    assert year_qs and all(" general" not in q.lower() for q in year_qs)


def test_generate_queries_emits_no_hardcoded_biomedical_tokens():
    """Regression guard: generate_queries must be domain-agnostic.
    For a physics topic the output must NOT contain biomedical tokens
    like 'pubmed', 'cohort', 'TCGA', etc. that a naive implementation
    might introduce."""
    qs = generate_queries(
        research_question=(
            "Does phonon scattering in graphene follow an acoustic "
            "mean-free-path model at cryogenic temperatures?"
        ),
        domain="condensed_matter_physics",
        topic_slug="phonon-graphene",
    )
    joined = " ".join(qs).lower()
    for biomed in ("pubmed", "tcga", "cohort", "cancer", "gene", "mesh"):
        assert biomed not in joined, (
            f"generate_queries leaked biomedical token {biomed!r} "
            f"into a physics query: {qs}"
        )


def test_generate_queries_handles_domain_general_without_appending():
    """When domain='general' we should NOT emit a Q3 that includes
    'general' as a keyword — it's a placeholder, not a real domain."""
    qs = generate_queries(
        research_question="What features predict Y?",
        domain="general",
        topic_slug="feature-y",
    )
    for q in qs:
        assert " general" not in q.lower(), (
            f"'general' domain placeholder leaked into query: {q}"
        )


def test_generate_queries_handles_empty_domain():
    qs = generate_queries(
        research_question="What drives tumor growth in hepatocytes?",
        domain="",
        topic_slug="tumor-growth",
    )
    # Must not crash and must still return at least the primary query, led
    # by the (real-word) slug anchor since there is no domain to fall back
    # on. ('slug-one'/'something' would be all-boilerplate/abbrev now.)
    assert len(qs) >= 1
    assert "tumor" in qs[0].lower() or "growth" in qs[0].lower()


def test_generate_queries_respects_extra_terms():
    qs = generate_queries(
        research_question="Basic question about X",
        domain="general",
        topic_slug="topic-x",
        extra_terms=["benchmark", "accuracy"],
    )
    joined = " ".join(qs).lower()
    assert "benchmark" in joined


def test_generate_queries_extracts_uppercase_entities():
    """ALL-CAPS acronyms (≥3 chars) should make it into Q2."""
    qs = generate_queries(
        research_question=(
            "How does LSTM compare to GRU for NER on CoNLL-2003?"
        ),
        domain="nlp",
        topic_slug="rnn-ner",
    )
    joined = " ".join(qs)
    # Entity query (Q2) collects acronyms not in the slug.
    entity_hits = sum(tok in joined for tok in ("LSTM", "GRU", "NER"))
    assert entity_hits >= 2, (
        f"expected at least 2 uppercase entities in queries; got {qs}"
    )


def test_generate_queries_slug_terms_filtered_from_key_terms():
    """Key terms (used in Q1 as 'extras') must exclude words already
    in the slug, so we don't get redundant queries like
    'pyroptosis luad pyroptosis'.
    """
    qs = generate_queries(
        research_question="Pyroptosis and LUAD interact with gasdermin",
        domain="oncology",
        topic_slug="pyroptosis-luad",
    )
    # Q1 should not repeat 'pyroptosis' or 'luad' after the slug.
    q1 = qs[0].lower()
    assert q1.count("pyroptosis") == 1
    assert q1.count("luad") == 1


# ---------------------------------------------------------------------------
# generate_queries — on-topic anchors, no slug-abbrev / boilerplate soup
# (audit 2026-06-14: topic_slug interp-worldmodel-mm-rai returned 0 hits on
# every query — "interp worldmodel mm rai RESEARCH PURPOSE wave" — then the
# domain-agnostic fallbacks pulled a 100%-contaminated biomedical corpus.)
# ---------------------------------------------------------------------------

# The real wave-2 DISCOVERY brief, faithfully replicated INCLUDING the
# YAML-frontmatter / IDEATE-mode framing that bleeds into the extracted
# research_question (research_mode: DISCOVERY, the "do NOT re-propose ...
# ADOPTED ... compute-FEASIBLE" comment, the NON-DUPLICATE/RUNNABLE
# constraint prose). This is the input that STILL leaked after the first
# fix (verified 2026-06-14): the slug abbreviations ("interp" appears
# verbatim inside "MECH-INTERP"; "mm"/"rai" un-searchable; "worldmodel"
# glued), the domain's glued "worldmodel"/"ai", AND the YAML structural
# words must all be scrubbed. The earlier simplified replica was too weak —
# it dropped the MECH-INTERP fragment and the DISCOVERY/IDEATE framing, so
# its assertions passed while the REAL brief still produced soup.
_WAVE2_RQ = (
    "research_mode: DISCOVERY  domain: "
    "ml_interpretability_worldmodel_multimodal_responsible_ai  topic_slug: "
    "interp-worldmodel-mm-rai. This is an IDEATE-mode case (wave 2); the "
    "IDEATE brainstorm surveys the literature and PROPOSES 6-10 RUNNABLE, "
    "compute-FEASIBLE directions; do NOT re-propose the prior wave's ADOPTED "
    "directions. RESEARCH PURPOSE (wave 2) — Mechanism-grounded, "
    "interpretability-VERIFIED results in MULTIMODAL models, WORLD MODELS, "
    "and RESPONSIBLE AI. Survey the landscape and propose concrete, "
    "high-impact, RUNNABLE directions at the intersection of: "
    "INTERPRETABILITY / MECH-INTERP x WORLD MODELS (learned internal state, "
    "dynamics) x MULTIMODAL (VLM / video) x RESPONSIBLE AI (safety, fairness, "
    "honesty, controllability, evaluation integrity). CONSTRAINTS every "
    "proposed direction must satisfy: compute-FEASIBLE, NON-DUPLICATE vs the "
    "prior wave, carries a Lean-FORMALIZABLE claim."
)
_WAVE2_DOMAIN = "ml_interpretability_worldmodel_multimodal_responsible_ai"
_WAVE2_SLUG = "interp-worldmodel-mm-rai"

# The acid-test vocabulary (verified residual-failure spec, 2026-06-14).
# Slug abbreviations + the YAML/IDEATE structural words that a reviewer
# would NEVER search for. None may appear as a bare token in ANY query.
_FORBIDDEN_BARE_TOKENS = frozenset({
    # slug abbreviations / glued compounds
    "worldmodel", "mm", "rai", "interp",
    # YAML-frontmatter + DISCOVERY/IDEATE-mode structural words
    "discovery", "ideate", "feasible", "feasibility", "mode", "domain",
    "adopted", "adopt", "research", "purpose", "wave", "verified",
    "runnable", "constraints", "nonduplicate", "slug",
})
# A real ML keyword a reviewer WOULD search for must appear in every query.
_REQUIRED_REAL_TERMS = (
    "interpretability", "world model", "multimodal", "responsible",
    "steering", "mechanism", "fairness",
)


def _assert_acid_test(qs, *, label):
    """Every query: (a) carries NO forbidden bare token, (b) carries >= 1
    real ML term. This is the spec acid test (2026-06-14)."""
    assert qs, f"{label}: no queries generated"
    for i, q in enumerate(qs, 1):
        toks = {t for t in re.findall(r"[A-Za-z0-9]+", q.lower())}
        leaked = toks & _FORBIDDEN_BARE_TOKENS
        assert not leaked, (
            f"{label} Q{i} leaked forbidden bare token(s) {leaked}: {q!r}"
        )
        assert any(t in q.lower() for t in _REQUIRED_REAL_TERMS), (
            f"{label} Q{i} carries no real ML term: {q!r}"
        )


def test_generate_queries_wave2_acid_test_no_slug_abbrev_or_yaml_soup():
    """The wave-2 case is the regression of record. Run on the FULL brief
    (YAML/IDEATE framing included): NO query may carry a slug abbreviation
    ('worldmodel','mm','rai','interp') or a YAML/IDEATE structural word
    ('DISCOVERY','IDEATE','FEASIBLE','mode','domain','RUNNABLE',...), and
    EVERY query must carry a real ML term."""
    qs = generate_queries(_WAVE2_RQ, _WAVE2_DOMAIN, _WAVE2_SLUG)
    _assert_acid_test(qs, label="wave2")

    # Belt-and-suspenders on the exact original-failure substrings.
    low = " ".join(qs).lower()
    assert "mm rai" not in low, f"slug-abbrev soup leaked: {qs}"
    assert "worldmodel" not in low, f"glued domain/slug compound leaked: {qs}"
    assert "interp worldmodel" not in low, f"raw slug anchor leaked: {qs}"
    for boiler in ("research", "purpose", "wave", "verified", "runnable",
                   "discovery", "ideate", "feasible", "propose", "survey",
                   "landscape", "mode", "domain"):
        assert not re.search(rf"\b{boiler}\b", low), (
            f"structural token {boiler!r} leaked into queries: {qs}"
        )


def test_generate_queries_wave2_no_unanchored_negative_result_query():
    """The contaminating Q4 ('replication failure OR negative result') must
    NOT appear unanchored: any query containing it must also carry the
    domain phrase so it can't match generic cross-science 'negative result'
    methodology papers (the biomedical 'Errata'/'Correction' contamination
    source — audit 2026-06-14)."""
    qs = generate_queries(_WAVE2_RQ, _WAVE2_DOMAIN, _WAVE2_SLUG)
    neg = [q for q in qs if "negative result" in q.lower()
           or "replication failure" in q.lower()]
    for q in neg:
        assert "multimodal" in q.lower() or "interpretability" in q.lower(), (
            f"negative-result query is domain-unanchored (contamination "
            f"risk): {q}"
        )


def test_generate_queries_drops_negative_result_query_without_domain():
    """With no informative domain, the negative-result angle has nothing to
    keep it on-topic, so it must be DROPPED entirely (not emitted bare)."""
    qs = generate_queries(
        research_question="What predicts Y in setting Z?",
        domain="general",
        topic_slug="predicts-y",
    )
    joined = " ".join(qs).lower()
    assert "negative result" not in joined
    assert "replication failure" not in joined


def test_generate_queries_prior_fairness_wave_still_on_topic():
    """Regression: the prior fairness wave's slug 'interp-steer-fair-mm'
    must still produce good, on-topic queries (the fix must not narrow the
    net for well-formed cases) and ALSO satisfy the full acid test — the
    slug fragments 'interp'/'steer'/'fair'/'mm' must be REWRITTEN to their
    prose words ('interpretability'/'steering'/'fairness'/...) and never
    survive as bare tokens, even though they appear verbatim in the brief's
    own slug references ('example-interp', 'steer for fairness')."""
    rq = (
        "research_mode: DISCOVERY  domain: "
        "ml_interpretability_steering_fairness_multimodal  topic_slug: "
        "interp-steer-fair-mm. This is an IDEATE-mode case. "
        "RESEARCH PURPOSE — Interpretability-VERIFIED steering for FAIRNESS "
        "and RESPONSIBLE AI in multimodal and world models. The program is "
        'NOT "steer for fairness." Survey the landscape and propose '
        "concrete steering, concept-erasure, and sparse-autoencoder "
        "directions at the intersection of INTERPRETABILITY x ACTIVATION "
        "STEERING x FAIRNESS x MULTIMODAL. Distinct from the broad cases "
        "ideate-example-interp and ideate-example-mllm; the sharpest "
        "MM fairness surface is faces. Carries a Lean-FORMALIZABLE claim."
    )
    qs = generate_queries(
        rq, "ml_interpretability_steering_fairness_multimodal",
        "interp-steer-fair-mm",
    )
    _assert_acid_test(qs, label="prior-fairness-wave")
    low = " ".join(qs).lower()
    assert any(t in low for t in (
        "steering", "interpretability", "fairness", "multimodal", "concept",
    )), f"prior fairness case lost its ML terms: {qs}"


def test_generate_queries_slug_abbrev_tokens_not_primary_anchor():
    """A slug whose tokens are short abbreviations absent from the prose
    must not become the Q1 anchor — Q1 should lead with the domain/real
    terms instead. Here the slug 'aa-bb-cc' tokens never appear in prose."""
    qs = generate_queries(
        research_question=(
            "Does contrastive pretraining improve calibration of "
            "vision-language retrieval?"
        ),
        domain="machine_learning_vision_language",
        topic_slug="aa-bb-cc",
    )
    # Q1 must contain a real term, not just 'aa bb cc'.
    assert qs[0].lower() != "aa bb cc"
    assert any(t in qs[0].lower() for t in (
        "machine", "learning", "vision", "language", "contrastive",
        "calibration",
    )), f"Q1 is bare abbreviation soup: {qs[0]!r}"


def test_slug_query_anchor_rewrites_abbrevs_to_prose_words():
    """Unit test of the anchor helper directly: a real standalone word the
    author used is kept verbatim; an abbreviation/prefix is REWRITTEN to the
    fuller prose word; a glued compound is un-glued; an un-resolvable
    abbreviation is dropped; the helper returns [] (not the raw slug) when
    nothing resolves."""
    from omicairn.literature import _slug_query_anchor

    # 'pyroptosis'/'luad' are real standalone words in the prose → kept.
    kept = _slug_query_anchor(
        ["pyroptosis", "luad"],
        "Pyroptosis drives LUAD progression.",
    )
    assert kept == ["pyroptosis", "luad"]

    # The wave-2 slug against prose that contains both the abbreviation
    # fragment ('interp' inside 'MECH-INTERP') AND its expansion: 'interp' is
    # REWRITTEN to 'interpretability' (never kept bare); 'worldmodel' is
    # un-glued to 'world model'; 'mm'/'rai' are dropped.
    kept = _slug_query_anchor(
        ["interp", "worldmodel", "mm", "rai"],
        "Interpretability of MECH-INTERP world models.",
    )
    assert "mm" not in kept and "rai" not in kept
    assert "worldmodel" not in kept
    assert "interp" not in kept, f"bare 'interp' must be rewritten: {kept}"
    assert "interpretability" in kept
    # un-glued compound: 'world' + a 'model'-rooted word (the prose has the
    # plural 'models', so the peeled remainder resolves to 'models').
    assert "world" in kept
    assert any(w.startswith("model") for w in kept), (
        f"glued 'worldmodel' not un-glued to a 'model' word: {kept}"
    )

    # When nothing resolves the helper returns [] — the caller falls back to
    # the cleaned domain phrase, NOT the raw slug (no soup is ever emitted).
    assert _slug_query_anchor(["aa", "bb"], "unrelated prose here") == []


# ---------------------------------------------------------------------------
# LiteratureFetcher — constructor + directory setup (no network)
# ---------------------------------------------------------------------------

def test_fetcher_constructs_without_network(tmp_path):
    f = LiteratureFetcher(
        output_dir=tmp_path / "lit",
        max_papers=10,
        domain="general",
    )
    assert f.max_papers == 10
    assert f.domain == "general"
    assert isinstance(f.stats, FetchStats)
    assert f.stats.queries_run == 0


# ---------------------------------------------------------------------------
# Default fetch target (soft) — bumped to gather a more comprehensive corpus
# ---------------------------------------------------------------------------

def test_fetcher_default_max_papers_is_soft_target():
    """The default corpus size is a SOFT target/ceiling the ranker trims
    to. It was raised so full runs gather a comprehensive pool; it must
    stay a generous number (>= the historical 50) and the constant must be
    the single source of truth wired into the constructor default."""
    assert LiteratureFetcher.DEFAULT_MAX_PAPERS == 60
    # Generous: at least the historical default, so we never narrow the net.
    assert LiteratureFetcher.DEFAULT_MAX_PAPERS >= 50
    # Constructor default tracks the constant (not a stale literal).
    f = LiteratureFetcher(output_dir="/tmp/does-not-matter")
    assert f.max_papers == LiteratureFetcher.DEFAULT_MAX_PAPERS


def test_fetch_entrypoint_default_max_papers_tracks_constant():
    """The high-level `fetch()` entry point's default must track the same
    constant as the constructor, so a full run with no explicit
    --max-papers gathers the comprehensive pool, not the old 50."""
    import inspect

    sig = inspect.signature(literature.fetch)
    assert (
        sig.parameters["max_papers"].default
        == LiteratureFetcher.DEFAULT_MAX_PAPERS
    )


def test_fetchstats_defaults_are_zero():
    s = FetchStats()
    assert s.queries_run == 0
    assert s.pubmed_hits == 0
    assert s.papers_after_dedup == 0
    assert s.errors == []


# ---------------------------------------------------------------------------
# Regression: literature.fetch must accept a ResearchMd-like shim
# ---------------------------------------------------------------------------

class _FakeResearchMd:
    """Bare minimum shim for things literature.fetch() looks at."""
    research_question = "Does foo improve bar?"
    domain = "general"
    topic_slug = "foo-bar"


def test_fetch_entrypoint_accepts_shim(monkeypatch, tmp_path):
    """We don't want to hit the network in tests, so monkeypatch
    LiteratureFetcher.search_and_fetch to assert it's called with the
    generated queries and return an empty list. This is the only
    regression test for the module-level `fetch(...)` glue."""
    seen: dict = {}

    def fake_search_and_fetch(self, queries, research_question=""):
        seen["queries"] = queries
        seen["rq"] = research_question
        return []

    monkeypatch.setattr(
        LiteratureFetcher, "search_and_fetch", fake_search_and_fetch
    )

    result = literature.fetch(
        research_md=_FakeResearchMd(),
        output_dir=tmp_path / "lit",
        max_papers=5,
    )
    assert result == []
    assert "queries" in seen
    assert seen["rq"] == "Does foo improve bar?"
    # fetch() must pass the generated queries through to search_and_fetch.
    # (The slug 'foo-bar' tokenizes to <= 3-char abbreviations that the
    # anchor now drops as un-searchable, so we assert non-empty queries
    # drawn from the research question rather than the literal slug tail.)
    assert seen["queries"], "fetch() generated no queries"
    assert any("improve" in q.lower() for q in seen["queries"])


# ---------------------------------------------------------------------------
# _parse_pubmed_article — PubMed XML parsing
# ---------------------------------------------------------------------------
#
# Takes ~90 lines of XPath-flavored XML navigation. The field layout
# (MedlineCitation/PMID, Article/ArticleTitle, Abstract/AbstractText
# with optional Label attribute, AuthorList/Author/LastName+Initials,
# Journal/Title + JournalIssue/Volume + PubDate/Year, ELocationID
# for DOI, MeshHeadingList) is stable enough to lock in with a
# realistic synthetic PubmedArticle. These tests protect against
# future refactors silently dropping fields.

import xml.etree.ElementTree as _ET
from omicairn.literature import _parse_pubmed_article


def _make_pubmed_xml(
    *,
    pmid: str = "30000000",
    title: str = "A Study of X",
    abstract_sections: list[tuple[str, str]] | None = None,
    authors: list[tuple[str, str]] | None = None,
    journal_title: str = "Nature",
    volume: str = "555",
    year: str = "2024",
    pages: str = "100-110",
    doi: str | None = "10.1038/test.12345",
    mesh_terms: list[str] | None = None,
) -> _ET.Element:
    abstract_sections = abstract_sections or [("", "Plain abstract text.")]
    authors = authors or [("Smith", "J"), ("Jones", "A")]
    mesh_terms = mesh_terms or ["Topic One", "Topic Two"]

    article_xml = f"""
    <PubmedArticle>
      <MedlineCitation>
        <PMID>{pmid}</PMID>
        <Article>
          <ArticleTitle>{title}</ArticleTitle>
          <Abstract>
            {"".join(
                f'<AbstractText{(" Label=" + chr(34) + lbl + chr(34)) if lbl else ""}>{txt}</AbstractText>'
                for lbl, txt in abstract_sections
            )}
          </Abstract>
          <AuthorList>
            {"".join(
                f"<Author><LastName>{last}</LastName><Initials>{init}</Initials></Author>"
                for last, init in authors
            )}
          </AuthorList>
          <Journal>
            <Title>{journal_title}</Title>
            <JournalIssue>
              <Volume>{volume}</Volume>
              <PubDate><Year>{year}</Year></PubDate>
            </JournalIssue>
          </Journal>
          <Pagination><MedlinePgn>{pages}</MedlinePgn></Pagination>
          {f'<ELocationID EIdType="doi">{doi}</ELocationID>' if doi else ""}
        </Article>
        <MeshHeadingList>
          {"".join(
              f"<MeshHeading><DescriptorName>{term}</DescriptorName></MeshHeading>"
              for term in mesh_terms
          )}
        </MeshHeadingList>
      </MedlineCitation>
      <PubmedData/>
    </PubmedArticle>
    """
    return _ET.fromstring(article_xml)


def test_parse_pubmed_article_happy_path():
    """A normal PubmedArticle with all fields should map to a Paper
    with every field populated."""
    article = _make_pubmed_xml()
    p = _parse_pubmed_article(article)
    assert p is not None
    assert p.pmid == "30000000"
    assert p.doi == "10.1038/test.12345"
    assert p.title == "A Study of X"
    assert "Plain abstract text" in p.abstract
    assert "Smith J" in p.authors and "Jones A" in p.authors
    assert p.journal == "Nature"
    assert p.volume == "555"
    assert p.year == 2024
    assert p.pages == "100-110"
    assert "Topic One" in p.keywords
    assert "Topic Two" in p.keywords
    assert p.source_api == "pubmed"


def test_parse_pubmed_article_labeled_abstract_joins_sections():
    """Multi-section structured abstracts (Background/Methods/Results/
    Conclusions) must be joined into a single string with each label
    prefixed, not collapsed into just the final section's text."""
    article = _make_pubmed_xml(
        abstract_sections=[
            ("BACKGROUND", "The context is X."),
            ("METHODS", "We did Y."),
            ("RESULTS", "We found Z."),
            ("CONCLUSIONS", "So W."),
        ]
    )
    p = _parse_pubmed_article(article)
    assert p is not None
    for token in ("BACKGROUND:", "METHODS:", "RESULTS:", "CONCLUSIONS:"):
        assert token in p.abstract
    assert "The context is X" in p.abstract
    assert "We found Z" in p.abstract


def test_parse_pubmed_article_author_truncation_at_five():
    """More than 5 authors must become 'A1, A2, A3, A4, A5 et al.'."""
    article = _make_pubmed_xml(
        authors=[
            ("A", "1"), ("B", "2"), ("C", "3"), ("D", "4"),
            ("E", "5"), ("F", "6"), ("G", "7"),
        ]
    )
    p = _parse_pubmed_article(article)
    assert p is not None
    assert "et al." in p.authors
    # Exactly 5 names before "et al.".
    names = p.authors.replace(" et al.", "").split(", ")
    assert len(names) == 5
    assert "A 1" in names
    assert "E 5" in names
    assert "F 6" not in p.authors  # truncated


def test_parse_pubmed_article_missing_doi_returns_paper_with_doi_none():
    """Missing DOI must not crash — DOI is optional."""
    article = _make_pubmed_xml(doi=None)
    p = _parse_pubmed_article(article)
    assert p is not None
    assert p.doi is None
    # Other fields still populated.
    assert p.pmid == "30000000"


def test_parse_pubmed_article_missing_medline_returns_none():
    """Completely malformed XML with no MedlineCitation returns None
    rather than raising."""
    article = _ET.fromstring("<PubmedArticle><PubmedData/></PubmedArticle>")
    p = _parse_pubmed_article(article)
    assert p is None


def test_parse_pubmed_article_mesh_capped_at_ten():
    """Keywords list is capped at 10 MeSH terms — stored on the Paper.
    A run-away MeSH list would otherwise bloat papers.json."""
    article = _make_pubmed_xml(
        mesh_terms=[f"Term{i}" for i in range(30)]
    )
    p = _parse_pubmed_article(article)
    assert p is not None
    assert len(p.keywords) == 10
    # Should be the FIRST ten, not the last.
    assert "Term0" in p.keywords
    assert "Term29" not in p.keywords


# ---------------------------------------------------------------------------
# LiteratureFetcher.search_and_fetch — full pipeline with mocked APIs
# ---------------------------------------------------------------------------
#
# This is the module's core integration: search -> dedup -> rank ->
# enrich -> OA check -> download -> save. Mocking pubmed_search,
# pubmed_fetch, semantic_scholar_search, crossref_enrich,
# unpaywall_check, epmc_fulltext, and _download_file exercises lines
# 571-738 (~167 LOC) without any HTTP. This is the single biggest
# coverage win in the module.

def test_search_and_fetch_end_to_end_with_mocked_apis(monkeypatch, tmp_path):
    """Full pipeline: two queries, overlapping + non-overlapping hits,
    dedup by DOI, ranked by citation_count, abstracts saved, papers.json
    written, README.md written with counts, search_log.json written."""

    # Query -> PMIDs stub. Query 1 returns one paper, query 2 returns a
    # different one.
    def fake_pubmed_search(query, max_results=20):
        return {
            "q1": ["pm_001"],
            "q2": ["pm_002"],
        }.get(query.split()[0], [])

    def fake_pubmed_fetch(pmids):
        db = {
            "pm_001": Paper(
                pmid="pm_001",
                doi="10.1/alpha",
                title="Alpha paper",
                authors="Smith J",
                year=2023,
                journal="Nature",
                abstract="Alpha abstract text body.",
                citation_count=0,  # will come from S2
                source_api="pubmed",
            ),
            "pm_002": Paper(
                pmid="pm_002",
                doi="10.2/beta",
                title="Beta paper",
                authors="Jones A",
                year=2024,
                journal="Science",
                abstract="Beta abstract text body.",
                citation_count=0,
                source_api="pubmed",
            ),
        }
        return [db[pm] for pm in pmids if pm in db]

    def fake_s2_search(query, max_results=20):
        # Query 1 returns the same alpha paper (should dedup) + a new gamma.
        # Query 2 returns empty.
        if query.startswith("q1"):
            return [
                Paper(
                    doi="10.1/alpha",  # same DOI -> dedup
                    title="Alpha paper (S2)",
                    citation_count=500,  # higher than PubMed's 0
                    is_open_access=True,
                    oa_pdf_url="https://example.org/alpha.pdf",
                    source_api="semantic_scholar",
                ),
                Paper(
                    doi="10.3/gamma",
                    title="Gamma paper",
                    year=2022,
                    abstract="Gamma abstract.",
                    citation_count=100,
                    source_api="semantic_scholar",
                ),
            ]
        return []

    # The enrichment/OA/fulltext functions are called on each paper but
    # we don't need them to do anything — just return the paper as-is.
    def passthrough_crossref(paper):
        return paper

    def fake_unpaywall(paper):
        # Mark alpha OA (so it attempts a download), others paywalled.
        if paper.doi == "10.1/alpha":
            paper.is_open_access = True
            paper.paywalled = False
        else:
            paper.paywalled = True
        return paper

    def fake_epmc(paper, output_dir):
        return paper

    download_calls: list[str] = []

    def fake_download(url, dest, max_bytes=100 * 1024 * 1024):
        download_calls.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"%PDF-1.4\nfake pdf\n" + b"x" * 50)
        return True

    monkeypatch.setattr(literature, "pubmed_search", fake_pubmed_search)
    monkeypatch.setattr(literature, "pubmed_fetch", fake_pubmed_fetch)
    monkeypatch.setattr(
        literature, "semantic_scholar_search", fake_s2_search
    )
    monkeypatch.setattr(literature, "crossref_enrich", passthrough_crossref)
    monkeypatch.setattr(literature, "unpaywall_check", fake_unpaywall)
    monkeypatch.setattr(literature, "epmc_fulltext", fake_epmc)
    monkeypatch.setattr(literature, "_download_file", fake_download)

    out = tmp_path / "lit"
    fetcher = LiteratureFetcher(output_dir=out, max_papers=10, domain="test")
    papers = fetcher.search_and_fetch(
        queries=["q1 first", "q2 second"], research_question="rq"
    )

    # 3 unique papers expected (alpha, beta, gamma); alpha deduped.
    assert len(papers) == 3
    dois = {p.doi for p in papers}
    assert dois == {"10.1/alpha", "10.2/beta", "10.3/gamma"}

    # Alpha should have the S2 citation count merged in.
    alpha = next(p for p in papers if p.doi == "10.1/alpha")
    assert alpha.citation_count == 500
    # Alpha OA merge from S2 during search phase.
    assert alpha.oa_pdf_url == "https://example.org/alpha.pdf"

    # Stats reflect the pipeline.
    assert fetcher.stats.queries_run == 2
    assert fetcher.stats.pubmed_hits == 2
    assert fetcher.stats.papers_after_dedup == 3
    assert fetcher.stats.abstracts_saved == 3  # all three have abstracts
    assert fetcher.stats.pdfs_downloaded == 1  # only alpha
    assert fetcher.stats.oa_papers == 1
    assert fetcher.stats.paywalled_papers == 2
    assert fetcher.stats.unpaywall_checked == 3

    # Files on disk.
    assert (out / "papers.json").exists()
    papers_json = json.loads((out / "papers.json").read_text())
    assert len(papers_json) == 3
    # Ranking: alpha (500 citations) should come before beta/gamma.
    assert papers_json[0]["doi"] == "10.1/alpha"

    assert (out / "search_log.json").exists()
    log = json.loads((out / "search_log.json").read_text())
    assert len(log["queries"]) == 2
    assert log["domain"] == "test"
    assert log["stats"]["papers_after_dedup"] == 3

    assert (out / "README.md").exists()
    readme = (out / "README.md").read_text()
    assert "3" in readme  # paper count surfaces somewhere
    assert "test" in readme  # domain

    # Abstracts saved per paper.
    abstract_files = list((out / "abstracts").glob("*.txt"))
    assert len(abstract_files) == 3

    # PDFs dir has one file (alpha only).
    pdf_files = list((out / "pdfs").glob("*.pdf"))
    assert len(pdf_files) == 1
    assert download_calls == ["https://example.org/alpha.pdf"]


def test_search_and_fetch_respects_max_total_bytes_stop(monkeypatch, tmp_path):
    """If max_total_bytes is exceeded mid-download loop, remaining OA
    papers must be skipped (but metadata still saved)."""

    def fake_pubmed_search(query, max_results=20):
        return ["pm_a", "pm_b"]

    def fake_pubmed_fetch(pmids):
        return [
            Paper(
                pmid=pm, doi=f"10.0/{pm}", title=f"T {pm}",
                abstract="abs", citation_count=10,
                is_open_access=True, oa_pdf_url=f"https://x/{pm}.pdf",
                source_api="pubmed",
            )
            for pm in pmids
        ]

    def fake_s2(query, max_results=20):
        return []

    def passthrough(p):
        return p

    def fake_unpaywall(p):
        p.is_open_access = True
        p.paywalled = False
        return p

    def fake_epmc(p, od):
        return p

    download_count = {"n": 0}

    def fake_download(url, dest, max_bytes=100 * 1024 * 1024):
        download_count["n"] += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        # 500 KB per file.
        dest.write_bytes(b"\x00" * 500_000)
        return True

    monkeypatch.setattr(literature, "pubmed_search", fake_pubmed_search)
    monkeypatch.setattr(literature, "pubmed_fetch", fake_pubmed_fetch)
    monkeypatch.setattr(literature, "semantic_scholar_search", fake_s2)
    monkeypatch.setattr(literature, "crossref_enrich", passthrough)
    monkeypatch.setattr(literature, "unpaywall_check", fake_unpaywall)
    monkeypatch.setattr(literature, "epmc_fulltext", fake_epmc)
    monkeypatch.setattr(literature, "_download_file", fake_download)

    # max_total_bytes < size of even one file → first download completes,
    # second is skipped.
    fetcher = LiteratureFetcher(
        output_dir=tmp_path / "lit",
        max_papers=10,
        max_total_bytes=100_000,  # smaller than one fake file (500KB)
        domain="test",
    )
    papers = fetcher.search_and_fetch(queries=["q"], research_question="rq")
    assert len(papers) == 2
    # First download went through, second was short-circuited by the
    # total-bytes check.
    assert download_count["n"] == 1
    assert fetcher.stats.pdfs_downloaded == 1


def test_search_and_fetch_merges_s2_abstract_and_counts_fulltexts(
    monkeypatch, tmp_path
):
    """Three search_and_fetch branches that the main end-to-end test
    doesn't reach:

      - L607: when a PubMed paper has NO abstract but the same paper
        from S2 does, the merge branch
        `if not existing.abstract and p.abstract: existing.abstract = p.abstract`
        must copy the abstract over. Without this, papers that slip
        past PubMed's abstract field (some editorials, letters) would
        ship with an empty abstract even when S2 has one.
      - L672: when epmc_fulltext sets `paper.fulltext_path`, the
        per-run `stats.fulltexts_downloaded` counter must increment.
        Stats drift here corrupts the README.md summary line.
      - L677: when a paper finishes the pipeline with no abstract at
        all, the Phase-7 save loop's `if not p.abstract: continue`
        must skip it cleanly (no empty .txt file written).

    Setup:
      - pm_nobody: PubMed paper with abstract="" (L607 merge target)
      - pm_ghost: PubMed paper with abstract="" and S2 ALSO has no
        abstract (L677 continue target — ends up with empty abstract
        after pipeline)
      - S2 returns pm_nobody's DOI with a filled-in abstract
      - fake_epmc sets fulltext_path on pm_nobody (L672 counter hit)
    """
    def fake_pubmed_search(query, max_results=20):
        return ["pm_nobody", "pm_ghost"]

    def fake_pubmed_fetch(pmids):
        db = {
            "pm_nobody": Paper(
                pmid="pm_nobody",
                doi="10.1/nobody",
                title="Nobody paper",
                authors="Smith J",
                year=2024,
                journal="J",
                abstract="",  # intentionally empty
                citation_count=5,
                source_api="pubmed",
            ),
            "pm_ghost": Paper(
                pmid="pm_ghost",
                doi="10.1/ghost",
                title="Ghost paper",
                authors="Doe J",
                year=2024,
                journal="J",
                abstract="",  # intentionally empty, will stay empty
                citation_count=3,
                source_api="pubmed",
            ),
        }
        return [db[p] for p in pmids if p in db]

    def fake_s2_search(query, max_results=20):
        return [
            Paper(
                doi="10.1/nobody",  # same DOI → dedup + abstract merge
                title="Nobody paper (S2)",
                abstract="S2 provided this abstract for nobody.",
                citation_count=50,
                source_api="semantic_scholar",
            ),
            Paper(
                doi="10.1/ghost",  # same DOI, still no abstract
                title="Ghost paper (S2)",
                abstract="",
                citation_count=20,
                source_api="semantic_scholar",
            ),
        ]

    def passthrough(p):
        return p

    def fake_unpaywall(p):
        # Mark nobody as OA so epmc_fulltext runs on it. ghost stays
        # paywalled so the fulltext loop skips it.
        if p.doi == "10.1/nobody":
            p.is_open_access = True
            p.paywalled = False
        else:
            p.paywalled = True
        return p

    def fake_epmc(p, output_dir):
        # Simulate a successful fulltext download for nobody only.
        if p.doi == "10.1/nobody":
            p.fulltext_path = "fulltext/PMC_nobody.xml"
        return p

    monkeypatch.setattr(literature, "pubmed_search", fake_pubmed_search)
    monkeypatch.setattr(literature, "pubmed_fetch", fake_pubmed_fetch)
    monkeypatch.setattr(literature, "semantic_scholar_search", fake_s2_search)
    monkeypatch.setattr(literature, "crossref_enrich", passthrough)
    monkeypatch.setattr(literature, "unpaywall_check", fake_unpaywall)
    monkeypatch.setattr(literature, "epmc_fulltext", fake_epmc)
    # No OA PDF URLs set on any paper → download loop is a no-op.
    monkeypatch.setattr(
        literature, "_download_file",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no downloads expected")
        ),
    )

    out = tmp_path / "lit"
    fetcher = LiteratureFetcher(output_dir=out, max_papers=10, domain="test")
    papers = fetcher.search_and_fetch(queries=["q"], research_question="rq")

    # Dedup reduces 4 hits (2 pm + 2 s2) to 2 unique papers.
    assert len(papers) == 2
    nobody = next(p for p in papers if p.doi == "10.1/nobody")
    ghost = next(p for p in papers if p.doi == "10.1/ghost")

    # L607: nobody's abstract came from S2 after the merge.
    assert nobody.abstract == "S2 provided this abstract for nobody."
    # Ghost stayed abstract-less through the whole pipeline.
    assert ghost.abstract == ""

    # L672: fulltexts_downloaded counter advanced exactly once (nobody).
    assert fetcher.stats.fulltexts_downloaded == 1

    # L677: Phase-7 abstract save loop wrote nobody's abstract but
    # skipped ghost. Only one .txt file on disk.
    abs_files = list((out / "abstracts").glob("*.txt"))
    assert len(abs_files) == 1
    assert fetcher.stats.abstracts_saved == 1


def test_search_and_fetch_all_zero_results_fails_loudly(monkeypatch, tmp_path):
    """REGRESSION (audit 2026-06-14): when EVERY query returns 0 results
    (the signal that the query build produced garbage), the fetcher must
    surface a clear warning in stats.errors + search_log.json + README.md
    rather than silently shipping an empty corpus that looks 'successful'.
    Crucially it must NOT fabricate a fallback that fills the corpus from
    off-domain papers — an empty on-topic set beats 50 off-topic papers."""
    monkeypatch.setattr(literature, "pubmed_search", lambda q, max_results=20: [])
    monkeypatch.setattr(literature, "pubmed_fetch", lambda pmids: [])
    monkeypatch.setattr(
        literature, "semantic_scholar_search", lambda q, max_results=20: []
    )
    # Enrichment/download must never run when there are no papers.
    monkeypatch.setattr(
        literature, "_download_file",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no downloads on an empty corpus")
        ),
    )

    out = tmp_path / "lit"
    fetcher = LiteratureFetcher(
        output_dir=out, max_papers=10, domain="ml_x", use_library=False
    )
    papers = fetcher.search_and_fetch(
        queries=["junk slug soup", "more junk"], research_question="rq"
    )

    # No silent fallback corpus.
    assert papers == []
    assert fetcher.stats.papers_after_dedup == 0

    # Loud signal in stats.errors.
    assert fetcher.stats.errors, "expected a loud all-zero-results error"
    err = fetcher.stats.errors[0].lower()
    assert "0 results for all" in err
    assert "not falling back" in err

    # Surfaced in search_log.json and README.md.
    log = json.loads((out / "search_log.json").read_text())
    assert log["stats"]["errors"], "error not propagated to search_log.json"
    readme = (out / "README.md").read_text()
    assert "WARNING" in readme and "0 results for all" in readme.lower()


def test_search_and_fetch_partial_hit_records_no_zero_error(
    monkeypatch, tmp_path
):
    """The loud signal fires ONLY on a total miss. If even one query yields
    a paper, no all-zero error is recorded (we don't cry wolf)."""
    def fake_s2(query, max_results=20):
        if query.startswith("good"):
            return [Paper(doi="10.1/hit", title="A hit", abstract="x",
                          citation_count=1, source_api="semantic_scholar")]
        return []

    monkeypatch.setattr(literature, "pubmed_search", lambda q, max_results=20: [])
    monkeypatch.setattr(literature, "pubmed_fetch", lambda pmids: [])
    monkeypatch.setattr(literature, "semantic_scholar_search", fake_s2)
    monkeypatch.setattr(literature, "crossref_enrich", lambda p: p)
    monkeypatch.setattr(literature, "unpaywall_check", lambda p: p)
    monkeypatch.setattr(literature, "epmc_fulltext", lambda p, d: p)

    out = tmp_path / "lit"
    fetcher = LiteratureFetcher(
        output_dir=out, max_papers=10, domain="ml_x", use_library=False
    )
    papers = fetcher.search_and_fetch(
        queries=["empty one", "good two"], research_question="rq"
    )
    assert len(papers) == 1
    assert not fetcher.stats.errors


# ---------------------------------------------------------------------------
# Network wrappers — monkeypatched _get_json / _get_xml (no real HTTP)
#
# These tests exercise the individual API client functions
# (pubmed_search, pubmed_fetch, semantic_scholar_search, crossref_enrich,
# unpaywall_check, epmc_fulltext) by stubbing out the underlying HTTP
# helpers. The end-to-end search_and_fetch test already stubs these
# wrappers at a higher level, which means refactors to pubmed_search's
# JSON-parsing logic or to crossref_enrich's field-merge logic would
# have gone silently unnoticed. These tests pin each wrapper directly.
# ---------------------------------------------------------------------------

import xml.etree.ElementTree as ET  # noqa: E402


def test_pubmed_search_returns_pmid_list(monkeypatch):
    def fake_get_json(url, params=None, delay=0.0, retries=3):
        assert "esearch.fcgi" in url
        assert params["db"] == "pubmed"
        assert params["term"] == "cancer immunotherapy"
        return {
            "esearchresult": {
                "idlist": ["12345", "67890", "11111"],
            }
        }

    monkeypatch.setattr(literature, "_get_json", fake_get_json)
    pmids = literature.pubmed_search("cancer immunotherapy", max_results=20)
    assert pmids == ["12345", "67890", "11111"]


def test_pubmed_search_returns_empty_when_api_returns_none(monkeypatch):
    monkeypatch.setattr(
        literature, "_get_json", lambda *a, **k: None
    )
    assert literature.pubmed_search("x") == []


def test_pubmed_search_returns_empty_when_idlist_missing(monkeypatch):
    """Malformed response without 'esearchresult.idlist' must not crash."""
    monkeypatch.setattr(
        literature,
        "_get_json",
        lambda *a, **k: {"esearchresult": {}},
    )
    assert literature.pubmed_search("x") == []


def test_pubmed_fetch_empty_pmid_list_returns_empty():
    """Early-out branch: no PMIDs → no HTTP call at all."""
    assert literature.pubmed_fetch([]) == []


def test_pubmed_fetch_parses_xml_and_batches(monkeypatch):
    """pubmed_fetch should batch PMIDs into groups of 20 and feed each
    batch XML response through _parse_pubmed_article. We return a
    single synthetic PubmedArticleSet with one article per call."""
    batch_calls: list[list[str]] = []

    def fake_get_xml(url, params=None, delay=0.0, retries=3):
        batch_calls.append(params["id"].split(","))
        # Build a minimal valid PubmedArticleSet element.
        xml = (
            "<PubmedArticleSet>"
            + "".join(
                f"<PubmedArticle>"
                f"<MedlineCitation><PMID>{pm}</PMID>"
                f"<Article><ArticleTitle>T{pm}</ArticleTitle></Article>"
                f"</MedlineCitation>"
                f"</PubmedArticle>"
                for pm in params["id"].split(",")
            )
            + "</PubmedArticleSet>"
        )
        return ET.fromstring(xml)

    monkeypatch.setattr(literature, "_get_xml", fake_get_xml)

    pmids = [f"{i:08d}" for i in range(25)]  # 25 PMIDs → 2 batches (20+5)
    papers = literature.pubmed_fetch(pmids)
    assert len(batch_calls) == 2
    assert len(batch_calls[0]) == 20
    assert len(batch_calls[1]) == 5
    assert len(papers) == 25
    assert papers[0].pmid == "00000000"
    assert papers[0].title == "T00000000"


def test_pubmed_fetch_skips_batches_that_return_none(monkeypatch):
    """If _get_xml fails (returns None) for a batch, that batch is
    skipped silently — other batches still contribute."""
    call_count = {"n": 0}

    def flaky(url, params=None, delay=0.0, retries=3):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None  # First batch fails.
        xml = (
            "<PubmedArticleSet>"
            "<PubmedArticle>"
            "<MedlineCitation><PMID>99</PMID>"
            "<Article><ArticleTitle>OK</ArticleTitle></Article>"
            "</MedlineCitation>"
            "</PubmedArticle>"
            "</PubmedArticleSet>"
        )
        return ET.fromstring(xml)

    monkeypatch.setattr(literature, "_get_xml", flaky)
    pmids = [f"{i:08d}" for i in range(25)]  # 25 → 2 batches
    papers = literature.pubmed_fetch(pmids)
    assert call_count["n"] == 2
    assert len(papers) == 1
    assert papers[0].pmid == "99"


def test_parse_pubmed_article_missing_article_element_returns_none():
    """Line 227-228: MedlineCitation without Article child → None."""
    xml = "<PubmedArticle><MedlineCitation><PMID>1</PMID></MedlineCitation></PubmedArticle>"
    el = ET.fromstring(xml)
    assert literature._parse_pubmed_article(el) is None


def test_parse_pubmed_article_swallows_exceptions():
    """Lines 306-308: the broad except at the bottom of the parser
    catches any exception and returns None. We force a crash by
    passing an element whose structure will trip up the parser — a
    None input elicits AttributeError inside the try block."""
    # article.find('MedlineCitation') on None raises AttributeError,
    # which is caught and None is returned.
    assert literature._parse_pubmed_article(None) is None  # type: ignore[arg-type]


def test_parse_pubmed_article_doi_from_article_id_list_fallback():
    """Lines 284-289: if ELocationID has no DOI, fall back to
    PubmedData/ArticleIdList/ArticleId[@IdType='doi']. Pins the
    fallback chain for papers that only carry their DOI under the
    pubmed_data element."""
    xml = """
<PubmedArticle>
  <MedlineCitation>
    <PMID>999</PMID>
    <Article>
      <ArticleTitle>X</ArticleTitle>
    </Article>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList>
      <ArticleId IdType="pubmed">999</ArticleId>
      <ArticleId IdType="doi">10.1234/fallback.doi</ArticleId>
    </ArticleIdList>
  </PubmedData>
</PubmedArticle>
"""
    el = ET.fromstring(xml)
    p = literature._parse_pubmed_article(el)
    assert p is not None
    assert p.doi == "10.1234/fallback.doi"


def test_semantic_scholar_search_parses_response(monkeypatch):
    def fake_get_json(url, params=None, delay=0.0, retries=3):
        assert "paper/search" in url
        return {
            "data": [
                {
                    "paperId": "ABC",
                    "title": "Deep Learning for Cancer",
                    "abstract": "We present...",
                    "authors": [
                        {"name": "Alice Smith"},
                        {"name": "Bob Jones"},
                        {"name": "Carol Lee"},
                        {"name": "Dave Kim"},
                        {"name": "Eve Park"},
                        {"name": "Frank Wu"},  # 6th author → et al.
                    ],
                    "year": 2024,
                    "venue": "Nature",
                    "citationCount": 250,
                    "isOpenAccess": True,
                    "openAccessPdf": {"url": "https://x/pdf"},
                    "externalIds": {
                        "DOI": "10.1/x",
                        "PubMed": "11",
                    },
                },
                {
                    # Test defaults + missing fields.
                    "paperId": "DEF",
                    "title": "Short",
                },
            ],
        }

    monkeypatch.setattr(literature, "_get_json", fake_get_json)
    papers = literature.semantic_scholar_search("cancer", max_results=20)
    assert len(papers) == 2
    p = papers[0]
    assert p.title == "Deep Learning for Cancer"
    assert p.doi == "10.1/x"
    assert p.pmid == "11"
    assert p.citation_count == 250
    assert p.is_open_access is True
    assert p.oa_pdf_url == "https://x/pdf"
    assert "et al." in p.authors
    assert p.source_api == "semantic_scholar"
    assert p.relevance_query == "cancer"
    # Second paper has no authors, no year, no venue — must still parse.
    assert papers[1].title == "Short"
    assert papers[1].authors == ""
    assert papers[1].year is None


def test_semantic_scholar_search_returns_empty_on_missing_data_key(
    monkeypatch,
):
    """Response without 'data' key is treated as empty, not a crash."""
    monkeypatch.setattr(
        literature, "_get_json", lambda *a, **k: {"error": "nope"}
    )
    assert literature.semantic_scholar_search("x") == []


def test_semantic_scholar_search_returns_empty_when_api_none(monkeypatch):
    monkeypatch.setattr(literature, "_get_json", lambda *a, **k: None)
    assert literature.semantic_scholar_search("x") == []


def test_crossref_enrich_without_doi_returns_unchanged():
    """Line 356-357: short-circuit when no DOI — no HTTP, same object."""
    p = Paper(pmid="1", title="no doi", journal="orig")
    out = literature.crossref_enrich(p)
    assert out is p
    assert out.journal == "orig"  # untouched


def test_crossref_enrich_merges_missing_fields_only(monkeypatch):
    """crossref_enrich fills in missing journal/volume/pages/authors/year
    but must NOT overwrite existing values. Source_api should get
    `+crossref` appended."""
    def fake_get_json(url, params=None, delay=0.0, retries=3):
        assert "works/" in url
        return {
            "message": {
                "container-title": ["New England Journal of Medicine"],
                "volume": "390",
                "page": "100-110",
                "author": [
                    {"family": "Smith", "given": "Alice"},
                    {"family": "Jones", "given": "Bob"},
                ],
                "issued": {"date-parts": [[2024, 1, 15]]},
            }
        }

    monkeypatch.setattr(literature, "_get_json", fake_get_json)

    # Paper with some fields already populated: crossref must NOT
    # overwrite them.
    p = Paper(
        doi="10.1/x",
        title="Y",
        journal="Pre-existing",  # must be preserved
        volume="",
        pages="",
        authors="",
        year=None,
        source_api="pubmed",
    )
    out = literature.crossref_enrich(p)
    assert out.journal == "Pre-existing"  # not overwritten
    assert out.volume == "390"
    assert out.pages == "100-110"
    assert "Smith" in out.authors
    assert out.year == 2024
    assert out.source_api == "pubmed+crossref"


def test_crossref_enrich_fills_empty_journal_and_truncates_authors(monkeypatch):
    """L366-367 + L380: when the input paper has an empty `journal`
    field, crossref_enrich pulls the first `container-title` entry
    from the CrossRef response into it. When the CrossRef response
    lists > 5 authors, the formatter emits the first 5 followed by
    `et al.`. The existing merges_missing_fields_only test uses a
    paper with `journal='Pre-existing'` and only 2 authors, so neither
    branch fires there — this test pins both."""
    def fake_get_json(url, params=None, delay=0.0, retries=3):
        return {
            "message": {
                "container-title": ["Cell Reports"],
                "volume": "44",
                "page": "123-130",
                "author": [
                    {"family": "Adams", "given": "Alice"},
                    {"family": "Brown", "given": "Bob"},
                    {"family": "Chen", "given": "Carol"},
                    {"family": "Davis", "given": "David"},
                    {"family": "Evans", "given": "Erin"},
                    # 6th and 7th authors — must collapse to "et al."
                    {"family": "Ford", "given": "Finn"},
                    {"family": "Gao", "given": "Grace"},
                ],
                "issued": {"date-parts": [[2025, 3, 10]]},
            }
        }

    monkeypatch.setattr(literature, "_get_json", fake_get_json)
    p = Paper(
        doi="10.1/x",
        title="Y",
        journal="",          # empty → crossref fills it
        volume="",
        pages="",
        authors="",          # empty → crossref formats from list
        year=None,
        source_api="pubmed",
    )
    out = literature.crossref_enrich(p)
    assert out.journal == "Cell Reports"
    # First 5 authors present; 6th/7th replaced by "et al.".
    assert "Adams" in out.authors
    assert "Evans" in out.authors
    assert "Ford" not in out.authors
    assert "Gao" not in out.authors
    assert out.authors.endswith("et al.")


def test_crossref_enrich_empty_container_leaves_journal_empty(monkeypatch):
    """L366-367 boundary: `container-title` exists as an empty list →
    the helper's `container[0] if container else ""` branch must
    return "" rather than IndexError."""
    def fake_get_json(url, params=None, delay=0.0, retries=3):
        return {"message": {"container-title": [], "author": []}}

    monkeypatch.setattr(literature, "_get_json", fake_get_json)
    p = Paper(doi="10.1/x", journal="", source_api="pubmed")
    out = literature.crossref_enrich(p)
    assert out.journal == ""  # empty container → empty journal, no crash


def test_crossref_enrich_returns_unchanged_when_api_none(monkeypatch):
    monkeypatch.setattr(literature, "_get_json", lambda *a, **k: None)
    p = Paper(doi="10.1/x", journal="orig", source_api="pubmed")
    out = literature.crossref_enrich(p)
    assert out.journal == "orig"
    assert out.source_api == "pubmed"  # not appended


def test_crossref_enrich_handles_missing_message_key(monkeypatch):
    monkeypatch.setattr(
        literature, "_get_json", lambda *a, **k: {"status": "empty"}
    )
    p = Paper(doi="10.1/x", journal="")
    out = literature.crossref_enrich(p)
    assert out.journal == ""  # nothing to enrich with


def test_unpaywall_check_without_doi_marks_paywalled():
    """Line 392-394: no DOI → paywalled=True, no HTTP call."""
    p = Paper(pmid="1", title="X")
    out = literature.unpaywall_check(p)
    assert out.paywalled is True


def test_unpaywall_check_happy_path_reads_oa_pdf(monkeypatch):
    def fake_get_json(url, params=None, delay=0.0, retries=3):
        assert "unpaywall" in url
        assert params["email"]
        return {
            "is_oa": True,
            "best_oa_location": {
                "url_for_pdf": "https://oa/file.pdf",
                "url": "https://oa/page",
            },
        }

    monkeypatch.setattr(literature, "_get_json", fake_get_json)
    p = Paper(doi="10.1/x")
    out = literature.unpaywall_check(p)
    assert out.is_open_access is True
    assert out.paywalled is False
    assert out.oa_pdf_url == "https://oa/file.pdf"


def test_unpaywall_check_non_oa_paper_marks_paywalled(monkeypatch):
    monkeypatch.setattr(
        literature,
        "_get_json",
        lambda *a, **k: {"is_oa": False, "best_oa_location": None},
    )
    p = Paper(doi="10.1/x")
    out = literature.unpaywall_check(p)
    assert out.is_open_access is False
    assert out.paywalled is True
    assert out.oa_pdf_url is None


def test_unpaywall_check_handles_api_none(monkeypatch):
    monkeypatch.setattr(literature, "_get_json", lambda *a, **k: None)
    p = Paper(doi="10.1/x")
    out = literature.unpaywall_check(p)
    # paywalled defaults False; since API returned None we just
    # return the paper unchanged (no OA flag set).
    assert out.paywalled is False
    assert out.is_open_access is False


def test_unpaywall_check_url_for_pdf_fallback_to_generic_url(monkeypatch):
    """If best_oa_location has no url_for_pdf, fall back to `url`."""
    monkeypatch.setattr(
        literature,
        "_get_json",
        lambda *a, **k: {
            "is_oa": True,
            "best_oa_location": {"url": "https://oa/landing"},
        },
    )
    p = Paper(doi="10.1/x")
    out = literature.unpaywall_check(p)
    assert out.oa_pdf_url == "https://oa/landing"


def test_epmc_fulltext_skips_paywalled_papers(tmp_path):
    """Line 413-414: paywalled → early return, no HTTP."""
    p = Paper(doi="10.1/x", paywalled=True)
    out = literature.epmc_fulltext(p, tmp_path)
    assert out is p
    assert out.fulltext_path is None


def test_epmc_fulltext_returns_unchanged_when_search_empty(
    tmp_path, monkeypatch
):
    """Empty EPMC search result list → no fulltext_path set."""
    monkeypatch.setattr(
        literature,
        "_get_json",
        lambda *a, **k: {"resultList": {"result": []}},
    )
    p = Paper(doi="10.1/x", is_open_access=True)
    out = literature.epmc_fulltext(p, tmp_path)
    assert out.fulltext_path is None


def test_epmc_fulltext_returns_unchanged_when_api_returns_none(
    tmp_path, monkeypatch
):
    """L423: when _get_json returns None entirely (HTTP error, timeout,
    every retry exhausted), epmc_fulltext must return the paper
    unchanged rather than AttributeError on `.get(...)`. Distinct from
    the empty-result-list case: there `data` is truthy but its
    resultList is empty; here `data is None`."""
    monkeypatch.setattr(literature, "_get_json", lambda *a, **k: None)
    p = Paper(doi="10.1/x", is_open_access=True)
    out = literature.epmc_fulltext(p, tmp_path)
    assert out is p
    assert out.fulltext_path is None


def test_epmc_fulltext_returns_unchanged_when_no_pmcid(
    tmp_path, monkeypatch
):
    """Search hit but no pmcid → return unchanged."""
    monkeypatch.setattr(
        literature,
        "_get_json",
        lambda *a, **k: {
            "resultList": {"result": [{"title": "x", "isOpenAccess": "Y"}]}
        },
    )
    p = Paper(doi="10.1/x", is_open_access=True)
    out = literature.epmc_fulltext(p, tmp_path)
    assert out.fulltext_path is None


def test_epmc_fulltext_skips_when_isopenaccess_not_Y(
    tmp_path, monkeypatch
):
    """pmcid present but isOpenAccess != 'Y' → no fulltext save."""
    monkeypatch.setattr(
        literature,
        "_get_json",
        lambda *a, **k: {
            "resultList": {
                "result": [{"pmcid": "PMC1", "isOpenAccess": "N"}]
            }
        },
    )
    p = Paper(doi="10.1/x", is_open_access=True)
    out = literature.epmc_fulltext(p, tmp_path)
    assert out.fulltext_path is None


def test_epmc_fulltext_writes_xml_file_for_oa_result(
    tmp_path, monkeypatch
):
    """Full happy path: OA search hit with pmcid + isOpenAccess='Y'
    → fetch fulltext XML via session.get, write to fulltext/ dir,
    set paper.fulltext_path."""
    monkeypatch.setattr(
        literature,
        "_get_json",
        lambda *a, **k: {
            "resultList": {
                "result": [{"pmcid": "PMC42", "isOpenAccess": "Y"}]
            }
        },
    )

    class FakeResponse:
        def __init__(self, content: bytes, status: int = 200):
            self.content = content
            self.status_code = status

    class FakeSession:
        def get(self, url, **kwargs):
            return FakeResponse(b"<article>synthetic</article>")

    monkeypatch.setattr(literature, "_get_session", FakeSession)
    # Also suppress the EPMC_DELAY sleep.
    monkeypatch.setattr(literature.time, "sleep", lambda _s: None)

    p = Paper(doi="10.1/x", is_open_access=True)
    out = literature.epmc_fulltext(p, tmp_path)
    assert out.fulltext_path == "fulltext/PMC42.xml"
    written = tmp_path / "fulltext" / "PMC42.xml"
    assert written.read_bytes() == b"<article>synthetic</article>"


def test_epmc_fulltext_swallows_request_exception(tmp_path, monkeypatch):
    """If the fulltext HTTP call raises RequestException, the paper
    is returned unchanged — the exception is logged but not
    propagated."""
    import requests as _rq

    monkeypatch.setattr(
        literature,
        "_get_json",
        lambda *a, **k: {
            "resultList": {
                "result": [{"pmcid": "PMC42", "isOpenAccess": "Y"}]
            }
        },
    )

    class ExplodingSession:
        def get(self, url, **kwargs):
            raise _rq.ConnectionError("simulated")

    monkeypatch.setattr(literature, "_get_session", ExplodingSession)
    monkeypatch.setattr(literature.time, "sleep", lambda _s: None)

    p = Paper(doi="10.1/x", is_open_access=True)
    out = literature.epmc_fulltext(p, tmp_path)
    assert out.fulltext_path is None


# ---------------------------------------------------------------------------
# HTTP helpers — _get_json, _get_xml, _download_file (fake session)
# ---------------------------------------------------------------------------

class _FakeResp:
    """Minimal drop-in for a requests.Response used by the HTTP helpers."""
    def __init__(
        self,
        status_code: int = 200,
        json_payload=None,
        content: bytes = b"",
        headers: dict | None = None,
        raise_on_status: bool = False,
    ):
        self.status_code = status_code
        self._json = json_payload
        self.content = content
        self.headers = headers or {}
        self._raise = raise_on_status

    def json(self):
        return self._json

    def raise_for_status(self):
        import requests as _rq
        if self._raise or self.status_code >= 400:
            raise _rq.HTTPError(f"HTTP {self.status_code}")

    # For the streaming download path.
    def iter_content(self, chunk_size: int = 8192):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_get_json_happy_path(monkeypatch):
    """_get_json returns parsed JSON on 200."""
    class Sess:
        def get(self, url, params=None, timeout=None):
            assert url == "https://api.example/x"
            return _FakeResp(status_code=200, json_payload={"ok": 1})

    monkeypatch.setattr(literature, "_get_session", lambda: Sess())
    monkeypatch.setattr(literature.time, "sleep", lambda _s: None)
    out = literature._get_json("https://api.example/x")
    assert out == {"ok": 1}


def test_get_json_retries_on_rate_limit_then_succeeds(monkeypatch):
    """429 response must trigger the rate-limit branch, wait, and
    retry. After one 429 we return 200 with a payload."""
    calls = {"n": 0}

    class Sess:
        def get(self, url, params=None, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return _FakeResp(status_code=429)
            return _FakeResp(status_code=200, json_payload={"done": True})

    monkeypatch.setattr(literature, "_get_session", lambda: Sess())
    monkeypatch.setattr(literature.time, "sleep", lambda _s: None)
    out = literature._get_json("https://x", retries=3)
    assert calls["n"] == 2
    assert out == {"done": True}


def test_get_json_retries_on_request_exception_then_gives_up(monkeypatch):
    """If every attempt raises RequestException, _get_json exhausts
    retries and returns None (not propagate)."""
    import requests as _rq

    class Sess:
        def get(self, url, params=None, timeout=None):
            raise _rq.ConnectionError("boom")

    monkeypatch.setattr(literature, "_get_session", lambda: Sess())
    monkeypatch.setattr(literature.time, "sleep", lambda _s: None)
    out = literature._get_json("https://x", retries=3)
    assert out is None


def test_get_json_with_delay_sleeps_before_request(monkeypatch):
    """delay>0 must trigger a time.sleep call before the request."""
    sleeps: list[float] = []

    class Sess:
        def get(self, url, params=None, timeout=None):
            return _FakeResp(status_code=200, json_payload={"x": 1})

    monkeypatch.setattr(literature, "_get_session", lambda: Sess())
    monkeypatch.setattr(
        literature.time, "sleep", lambda s: sleeps.append(s)
    )
    literature._get_json("https://x", delay=0.5)
    assert 0.5 in sleeps


def test_get_xml_happy_path(monkeypatch):
    class Sess:
        def get(self, url, params=None, timeout=None):
            return _FakeResp(
                status_code=200,
                content=b"<root><child/></root>",
            )

    monkeypatch.setattr(literature, "_get_session", lambda: Sess())
    monkeypatch.setattr(literature.time, "sleep", lambda _s: None)
    el = literature._get_xml("https://x")
    assert el is not None
    assert el.tag == "root"


def test_get_xml_returns_none_on_parse_error(monkeypatch):
    """Invalid XML in response body → ParseError → retry loop →
    eventually return None. Must not raise."""
    class Sess:
        def get(self, url, params=None, timeout=None):
            return _FakeResp(status_code=200, content=b"<<< not xml")

    monkeypatch.setattr(literature, "_get_session", lambda: Sess())
    monkeypatch.setattr(literature.time, "sleep", lambda _s: None)
    assert literature._get_xml("https://x", retries=2) is None


def test_get_xml_with_delay_sleeps_before_request(monkeypatch):
    """L136: mirrors test_get_json_with_delay_sleeps_before_request for
    the XML variant. `delay>0` must trigger a time.sleep call before
    the session GET — this is how we honour PubMed's 3 req/sec budget
    for efetch requests. A regression that drops the sleep would
    silently make the fetcher hammer the E-utilities endpoint and get
    429-rate-limited on every batch."""
    sleeps: list[float] = []

    class Sess:
        def get(self, url, params=None, timeout=None):
            return _FakeResp(status_code=200, content=b"<root/>")

    monkeypatch.setattr(literature, "_get_session", lambda: Sess())
    monkeypatch.setattr(
        literature.time, "sleep", lambda s: sleeps.append(s)
    )
    literature._get_xml("https://x", delay=0.34)
    assert 0.34 in sleeps


def test_get_xml_rate_limit_retry(monkeypatch):
    """429 path in _get_xml: retry, then return parsed XML."""
    calls = {"n": 0}

    class Sess:
        def get(self, url, params=None, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return _FakeResp(status_code=429)
            return _FakeResp(status_code=200, content=b"<r/>")

    monkeypatch.setattr(literature, "_get_session", lambda: Sess())
    monkeypatch.setattr(literature.time, "sleep", lambda _s: None)
    el = literature._get_xml("https://x", retries=3)
    assert el is not None
    assert el.tag == "r"


def test_download_file_writes_bytes(tmp_path, monkeypatch):
    """_download_file streams chunks to the dest path and returns
    True. The dest's parent dir is created automatically."""
    class Sess:
        def get(self, url, **kwargs):
            return _FakeResp(
                status_code=200,
                content=b"PDFBYTES" * 100,
                headers={"content-length": str(8 * 100)},
            )

    monkeypatch.setattr(literature, "_get_session", lambda: Sess())
    dest = tmp_path / "out" / "file.pdf"
    ok = literature._download_file("https://x/file.pdf", dest)
    assert ok is True
    assert dest.read_bytes() == b"PDFBYTES" * 100


def test_download_file_rejects_when_content_length_exceeds_max(
    tmp_path, monkeypatch
):
    """Large content-length header → skip download, return False."""
    class Sess:
        def get(self, url, **kwargs):
            return _FakeResp(
                status_code=200,
                content=b"",
                headers={"content-length": str(999_999_999)},
            )

    monkeypatch.setattr(literature, "_get_session", lambda: Sess())
    dest = tmp_path / "big.pdf"
    ok = literature._download_file(
        "https://x/big.pdf", dest, max_bytes=1024
    )
    assert ok is False
    assert not dest.exists()


def test_download_file_truncates_when_chunks_exceed_max(
    tmp_path, monkeypatch
):
    """content-length missing (0), actual chunks exceed max_bytes →
    stop writing at the limit (return True, file truncated)."""
    class BigResp(_FakeResp):
        def iter_content(self, chunk_size=8192):
            # 3 chunks of 5000 bytes → 15000 total, max_bytes=10000
            for _ in range(3):
                yield b"x" * 5000

    class Sess:
        def get(self, url, **kwargs):
            return BigResp(status_code=200, headers={"content-length": "0"})

    monkeypatch.setattr(literature, "_get_session", lambda: Sess())
    dest = tmp_path / "capped.pdf"
    ok = literature._download_file(
        "https://x/capped.pdf", dest, max_bytes=10_000
    )
    assert ok is True
    # After chunk 1: written=5000 (<=10000) → write. After chunk 2:
    # written=10000 (<=10000) → write. After chunk 3: written=15000
    # (>10000) → break before writing. So exactly 10000 bytes on disk.
    assert dest.stat().st_size == 10000


def test_download_file_returns_false_on_request_exception(
    tmp_path, monkeypatch
):
    import requests as _rq

    class Sess:
        def get(self, url, **kwargs):
            raise _rq.ConnectionError("simulated")

    monkeypatch.setattr(literature, "_get_session", lambda: Sess())
    ok = literature._download_file(
        "https://x/f.pdf", tmp_path / "f.pdf"
    )
    assert ok is False


def test_get_session_lazy_init_and_reuse(monkeypatch):
    """_get_session must create a Session on first call and return the
    same instance on subsequent calls. Exercises lines 99-104."""
    # Clear the module-level cache before the test to force re-init.
    monkeypatch.setattr(literature, "_session", None)
    s1 = literature._get_session()
    s2 = literature._get_session()
    assert s1 is s2
    # User-Agent was set.
    assert "OmiCairn" in s1.headers.get("User-Agent", "")


# ---------------------------------------------------------------------------
# main() CLI entry point
# ---------------------------------------------------------------------------

def test_literature_main_cli_happy_path(monkeypatch, tmp_path, capsys):
    """literature.main() is the `python -m omicairn.literature` entry
    point. Covers lines 780-804: argparse setup, research_md load,
    output dir mkdir, fetch() call, final print. fetch() is stubbed
    so no network I/O happens."""
    # Create a minimal RESEARCH.md the research_md loader can parse.
    rmd_path = tmp_path / "RESEARCH.md"
    rmd_path.write_text(
        "---\n"
        "research_mode: METHOD\n"
        "domain: test_domain\n"
        "topic_slug: lit-cli-test\n"
        "research_question: |\n"
        "  Does the literature CLI work?\n"
        "data:\n"
        "  location: " + str(tmp_path) + "\n"
        "  description: synthetic\n"
        "  cohorts: [a]\n"
        "  modalities: [tabular]\n"
        "success_criteria:\n"
        "  primary_metric: auc\n"
        "  baseline: null\n"
        "bounds:\n"
        "  max_iters_per_phase: 3\n"
        "  max_backtracks: 5\n"
        "budgets:\n"
        "  max_phase_wall_minutes: 5\n"
        "  allow_web: false\n"
        "  claude_model: claude-sonnet-4-6\n"
        "  max_phase_budget_usd: 1.0\n"
        "auto_approve: true\n"
        "---\n\n"
        "# Test\n"
    )
    out_dir = tmp_path / "out"

    calls: list[dict] = []

    def fake_fetch(rmd, output_dir, max_papers=50):
        calls.append(
            {
                "topic": rmd.topic_slug,
                "output_dir": output_dir,
                "max_papers": max_papers,
            }
        )
        return [Paper(pmid=str(i), title=f"P{i}") for i in range(4)]

    monkeypatch.setattr(literature, "fetch", fake_fetch)
    monkeypatch.setattr(
        "sys.argv",
        [
            "omicairn-literature",
            "--research-md",
            str(rmd_path),
            "--output-dir",
            str(out_dir),
            "--max-papers",
            "4",
        ],
    )

    rc = literature.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "Done: 4 papers fetched" in out
    assert out_dir.is_dir()
    assert len(calls) == 1
    assert calls[0]["topic"] == "lit-cli-test"
    assert calls[0]["max_papers"] == 4


def test_literature_main_cli_verbose_flag(monkeypatch, tmp_path):
    """--verbose flag raises log level to DEBUG. Covers the branch
    at line 793."""
    import logging as _logging

    rmd_path = tmp_path / "RESEARCH.md"
    rmd_path.write_text(
        "---\n"
        "research_mode: METHOD\n"
        "domain: test\n"
        "topic_slug: verbose-test\n"
        "research_question: |\n"
        "  verbose?\n"
        "data:\n"
        "  location: " + str(tmp_path) + "\n"
        "  description: x\n"
        "  cohorts: [a]\n"
        "  modalities: [tabular]\n"
        "success_criteria:\n"
        "  primary_metric: auc\n"
        "  baseline: null\n"
        "bounds:\n"
        "  max_iters_per_phase: 3\n"
        "  max_backtracks: 5\n"
        "budgets:\n"
        "  max_phase_wall_minutes: 5\n"
        "  allow_web: false\n"
        "  claude_model: claude-sonnet-4-6\n"
        "  max_phase_budget_usd: 1.0\n"
        "auto_approve: true\n"
        "---\n\n"
        "# Test\n"
    )

    monkeypatch.setattr(literature, "fetch", lambda *a, **k: [])
    monkeypatch.setattr(
        "sys.argv",
        [
            "omicairn-literature",
            "--research-md",
            str(rmd_path),
            "--output-dir",
            str(tmp_path / "out"),
            "--verbose",
        ],
    )
    # basicConfig in main() reconfigures logging — capture and restore.
    rc = literature.main()
    assert rc == 0


# ---------------------------------------------------------------------------
# LITERATURE_INDEX.md renderer
# ---------------------------------------------------------------------------


def _index_sample_paper(**overrides) -> Paper:
    base = dict(
        pmid="12345678",
        doi="10.1038/nature12113",
        title="A prognostic signature for lung adenocarcinoma",
        authors="Curtis C, Shah SP, Chin SF, Turashvili G, Rueda OM",
        year=2012,
        journal="Nature",
        volume="486",
        pages="346-352",
        is_open_access=True,
        oa_pdf_path="pdfs/10.1038_nature12113.pdf",
        paywalled=False,
    )
    base.update(overrides)
    return Paper(**base)


def test_render_literature_index_renders_sections_and_headers():
    papers = [_index_sample_paper()]
    out = render_literature_index(papers, domain="survival_prognostic")
    assert "# Literature Index" in out
    assert "survival_prognostic" in out
    assert "## Entries" in out
    assert "## Legend" in out
    # Header row of the entries table.
    assert "| # | Access | Reference | Abstract | PDF / Full-text |" in out


def test_render_literature_index_counts_access_tiers():
    papers = [
        _index_sample_paper(),  # OA+PDF
        _index_sample_paper(
            pmid="2", doi="10.9/x", oa_pdf_path=None,  # OA no PDF
        ),
        _index_sample_paper(
            pmid="3", doi="10.9/y",
            is_open_access=False, oa_pdf_path=None, paywalled=True,
        ),
    ]
    out = render_literature_index(papers, domain="general")
    assert "Papers: 3" in out
    assert "OA: 2" in out
    assert "Paywalled: 1" in out
    assert "PDFs on disk: 1" in out


def test_render_literature_index_row_per_paper():
    papers = [_index_sample_paper(), _index_sample_paper(pmid="222", doi="10.1/z")]
    out = render_literature_index(papers)
    # One row per paper, plus the header + separator.
    rows = [l for l in out.splitlines() if l.startswith("| ") and not l.startswith("| # ") and not l.startswith("|---")]
    assert len(rows) == 2
    assert "OA+PDF" in rows[0]
    # DOI appears in the reference cell.
    assert "10.1038/nature12113" in rows[0]


def test_render_literature_index_accepts_dict_rows():
    """Round-trip from papers.json (plain dicts) works without a Paper."""
    papers_dicts = [dict(
        pmid="1",
        doi="10.1/abc",
        title="T",
        authors="A B, C D",
        year=2021,
        journal="J",
        volume="1",
        pages="2",
        is_open_access=False,
        oa_pdf_path=None,
        paywalled=True,
    )]
    out = render_literature_index(papers_dicts)
    assert "PAYWALL" in out
    assert "10.1/abc" in out


def test_render_literature_index_empty_list_ok():
    out = render_literature_index([])
    assert "Papers: 0" in out
    assert "## Entries" in out


# ---------------------------------------------------------------------------
# Lab-library sharing: contribute + reuse (no network, pdftotext mocked)
# ---------------------------------------------------------------------------

class _FakeProc:
    def __init__(self, stdout=b"", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _mock_apis(monkeypatch, *, oa_pdf_url="https://example.org/a.pdf"):
    """Wire the search/enrich/download functions to canned in-memory data
    so search_and_fetch runs end to end with zero HTTP."""
    def fake_pubmed_search(query, max_results=20):
        return ["pm_001"] if query.startswith("q1") else []

    def fake_pubmed_fetch(pmids):
        return [Paper(pmid="pm_001", doi="10.7/share", title="Shared paper",
                      authors="A", year=2024, journal="J",
                      abstract="abs body", source_api="pubmed")] \
            if "pm_001" in pmids else []

    monkeypatch.setattr(literature, "pubmed_search", fake_pubmed_search)
    monkeypatch.setattr(literature, "pubmed_fetch", fake_pubmed_fetch)
    monkeypatch.setattr(literature, "semantic_scholar_search",
                        lambda q, max_results=20: [])
    monkeypatch.setattr(literature, "crossref_enrich", lambda p: p)

    def fake_unpaywall(p):
        p.is_open_access = True
        p.paywalled = False
        p.oa_pdf_url = oa_pdf_url
        return p

    monkeypatch.setattr(literature, "unpaywall_check", fake_unpaywall)
    monkeypatch.setattr(literature, "epmc_fulltext", lambda p, d: p)


def test_fetch_contributes_pdf_and_markdown_to_shared_library(
        tmp_path, monkeypatch):
    from omicairn import paper_library
    monkeypatch.delenv("OMICAIRN_PAPER_LIB", raising=False)
    _mock_apis(monkeypatch)
    dl_calls: list[str] = []

    def fake_download(url, dest, max_bytes=100 * 1024 * 1024):
        dl_calls.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"%PDF-1.4\nbody\n")
        return True

    monkeypatch.setattr(literature, "_download_file", fake_download)
    monkeypatch.setattr(paper_library.subprocess, "run",
                        lambda *a, **k: _FakeProc(stdout=b"FULLTEXT MD"))

    # Workspace-shaped layout: <ws_root>/literature ; library resolves to
    # <workspaces_root>/lab_library (a sibling of the workspace).
    workspaces = (tmp_path / "workspaces").resolve()
    out = workspaces / "ws_alpha" / "literature"
    fetcher = LiteratureFetcher(output_dir=out, max_papers=10, domain="test")
    papers = fetcher.search_and_fetch(queries=["q1 x"], research_question="rq")
    assert len(papers) == 1
    assert dl_calls == ["https://example.org/a.pdf"]  # one real download

    lib_root = workspaces / "lab_library"
    assert lib_root.is_dir()  # visible default, beside the workspace
    # Contributed to the shared library (PDF + converted markdown).
    cached = paper_library.get("10.7/share", root=lib_root)
    assert cached is not None
    assert cached.get("md_path")
    assert paper_library.get_markdown("10.7/share", root=lib_root) == "FULLTEXT MD"
    # And the run got the markdown back into literature/markdown/.
    run_md = out / "markdown" / "10.7_share.md"
    assert run_md.read_text(encoding="utf-8") == "FULLTEXT MD"


def test_fetch_reuses_cached_pdf_and_markdown_skipping_download(
        tmp_path, monkeypatch):
    from omicairn import paper_library
    monkeypatch.delenv("OMICAIRN_PAPER_LIB", raising=False)
    workspaces = (tmp_path / "workspaces").resolve()
    lib_root = workspaces / "lab_library"
    # Pre-seed the shared library with a cached PDF + markdown.
    seed_pdf = tmp_path / "seed.pdf"
    seed_pdf.write_bytes(b"%PDF-1.4\ncached\n")
    monkeypatch.setattr(paper_library.subprocess, "run",
                        lambda *a, **k: _FakeProc(stdout=b"CACHED MD"))
    paper_library.add(
        Paper(doi="10.7/share", title="Shared paper", abstract="abs"),
        root=lib_root, pdf_source=seed_pdf, workspace="prior", convert=True)

    _mock_apis(monkeypatch)
    dl_calls: list[str] = []

    def fake_download(url, dest, max_bytes=100 * 1024 * 1024):
        dl_calls.append(url)  # must NOT be called — cache hit
        dest.write_bytes(b"new")
        return True

    monkeypatch.setattr(literature, "_download_file", fake_download)

    out = workspaces / "ws_beta" / "literature"
    fetcher = LiteratureFetcher(output_dir=out, max_papers=10, domain="test")
    fetcher.search_and_fetch(queries=["q1 x"], research_question="rq")

    assert dl_calls == []  # reuse: HTTP download skipped entirely
    # Cached PDF is REFERENCED into the run, not byte-duplicated: same bytes
    # AND the same inode as the library copy (hardlink) — so a paper present
    # in central exists ONCE on disk (audit 2026-06-10 dedup contract (a)).
    run_pdf = out / "pdfs" / "10.7_share.pdf"
    lib_pdf = lib_root / "pdfs" / "10.7_share.pdf"
    assert run_pdf.read_bytes() == b"%PDF-1.4\ncached\n"
    assert run_pdf.stat().st_ino == lib_pdf.stat().st_ino  # hardlink, not copy
    assert run_pdf.stat().st_nlink >= 2
    # Cached markdown copied into the run.
    assert (out / "markdown" / "10.7_share.md").read_text() == "CACHED MD"


def _mock_apis_no_doi(monkeypatch, *, oa_pdf_url="https://arxiv.org/pdf/2310.01405"):
    """Like _mock_apis but the surveyed paper carries NO DOI and NO PMID — an
    arXiv paper. Semantic Scholar returns it (PubMed yields nothing) so the
    pipeline runs end to end with a paper whose identity is the title slug."""
    monkeypatch.setattr(literature, "pubmed_search",
                        lambda q, max_results=20: [])
    monkeypatch.setattr(literature, "pubmed_fetch", lambda pmids: [])

    def fake_s2(query, max_results=20):
        return [Paper(title="Representation Engineering Top-Down",
                      authors="Zou", year=2023, journal="arXiv",
                      abstract="abs body", source_api="semantic_scholar")] \
            if query.startswith("q1") else []

    monkeypatch.setattr(literature, "semantic_scholar_search", fake_s2)
    monkeypatch.setattr(literature, "crossref_enrich", lambda p: p)

    def fake_unpaywall(p):
        p.is_open_access = True
        p.paywalled = False
        p.oa_pdf_url = oa_pdf_url
        return p

    monkeypatch.setattr(literature, "unpaywall_check", fake_unpaywall)
    monkeypatch.setattr(literature, "epmc_fulltext", lambda p, d: p)


def test_fetch_reuses_cached_no_doi_arxiv_pdf_skipping_download(
        tmp_path, monkeypatch):
    """REGRESSION (disk-redundancy audit): an arXiv paper (NO DOI) already in
    the shared library must be HARDLINK-reused into the workspace, NOT
    re-downloaded + byte-copied. The old reuse gate `if ... and p.doi ...`
    skipped no-DOI papers entirely, so every run re-fetched them and the
    bytes duplicated. The fix keys reuse on the slug both sides agree on."""
    from omicairn import paper_library
    monkeypatch.delenv("OMICAIRN_PAPER_LIB", raising=False)
    workspaces = (tmp_path / "workspaces").resolve()
    lib_root = workspaces / "lab_library"

    # Pre-seed the shared library with the SAME no-DOI arXiv paper (matching
    # title → matching slug), with a cached PDF + markdown.
    seed_pdf = tmp_path / "seed.pdf"
    seed_pdf.write_bytes(b"%PDF-1.4\narxiv-cached\n")
    monkeypatch.setattr(paper_library.subprocess, "run",
                        lambda *a, **k: _FakeProc(stdout=b"ARXIV MD"))
    seed_paper = Paper(title="Representation Engineering Top-Down",
                       authors="Zou", year=2023, abstract="abs")
    assert seed_paper.doi is None and seed_paper.pmid is None
    paper_library.add(seed_paper, root=lib_root, pdf_source=seed_pdf,
                      workspace="prior", convert=True)
    slug = paper_library.slug_for(seed_paper)

    _mock_apis_no_doi(monkeypatch)
    dl_calls: list[str] = []

    def fake_download(url, dest, max_bytes=100 * 1024 * 1024):
        dl_calls.append(url)  # must NOT be called — cache hit on the slug
        dest.write_bytes(b"new")
        return True

    monkeypatch.setattr(literature, "_download_file", fake_download)

    out = workspaces / "ws_arxiv" / "literature"
    fetcher = LiteratureFetcher(output_dir=out, max_papers=10, domain="test")
    fetcher.search_and_fetch(queries=["q1 x"], research_question="rq")

    assert dl_calls == []  # no-DOI paper reused, HTTP download skipped
    run_pdf = out / "pdfs" / f"{slug}.pdf"
    lib_pdf = lib_root / "pdfs" / f"{slug}.pdf"
    assert run_pdf.read_bytes() == b"%PDF-1.4\narxiv-cached\n"
    assert run_pdf.stat().st_ino == lib_pdf.stat().st_ino  # hardlink, not copy
    assert run_pdf.stat().st_nlink >= 2
    # Cached markdown reused into the run (resolved by slug, no DOI).
    assert (out / "markdown" / f"{slug}.md").read_text() == "ARXIV MD"


def test_fetch_use_library_false_does_not_touch_shared_library(
        tmp_path, monkeypatch):
    from omicairn import paper_library
    monkeypatch.delenv("OMICAIRN_PAPER_LIB", raising=False)
    _mock_apis(monkeypatch)
    monkeypatch.setattr(
        literature, "_download_file",
        lambda url, dest, max_bytes=100 * 1024 * 1024:
        (dest.write_bytes(b"%PDF-1.4\n"), True)[1])

    workspaces = (tmp_path / "workspaces").resolve()
    out = workspaces / "ws_gamma" / "literature"
    fetcher = LiteratureFetcher(output_dir=out, max_papers=10, domain="test",
                               use_library=False)
    fetcher.search_and_fetch(queries=["q1 x"], research_question="rq")
    # No library created/written when use_library is off.
    assert not (workspaces / "lab_library").exists()


def test_export_sources_contributes_and_pulls_markdown(tmp_path, monkeypatch):
    from omicairn import paper_library
    monkeypatch.delenv("OMICAIRN_PAPER_LIB", raising=False)
    monkeypatch.setattr(
        literature, "_download_file",
        lambda url, dest, max_bytes=100 * 1024 * 1024:
        (dest.write_bytes(b"%PDF-1.4\nsrc\n"), True)[1])
    monkeypatch.setattr(paper_library.subprocess, "run",
                        lambda *a, **k: _FakeProc(stdout=b"IDEATE MD"))

    workspaces = (tmp_path / "workspaces").resolve()
    out = workspaces / "ws_delta" / "literature"
    sources = [{"title": "Survey paper", "summary": "sum",
                "source_type": "paper", "url": "https://example.org/s.pdf",
                "author": "Z", "year": 2025}]
    counts = literature.export_sources_to_literature(
        out, sources, domain="test", download=True)
    assert counts["downloaded"] == 1
    assert counts["contributed"] == 1
    assert counts["markdown_pulled"] == 1

    lib_root = workspaces / "lab_library"
    assert lib_root.is_dir()
    # The downloaded PDF was contributed (slug derived from the title since
    # the IDEATE source carries no DOI/PMID).
    s = paper_library.stats(root=lib_root)
    assert s["pdfs"] == 1 and s["markdowns"] == 1
    # Run got the converted markdown pulled into literature/markdown/.
    mds = list((out / "markdown").glob("*.md"))
    assert len(mds) == 1
    assert mds[0].read_text() == "IDEATE MD"


# ---------------------------------------------------------------------------
# Literature-dedup audit (2026-06-10): the central lab_library is the
# canonical store runs REFERENCE; no per-workspace lab_library copy may form,
# even when the fetcher runs from a stage-nested literature dir.
# ---------------------------------------------------------------------------

def _seed_workspace(workspaces: pathlib.Path, name: str) -> pathlib.Path:
    """Create a minimal run workspace (STATE.md marks the root, exactly as
    workspace.init writes it) and return its root."""
    ws = workspaces / name
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "STATE.md").write_text("---\ntopic_slug: t\n---\n", encoding="utf-8")
    return ws


def test_fetch_from_stage_nested_dir_uses_central_not_per_ws_copy(
        tmp_path, monkeypatch):
    """REGRESSION (audit 2026-06-10): the literature-fetcher subagent runs
    `fetch(rmd, output_dir='literature/')` with cwd in the STAGE dir, so
    output_dir is `<ws>/<NNN_stage>/literature`. The old
    `output_dir.parent.parent` guess resolved the library to `<ws>` and
    stranded a per-workspace `<ws>/lab_library` full copy (46 real files on
    d3). It must resolve to the CENTRAL `<workspaces>/lab_library` instead —
    contract (b): no per-workspace lab_library copy."""
    from omicairn import paper_library
    monkeypatch.delenv("OMICAIRN_PAPER_LIB", raising=False)
    _mock_apis(monkeypatch)
    monkeypatch.setattr(
        literature, "_download_file",
        lambda url, dest, max_bytes=100 * 1024 * 1024:
        (dest.parent.mkdir(parents=True, exist_ok=True),
         dest.write_bytes(b"%PDF-1.4\nbody\n"), True)[2])
    monkeypatch.setattr(paper_library.subprocess, "run",
                        lambda *a, **k: _FakeProc(stdout=b"MD"))

    workspaces = (tmp_path / "workspaces").resolve()
    ws = _seed_workspace(workspaces, "run_x")
    # Stage-nested literature dir, the layout that triggered the bug.
    out = ws / "001_plan" / "literature"
    fetcher = LiteratureFetcher(output_dir=out, max_papers=10, domain="test")
    fetcher.search_and_fetch(queries=["q1 x"], research_question="rq")

    # Contributed to the CENTRAL library beside the workspaces, not a copy
    # inside the workspace.
    central = workspaces / "lab_library"
    assert central.is_dir()
    assert paper_library.get("10.7/share", root=central) is not None
    # The bug artifact MUST NOT exist.
    assert not (ws / "lab_library").exists(), "per-workspace lab_library copy"
    assert not (ws / "001_plan" / "lab_library").exists()


def test_fetch_new_paper_contributed_to_central_and_no_per_ws_copy(
        tmp_path, monkeypatch):
    """A brand-new paper a run downloads is ADDED to the central library (so
    a FUTURE run reuses it by hardlink instead of re-downloading), and no
    per-workspace lab_library copy forms. Contract (c).

    The contributing run legitimately holds its own freshly-downloaded bytes;
    the dedup win is on the *next* run's cache hit — exercised by a second
    fetch here, which must land a hardlink to central (no second download)."""
    from omicairn import paper_library
    monkeypatch.delenv("OMICAIRN_PAPER_LIB", raising=False)
    _mock_apis(monkeypatch)
    dl_calls: list[str] = []

    def fake_download(url, dest, max_bytes=100 * 1024 * 1024):
        dl_calls.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"%PDF-1.4\nfresh\n")
        return True

    monkeypatch.setattr(literature, "_download_file", fake_download)
    monkeypatch.setattr(paper_library.subprocess, "run",
                        lambda *a, **k: _FakeProc(stdout=b"MD"))

    workspaces = (tmp_path / "workspaces").resolve()
    central = workspaces / "lab_library"

    # Run 1 — brand-new paper: one real download, contributed to central.
    ws1 = _seed_workspace(workspaces, "run_y1")
    out1 = ws1 / "literature"
    LiteratureFetcher(output_dir=out1, max_papers=10,
                      domain="test").search_and_fetch(
        queries=["q1 x"], research_question="rq")
    assert dl_calls == ["https://example.org/a.pdf"]  # one download
    cached = paper_library.get("10.7/share", root=central)
    assert cached is not None and cached.get("oa_pdf_path")
    assert not (ws1 / "lab_library").exists()

    # Run 2 — same paper now in central: NO new download, referenced by
    # hardlink to the central copy (bytes exist once for the second run).
    ws2 = _seed_workspace(workspaces, "run_y2")
    out2 = ws2 / "literature"
    LiteratureFetcher(output_dir=out2, max_papers=10,
                      domain="test").search_and_fetch(
        queries=["q1 x"], research_question="rq")
    assert dl_calls == ["https://example.org/a.pdf"]  # still just the one
    run2_pdf = out2 / "pdfs" / "10.7_share.pdf"
    lib_pdf = central / "pdfs" / "10.7_share.pdf"
    assert run2_pdf.stat().st_ino == lib_pdf.stat().st_ino  # hardlink reuse
    assert not (ws2 / "lab_library").exists()


def test_pdf_url_for_normalizes_arxiv_abs_to_pdf():
    assert (literature._pdf_url_for("https://arxiv.org/abs/2401.00001")
            == "https://arxiv.org/pdf/2401.00001")
    assert (literature._pdf_url_for("http://www.arxiv.org/abs/2310.12345v2")
            == "https://arxiv.org/pdf/2310.12345v2")


def test_pdf_url_for_passes_through_non_arxiv_and_empty():
    assert literature._pdf_url_for("") == ""
    assert (literature._pdf_url_for("https://www.anthropic.com/research/x")
            == "https://www.anthropic.com/research/x")
    # An already-direct arXiv PDF url is left untouched.
    assert (literature._pdf_url_for("https://arxiv.org/pdf/2401.00001")
            == "https://arxiv.org/pdf/2401.00001")


def test_export_metadata_pass_then_download_pass_tops_up_pdf(
        tmp_path, monkeypatch):
    """Regression: a metadata-only pass (download=False) followed by a
    download pass (download=True) MUST fetch the PDF the first pass
    skipped. The IDEATE ingest hook runs download=False and cmd_ideate's
    post-DONE sync runs download=True against the SAME papers.json; the
    dedup `continue` used to swallow every entry in the second pass, so a
    161-source survey landed 0 PDFs (2026-06-09 IDEATE→literature bug)."""
    from omicairn import paper_library
    monkeypatch.delenv("OMICAIRN_PAPER_LIB", raising=False)
    monkeypatch.setattr(
        literature, "_download_file",
        lambda url, dest, max_bytes=100 * 1024 * 1024:
        (dest.write_bytes(b"%PDF-1.4\nsrc\n"), True)[1])
    monkeypatch.setattr(paper_library.subprocess, "run",
                        lambda *a, **k: _FakeProc(stdout=b"MD"))

    workspaces = (tmp_path / "workspaces").resolve()
    out = workspaces / "ws_eps" / "literature"
    sources = [{"title": "Two pass paper", "summary": "sum",
                "source_type": "paper", "url": "https://example.org/s.pdf",
                "author": "Z", "year": 2025}]

    # Pass 1: metadata only — entry written, NO PDF.
    c1 = literature.export_sources_to_literature(
        out, sources, domain="test", download=False)
    assert c1["added"] == 1
    assert c1["downloaded"] == 0
    papers = json.loads((out / "papers.json").read_text())
    assert papers[0].get("oa_pdf_path") in (None, "")

    # Pass 2: download — must top up the missing PDF on the SAME entry
    # (not add a duplicate).
    c2 = literature.export_sources_to_literature(
        out, sources, domain="test", download=True)
    assert c2["downloaded"] == 1, "second pass must fetch the skipped PDF"
    papers = json.loads((out / "papers.json").read_text())
    assert len(papers) == 1, "must update in place, not duplicate"
    pdf_rel = papers[0]["oa_pdf_path"]
    assert pdf_rel and (out / pdf_rel).exists()
    assert papers[0].get("paywalled") is False

    # Pass 3: idempotent — PDF already on disk, nothing re-downloaded.
    c3 = literature.export_sources_to_literature(
        out, sources, domain="test", download=True)
    assert c3["downloaded"] == 0, "PDF already present — no re-download"


def test_export_download_pass_marks_paywalled_when_fetch_fails(
        tmp_path, monkeypatch):
    """A download top-up that fails (paywall/HTML/403) marks the existing
    entry paywalled rather than silently leaving it as if untried."""
    monkeypatch.delenv("OMICAIRN_PAPER_LIB", raising=False)
    monkeypatch.setattr(
        literature, "_download_file",
        lambda url, dest, max_bytes=100 * 1024 * 1024: False)

    out = (tmp_path / "workspaces" / "ws_pay" / "literature").resolve()
    sources = [{"title": "Paywalled paper", "summary": "s",
                "source_type": "paper", "url": "https://paywall.example/p",
                "author": "Q", "year": 2024}]
    literature.export_sources_to_literature(
        out, sources, domain="test", download=False, use_library=False)
    c2 = literature.export_sources_to_literature(
        out, sources, domain="test", download=True, use_library=False)
    assert c2["downloaded"] == 0
    papers = json.loads((out / "papers.json").read_text())
    assert papers[0].get("paywalled") is True


def test_format_authors_short_accepts_list():
    """`authors` is LLM-authored (papers.json) and can arrive as a JSON
    list, not a delimited string. A list-valued `authors` previously crashed
    reference formatting
    (`re.split(pattern, list)` -> TypeError). Both shapes must format."""
    from omicairn.literature import _format_authors_short, _format_reference_entry
    # string shape (legacy) unchanged
    assert _format_authors_short("Arditi A et al.") == "Arditi A et al."
    assert _format_authors_short("Yu Y, Wang T, Samworth RJ") == \
        "Yu Y, Wang T, Samworth RJ"
    # list shape no longer raises and truncates at max_names
    assert _format_authors_short(["Qwen Team"]) == "Qwen Team"
    assert _format_authors_short(["She X", "Wang Y", "Li Z"]) == \
        "She X, Wang Y, Li Z"
    assert _format_authors_short(["A", "B", "C", "D"]) == "A, B, C, et al."
    # empty / None -> ""
    assert _format_authors_short(None) == ""
    assert _format_authors_short([]) == ""
    # a full reference entry with list authors renders without raising
    entry = _format_reference_entry(
        {"title": "T", "authors": ["She X", "Wang Y"], "year": 2025,
         "journal": "Findings of EMNLP"})
    assert "She X" in entry and "Wang Y" in entry
