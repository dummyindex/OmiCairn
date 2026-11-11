"""Environment lockfile capture.

EXECUTE writes ``code/environment.lock.json`` at the end of every
run so analysis.md's methods can cite **the actual environment** the
analysis ran in, not the agent's prose recollection. CROSS_CHECK
verifies that every Python module imported by ``code/*.py`` is
present in the lockfile.

The lockfile is intentionally small and machine-readable. We do NOT
emit a full ``pip freeze --all`` because the working set of imports
is what matters for reproducibility audits; the bigger list churns
on every minor pip upgrade and bloats the diff without value.

Schema (one entry per package, alphabetised)::

    {
      "version": 1,
      "captured_at": "<ISO8601>",
      "python_version": "3.11.15",
      "platform": "darwin-25.0.0-arm64",
      "executable": "/Users/.../envs/omicairn/bin/python",
      "packages": [
        {"name": "numpy", "version": "2.4.3"},
        ...
      ],
      "imported_modules": ["numpy", "pandas", "scipy", ...]
    }

The ``imported_modules`` field is the union of top-level imports
discovered by AST-walking ``code/*.py``. CROSS_CHECK joins this
against the package-distribution names (lower-cased,
underscore→hyphen normalised) before raising a missing-import
error, so importing ``sklearn`` from a ``scikit-learn`` install is
not a false positive.
"""
from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import importlib.metadata
import json
import pathlib
import platform
import sys
from typing import Iterable

# Modules in the standard library — we never report these as missing
# from the lockfile because they ship with Python. Limited to common
# scientific and stdlib names; expand as needed.
_STDLIB_NAMES: frozenset[str] = frozenset({
    "abc", "argparse", "ast", "asyncio", "base64", "bisect", "calendar",
    "collections", "concurrent", "contextlib", "copy", "csv", "dataclasses",
    "datetime", "decimal", "difflib", "enum", "errno", "fnmatch", "functools",
    "gc", "glob", "gzip", "hashlib", "heapq", "html", "http", "importlib",
    "inspect", "io", "ipaddress", "itertools", "json", "logging", "math",
    "multiprocessing", "operator", "os", "pathlib", "pickle", "platform",
    "queue", "random", "re", "shlex", "shutil", "signal", "socket",
    "sqlite3", "ssl", "stat", "string", "struct", "subprocess", "sys",
    "tarfile", "tempfile", "textwrap", "threading", "time", "timeit",
    "tomllib", "traceback", "types", "typing", "unicodedata", "unittest",
    "urllib", "uuid", "warnings", "weakref", "xml", "zipfile", "zlib",
    "__future__", "_thread",
})

# Distribution-name aliases for common rename mismatches between an
# import name and the PyPI package name. Lower-cased, underscore→hyphen
# normalised for both directions.
_IMPORT_TO_DIST_ALIASES: dict[str, frozenset[str]] = {
    "sklearn": frozenset({"scikit-learn"}),
    "skimage": frozenset({"scikit-image"}),
    "cv2": frozenset({"opencv-python", "opencv-python-headless"}),
    "PIL": frozenset({"pillow"}),
    "yaml": frozenset({"pyyaml"}),
    "Crypto": frozenset({"pycryptodome", "pycryptodomex"}),
    "rich": frozenset({"rich"}),
    "lifelines": frozenset({"lifelines"}),
    "scanpy": frozenset({"scanpy"}),
    "anndata": frozenset({"anndata"}),
    "h5py": frozenset({"h5py"}),
    "openpyxl": frozenset({"openpyxl"}),
}


@dataclasses.dataclass
class EnvLockfile:
    version: int
    captured_at: str
    python_version: str
    platform: str
    executable: str
    # Either list[{"name": str, "version": str}] (canonical),
    # dict[str, str] (pip-list-style), or list[str] (pip-freeze-style).
    # imports_present_in_lockfile handles all three.
    packages: list | dict
    imported_modules: list[str]

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def capture_current_env(imported_modules: Iterable[str] = ()) -> EnvLockfile:
    """Snapshot the active environment plus a sorted unique import set."""
    pkgs: list[dict] = []
    for d in sorted(
        importlib.metadata.distributions(),
        key=lambda dd: (dd.metadata["Name"] or "").lower(),
    ):
        name = d.metadata["Name"]
        if not name:
            continue
        pkgs.append({"name": name, "version": d.version})
    return EnvLockfile(
        version=1,
        captured_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        python_version=platform.python_version(),
        platform=f"{platform.system().lower()}-{platform.release()}-{platform.machine()}",
        executable=sys.executable,
        packages=pkgs,
        imported_modules=sorted(set(imported_modules)),
    )


