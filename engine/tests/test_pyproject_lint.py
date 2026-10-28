"""pyproject.toml drift lint.

pyproject.toml is the packaging contract for omicairn: it tells
pip how to install the package, what the runtime dependencies are, and
— most importantly for drift — what the `omicairn` shell entry point
actually resolves to in Python code.

Two drift surfaces that are otherwise invisible until a real install:

1. **Entry-point rename.** pyproject declares `omicairn = "omicairn.cli:main"`.
   If someone renames `omicairn/cli.py` to `omicairn/cli_main.py` or renames
   `main()` to `cli_main()` without updating pyproject, the Python
   source still runs fine under `python -m omicairn`, every existing test
   still passes (they import `main` directly), and the rename survives
   until a fresh `pip install .` produces a `omicairn` console script that
   crashes on invocation with ModuleNotFoundError or AttributeError.

2. **Required-metadata bitrot.** An edit that accidentally removes
   `requires-python`, `dependencies`, or `name` would break packaging
   but not the import-time tests. Catching this at edit time prevents
   a broken wheel from shipping.

This lint parses pyproject.toml directly and resolves the declared
entry points back into the running Python tree.
"""
from __future__ import annotations

import importlib
import pathlib
import tomllib

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT_PATH.read_text())


# ---------------------------------------------------------------------------
# 1. Metadata shape
# ---------------------------------------------------------------------------

def test_pyproject_exists_and_parses():
    """pyproject.toml must exist at the repo root and parse as valid
    TOML. A corrupt file is an immediate install-time failure."""
    assert PYPROJECT_PATH.is_file(), f"{PYPROJECT_PATH} is missing"
    data = _pyproject()
    assert "project" in data, "pyproject.toml has no [project] table"


@pytest.mark.parametrize(
    "field",
    ["name", "version", "description", "requires-python", "dependencies", "scripts"],
)
def test_pyproject_declares_required_project_fields(field):
    """Every field that downstream tooling (pip, setuptools, our own
    version introspection) reads must be present. Removing any of
    these breaks packaging silently — tests still pass because they
    bypass pip entirely — but `pip install .` produces a broken wheel."""
    data = _pyproject()
    project = data.get("project", {})
    assert field in project, (
        f"pyproject.toml [project] is missing required field {field!r}. "
        f"Either restore it or, if the field is intentionally removed, "
        f"update this test."
    )


def test_pyproject_requires_python_is_satisfiable():
    """requires-python must be a >= constraint (our CLAUDE.md pins
    3.11). A typo like `>=3.x` or `>=3.12` (tightening without
    justification) would ship silently."""
    data = _pyproject()
    req = data["project"]["requires-python"]
    assert req.startswith(">=3."), (
        f"requires-python={req!r} should be a >=3.x constraint. "
        f"The conda env is py3.11 per CLAUDE.md."
    )


# ---------------------------------------------------------------------------
# 2. Entry points must resolve to callable Python symbols
# ---------------------------------------------------------------------------

def test_every_script_entry_point_resolves_to_callable():
    """Every `[project.scripts]` entry must point at a real, callable
    Python symbol. Parses `module:symbol` target strings and imports
    them via importlib. Catches:

      - module rename (module no longer importable)
      - symbol rename (module importable but attr missing)
      - accidental demotion of `main` to a non-callable (e.g. dataclass
        field, or a module-level constant)

    All of these survive pytest today because tests import `main`
    directly by its current name — they don't exercise the pyproject
    indirection."""
    data = _pyproject()
    scripts = data["project"].get("scripts", {})
    assert scripts, "pyproject.toml [project.scripts] is empty"

    errors: list[str] = []
    for script_name, target in scripts.items():
        if ":" not in target:
            errors.append(
                f"{script_name!r}: target {target!r} has no ':' — "
                f"expected 'module:symbol' form"
            )
            continue
        module_path, _, symbol = target.partition(":")
        try:
            mod = importlib.import_module(module_path)
        except ImportError as e:
            errors.append(
                f"{script_name!r} → {target!r}: module "
                f"{module_path!r} not importable ({e})"
            )
            continue
        fn = getattr(mod, symbol, None)
        if fn is None:
            errors.append(
                f"{script_name!r} → {target!r}: symbol {symbol!r} "
                f"not found in module {module_path!r}"
            )
            continue
        if not callable(fn):
            errors.append(
                f"{script_name!r} → {target!r}: symbol {symbol!r} "
                f"exists but is not callable (got {type(fn).__name__})"
            )
    assert not errors, (
        "Broken entry point(s) in pyproject.toml:\n  "
        + "\n  ".join(errors)
        + "\nThis drift is invisible until `pip install .` produces "
        "a console script that crashes on invocation."
    )


# ---------------------------------------------------------------------------
# 3. The `omicairn` package described by [tool.setuptools.packages.find]
#    must actually exist on disk with an __init__.py
# ---------------------------------------------------------------------------

def test_setuptools_package_include_matches_on_disk_layout():
    """pyproject declares `include = ["omicairn*"]`. If someone renames
    the `omicairn/` package without updating the include pattern (or vice
    versa), `pip install .` silently builds an empty wheel — the
    console script still resolves in a live source tree because
    PYTHONPATH finds `omicairn/` directly, but the shipped wheel is
    broken."""
    data = _pyproject()
    find_cfg = (
        data.get("tool", {})
        .get("setuptools", {})
        .get("packages", {})
        .get("find", {})
    )
    includes = find_cfg.get("include", [])
    assert includes, (
        "pyproject.toml [tool.setuptools.packages.find].include is "
        "missing or empty"
    )
    # For every include pattern of the form 'pkgname*', the stripped
    # prefix must exist as an importable directory on disk.
    for pattern in includes:
        prefix = pattern.rstrip("*")
        if not prefix:
            continue
        pkg_dir = REPO_ROOT / prefix
        assert pkg_dir.is_dir(), (
            f"packages.find include pattern {pattern!r} expects a "
            f"directory at {pkg_dir}, but none exists"
        )
        init = pkg_dir / "__init__.py"
        assert init.is_file(), (
            f"{pkg_dir} exists but has no __init__.py — "
            f"setuptools won't treat it as a package"
        )
