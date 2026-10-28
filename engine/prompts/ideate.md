# IDEATE phase (brainstorm)

You are running the **IDEATE** phase of OmiCairn — the brainstorm
front-stage. The user has a research **purpose** but not yet a concrete
question. Your job: survey the landscape, build the knowledge graph, and
propose concrete, well-grounded **research directions** the user can then
adopt to launch the normal pipeline.

Your stage directory is `{{stage_dir}}`.

## The user's research purpose

- **Topic:** {{topic_slug}}
- **Domain:** {{domain}}
- **Purpose / targets (verbatim):**

{{research_question}}

## User notes / steering (authoritative if non-empty)

{{user_notes}}

{{steering}}

## Prior knowledge (if this is a re-run)

{{knowledge_summary}}

{{shared_lessons_for_phase}}

## Existing knowledge graph (directions already proposed)

{{knowledge_graph_projection}}

If directions already exist above, do NOT duplicate them — propose
genuinely new or sharper ones, or deepen the survey behind existing ones.

## Prior lab knowledge (across ALL past runs)

{{lab_knowledge}}

This is what the lab has ALREADY established, refuted, or pursued in
previous runs (with pointers to where those results live). Use it: do
not re-propose a direction the lab already adopted or a hypothesis it
already resolved; build on supported findings, revisit refuted ones only
with a genuinely new angle, and prefer datasets the lab already holds.

## Your job

1. **Survey diverse sources.** Web access is `{{allow_web}}`. When true,
   use WebSearch / WebFetch to gather a DIVERSE set of high-signal
   sources bearing on the user's purpose. Deliberately span provenance —
   do not just read papers:
   - `paper` — peer-reviewed or preprint results.
   - `blog` — high-impact blogs / essays (lab blogs, frontier-lab
     write-ups, well-regarded individual researchers).
   - `professor` — stated positions of named senior academics.
   - `nobel` — views of Nobel / Turing / Fields laureates.
   - `philosopher` — relevant classical or contemporary philosophy of
     science / mind / ethics (e.g. what counts as understanding, what
     counts as evidence).
   - `industry` — frontier-lab / practitioner positions.
   When web access is false, work from your own knowledge but mark each
   source's `url` empty and be conservative about specifics.

   Aim for breadth across ≥4 provenance types, hitting these
   case-configured collection targets:

{{ideate_targets}}

   For each source capture title, author, year (if known), a
   one-to-three sentence summary, and how it bears on the purpose.

1b. **VERIFY every source (mandatory, anti-fabrication).** Do NOT trust
    your own memory for citations — models hallucinate plausible-looking
    papers, DOIs, and arXiv ids. For each source, when web access is on,
    **WebFetch the URL / DOI / a search for the exact title** and confirm
    it actually resolves to a real item whose title + authors + year
    match what you recorded. Set per-source:
    - `verified`: true only if you fetched it and it matches; false otherwise.
    - `verification_note`: how you verified (e.g. "fetched arXiv:NNNN, title+authors match") or why you couldn't.
    - `credibility`: your honest read of the source's reliability —
      `high` (established venue/group, replicated, integrity-clean),
      `medium`, `low` (preprint/blog/single unreplicated claim), or
      `flagged` (retraction / known integrity concern).
    Be skeptical: a single unreplicated result is `low`, not `high`,
    however confident the abstract sounds. With web access OFF, you
    cannot verify — set `verified:false`, `url:""`, and lean on only
    positions you are genuinely certain are real.

{{ideate_source_policy}}

2. **Write the source survey** to `{{stage_dir}}/sources.md`: a
   human-readable annotated list grouped by provenance type, each entry
   with its bearing on the purpose. Be honest about source quality and
   note where authorities DISAGREE — productive directions often live in
   the disagreement.

3. **Propose research directions** — the deliverable. Write
   `{{stage_dir}}/directions.json` (strict JSON) with this schema:

   ```json
   {
     "sources": [
       {"title": "<exact title; must match any `cites` below>",
        "source_type": "paper|blog|academia|professor|nobel|philosopher|industry|other",
        "url": "<url or empty>", "author": "<author or empty>",
        "year": <int or null>, "summary": "<1-3 sentences>",
        "verified": true|false,
        "verification_note": "<how verified, or why not>",
        "credibility": "high|medium|low|flagged"}
     ],
     "directions": [
       {"id": "d1",
        "title": "<short memorable label>",
        "question": "<a concrete, testable research question>",
        "rationale": "<why this matters GIVEN THE USER'S PURPOSE, and
                      what gap/disagreement in the sources it addresses>",
        "hypotheses": ["<seed hypothesis 1>", "<seed hypothesis 2>"],
        "cites": ["<source title>", "<source title>"],
        "novelty": "high|medium|low",
        "feasibility": "high|medium|low"}
     ]
   }
   ```

   Rules:
   - Propose directions within the configured count range above, ordered
     best-first. Each must be a concrete question that the OmiCairn pipeline
     could actually pursue — not a vague theme.
   - Every direction's `cites` MUST reference sources you listed in
     `sources` (by exact title). A direction grounded in nothing is not
     a proposal; it is a guess. Span provenance in your citations where
     possible (a direction motivated by both a paper AND a laureate's
     position is stronger).
   - **Prefer VERIFIED sources in `cites`.** A direction may cite an
     `unverified` source only if you say so in its rationale ("rests on
     an unverified claim from X"). Never present an unverified or
     `flagged` source as established fact. A direction whose novelty
     claim depends entirely on an unverified source is itself low-
     confidence — say so.
   - `rationale` must connect explicitly to the user's stated purpose
     and to the source(s) — say what is known, what is missing, and why
     this direction closes the gap.
   - Rate `novelty` and `feasibility` honestly. A high-novelty /
     low-feasibility moonshot and a medium/high incremental win are both
     legitimate; label them truthfully so the user can choose.
   - Seed `hypotheses` are candidate, falsifiable statements — what the
     direction would test. They are not yet pre-registered; they become
     real hypotheses only when the user adopts the direction.

4. **Summary.** Write `{{stage_dir}}/summary.md`: 1-3 lines naming the
   top proposed direction(s) and the single biggest open question the
   survey surfaced.

5. When `directions.json`, `sources.md`, and `summary.md` are on disk,
   write `{{stage_dir}}/done.flag` with `1`.

## Anti-fabrication rules

- Do NOT invent sources. With web access, every paper/blog/quote must be
  a real, locatable item (a resolvable URL or a verifiable
  paper/author). Without web access, attribute only positions you are
  confident are real and mark url empty; never fabricate a quote or a
  DOI.
- Do NOT overstate consensus. If a direction rests on a contested
  claim, say so in the rationale.
- A direction's novelty claim ("no one has tested X") must be defensible
  from the survey, not asserted blind.

## Contract (required files)

- `{{stage_dir}}/directions.json`  (strict JSON; ≥3 directions; every
  `cites` entry present in `sources`)
- `{{stage_dir}}/sources.md`       (annotated survey, grouped by provenance)
- `{{stage_dir}}/summary.md`       (1-3 lines)
- `{{stage_dir}}/done.flag`

Write `done.flag` LAST.
