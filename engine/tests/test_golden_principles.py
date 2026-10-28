"""Mechanical enforcement of the golden principles from the project's golden principles.

Three guards:

1. **Thin orchestrator** — total ``omicairn/*.py`` LOC stays under a ceiling.
2. **Domain-agnostic code** — no hardcoded biomedical/domain terms in
   ``omicairn/*.py`` string literals or identifiers (comments are exempt).
3. **No unused imports** — every ``import X`` in ``omicairn/*.py`` must be
   referenced in the same file.  Catches dead imports that accumulate
   after refactors.
"""
from __future__ import annotations

import ast
import pathlib
import re
import tokenize

import pytest

OMICAIRN_DIR = pathlib.Path(__file__).resolve().parent.parent / "omicairn"


# ---------------------------------------------------------------------------
# GP-1: Thin orchestrator — LOC ceiling
# ---------------------------------------------------------------------------

# The design principles originally stated ~2500 LOC. Legitimate growth
# (cross_check gates, literature, domain packs, steering, the knowledge
# graph, Lean verification, the dynamic time/iteration guards) pushes the
# real total higher. The ceiling is set high enough to tolerate reasonable
# growth but low enough to force a conversation before the orchestrator
# bloats further; raise it only with a short justification here.
# Reset to 30600 when the manuscript pipeline (draft/audit/typeset/venue
# stages) was removed and the run ended at the analysis REVIEW: measured
# ~30200 LOC, ceiling set with ~400 LOC headroom.
_LOC_CEILING = 30600


def test_omicairn_loc_ceiling():
    """Total ``omicairn/*.py`` LOC must stay under the ceiling."""
    total = 0
    for py in sorted(OMICAIRN_DIR.glob("*.py")):
        total += sum(1 for _ in py.open())
    assert total <= _LOC_CEILING, (
        f"omicairn/*.py total LOC = {total}, exceeds ceiling {_LOC_CEILING}.  "
        f"Either refactor to reduce LOC or raise the ceiling after discussion."
    )


# ---------------------------------------------------------------------------
# GP-2: Domain-agnostic code — no hardcoded biomedical terms
# ---------------------------------------------------------------------------

# Terms that should never appear in omicairn/*.py runtime code (string literals,
# identifiers).  Comments are exempt because they occasionally contain usage
# examples.  The pattern matches whole-word case-insensitively.
#
# Organised by category:
#   - Specific biological processes
#   - Cancer subtypes / tissue types
#   - Gene / protein names (common offenders)
#   - Dataset identifiers
_BANNED_DOMAIN_TERMS: tuple[str, ...] = (
    # Biological processes (too specific for infrastructure code)
    "pyroptosis",
    "ferroptosis",
    "necroptosis",
    "autophagy",
    # Cancer subtypes / tissue types
    "LUAD",
    "BRCA",       # the cancer, not the gene (gene caught by BRCA1/BRCA2)
    "HCC",
    "NSCLC",
    "SCLC",
    "glioblastoma",
    "melanoma",
    "carcinoma",
    "hepatocellular",
    "adenocarcinoma",
    "mesothelioma",
    # Gene / protein names
    "TP53",
    "KRAS",
    "BRCA1",
    "BRCA2",
    "EGFR",
    "BRAF",
    "MYC",
    # Dataset identifiers (specific cohort/data collections only).
    # NOTE: public-data repository names (GEO, SRA, ArrayExpress, PRIDE,
    # MassIVE, Synapse, dbGaP, CELLxGENE, Zenodo) are infrastructure
    # — analogous to "GitHub" or "S3" — and are allowed in omicairn/ when
    # used as canonical URL hosts. See asset_probe._BARE_ACCESSION_PATTERNS
    # for the policy explainer.
    "TCGA",
    "METABRIC",
    "CCLE",
)

# Pre-compile a single pattern for speed.
_BANNED_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(t) for t in _BANNED_DOMAIN_TERMS) + r")\b",
    re.IGNORECASE,
)


def _extract_non_comment_strings(filepath: pathlib.Path) -> list[tuple[int, str]]:
    """Return (line_number, token_string) for every non-comment token in a
    Python file.  Comments (tokenize.COMMENT) are excluded so that example
    text in code comments doesn't trigger false positives.
    """
    tokens: list[tuple[int, str]] = []
    try:
        with filepath.open("rb") as f:
            for tok in tokenize.tokenize(f.readline):
                if tok.type == tokenize.COMMENT:
                    continue
                tokens.append((tok.start[0], tok.string))
    except tokenize.TokenError:
        pass  # Malformed file — skip; other tests will catch syntax errors.
    return tokens


def test_no_hardcoded_domain_terms():
    """No ``omicairn/*.py`` string literal or identifier contains a banned
    biomedical term.  Comments are exempt.
    """
    violations: list[str] = []
    for py in sorted(OMICAIRN_DIR.glob("*.py")):
        for lineno, text in _extract_non_comment_strings(py):
            if _BANNED_PATTERN.search(text):
                violations.append(f"  {py.name}:{lineno}  {text.strip()[:120]}")

    if violations:
        detail = "\n".join(violations[:20])
        pytest.fail(
            f"Domain-specific terms found in omicairn/*.py "
            f"(golden principle: domain-agnostic code):\n{detail}"
        )


# ---------------------------------------------------------------------------
# GP-3: No unused imports
# ---------------------------------------------------------------------------
#
# After every refactor we accumulate dead imports (os, sys, RunStatus, …).
# This lint catches them mechanically using the stdlib `ast` module.

def _unused_imports(filepath: pathlib.Path) -> list[str]:
    """Return list of 'module:line  name' for each unused import."""
    try:
        tree = ast.parse(filepath.read_text())
    except SyntaxError:
        return []

    # Collect all imported names and their line numbers.
    # Skip `from __future__` entirely — those are compiler directives,
    # not runtime names.
    imported: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                imported[name] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                name = alias.asname or alias.name
                imported[name] = node.lineno

    if not imported:
        return []

    # Collect all Name references in non-import nodes.
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Name):
            used.add(node.id)
        # Attribute access like `os.path` — the `os` part is an ast.Name,
        # already caught above.  Also catch string annotations:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Forward references in type annotations (e.g., "Workspace")
            for name in imported:
                if name in node.value:
                    used.add(name)

    unused = []
    for name, lineno in sorted(imported.items(), key=lambda x: x[1]):
        if name not in used:
            unused.append(f"{filepath.name}:{lineno}  {name}")
    return unused


def test_no_unused_imports():
    """Every import in ``omicairn/*.py`` must be referenced in the same file."""
    violations: list[str] = []
    for py in sorted(OMICAIRN_DIR.glob("*.py")):
        violations.extend(_unused_imports(py))

    if violations:
        detail = "\n".join(violations[:20])
        pytest.fail(
            f"Unused imports in omicairn/*.py "
            f"(golden principle: no dead code):\n{detail}"
        )
