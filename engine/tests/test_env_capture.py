"""Tests for omicairn.env_capture (Tier 1.3)."""
from __future__ import annotations

import json
import pathlib
import tempfile

import pytest

from omicairn import env_capture


def test_capture_current_env_records_python_and_packages():
    lock = env_capture.capture_current_env(["numpy", "pandas"])
    assert lock.version == 1
    assert lock.python_version  # non-empty
    assert lock.executable
    assert lock.platform
    assert lock.imported_modules == ["numpy", "pandas"]
    assert any("name" in p and "version" in p for p in lock.packages)


def test_write_and_load_lockfile_roundtrip():
    with tempfile.TemporaryDirectory() as t:
        out = env_capture.write_lockfile(pathlib.Path(t), ["numpy"])
        assert out.name == "environment.lock.json"
        data = json.loads(out.read_text())
        assert data["version"] == 1
        loaded = env_capture.load_lockfile(out)
        assert loaded is not None
        assert loaded.imported_modules == ["numpy"]


def test_load_lockfile_returns_none_on_garbage():
    with tempfile.TemporaryDirectory() as t:
        p = pathlib.Path(t) / "x.json"
        p.write_text("not json")
        assert env_capture.load_lockfile(p) is None


def test_imports_from_code_dir_finds_external_modules():
    with tempfile.TemporaryDirectory() as t:
        d = pathlib.Path(t)
        (d / "main.py").write_text(
            "import numpy as np\n"
            "import pandas\n"
            "from scipy import stats\n"
            "from . import data_loader  # relative — ignored\n"
            "import json  # stdlib — ignored\n"
        )
        (d / "data_loader.py").write_text("def load(): pass\n")
        (d / "models.py").write_text(
            "import sklearn\n"
            "from data_loader import load  # local helper — ignored\n"
        )
        imports = env_capture.imports_from_code_dir(d)
        assert imports == {"numpy", "pandas", "scipy", "sklearn"}


def test_imports_from_code_dir_handles_unparseable_file():
    with tempfile.TemporaryDirectory() as t:
        d = pathlib.Path(t)
        (d / "good.py").write_text("import numpy\n")
        (d / "broken.py").write_text("import (((")  # syntax error
        imports = env_capture.imports_from_code_dir(d)
        assert "numpy" in imports


def test_imports_present_in_lockfile_aliases_sklearn_to_scikit_learn():
    lock = env_capture.EnvLockfile(
        version=1, captured_at="t", python_version="3.11",
        platform="x", executable="y",
        packages=[
            {"name": "scikit-learn", "version": "1.5"},
            {"name": "numpy", "version": "2.4"},
        ],
        imported_modules=["sklearn", "numpy"],
    )
    present, missing = env_capture.imports_present_in_lockfile(
        ["sklearn", "numpy"], lock,
    )
    assert present == {"sklearn", "numpy"}
    assert missing == set()


def test_imports_present_in_lockfile_pillow_alias():
    lock = env_capture.EnvLockfile(
        version=1, captured_at="t", python_version="3.11",
        platform="x", executable="y",
        packages=[{"name": "pillow", "version": "10.0"}],
        imported_modules=["PIL"],
    )
    present, missing = env_capture.imports_present_in_lockfile(["PIL"], lock)
    assert present == {"PIL"}
    assert missing == set()


def test_imports_present_in_lockfile_handles_dict_packages_shape():
    """Real-world finding (ferroptosis-hcc EXECUTE): the agent emitted
    `packages` as `{name: version}` dict, not list[dict]. Old code
    crashed with AttributeError. The fix handles both shapes."""
    lock = env_capture.EnvLockfile(
        version=1, captured_at="t", python_version="3.11",
        platform="x", executable="y",
        packages={"numpy": "2.4.3", "pandas": "3.0.2", "scikit-learn": "1.5.0"},
        imported_modules=["numpy", "sklearn"],
    )
    present, missing = env_capture.imports_present_in_lockfile(
        ["numpy", "sklearn"], lock,
    )
    assert present == {"numpy", "sklearn"}
    assert missing == set()


def test_imports_present_in_lockfile_handles_list_of_strings():
    """pip-freeze style list[str] (`numpy==2.4.3`). Defensive against the
    other natural shape an LLM agent might emit."""
    lock = env_capture.EnvLockfile(
        version=1, captured_at="t", python_version="3.11",
        platform="x", executable="y",
        packages=["numpy==2.4.3", "pandas>=3.0", "matplotlib"],
        imported_modules=["numpy", "pandas", "matplotlib"],
    )
    present, missing = env_capture.imports_present_in_lockfile(
        ["numpy", "pandas", "matplotlib"], lock,
    )
    assert present == {"numpy", "pandas", "matplotlib"}
    assert missing == set()


def test_imports_present_in_lockfile_reports_missing():
    lock = env_capture.EnvLockfile(
        version=1, captured_at="t", python_version="3.11",
        platform="x", executable="y",
        packages=[{"name": "numpy", "version": "2.4"}],
        imported_modules=["numpy"],
    )
    present, missing = env_capture.imports_present_in_lockfile(
        ["numpy", "torch"], lock,
    )
    assert present == {"numpy"}
    assert missing == {"torch"}
