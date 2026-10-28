# OmiCairn architecture — the lab literature library

> **⚠ Living document.** This describes how literature sharing works
> *today*. When you change the library layout, the resolve_root
> precedence, the PDF→markdown layer, or the contribute/reuse wiring,
> **update this file in the same change** and refresh the "Last reviewed"
> date below. See the maintenance note in `the design principles`.
> Last reviewed: 2026-06-07.

The **lab literature library** is the literature analogue of the lab
knowledge base (`lab-knowledge-architecture.md`). Where the lab KB
compounds *findings* across runs, the lab library compounds *papers*:
metadata, PDFs, and converted markdown full-text. A paper is downloaded
once and converted once for the whole lab; every subsequent run reuses
it instead of re-fetching.

Two stores, do not conflate:

| | Per-run bibliography | Lab literature library |
|---|---|---|
| **File** | `<ws>/literature/papers.json` | `<workspaces_root>/lab_library/papers.json` |
| **Scope** | one workspace | all runs in the tree |
| **Holds** | papers this run fetched and read | every unique paper any run ever fetched |
| **Authoritative for** | this run's bibliography (what PLAN's literature review used) | de-dup cache + shared full-text |

The per-workspace `literature/` stays authoritative for what a run
actually read; the lab library is a cache that runs CONTRIBUTE to and REUSE
from.

---

## 1. Layout

```
  <workspaces_root>/lab_library/        ◄── the resolved default (visible)
    ├── papers.json          manifest: one entry per unique DOI/PMID (+ md_path)
    ├── pdfs/<slug>.pdf       content-addressed by DOI / PMID / title slug
    ├── abstracts/<slug>.txt
    ├── markdown/<slug>.md    converted PDF full-text (pdftotext -layout)
    ├── provenance.jsonl      append-only {workspace, key, source, added_at}
    └── .lock                 atomic-create lock for the read-modify-write cycle
```

`<slug>` is `slug_for(paper)`: the DOI (slashes→underscores), else
`pmid_<id>`, else a truncated title slug. The same paper always maps to
the same slug, so a repeat fetch from a different API still hits the
cache.

---

## 2. Where is it? `resolve_root()` — configurable (mirrors lab_knowledge)

```
  ╔═══════════════ resolve_root(explicit, workspaces_root) ═══════════════╗
  ║   1. explicit  ──  `omicairn paperlib … --root /path`   (highest)         ║
  ║   2. $OMICAIRN_PAPER_LIB                                                 ║
  ║   3. <workspaces_root>/lab_library   ◄── default (visible, no dot)     ║
  ║   4. <cwd>/workspaces/lab_library    (CLI fallback, no ws context)     ║
  ║   • created on demand (ensure_library)  • old ~/.cache path retired    ║
  ╚═══════════════════════════════════════════════════════════════════════╝
```

`LiteratureFetcher` and the IDEATE bridge pass
`workspaces_root = <ws>/literature → .parent.parent`, so the library is
a sibling of the run workspaces and resolution is cwd-independent during
a run. `default_library_root()` is a back-compat parameterless wrapper
(env or cwd default).

---

## 3. Contribute / reuse flow (write + read paths)

```
  ╔══════════════════════════ CONTRIBUTE (run downloads a new paper) ═════════╗
  ║  LiteratureFetcher Phase 5 downloads OA PDF → run literature/pdfs/        ║
  ║  Phase 12: paper_library.add(p, pdf_source=…, convert=True, root=R)       ║
  ║     • copies the PDF into R/pdfs/<slug>.pdf (if new)                      ║
  ║     • converts it ONCE via pdftotext → R/markdown/<slug>.md              ║
  ║     • records md_path in the manifest, appends provenance                 ║
  ║  …then copies that markdown back into the run's literature/markdown/      ║
  ║                                                                           ║
  ║  IDEATE bridge (export_sources_to_literature, download=True) does the     ║
  ║  same for surveyed source URLs it fetches.                                ║
  ╚═══════════════════════════════════════════════════════════════════════════╝

  ╔══════════════════════════ REUSE (run needs a cached paper) ══════════════╗
  ║  LiteratureFetcher Phase 5: before HTTP, paper_library.get(doi, root=R)   ║
  ║     • cache HIT → copy R/pdfs/<slug>.pdf into the run, SKIP the download  ║
  ║     • also pull R/markdown/<slug>.md → run literature/markdown/<slug>.md  ║
  ║  IDEATE bridge: pulls shared markdown for every cited paper it can key.   ║
  ╚═══════════════════════════════════════════════════════════════════════════╝
```

All sharing is **best-effort**: a library problem (permissions, full
disk, missing `pdftotext`) is logged and the run continues. Conversion
degrades gracefully — `convert_pdf_to_markdown` returns `None` (no
markdown stored) when the binary is absent.

---

## 4. Code map

| Concept | Code |
|---|---|
| Location resolution | `paper_library.resolve_root` (explicit / `$OMICAIRN_PAPER_LIB` / `<workspaces_root>/lab_library` / cwd) |
| Manifest + PDF/abstract store | `paper_library.add` / `get` / `search` / `stats` / `ensure_library` |
| PDF→markdown layer | `paper_library.convert_pdf_to_markdown`, `add(convert=...)`, `get_markdown` |
| Contribute + reuse wiring | `literature.LiteratureFetcher` (Phase 5 reuse, Phase 12 contribute, `_copy_cached_markdown`) |
| IDEATE→literature bridge | `literature.export_sources_to_literature` (contribute + pull markdown) |
| CLI | `omicairn paperlib {list,search,get,add,stats,convert} [--root] [--all]` (`cli.cmd_paperlib`) |
| Constants | `constants.py` (`ENV_PAPER_LIB`, `PAPER_LIB_DIRNAME`, `PAPER_LIB_MARKDOWN_DIR`, …) |

---

## 5. Inspecting the library

```
omicairn paperlib stats                    # counts (papers/pdfs/abstracts/markdowns) + root
omicairn paperlib list --limit 50          # newest-first listing
omicairn paperlib search "APOE4"           # title/abstract/author search
omicairn paperlib get 10.1038/nature12113  # one manifest entry as JSON
omicairn paperlib convert --all            # convert library PDFs lacking markdown
omicairn paperlib convert 10.1038/x        # convert a single paper's PDF
# override location: --root /path   or   OMICAIRN_PAPER_LIB=/path
```

---

## 6. Future transport options (deliberately NOT built)

The library is intentionally a **flat-file store** (JSON manifest +
per-file PDFs/markdown), matching the rest of the codebase's flat-file
discipline (the per-run graph, the lab KB, provenance logs). A database
or an **MCP server** could front this *same* store later — the public
API (`resolve_root`, `add`, `get`, `get_markdown`, `stats`) is a clean
seam to put a different backend behind. That is **not** built today on
purpose: flat files are inspectable with `ls`/`cat`, need no daemon, and
scale fine at O(10k) papers. Add a backend only when a concrete need
(concurrent multi-host writers, full-text indexing) actually appears.
