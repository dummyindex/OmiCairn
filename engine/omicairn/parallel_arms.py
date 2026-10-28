"""Within-EXECUTE parallel-arm fan-out contract checker (W32).

An EXECUTE stage may fan out an embarrassingly-parallel workload — a
cross-model-transfer matrix (model-pair x layer), a model x dataset
table, or multi-seed replicates — into independent ARMS. Each arm is run
by a subagent (in the SAME turn, concurrent) into its OWN disjoint output
subdir with its OWN explicit seed; ``main.py`` then merges the per-arm
fragments into the single canonical ``results.json``.

This module makes the parallelism CONTRACT code-checked, not prose-only.
It is a pure-stdlib module (no orchestrator/lock/stage import) so it is
importable via ``python -c`` and ``python -m omicairn.parallel_arms``.

Three entry points + a CLI:

* :func:`validate_manifest` — the UP-FRONT checker. Enforces the four HARD
  invariants (BLOCKING): (1) DISJOINT output paths (no two arms equal /
  nest / prefix / escape / collide with a reserved stage file or a
  workspace shared append-log); (2) EXPLICIT DISTINCT int seeds; (3) NO
  CONCURRENT GPU LAUNCH — a GPU arm (``gpu=True`` OR routed to a
  ``GPU_ROUTED_HOSTS`` box) is counted against an EXTERNAL slot budget
  clamped to ``DEFAULT_GPU_SLOTS``=1 (a manifest cannot self-authorize its
  own budget), and a GPU-host arm that omits the flag is rejected → fan-out
  is CPU-only; (4) ROUTING (no arm on the reserved host). Also
  bounds ``arm_id`` uniqueness, a non-empty manifest, and ``max_parallel``
  <= ``MAX_IN_STAGE_PARALLEL``.

* :func:`validate_recorded` — the POST-HOC, ADVISORY re-check (Proposal C
  graft). Reads the UNION of per-arm ``arms/<arm_id>/arm_provenance.json``
  fragments (one race-free file per writer — NOT a shared append-log) +
  inspects the on-disk arm dirs and returns WARN-level findings only
  (materialized-dir overlap, duplicate recorded seed, empty/absent arm
  dir, two GPU arms with overlapping ``launched_at`` windows or the same
  ``gpu_devices``). No-op (ok=True) when no fragment is present.

* :func:`record_manifest` — writes the ``parallel_arms.json`` verdict
  stamp and appends a ``command: 'parallel_arms'`` line to
  ``provenance.jsonl`` (best-effort, never raises).

* :func:`main` / the ``if __name__ == '__main__'`` CLI:
  ``validate <manifest_path> [--stage-dir DIR] [--gpu-slots N]`` — prints
  the verdict and exits 0 on ok, else 1. This is the agent's HARD GATE.

Everything is best-effort JSON I/O — a malformed / absent manifest returns
a FAIL verdict, it never raises. All field names + budgets come from
``constants`` (GP7 — no bare literals).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import pathlib
from typing import Any

from .constants import (
    ARM_PROVENANCE_FRAGMENT,
    ArmManifestKey,
    DEFAULT_GPU_SLOTS,
    GPU_ROUTED_HOSTS,
    MAX_IN_STAGE_PARALLEL,
    RESERVED_ARM_HOST,
    PARALLEL_ARMS_SCHEMA_VERSION,
    PARALLEL_ARMS_STAMP_FILE,
    PROVENANCE_LOG,
    ParallelArmIndependenceGate,
    RESERVED_STAGE_OUTPUT_NAMES,
)

_GATE = ParallelArmIndependenceGate.GATE_ID  # "W32" — used in every message


@dataclasses.dataclass(frozen=True)
class ArmSpec:
    """One fan-out arm: an id, an explicit seed, a stage-relative output_dir,
    a GPU flag, and an optional host + description."""

    arm_id: str
    seed: int
    output_dir: str
    gpu: bool = False
    host: str | None = None
    description: str | None = None


@dataclasses.dataclass
class ArmManifest:
    """A parsed ``arm_manifest.json``: schema version + the arm list +
    the two budgets."""

    version: int
    arms: list[ArmSpec]
    max_parallel: int
    gpu_slots: int


@dataclasses.dataclass
class ManifestVerdict:
    """The result of a validate call. ``ok`` is False iff ``errors`` is
    non-empty (warnings never flip ok). ``normalized`` is the parsed arm
    list; ``provenance`` is the audit payload embedded into
    ``parallel_arms.json`` / ``results.json`` provenance."""

    ok: bool
    errors: list[str]
    warnings: list[str]
    normalized: list[ArmSpec]
    provenance: dict[str, Any]

    def human_summary(self) -> str:
        """One-block human summary for the CLI. Prints 'OK' + a one-line
        arm digest when clean, else the errors (and any warnings)."""
        lines: list[str] = []
        if self.ok:
            lines.append(f"OK ({_GATE}): {len(self.normalized)} arm(s) validated")
            for a in self.normalized:
                gpu = " gpu" if a.gpu else ""
                lines.append(f"  - {a.arm_id}: seed={a.seed} dir={a.output_dir}{gpu}")
        else:
            lines.append(f"FAIL ({_GATE}): {len(self.errors)} error(s)")
            for e in self.errors:
                lines.append(f"  ERROR: {e}")
        for w in self.warnings:
            lines.append(f"  WARN: {w}")
        return "\n".join(lines)


def _fail(msg: str) -> ManifestVerdict:
    """A FAIL verdict carrying one error (used for malformed/absent input)."""
    return ManifestVerdict(
        ok=False, errors=[f"{_GATE}: {msg}"], warnings=[], normalized=[],
        provenance={},
    )


def _coerce_arm(obj: Any) -> ArmSpec | str:
    """Best-effort parse of one arm dict into an ArmSpec, or an error string.

    The seed is NOT coerced here (a missing/None/non-int seed must be a
    validation ERROR, not silently patched); it is passed through as-is so
    :func:`validate_manifest` can flag it. ``output_dir`` / ``arm_id`` fall
    back to empty strings so the disjoint-write / id-uniqueness checks fire
    with a stable message rather than a KeyError.
    """
    if not isinstance(obj, dict):
        return f"arm entry is not an object: {obj!r}"
    K = ArmManifestKey
    return ArmSpec(
        arm_id=str(obj.get(K.ARM_ID, "") or ""),
        seed=obj.get(K.SEED),  # type: ignore[arg-type]  — validated downstream
        output_dir=str(obj.get(K.OUTPUT_DIR, "") or ""),
        gpu=bool(obj.get(K.GPU, False)),
        host=(obj.get(K.HOST) or None),
        description=(obj.get(K.DESCRIPTION) or None),
    )


def load_manifest(path_or_obj: Any) -> ArmManifest | ManifestVerdict:
    """Load a manifest from a path (str/Path), a raw dict, or a raw list of
    arm dicts. Returns an :class:`ArmManifest` on success or a FAIL
    :class:`ManifestVerdict` on malformed / absent / unparseable input —
    never raises (best-effort, mirroring asset_probe/provenance I/O)."""
    obj: Any = path_or_obj
    if isinstance(path_or_obj, (str, pathlib.Path)):
        p = pathlib.Path(path_or_obj)
        if not p.exists():
            return _fail(f"manifest not found: {p}")
        try:
            obj = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError) as e:
            return _fail(f"manifest does not parse: {e}")

    K = ArmManifestKey
    if isinstance(obj, list):
        raw_arms = obj
        version = PARALLEL_ARMS_SCHEMA_VERSION
        max_parallel = len(obj)
        gpu_slots = DEFAULT_GPU_SLOTS
    elif isinstance(obj, dict):
        raw_arms = obj.get(K.ARMS)
        if not isinstance(raw_arms, list):
            return _fail(f"manifest `{K.ARMS}` is missing or not a list")
        version = int(obj.get(K.VERSION, PARALLEL_ARMS_SCHEMA_VERSION) or PARALLEL_ARMS_SCHEMA_VERSION)
        max_parallel = int(obj.get(K.MAX_PARALLEL, len(raw_arms)) or len(raw_arms))
        gpu_slots = int(obj.get(K.GPU_SLOTS, DEFAULT_GPU_SLOTS) or DEFAULT_GPU_SLOTS)
    else:
        return _fail(f"manifest is neither an object nor a list: {type(obj).__name__}")

    arms: list[ArmSpec] = []
    for raw in raw_arms:
        parsed = _coerce_arm(raw)
        if isinstance(parsed, str):
            return _fail(parsed)
        arms.append(parsed)
    return ArmManifest(version=version, arms=arms, max_parallel=max_parallel, gpu_slots=gpu_slots)


def _normalize_output_dir(output_dir: str) -> tuple[str, str | None]:
    """Return ``(normalized_posix, error_or_None)`` for an arm output_dir.

    Rejects (returns an error string): an empty dir, an absolute path, a
    ``..`` escape, or a path that resolves outside the stage root. The
    normalized form is used for equality / nesting comparison + for the
    reserved-file collision check.
    """
    if not output_dir:
        return "", "output_dir is empty"
    p = pathlib.PurePosixPath(output_dir)
    if p.is_absolute():
        return "", f"output_dir must be stage-relative, not absolute: {output_dir!r}"
    parts = p.parts
    if ".." in parts:
        return "", f"output_dir must not contain '..' (escapes stage_dir): {output_dir!r}"
    # Strip a leading "./" and any redundant separators via PurePosixPath.
    norm = "/".join(parts)
    if not norm:
        return "", f"output_dir normalizes to empty: {output_dir!r}"
    return norm, None


def _nests_or_equals(a: str, b: str) -> bool:
    """True iff normalized dirs ``a`` and ``b`` are equal, or one is a
    path-prefix (nests under) the other. ``arms/a`` nests ``arms/a/b`` and
    also collides with ``arms/a`` — but ``arms/ab`` does NOT nest ``arms/a``
    (component-wise, not string-prefix)."""
    if a == b:
        return True
    pa = a.split("/")
    pb = b.split("/")
    shorter, longer = (pa, pb) if len(pa) <= len(pb) else (pb, pa)
    return longer[: len(shorter)] == shorter


def validate_manifest(
    manifest: Any,
    *,
    stage_dir: pathlib.Path | None = None,
    gpu_slots: int | None = None,
) -> ManifestVerdict:
    """Validate a manifest against the four HARD invariants + the structural
    bounds. Accepts an :class:`ArmManifest`, a path, a dict, or a list of
    arm dicts (anything :func:`load_manifest` accepts). Returns a
    :class:`ManifestVerdict`; ``ok`` is False iff any invariant is violated.

    ``gpu_slots`` is the EXTERNAL, engine-supplied device-slot budget. It is
    the ONLY way to permit concurrent GPU arms: when it is ``None`` (the
    default — the prompt-driven CLI gate and the cross_check HARD backstop both
    pass ``None``), the GPU budget is CLAMPED to :data:`DEFAULT_GPU_SLOTS`
    regardless of what the manifest self-declares. The invariant-3 contract is
    therefore "GPU concurrency <= an externally-authorized slot budget", NOT
    "<= whatever the artifact declares for itself" — a manifest that authors
    ``gpu_slots: 8`` cannot self-authorize 8 concurrent GPU launches onto the
    shared host. ``stage_dir`` is currently only used for the provenance record
    — output_dir checks are stage-relative and do not need the concrete root
    (the on-disk materialization check lives in cross_check W32 /
    :func:`validate_recorded`).
    """
    loaded = manifest if isinstance(manifest, ArmManifest) else load_manifest(manifest)
    if isinstance(loaded, ManifestVerdict):
        return loaded  # already a FAIL (malformed/absent)

    errors: list[str] = []
    warnings: list[str] = []
    # GPU-slot budget is NOT self-authorizing: only an EXTERNAL gpu_slots arg
    # can raise it above the default. The manifest's own gpu_slots is advisory
    # (recorded in provenance) but never trusted for enforcement.
    manifest_declared_gpu_slots = loaded.gpu_slots
    if gpu_slots is not None:
        effective_gpu_slots = gpu_slots
    else:
        effective_gpu_slots = DEFAULT_GPU_SLOTS
        if manifest_declared_gpu_slots > DEFAULT_GPU_SLOTS:
            warnings.append(
                f"manifest self-declares gpu_slots={manifest_declared_gpu_slots} "
                f"but a manifest CANNOT self-authorize concurrent GPU launches; "
                f"clamping to DEFAULT_GPU_SLOTS={DEFAULT_GPU_SLOTS} (pass an "
                f"external --gpu-slots budget to raise it)"
            )

    arms = loaded.arms
    if not arms:
        errors.append("manifest declares no arms (need >= 1)")

    # max_parallel bound.
    if loaded.max_parallel > MAX_IN_STAGE_PARALLEL:
        errors.append(
            f"max_parallel={loaded.max_parallel} exceeds the host-load cap "
            f"MAX_IN_STAGE_PARALLEL={MAX_IN_STAGE_PARALLEL}"
        )

    # arm_id uniqueness.
    ids: list[str] = []
    for a in arms:
        if not a.arm_id:
            errors.append("an arm has an empty arm_id")
        ids.append(a.arm_id)
    dup_ids = sorted({i for i in ids if ids.count(i) > 1 and i})
    if dup_ids:
        errors.append(f"duplicate arm_id(s): {dup_ids}")

    # DISTINCT EXPLICIT int seeds (invariant 2).
    seeds: list[int] = []
    for a in arms:
        if a.seed is None:
            errors.append(f"arm {a.arm_id!r} has no seed (an explicit int seed is required)")
            continue
        if isinstance(a.seed, bool) or not isinstance(a.seed, int):
            errors.append(f"arm {a.arm_id!r} seed must be an int, got {a.seed!r}")
            continue
        seeds.append(a.seed)
    dup_seeds = sorted({s for s in seeds if seeds.count(s) > 1})
    if dup_seeds:
        errors.append(f"duplicate seed(s) across arms: {dup_seeds} (each arm needs a distinct seed)")

    # DISJOINT output paths (invariant 1).
    norm_dirs: list[tuple[str, str]] = []  # (arm_id, normalized_dir)
    reserved = set(RESERVED_STAGE_OUTPUT_NAMES)
    for a in arms:
        norm, err = _normalize_output_dir(a.output_dir)
        if err:
            errors.append(f"arm {a.arm_id!r}: {err}")
            continue
        # Reserved-file collision: the dir equals / nests / is nested by a
        # reserved stage artifact name.
        for res in reserved:
            if _nests_or_equals(norm, res):
                errors.append(
                    f"arm {a.arm_id!r} output_dir {norm!r} collides with reserved "
                    f"stage artifact {res!r} (an arm must not race the canonical "
                    f"stage files main.py assembles)"
                )
                break
        norm_dirs.append((a.arm_id, norm))
    # Pairwise overlap / nesting between arm dirs.
    for i in range(len(norm_dirs)):
        for j in range(i + 1, len(norm_dirs)):
            id_i, d_i = norm_dirs[i]
            id_j, d_j = norm_dirs[j]
            if _nests_or_equals(d_i, d_j):
                errors.append(
                    f"arms {id_i!r} and {id_j!r} have overlapping output dirs "
                    f"({d_i!r} vs {d_j!r}); every arm must write a DISJOINT dir"
                )

    # ROUTING-DERIVED GPU FLAG (invariant 3 anti-bypass): an arm routed to a
    # known GPU host is GPU work by CONSTRUCTION. If it does not self-declare
    # gpu=True it would escape the concurrent-GPU accounting entirely (the
    # gpu==True-only count), so N such arms could fan onto the shared GPU box
    # with zero slot accounting. Reject the mislabel at source: a GPU-host arm
    # MUST carry gpu=True. This makes the gpu flag DERIVABLE from the routed
    # host, so it cannot be silently omitted.
    for a in arms:
        if a.host and a.host in GPU_ROUTED_HOSTS and not a.gpu:
            errors.append(
                f"arm {a.arm_id!r} is routed to GPU host {a.host!r} but is not "
                f"marked gpu=True: a GPU-host arm is GPU work and MUST set "
                f"gpu=True so it is counted against gpu_slots (an omitted/"
                f"mislabeled gpu flag cannot be allowed to bypass the "
                f"concurrent-GPU-launch gate)"
            )

    # NO CONCURRENT GPU LAUNCH (invariant 3): count as a GPU arm anything that
    # self-declares gpu=True OR is routed to a known GPU host (belt-and-braces
    # with the reject above — even if the reject were relaxed, a GPU-host arm
    # still consumes a slot).
    gpu_arms = [
        a.arm_id for a in arms if a.gpu or (a.host and a.host in GPU_ROUTED_HOSTS)
    ]
    if len(gpu_arms) > effective_gpu_slots:
        errors.append(
            f"{len(gpu_arms)} GPU arm(s) {sorted(gpu_arms)} exceed gpu_slots="
            f"{effective_gpu_slots}: GPU arms must SERIALIZE (v1 fan-out is "
            f"CPU/inference-light only; raise gpu_slots only with an explicit "
            f"external device-slot budget that cannot OOM the shared host)"
        )

    # ROUTING (invariant 4): no arm on the reserved host.
    for a in arms:
        if a.host and a.host == RESERVED_ARM_HOST:
            errors.append(
                f"arm {a.arm_id!r} is routed to the reserved host "
                f"{RESERVED_ARM_HOST!r}: this host is reserved for other "
                f"workloads and must never carry an OmiCairn parallel arm"
            )

    provenance = {
        "gate": _GATE,
        "schema_version": loaded.version,
        "arm_count": len(arms),
        "gpu_slots": effective_gpu_slots,
        "gpu_slots_manifest_declared": manifest_declared_gpu_slots,
        "gpu_slots_externally_supplied": gpu_slots is not None,
        "max_parallel": loaded.max_parallel,
        "arms": [
            {
                ArmManifestKey.ARM_ID: a.arm_id,
                ArmManifestKey.SEED: a.seed,
                ArmManifestKey.OUTPUT_DIR: a.output_dir,
                ArmManifestKey.GPU: a.gpu,
            }
            for a in arms
        ],
        "verdict_ok": not errors,
    }
    if stage_dir is not None:
        provenance["stage_dir"] = str(stage_dir)

    return ManifestVerdict(
        ok=not errors, errors=errors, warnings=warnings,
        normalized=arms, provenance=provenance,
    )


def _read_provenance_records(stage_dir: pathlib.Path) -> list[dict[str, Any]] | None:
    """Read the UNION of per-arm provenance FRAGMENTS into a list of dicts, or
    None when no fragment exists.

    Each concurrent subagent writes exactly ONE ``arm_provenance.json`` into
    ITS OWN disjoint ``arms/<arm_id>/`` dir (never a shared append-only log —
    that had a lost-update / torn-line race for N concurrent writers). Here we
    glob ``*/arm_provenance.json`` under ``stage_dir`` and read each as an
    independent JSON object: one writer per file means no interleaving, so a
    fragment either parses cleanly or is skipped as a failed arm (never a torn
    line silently dropped from the middle of a shared log). Returns None only
    when NO fragment is found at all (the backward-compatible no-op case).
    Tolerates a malformed single fragment (skips it — its arm is a failed arm,
    surfaced elsewhere by the empty/absent-dir check).
    """
    root = pathlib.Path(stage_dir)
    if not root.exists():
        return None
    # Per-arm fragments live one level down: arms/<arm_id>/arm_provenance.json
    # (and defensively any */arm_provenance.json). Sort for determinism.
    fragments = sorted(root.glob(f"*/*/{ARM_PROVENANCE_FRAGMENT}")) + sorted(
        root.glob(f"*/{ARM_PROVENANCE_FRAGMENT}")
    )
    # De-dup by resolved path (the two globs can overlap for depth-1 layouts).
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    found_any = False
    for frag in fragments:
        key = str(frag)
        if key in seen:
            continue
        seen.add(key)
        found_any = True
        try:
            obj = json.loads(frag.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue  # a failed/partial arm — surfaced by the empty-dir check
        if isinstance(obj, dict):
            out.append(obj)
    if not found_any:
        return None
    return out


def _windows_overlap(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    """True iff [a_start, a_end] and [b_start, b_end] overlap. Timestamps are
    ISO8601 strings compared lexically (ISO8601 sorts chronologically for a
    fixed zone). A missing end is treated as "still running" (open-ended)."""
    a_end = a_end or "9999"
    b_end = b_end or "9999"
    if not a_start or not b_start:
        return False
    return a_start <= b_end and b_start <= a_end


def validate_recorded(stage_dir: pathlib.Path) -> ManifestVerdict:
    """ADVISORY (WARN-only) post-hoc re-check of RECORDED reality (Proposal C).

    Reads the UNION of per-arm ``arms/<arm_id>/arm_provenance.json`` fragments
    (see :func:`_read_provenance_records` — one race-free file per writer, NOT
    a shared append-log) + inspects the on-disk arm dirs and surfaces
    WARN-level findings ONLY (never errors, so ``ok`` stays True):

    * a materialized arm dir that another recorded arm also claims (overlap);
    * a duplicate recorded seed across arms;
    * an arm dir that is absent or empty on disk (a failed / no-op arm);
    * two GPU arms with overlapping ``launched_at``..``finished_at`` windows,
      or the same claimed ``gpu_devices`` (a mis-fanned-out concurrent GPU
      run that could OOM / collide on the shared host).

    No-op (ok=True, no findings) when no per-arm fragment is present.
    """
    K = ArmManifestKey
    warnings: list[str] = []
    records = _read_provenance_records(stage_dir)
    if records is None:
        return ManifestVerdict(
            ok=True, errors=[], warnings=[], normalized=[],
            provenance={"gate": _GATE, "recorded": "absent"},
        )

    stage_root = pathlib.Path(stage_dir)
    seen_dirs: dict[str, str] = {}   # normalized_dir -> arm_id
    seen_seeds: dict[Any, str] = {}  # seed -> arm_id
    gpu_windows: list[tuple[str, str, str, str]] = []  # (arm_id, start, end, devices)
    seen_devices: dict[str, str] = {}  # gpu_devices -> arm_id

    for rec in records:
        arm_id = str(rec.get(K.ARM_ID, "") or "?")
        out_dir = str(rec.get(K.OUTPUT_DIR, "") or "")

        # Materialized-dir overlap.
        if out_dir:
            norm, err = _normalize_output_dir(out_dir)
            if not err:
                if norm in seen_dirs and seen_dirs[norm] != arm_id:
                    warnings.append(
                        f"recorded arms {seen_dirs[norm]!r} and {arm_id!r} both "
                        f"claim materialized dir {norm!r}"
                    )
                seen_dirs[norm] = arm_id
                # Empty / absent on disk.
                d = stage_root / norm
                if not d.exists() or (d.is_dir() and not any(d.iterdir())):
                    warnings.append(
                        f"recorded arm {arm_id!r} dir {norm!r} is absent or empty on "
                        f"disk — a FAILED arm; do not synthesize its cell"
                    )

        # Duplicate recorded seed.
        seed = rec.get(K.SEED, rec.get(K.SEED_ECHO))
        if seed is not None:
            if seed in seen_seeds and seen_seeds[seed] != arm_id:
                warnings.append(
                    f"recorded arms {seen_seeds[seed]!r} and {arm_id!r} both used "
                    f"seed {seed!r}"
                )
            seen_seeds[seed] = arm_id

        # GPU launch-window / device overlap.
        if bool(rec.get(K.GPU, False)):
            start = str(rec.get(K.LAUNCHED_AT, "") or "")
            end = str(rec.get(K.FINISHED_AT, "") or "")
            devices = rec.get(K.GPU_DEVICES)
            dev_key = json.dumps(devices, sort_keys=True) if devices is not None else ""
            for (o_id, o_start, o_end, _o_dev) in gpu_windows:
                if o_id != arm_id and _windows_overlap(start, end, o_start, o_end):
                    warnings.append(
                        f"recorded GPU arms {o_id!r} and {arm_id!r} have overlapping "
                        f"launch windows ([{o_start}..{o_end}] vs [{start}..{end}]) — "
                        f"a mis-fanned-out concurrent GPU run"
                    )
            if dev_key:
                if dev_key in seen_devices and seen_devices[dev_key] != arm_id:
                    warnings.append(
                        f"recorded GPU arms {seen_devices[dev_key]!r} and {arm_id!r} "
                        f"claim the same gpu_devices {devices!r}"
                    )
                seen_devices[dev_key] = arm_id
            gpu_windows.append((arm_id, start, end, dev_key))

    return ManifestVerdict(
        ok=True, errors=[], warnings=warnings, normalized=[],
        provenance={"gate": _GATE, "recorded": "present", "records": len(records)},
    )


def record_manifest(
    stage_dir: pathlib.Path,
    manifest: ArmManifest | Any,
    verdict: ManifestVerdict,
) -> dict[str, Any]:
    """Write the ``parallel_arms.json`` verdict stamp into ``stage_dir`` and
    append a ``command: 'parallel_arms'`` line to ``provenance.jsonl``.

    Best-effort: swallows all IO errors so it can never fail a stage.
    Returns the stamp dict (for callers/tests).
    """
    loaded = manifest if isinstance(manifest, ArmManifest) else load_manifest(manifest)
    K = ArmManifestKey
    if isinstance(loaded, ArmManifest):
        arm_rows = [
            {
                K.ARM_ID: a.arm_id,
                K.SEED: a.seed,
                K.OUTPUT_DIR: a.output_dir,
                K.GPU: a.gpu,
            }
            for a in loaded.arms
        ]
        gpu_slots = loaded.gpu_slots
        max_parallel = loaded.max_parallel
    else:  # a FAIL verdict came back from load — stamp what the verdict knows
        arm_rows = verdict.provenance.get("arms", [])
        gpu_slots = verdict.provenance.get("gpu_slots", DEFAULT_GPU_SLOTS)
        max_parallel = verdict.provenance.get("max_parallel", 0)

    stamp: dict[str, Any] = {
        "schema_version": PARALLEL_ARMS_SCHEMA_VERSION,
        "validated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "arm_count": len(arm_rows),
        "gpu_slots": gpu_slots,
        "max_parallel": max_parallel,
        "arms": arm_rows,
        "verdict_ok": verdict.ok,
    }
    stage_root = pathlib.Path(stage_dir)
    try:
        stage_root.mkdir(parents=True, exist_ok=True)
        (stage_root / PARALLEL_ARMS_STAMP_FILE).write_text(
            json.dumps(stamp, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        return stamp
    # Append a provenance line (best-effort). Reuses the workspace-root
    # provenance.jsonl append shape (command-tagged), like provenance.record_invocation.
    try:
        prov_line = {
            "command": "parallel_arms",
            "gate": _GATE,
            "recorded_at": stamp["validated_at"],
            "arm_count": stamp["arm_count"],
            "verdict_ok": verdict.ok,
        }
        with (stage_root / PROVENANCE_LOG).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(prov_line, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return stamp


def main(argv: list[str] | None = None) -> int:
    """CLI: ``validate <manifest_path> [--stage-dir DIR] [--gpu-slots N]``.

    Prints ``verdict.human_summary()`` and returns 0 on ok else 1. This is
    the agent's HARD pre-dispatch gate: dispatch subagents ONLY if this
    exits 0.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="python -m omicairn.parallel_arms")
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="validate an arm_manifest.json")
    v.add_argument("manifest_path")
    v.add_argument("--stage-dir", default=None)
    v.add_argument("--gpu-slots", type=int, default=None)
    ns = parser.parse_args(argv)

    if ns.cmd == "validate":
        stage_dir = pathlib.Path(ns.stage_dir) if ns.stage_dir else None
        verdict = validate_manifest(
            ns.manifest_path, stage_dir=stage_dir, gpu_slots=ns.gpu_slots
        )
        print(verdict.human_summary())
        return 0 if verdict.ok else 1
    return 2


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main(sys.argv[1:]))
