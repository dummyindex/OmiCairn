"""Domain-pack loader.

Domain packs are self-contained bundles of per-phase guidance plus
seed lessons that the orchestrator injects into a phase prompt via the
`{{domain_pack_guidance}}` token.

Layout:

    engine/domain_packs/
    ├── _generic/
    │   ├── profile.yaml
    │   ├── phase_scope.md
    │   ├── phase_plan.md
    │   ├── phase_execute.md
    │   ├── phase_review.md      (optional)
    │   └── lessons/
    │       └── *.jsonl
    └── <domain>/
        └── (same layout)

The loader resolves the domain pack by name with a `_generic` fallback.
If neither is present, `render_guidance()` returns an empty string and
the prompt simply drops the block — no crash.

Design goals:

1. **No heavy deps.** A tiny YAML-lite parser handles `profile.yaml`
   because we do not want to pull PyYAML into the base OmiCairn install.
2. **Graceful degradation.** A missing pack, a missing phase file, or
   a missing directory is logged silently as an empty guidance block.
   A domain pack is a hint, not a hard dependency.
3. **Pure read.** This module never writes to the domain pack dir; it
   only reads guidance files and lessons.
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

from .constants import (
    DOMAIN_PACK_GENERIC,
    DOMAIN_PACK_LESSONS_DIR,
    DOMAIN_PACK_PROFILE_FILE,
    DOMAIN_PACKS_DIR,
    PIPELINE_ORDER,
    domain_pack_phase_file,
)


# ---------------------------------------------------------------------------
# Root directory resolution
# ---------------------------------------------------------------------------

def _omicairn_install_root() -> pathlib.Path:
    """Return the engine/ directory containing this module's parent.

    We walk up from `omicairn/domain_pack.py` → `omicairn/` → `engine/`.
    """
    return pathlib.Path(__file__).resolve().parent.parent


def packs_root() -> pathlib.Path:
    return _omicairn_install_root() / DOMAIN_PACKS_DIR


def pack_dir(name: str) -> pathlib.Path:
    return packs_root() / name


def available_packs() -> list[str]:
    root = packs_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


# ---------------------------------------------------------------------------
# Profile (tiny key: value parser — no PyYAML)
# ---------------------------------------------------------------------------

def _parse_mini_yaml(text: str) -> dict[str, Any]:
    """Parse a trivially simple `key: value` YAML file.

    Supports:
    - `key: value` (string values, one per line)
    - `# comment` lines and trailing comments
    - `key:` followed by indented `- item` lines (a list)
    - Blank lines

    Does NOT support nested maps, multi-line strings, or anchors. If a
    domain pack needs more, bolt on PyYAML.
    """
    out: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        stripped = line.lstrip()
        # Continuation list item.
        if stripped.startswith("- ") and current_list_key is not None:
            item = stripped[2:].strip()
            # Strip trailing comment.
            if " #" in item:
                item = item.split(" #", 1)[0].rstrip()
            out[current_list_key].append(item.strip('"').strip("'"))
            continue
        # New key.
        current_list_key = None
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # Strip trailing comment.
        if " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if value == "":
            # Start of a list block.
            out[key] = []
            current_list_key = key
            continue
        out[key] = value.strip('"').strip("'")
    return out


def load_profile(name: str) -> dict[str, Any]:
    """Load the profile.yaml for a domain pack, or an empty dict.

    Missing file → empty dict. Parse errors are treated as empty.
    """
    path = pack_dir(name) / DOMAIN_PACK_PROFILE_FILE
    if not path.is_file():
        return {}
    try:
        return _parse_mini_yaml(path.read_text())
    except OSError:
        return {}


# ---------------------------------------------------------------------------
# Pack resolution
# ---------------------------------------------------------------------------

def resolve_pack(domain: str | None) -> str | None:
    """Pick the best domain-pack name for a given research domain.

    Strategy:
    1. If `domain` names an existing pack directory, use it.
    2. If `domain` matches any pack's `matches:` list in profile.yaml,
       use that pack.
    3. If `_generic` exists, use it.
    4. Otherwise return None.
    """
    if domain:
        direct = pack_dir(domain)
        if direct.is_dir():
            return domain
        # Indirect match: scan profiles.
        for name in available_packs():
            profile = load_profile(name)
            matches = profile.get("matches") or []
            if not isinstance(matches, list):
                continue
            domain_l = domain.lower()
            for m in matches:
                if not isinstance(m, str):
                    continue
                if m.lower() in domain_l or domain_l in m.lower():
                    return name
    # Fallback.
    if pack_dir(DOMAIN_PACK_GENERIC).is_dir():
        return DOMAIN_PACK_GENERIC
    return None


# ---------------------------------------------------------------------------
# Per-phase guidance rendering
# ---------------------------------------------------------------------------

def render_guidance(domain: str | None, phase_name: str) -> str:
    """Return the phase-specific guidance block for the current run.

    Never raises. Missing packs, missing phase files, and empty files
    all yield an empty string.
    """
    if phase_name not in PIPELINE_ORDER:
        return ""
    name = resolve_pack(domain)
    if name is None:
        return ""
    phase_file = pack_dir(name) / domain_pack_phase_file(phase_name)
    if not phase_file.is_file():
        return ""
    try:
        body = phase_file.read_text().strip()
    except OSError:
        return ""
    if not body:
        return ""
    header = f"<!-- domain_pack: {name} / {phase_name} -->"
    return f"{header}\n\n{body}"


# ---------------------------------------------------------------------------
# Seed lessons
# ---------------------------------------------------------------------------

def iter_seed_lessons(domain: str | None) -> list[dict[str, Any]]:
    """Return all lesson records from the resolved pack's lessons/ dir.

    Each .jsonl file under `lessons/` is read line-by-line; malformed
    lines are skipped.
    """
    name = resolve_pack(domain)
    if name is None:
        return []
    lessons_dir = pack_dir(name) / DOMAIN_PACK_LESSONS_DIR
    if not lessons_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(lessons_dir.glob("*.jsonl")):
        try:
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
        except OSError:
            continue
    return out
