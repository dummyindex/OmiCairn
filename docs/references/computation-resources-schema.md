# Compute resources — schema & resolution

OmiCairn lets each research case declare the **hardware envelope** it should
run against. A case names a resource (by `id`) in its `omicairn_config.yaml`
`compute:` block; OmiCairn resolves that name against a `COMPUTATION_RESOURCES.md`
catalog and surfaces the resolved envelope to the EXECUTE and PLAN phases
via the `{{compute_resources}}` prompt token. This replaces the previous
hardcoded assumption that "a GPU is not available locally".

## Two files, two jobs

| File | Role |
|---|---|
| `COMPUTATION_RESOURCES.md` (repo root) | The **catalog**: named hardware envelopes (`gpu-host-1`, `default-local`, …), one fenced ```yaml block each. |
| `<case>/omicairn_config.yaml` `compute:` block | The **per-case reference**: which catalog resource this case uses, plus optional inline overrides. |

## The `compute:` block

```yaml
compute:
  resource: gpu-host-1        # an id from COMPUTATION_RESOURCES.md
  # optional inline overrides (take precedence over the catalog entry):
  # max_runtime_hours: 12
  # notes: "share the node; cap activation dumps at 500 GB"
```

- **`resource`** — the catalog id. Required unless you give a full inline
  resource (an inline block with its own `id` and fields, no `resource:`).
- **inline overrides** — any resource field (see below) set alongside
  `resource:` overrides the catalog entry's value for this case only.

Precedence mirrors the rest of the overlay: a `compute:` block in
`omicairn_config.yaml` wins over one in `RESEARCH.md`.

## Resource fields

| field | type | default | meaning |
|---|---|---|---|
| `id` | str | — (required) | catalog key referenced by `compute.resource` |
| `display_name` | str | `""` | human label |
| `kind` | `gpu`\|`cpu`\|`cluster` | `cpu` | coarse class |
| `gpus` | int | `0` | GPU count (0 = CPU-only) |
| `gpu_model` | str | `""` | e.g. `NVIDIA A100 80GB` |
| `gpu_memory_gb` | number | `0` | per-GPU VRAM (GB) |
| `cpus` | int | `0` | logical cores |
| `memory_gb` | number | `0` | host RAM (GB) |
| `storage_gb` | number | `0` | scratch storage (GB) |
| `interconnect` | str | `""` | `single-node` / `NVLink` / `EFA` / … |
| `max_runtime_hours` | number | `0` | soft hardware wall ceiling |
| `gpus_per_run` | int | `0` | regulation cap on a SHARED host: max GPUs ONE run may claim (0 = unset). Surfaced so EXECUTE sets `CUDA_VISIBLE_DEVICES` to that many idle devices and leaves the rest for colleagues. |
| `gpus_per_run_max` | int | `0` | OPTIONAL between-launch GPU scale-UP CEILING (0 / `<= gpus_per_run` = OFF → static cap). When `> gpus_per_run`, an EXECUTE arm MAY claim up to `effective_gpu_ceiling = min(gpus, max(gpus_per_run, gpus_per_run_max))` GPUs WHEN the host is idle. Scale-up of ONE run; permission not mandate. |
| `max_runs_per_gpu` | int | `1` | OPTIONAL GPU-SHARING / co-tenancy permission (1 or 0 = OFF → whole-GPU semantics; existing hosts unchanged). When `> 1`, the host PERMITS that many DISTINCT runs to co-reside on ONE physical GPU (a ≤7B VLM ~14-16 GB on a 40 GB A100 → 2-3 fit). Run-level co-scheduling capacity = `gpus * max_runs_per_gpu`. Orthogonal to `gpus_per_run`/`gpus_per_run_max`; CEILING/permission only — the agent's live `nvidia-smi` VRAM check decides actual room (no OOM collision). |
| `conda_env` | str | `""` | named conda env the run must **create-or-reuse** (one env reused across a research round saves disk vs. a fresh env per run). |
| `conda_path` | str | `""` | conda install prefix to `source <conda_path>/bin/activate` from. |
| `usage_policy` | str | `""` | free-text shared-host rules surfaced verbatim to the agent (e.g. never delete others' files/envs, pick idle GPUs, scratch location). |
| `scratch_root` | str | `""` | per-run working root convention (`scratch_root/<run-slug>/`): where EXECUTE writes code, caches activations/weights, harvests results; include the cleanup convention. |
| `hf_home` | str | `""` | shared HuggingFace **hub** cache path (download-once across runs), e.g. `/scratch/hf_home`. |
| `datasets_root` | str | `""` | HOST-side shared dataset root for **NON-hub / manually-prepared / re-used-derived** datasets, keyed by DATASET name (`datasets_root/<dataset-name>/`, NOT per run-slug) — prepared once, referenced by many runs. Remote-host analogue of the orchestrator-local `{{shared_cache_dir}}`. Boundary: HF-hub → `hf_home`; per-run ephemera → `scratch_root`; persistent cross-run datasets → `datasets_root`. |
| `activation_recipe` | str | `""` | how to activate the env **by PATH** (not `conda activate <name>` — the real host quirk), plus any runtime caveat (e.g. no vLLM → use HF generate). |
| `gated_unavailable` | str | `""` | gated assets unreachable due to missing creds (e.g. no HF_TOKEN) + the ungated-substitution / RESOURCE_REQUEST guidance. |
| `notes` | str | `""` | free text (queueing, sharing, env, caveats) |

### Shared-GPU-host fields (regulation + execution env)

`gpus_per_run`, `conda_env`, `conda_path`, and `usage_policy` exist for
**real, shared GPU hosts** (e.g. an 8-GPU node colleagues also use). They
are surfaced into the EXECUTE prompt (`{{compute_resources}}`) so the agent:
- creates-or-reuses the named conda env (never a fresh env per run);
- claims at most `gpus_per_run` idle GPUs (checks `nvidia-smi`, sets
  `CUDA_VISIBLE_DEVICES`) rather than the whole node;
- obeys the `usage_policy` verbatim — **never deleting/overwriting files,
  envs, or processes it did not create** on a multi-user machine.

These are generic *mechanism* fields; the concrete values (which env,
which host, how many GPUs) are catalog **data** in `COMPUTATION_RESOURCES.md`,
never hardcoded in `omicairn/` (design principles).

### Host-truth fields (env activation, caches, dataset roots)

`scratch_root`, `hf_home`, `activation_recipe`, `gated_unavailable`, and
`datasets_root` surface operational host-truth that previously lived only in
tribal memory. They ride the same `{{compute_resources}}` token and are pure
catalog **data** — re-pointable by editing this one file, never hardcoded in
`omicairn/`.

The three data roots are non-overlapping by design:

- **`hf_home`** — HuggingFace **hub** cache (`from_pretrained` / `load_dataset`).
- **`datasets_root`** — HOST-side shared root for NON-hub / manually-prepared /
  re-used-derived datasets, keyed by DATASET name (`datasets_root/<dataset-name>/`),
  so a dataset is prepared once and referenced by many runs. It is the
  remote-host analogue of the orchestrator-local `{{shared_cache_dir}}` token
  (which defaults to `<project>/datasets/`); same convention, two filesystems.
- **`scratch_root`** — per-run ephemera (`scratch_root/<run-slug>/`).

`datasets_root` is complementary to the `DATASET_REF` knowledge-graph nodes
(`asset_probe.seed_dataset_refs` → `lab_knowledge`): the KG records *what the
lab holds and where* at the knowledge level; `datasets_root` is the filesystem
convention for *where the shared copy physically lives*. The full layout
convention is in `COMPUTATION_RESOURCES.md` § "Remote working conventions".

## Resolution semantics

`research_md.load(<case>/RESEARCH.md)`:

1. Reads the `compute:` block (omicairn_config.yaml preferred, RESEARCH.md
   fallback).
2. **No block** → resolves to the built-in `default-local` (CPU-only;
   GPU must be requested) — historical behaviour, so existing cases are
   unchanged.
3. **`resource: <id>`** → walks up from the case directory to the nearest
   `COMPUTATION_RESOURCES.md`, parses it, and looks up `<id>`.
   - Unknown `<id>` (or no catalog found) → **`ValueError` at init**. A
     typo must fail loud, never silently degrade to "no compute info".
   - Inline keys override the catalog entry's fields.
4. **Inline-only** (no `resource:`) → builds an ad-hoc resource from the
   inline fields (`id` defaults to `inline`).

The resolved `ComputeResource` is exposed on `ResearchMd.compute` and
rendered to the prompts via `ComputeResource.render_md()`.

## How the agent uses it

The EXECUTE prompt's "Compute resources for this run" block and the PLAN
prompt's "Resource estimate" subsection both render `{{compute_resources}}`.
The agent uses it to:

- decide whether GPU-bound methods are in scope (and size them to the
  stated VRAM), vs. filing a `RESOURCE_REQUEST.md`;
- size full-data runs to the real RAM/runtime envelope (rules R1/R3 — no
  silent subsampling to beat the clock).

## See also

- Catalog: `COMPUTATION_RESOURCES.md` (repo root)
- Code: `engine/omicairn/compute_resources.py`
- Parser hook: `engine/omicairn/research_md.py::load`
- Prompt token: `Token.COMPUTE_RESOURCES` in `constants.py`; rendered in
  `prompts/execute.md` + `prompts/plan.md`
- Tests: `engine/tests/test_compute_resources.py`
- Overlay reference: `docs/references/omicairn-config-schema.md`
