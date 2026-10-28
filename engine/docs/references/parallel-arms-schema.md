# Parallel-arm fan-out schema (`arm_manifest.json` / `parallel_arms.json` / `arms/<id>/arm_provenance.json`)

Within-EXECUTE parallel-arm fan-out lets one EXECUTE stage split an
embarrassingly-parallel workload — a cross-model-transfer matrix
(model-pair × layer), a model × dataset table, or multi-seed replicates —
into independent **arms**, each run by a subagent into its own disjoint
output subdir with its own explicit seed, then merged by `main.py` into the
single canonical `results.json`.

This is **within-run** parallelism only. The sequential stage loop and the
per-workspace lock are UNTOUCHED (a safety property). Engine-level
stage-loop parallelism (running multiple EXECUTE stages / a second lock of
one run concurrently) is explicit **future work**, out of scope for v1.

A run with **no `arm_manifest.json`** is a silent no-op through both the
pre-dispatch CLI gate and the cross_check W32 backstop — fully backward
compatible. Fan-out is CPU / inference-light only; GPU arms serialize.

## Artifacts

| File | Written by | Role |
|---|---|---|
| `arm_manifest.json` | EXECUTE agent | the fan-out plan; the pre-dispatch CLI-gate input. Declared as an EXECUTE `optional_output`. |
| `parallel_arms.json` | `omicairn.parallel_arms.record_manifest` | the recorded verdict stamp. Declared as an EXECUTE `optional_output`. |
| `arms/<arm_id>/arm_provenance.json` | each fan-out subagent (ONE file, in its OWN dir) | per-arm evidence FRAGMENT. Each concurrent writer writes exactly one file into its own disjoint dir — there is NO shared append-only log for N writers to race (a shared JSONL append tears/interleaves once a line exceeds the POSIX `PIPE_BUF` bound, even under `O_APPEND`). The re-check reads the UNION of these fragments. |

### `arm_manifest.json`

```json
{
  "version": 1,
  "arms": [
    {"arm_id": "qwen_layer12", "seed": 1, "output_dir": "arms/qwen_layer12", "gpu": false, "host": "cpu-host-2"},
    {"arm_id": "mistral_layer12", "seed": 2, "output_dir": "arms/mistral_layer12", "gpu": false}
  ],
  "max_parallel": 2
}
```

* `arm_id` — unique per arm.
* `seed` — an **explicit distinct integer** per arm (no implicit/derived seeds).
* `output_dir` — a **stage-relative** `arms/<arm_id>` subdir, disjoint from
  every other arm and from the canonical stage files AND the workspace-level
  shared append-logs (see invariant 1).
* `gpu` — bool. `gpu: true` arms count against the GPU slot budget. An arm
  routed to a known **GPU host** (`host` ∈ `GPU_ROUTED_HOSTS`, e.g.
  `gpu-host-1`) is GPU work by construction and **MUST** set `gpu: true` — an
  omitted/mislabeled flag on a GPU host is a **blocking error** (it cannot be
  allowed to bypass the concurrent-GPU gate). GPU-host arms are also counted
  against the budget even if the flag is (wrongly) omitted.
* `host` — optional routing host (e.g. a CPU box such as `cpu-host-1`, NEVER the
  reserved host; GPU → a `GPU_ROUTED_HOSTS` box).
* `max_parallel` — worker-pool bound, hard-capped at `MAX_IN_STAGE_PARALLEL`
  (8). These are within-run workers, NOT `omicairn run` invocations, so the 25
  global concurrent-run cap is unchanged (1 run = 1 lock).
* `gpu_slots` — **NOT self-authorizing.** A manifest-declared `gpu_slots` is
  advisory only: the validator and both enforcement paths (the prompt-driven
  CLI gate and the cross_check HARD backstop) **ignore it and clamp the budget
  to `DEFAULT_GPU_SLOTS` = 1** — serialize all GPU arms. The ONLY way to permit
  concurrent GPU arms is an **external, engine-supplied** device-slot budget
  (`--gpu-slots N` on the CLI / `gpu_slots=N` kwarg), which by contract cannot
  OOM the shared host. v1 fan-out is CPU/inference-light only. Do not author
  `gpu_slots` in the manifest expecting it to raise the budget; it will not.

