"""Shared pytest fixtures for the omicairn test suite.

The autouse `isolated_paper_lib` fixture redirects the shared paper
library at `$OMICAIRN_PAPER_LIB` to a per-test `tmp_path/paper_library`
directory. Without this, tests that exercise
`LiteratureFetcher.search_and_fetch` would write into the developer's
real cache at `~/.cache/omicairn/paper_library/`, which:

  - Pollutes the developer's machine.
  - Causes subsequent test runs to hit the library cache and skip
    downloads the tests assert happen — producing order-dependent
    failures.

Isolation is safe: `paper_library.default_library_root()` respects the
env var on every call, and nothing in the module keeps a cached root.
"""
from __future__ import annotations

import pathlib

import pytest


@pytest.fixture(autouse=True)
def isolated_paper_lib(tmp_path: pathlib.Path, monkeypatch) -> pathlib.Path:
    lib_root = tmp_path / "paper_library"
    monkeypatch.setenv("OMICAIRN_PAPER_LIB", str(lib_root))
    return lib_root