def write_lockfile(
    target_dir: pathlib.Path,
    imported_modules: Iterable[str] = (),
) -> pathlib.Path:
    """Write ``environment.lock.json`` atomically into ``target_dir``."""
    target_dir = pathlib.Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    lock = capture_current_env(imported_modules)
    out = target_dir / "environment.lock.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(lock.to_dict(), indent=2) + "\n")
    tmp.replace(out)
    return out


def load_lockfile(path: pathlib.Path) -> EnvLockfile | None:
    """Load a lockfile from disk; returns None on parse failure.

    Preserves the original ``packages`` shape (list[dict] /
    dict[str,str] / list[str]) — `imports_present_in_lockfile` handles
    all three. A previous version coerced via ``list(...)`` which
    silently dropped versions on a ``dict``-shaped packages block;
    we now keep the structure intact.
    """
    try:
        data = json.loads(pathlib.Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    pkgs = data.get("packages")
    if pkgs is None:
        pkgs = []
    elif not isinstance(pkgs, (list, dict)):
        # Defensive — unknown shape, fall back to empty rather than crash.
        pkgs = []
    try:
        return EnvLockfile(
            version=int(data.get("version", 1)),
            captured_at=str(data.get("captured_at", "")),
            python_version=str(data.get("python_version", "")),
            platform=str(data.get("platform", "")),
            executable=str(data.get("executable", "")),
            packages=pkgs,
            imported_modules=list(data.get("imported_modules") or []),
        )
    except (TypeError, ValueError):
        return None


def imports_from_code_dir(code_dir: pathlib.Path) -> set[str]:
    """AST-walk every ``*.py`` under ``code_dir`` and return the union of
    top-level external module names imported.

    Filters out (a) standard library modules, (b) relative imports
    (``from . import ...``), and (c) imports of co-located helper
    modules (``data_loader``, ``models``, etc. — anything whose
    module file lives next to the importing file).
    """
    code_dir = pathlib.Path(code_dir)
    if not code_dir.exists():
        return set()
    # Local-helper module names: stems of all py files under code_dir.
    locals_: set[str] = {
        f.stem for f in code_dir.rglob("*.py") if f.is_file()
    }
    out: set[str] = set()
    for py in code_dir.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    out.add(root)
            elif isinstance(node, ast.ImportFrom):
                if node.level != 0:
                    continue  # relative import
                if not node.module:
                    continue
                root = node.module.split(".")[0]
                out.add(root)
    return {
        m for m in out
        if m not in _STDLIB_NAMES and m not in locals_
    }


def _normalise_dist_name(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-").strip()


def imports_present_in_lockfile(
    imports: Iterable[str],
    lock: EnvLockfile,
) -> tuple[set[str], set[str]]:
    """Return (present, missing) names from ``imports`` against the
    lockfile, accounting for import-vs-distribution renames.

    A module ``foo`` is considered present if any of:
    - ``foo`` matches a package name (case-insensitive, hyphen-norm)
    - ``foo`` is in ``_IMPORT_TO_DIST_ALIASES`` and one of its dist
      aliases matches a package name

    Handles three lockfile shapes empirically observed:
    1. list[{"name": str, "version": str}]   — what capture_current_env emits
    2. dict[str, str]                          — agents using pip-freeze-style {name: version}
    3. list[str]                               — agents using pip-freeze "name==version" lines
    """
    available: set[str] = set()
    pkgs = lock.packages
    if isinstance(pkgs, dict):
        # Shape 2: {"numpy": "2.4.3", "pandas": "..."}
        for name in pkgs:
            if isinstance(name, str) and name.strip():
                available.add(_normalise_dist_name(name))
    elif isinstance(pkgs, list):
        for entry in pkgs:
            name = ""
            if isinstance(entry, dict):
                # Shape 1.
                name = (entry.get("name") or "").strip()
            elif isinstance(entry, str):
                # Shape 3: "name==version" / "name>=1.0" / bare "name".
                stripped = entry.strip()
                # Split on the first ==/>=/<=/<>~= operator.
                for sep in ("==", ">=", "<=", "~=", ">", "<", "="):
                    if sep in stripped:
                        stripped = stripped.split(sep, 1)[0]
                        break
                name = stripped.strip()
            if name:
                available.add(_normalise_dist_name(name))
    present: set[str] = set()
    missing: set[str] = set()
    for mod in imports:
        norm = _normalise_dist_name(mod)
        if norm in available:
            present.add(mod)
            continue
        aliases = _IMPORT_TO_DIST_ALIASES.get(mod) or _IMPORT_TO_DIST_ALIASES.get(norm)
        if aliases and any(_normalise_dist_name(a) in available for a in aliases):
            present.add(mod)
            continue
        missing.add(mod)
    return present, missing