### `parallel_arms.json`

```json
{
  "schema_version": 1,
  "validated_at": "2026-07-01T00:00:00+00:00",
  "arm_count": 2,
  "gpu_slots": 1,
  "max_parallel": 2,
  "arms": [{"arm_id": "...", "seed": 1, "output_dir": "arms/...", "gpu": false}],
  "verdict_ok": true
}
```

### `arms/<arm_id>/arm_provenance.json` (advisory recorded evidence)

ONE JSON object per arm, written into that arm's OWN disjoint dir (one writer
per file — race-free by construction, no shared append-log). Fields the
recorded-evidence re-check reads: `arm_id`, `seed` (or `seed_echo`),
`output_dir`, `gpu`, `launched_at`, `finished_at`, `gpu_devices`. The re-check
reads the **union** of all `arms/*/arm_provenance.json` fragments; a torn/
malformed single fragment is skipped (its arm is surfaced as a failed arm by
the empty/absent-dir check) rather than silently corrupting the others.

## The four invariants (BLOCKING at the manifest level)

1. **Disjoint writes.** No two arms may write the same, a nested, or an
   escaping `output_dir`; none may be absolute, contain `..`, or collide
   with a reserved stage artifact OR a workspace-level shared append-log:
   `results.json`, `analysis.md`, `code`, `figures`, `done.flag`,
   `job_marker.json`, `preregistration.json`, **`provenance.jsonl`,
   `events.jsonl`, `arm_manifest.json`, `parallel_arms.json`,
   `arm_provenance.jsonl`** (the last five are the concurrency-critical shared
   logs / fan-out control files an arm must never race).
2. **Distinct explicit seeds.** Every arm carries an int `seed`; missing /
   `None` / non-int / duplicate seeds are rejected.
3. **No concurrent GPU launch.** A GPU arm = one with `gpu: true` OR one routed
   to a `GPU_ROUTED_HOSTS` box; GPU arms are counted against the (externally-
   budgeted, default-1) GPU slot budget. A GPU-host arm that omits/mislabels
   `gpu` is rejected. The budget is NOT manifest-self-declared (see `gpu_slots`
   above). v1 fan-out is CPU/inference-light only; GPU arms serialize (or use
   the block-2c detached job-marker round-trip).
4. **Routing.** No arm may be routed to the reserved host
   (`RESERVED_ARM_HOST`, a placeholder you set for your cluster).

Plus structural bounds: ≥ 1 arm, unique `arm_id`, `max_parallel` ≤ 8.

## Gate (W32) and enforcement

* **Pre-dispatch (soft, prompt-driven).** The EXECUTE agent runs
  `python -m omicairn.parallel_arms validate {{stage_dir}}/arm_manifest.json` and
  dispatches only if it prints `OK` / exits 0; on any violation it fixes the
  manifest or falls back to running the arms sequentially.
* **Backstop (hard, cross_check W32).** `_check_parallel_arm_contract`
  re-validates the on-disk manifest and **BLOCKS progression to REVIEW** (errors) on a
  genuine invariant violation, so a skipped pre-check cannot ship a
  contract-violating fan-out. It also folds in an **ADVISORY** (WARN-only)
  recorded-evidence re-check off the union of `arms/*/arm_provenance.json`
  fragments (materialized-dir overlap, empty/absent arm dir, duplicate recorded
  seed, overlapping GPU launch windows or identical `gpu_devices`). Both the
  CLI gate and this backstop pass **no** external `gpu_slots`, so a manifest
  cannot self-authorize concurrent GPU launches. W32 is default-ON; a domain
  pack may opt out via `disable_execute_checks: [w32]`.

## Merge-back statistical safety

`main.py` recomputes all cross-arm paired deltas + CIs from the per-arm
**per-item** outputs (not from per-arm summary CIs) so the paired-resample-
index contract holds. The merged `results.json` still passes the existing
assemble / CI-containment (point-in-CI) + W13/W15 provenance gates unchanged
— the fan-out changes only HOW cells are produced, not the downstream
honesty gates. The checker verdict + per-arm `{arm_id, seed, output_dir,
gpu}` are embedded into `results.json` provenance.
