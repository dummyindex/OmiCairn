"""Per-stage contract verification.

After a stage's Claude Code session exits, we verify that the required
files exist and are non-trivial inside the stage directory. If not,
the orchestrator decides whether to retry the stage (up to the
per-phase bound) or halt.

Contracts operate on a single stage directory, not the whole workspace.
Every filename in `phase.required_outputs` is resolved relative to
`stage_dir`. This keeps stages self-contained — a stage at
`workspace/004_execute/` that lists `results.json` means exactly
`workspace/004_execute/results.json`, never `workspace/execute/results.json`.
"""
from __future__ import annotations

import dataclasses
import pathlib

from .phases import Phase


# Contract threshold: any non-flag artifact smaller than this is treated
# as "trivial" and the stage fails the contract. 32 bytes is enough to
# distinguish an empty/placeholder file from a real one-line artifact
# without burdening legitimate short JSON files.
_TRIVIAL_SIZE_BYTES: int = 32


@dataclasses.dataclass
class ContractResult:
    ok: bool
    missing: list[str]
    trivial: list[str]  # present but < _TRIVIAL_SIZE_BYTES

    def summary(self) -> str:
        parts = []
        if self.missing:
            parts.append(f"missing: {', '.join(self.missing)}")
        if self.trivial:
            parts.append(f"trivial (<{_TRIVIAL_SIZE_BYTES}B): {', '.join(self.trivial)}")
        return "; ".join(parts) if parts else "ok"


def verify(phase: Phase, stage_dir: pathlib.Path) -> ContractResult:
    """Check that every `phase.required_outputs` file exists in `stage_dir`.

    Each path in `phase.required_outputs` is interpreted as relative to
    `stage_dir`. A `.flag` file is allowed to be one byte; any other
    required file must be at least `_TRIVIAL_SIZE_BYTES` bytes.
    """
    missing: list[str] = []
    trivial: list[str] = []
    for rel in phase.required_outputs:
        p = stage_dir / rel
        if not p.exists():
            missing.append(rel)
            continue
        if p.suffix == ".flag":
            if p.stat().st_size == 0:
                trivial.append(rel)
            continue
        if p.stat().st_size < _TRIVIAL_SIZE_BYTES:
            trivial.append(rel)
    return ContractResult(ok=not (missing or trivial), missing=missing, trivial=trivial)
