"""Tests for the configurable compute-resource feature.

Covers: catalog parsing (incl. the indented-fence regression), per-case
resolution (catalog ref, inline overrides, inline-only, unknown-id loud
fail, no-block fallback), render_md, the omicairn_config.yaml `compute:`
overlay wiring through research_md.load(), and the {{compute_resources}}
prompt-token rendering.
"""
from __future__ import annotations

import pathlib

import pytest

from omicairn import compute_resources as cr
from omicairn import research_md


# ---------------------------------------------------------------------------
# A catalog fixture that reproduces the real file's shape, including an
# INDENTED ```yaml snippet inside prose (the bug that made the first regex
# swallow the first real entry).
# ---------------------------------------------------------------------------
_CATALOG = """\
# Computation Resources

How a case references a resource:

   ```yaml
   compute:
     resource: gpu-host-1
   ```

## gpu-host-1

```yaml
id: gpu-host-1
display_name: "A100 GPU node (single 80GB)"
kind: gpu
gpus: 1
gpu_model: "NVIDIA A100 80GB"
gpu_memory_gb: 80
cpus: 32
memory_gb: 256
storage_gb: 2000
interconnect: single-node
max_runtime_hours: 24
notes: >
  Primary GPU compute node. GPU IS available.
```

## default-local

```yaml
id: default-local
display_name: "Local CPU (no GPU)"
kind: cpu
gpus: 0
cpus: 8
memory_gb: 32
max_runtime_hours: 6
notes: CPU-only fallback.
```
"""


def _write_catalog(tmp_path: pathlib.Path, text: str = _CATALOG) -> pathlib.Path:
    p = tmp_path / cr.CATALOG_FILENAME
    p.write_text(text)
    return p


# ---------------------------------------------------------------------------
# Catalog parsing
# ---------------------------------------------------------------------------
def test_parse_catalog_extracts_all_real_entries():
    cat = cr.parse_catalog(_CATALOG)
    assert set(cat) == {"gpu-host-1", "default-local"}


def test_parse_catalog_indented_fence_does_not_swallow_real_entry():
    """Regression: an indented ```yaml snippet in prose must not merge with
    later blocks. The first real entry (gpu-host-1) must still parse."""
    cat = cr.parse_catalog(_CATALOG)
    assert "gpu-host-1" in cat, "indented prose fence swallowed the entry"


def test_parse_catalog_coerces_field_types():
    a = cr.parse_catalog(_CATALOG)["gpu-host-1"]
    assert a.gpus == 1 and isinstance(a.gpus, int)
    assert a.gpu_memory_gb == 80.0 and isinstance(a.gpu_memory_gb, float)
    assert a.cpus == 32
    assert a.memory_gb == 256.0
    assert a.gpu_model == "NVIDIA A100 80GB"
    assert a.kind == "gpu"


def test_parse_catalog_skips_blocks_without_id():
    text = "```yaml\nfoo: bar\n```\n\n```yaml\nid: real\n```\n"
    cat = cr.parse_catalog(text)
    assert set(cat) == {"real"}


def test_parse_catalog_empty_text_is_empty_dict():
    assert cr.parse_catalog("no fenced blocks here") == {}


def test_has_gpu_property():
    cat = cr.parse_catalog(_CATALOG)
    assert cat["gpu-host-1"].has_gpu is True
    assert cat["default-local"].has_gpu is False


# ---------------------------------------------------------------------------
# Catalog discovery (walk up)
# ---------------------------------------------------------------------------
def test_find_catalog_walks_up(tmp_path):
    _write_catalog(tmp_path)
    nested = tmp_path / "research_cases" / "case-x"
    nested.mkdir(parents=True)
    found = cr.find_catalog(nested)
    assert found == (tmp_path / cr.CATALOG_FILENAME)


def test_find_catalog_returns_none_when_absent(tmp_path):
    # pytest tmp_path lives under /tmp/pytest-of-*, which is NOT under the
    # repo tree, so no COMPUTATION_RESOURCES.md exists at or above it. The
    # walk-up must reach the fs root and return None. (A non-tautological
    # assertion: the previous `is None or .name == CATALOG_FILENAME` form
    # could never fail because find_catalog only ever returns a path whose
    # name IS CATALOG_FILENAME — see audit 2026-06-09.)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert cr.find_catalog(nested) is None


# ---------------------------------------------------------------------------
# resolve()
# ---------------------------------------------------------------------------
def test_resolve_none_returns_default_local(tmp_path):
    res = cr.resolve(None, tmp_path)
    assert res.id == "default-local"
    assert res.has_gpu is False


def test_resolve_empty_block_returns_default_local(tmp_path):
    assert cr.resolve({}, tmp_path).id == "default-local"


def test_resolve_named_resource_from_catalog(tmp_path):
    _write_catalog(tmp_path)
    case = tmp_path / "case"
    case.mkdir()
    res = cr.resolve({"resource": "gpu-host-1"}, case)
    assert res.id == "gpu-host-1"
    assert res.has_gpu is True
    assert res.gpu_memory_gb == 80.0


def test_resolve_unknown_id_fails_loud(tmp_path):
    _write_catalog(tmp_path)
    case = tmp_path / "case"
    case.mkdir()
    with pytest.raises(ValueError, match="not in the catalog"):
        cr.resolve({"resource": "nonexistent-gpu"}, case)


def test_resolve_named_without_catalog_fails_loud(tmp_path):
    case = tmp_path / "case"
    case.mkdir()
    with pytest.raises(ValueError, match="no .* catalog was found"):
        cr.resolve({"resource": "gpu-host-1"}, case)


def test_resolve_inline_overrides_catalog_entry(tmp_path):
    _write_catalog(tmp_path)
    case = tmp_path / "case"
    case.mkdir()
    res = cr.resolve(
        {"resource": "gpu-host-1", "max_runtime_hours": 12, "notes": "shared"},
        case,
    )
    assert res.id == "gpu-host-1"
    assert res.max_runtime_hours == 12.0   # overridden
    assert res.notes == "shared"           # overridden
    assert res.gpus == 1                   # inherited from catalog


def test_resolve_inline_only_adhoc_resource(tmp_path):
    res = cr.resolve(
        {"id": "adhoc-gpu", "kind": "gpu", "gpus": 2, "gpu_memory_gb": 40},
        tmp_path,
    )
    assert res.id == "adhoc-gpu"
    assert res.gpus == 2
    assert res.has_gpu is True


def test_resolve_inline_only_without_id_gets_default_id(tmp_path):
    res = cr.resolve({"kind": "gpu", "gpus": 1}, tmp_path)
    assert res.id == "inline"


def test_resolve_non_mapping_block_raises(tmp_path):
    with pytest.raises(ValueError, match="must be a mapping"):
        cr.resolve(["not", "a", "dict"], tmp_path)


# ---------------------------------------------------------------------------
# render_md (the {{compute_resources}} prompt block)
# ---------------------------------------------------------------------------
def test_render_md_gpu_says_available():
    a = cr.parse_catalog(_CATALOG)["gpu-host-1"]
    md = a.render_md()
    assert "GPU:" in md and "AVAILABLE" in md
    assert "80" in md  # VRAM surfaced
    assert "gpu-host-1" in md


def test_render_md_cpu_says_not_available_and_request():
    md = cr.DEFAULT_LOCAL.render_md()
    assert "NOT available" in md
    assert "RESOURCE_REQUEST.md" in md


# ---------------------------------------------------------------------------
# End-to-end: research_md.load() overlay wiring
# ---------------------------------------------------------------------------
_RESEARCH_MD = """\
---
topic_slug: compute-case
research_question: |
  A test case for compute resolution.
data:
  location: {data_dir}
---
# body
"""

_CFG_WITH_COMPUTE = """\
budgets:
  claude_model: claude-sonnet-4-6
compute:
  resource: gpu-host-1
"""


def test_load_resolves_compute_from_omicairn_config(tmp_path):
    _write_catalog(tmp_path)
    case = tmp_path / "research_cases" / "compute-case"
    case.mkdir(parents=True)
    data_dir = case / "data"
    data_dir.mkdir()
    (case / "RESEARCH.md").write_text(_RESEARCH_MD.format(data_dir=data_dir))
    (case / "omicairn_config.yaml").write_text(_CFG_WITH_COMPUTE)

    rm = research_md.load(case / "RESEARCH.md")
    assert rm.compute.id == "gpu-host-1"
    assert rm.compute.has_gpu is True
    assert rm.compute.gpu_memory_gb == 80.0


def test_load_defaults_to_local_when_no_compute_block(tmp_path):
    _write_catalog(tmp_path)
    case = tmp_path / "research_cases" / "no-compute"
    case.mkdir(parents=True)
    data_dir = case / "data"
    data_dir.mkdir()
    (case / "RESEARCH.md").write_text(_RESEARCH_MD.format(data_dir=data_dir))
    # No omicairn_config.yaml at all.
    rm = research_md.load(case / "RESEARCH.md")
    assert rm.compute.id == "default-local"
    assert rm.compute.has_gpu is False


def test_load_unknown_compute_resource_fails_loud(tmp_path):
    _write_catalog(tmp_path)
    case = tmp_path / "research_cases" / "bad-compute"
    case.mkdir(parents=True)
    data_dir = case / "data"
    data_dir.mkdir()
    (case / "RESEARCH.md").write_text(_RESEARCH_MD.format(data_dir=data_dir))
    (case / "omicairn_config.yaml").write_text("compute:\n  resource: typo-gpu\n")
    with pytest.raises(ValueError, match="not in the catalog"):
        research_md.load(case / "RESEARCH.md")


def test_load_compute_from_research_md_body_fallback(tmp_path):
    """A `compute:` block in RESEARCH.md (no omicairn_config.yaml) is honoured
    as the fallback, mirroring the budgets/bounds overlay philosophy."""
    _write_catalog(tmp_path)
    case = tmp_path / "research_cases" / "rmd-compute"
    case.mkdir(parents=True)
    data_dir = case / "data"
    data_dir.mkdir()
    rmd_text = _RESEARCH_MD.format(data_dir=data_dir).replace(
        "# body", "compute:\n  resource: gpu-host-1\n"
    )
    # Put the compute block inside the YAML front-matter region instead.
    rmd_text = (
        "---\n"
        "topic_slug: rmd-compute\n"
        "research_question: |\n  A test.\n"
        f"data:\n  location: {data_dir}\n"
        "compute:\n  resource: gpu-host-1\n"
        "---\n# body\n"
    )
    (case / "RESEARCH.md").write_text(rmd_text)
    rm = research_md.load(case / "RESEARCH.md")
    assert rm.compute.id == "gpu-host-1"


def test_load_omicairn_config_compute_wins_over_research_md(tmp_path):
    """When both RESEARCH.md and omicairn_config.yaml declare compute, the
    omicairn_config.yaml value wins (overlay precedence)."""
    # Two-entry catalog so we can tell which one won.
    _write_catalog(tmp_path)
    case = tmp_path / "research_cases" / "both-compute"
    case.mkdir(parents=True)
    data_dir = case / "data"
    data_dir.mkdir()
    rmd_text = (
        "---\n"
        "topic_slug: both-compute\n"
        "research_question: |\n  A test.\n"
        f"data:\n  location: {data_dir}\n"
        "compute:\n  resource: default-local\n"
        "---\n# body\n"
    )
    (case / "RESEARCH.md").write_text(rmd_text)
    (case / "omicairn_config.yaml").write_text("compute:\n  resource: gpu-host-1\n")
    rm = research_md.load(case / "RESEARCH.md")
    assert rm.compute.id == "gpu-host-1", "omicairn_config compute must win"


# ---------------------------------------------------------------------------
# Host GPU probe + reconciliation note
# ---------------------------------------------------------------------------
def test_detect_host_gpu_opt_out_returns_none(monkeypatch):
    monkeypatch.setenv(cr.ENV_NO_HOST_PROBE, "1")
    assert cr.detect_host_gpu() is None


def test_render_probe_note_when_declared_gpu_but_host_has_none(monkeypatch):
    """The decisive case: a GPU-declaring resource on a GPU-less host must
    emit the HOST CHECK re-scope warning (drives REVIEW away from a futile
    retry_execute loop — 2026-06-10 d5)."""
    monkeypatch.setattr(cr, "detect_host_gpu", lambda: False)
    gpu_res = cr.ComputeResource(
        id="gpu-host-1", kind="gpu", gpus=1, gpu_model="A100", gpu_memory_gb=80)
    md = gpu_res.render_md(probe_host=True)
    assert "HOST CHECK" in md
    assert "re-scope" in md and "retry_execute" in md


def test_render_no_probe_note_when_host_gpu_matches(monkeypatch):
    monkeypatch.setattr(cr, "detect_host_gpu", lambda: True)
    gpu_res = cr.ComputeResource(id="g", kind="gpu", gpus=1)
    assert "HOST CHECK" not in gpu_res.render_md(probe_host=True)


def test_render_no_probe_note_when_undetermined(monkeypatch):
    """An undetermined probe (None) must never contradict the declaration."""
    monkeypatch.setattr(cr, "detect_host_gpu", lambda: None)
    gpu_res = cr.ComputeResource(id="g", kind="gpu", gpus=1)
    assert "HOST CHECK" not in gpu_res.render_md(probe_host=True)


def test_render_probe_note_when_cpu_declared_but_gpu_present(monkeypatch):
    monkeypatch.setattr(cr, "detect_host_gpu", lambda: True)
    cpu_res = cr.ComputeResource(id="default-local", kind="cpu", gpus=0)
    md = cpu_res.render_md(probe_host=True)
    assert "HOST CHECK" in md and "runnable here" in md


def test_render_default_no_probe_is_unchanged(monkeypatch):
    """probe_host defaults to False — no host call, no note."""
    monkeypatch.setattr(cr, "detect_host_gpu",
                        lambda: (_ for _ in ()).throw(AssertionError("probed!")))
    gpu_res = cr.ComputeResource(id="g", kind="gpu", gpus=1)
    # Must not call detect_host_gpu when probe_host is False.
    assert "HOST CHECK" not in gpu_res.render_md()


# ---------------------------------------------------------------------------
# Shared-GPU-host fields: conda env, GPU regulation, usage policy
# ---------------------------------------------------------------------------
def test_coerce_parses_shared_host_fields():
    res = cr._coerce({
        "id": "gpu-node", "kind": "gpu", "gpus": 8, "gpus_per_run": 1,
        "conda_env": "omicairn-env", "conda_path": "/opt/miniconda3",
        "usage_policy": "shared — do not delete others' files",
    })
    assert res.gpus == 8 and res.gpus_per_run == 1
    assert res.conda_env == "omicairn-env"
    assert res.conda_path == "/opt/miniconda3"
    assert "do not delete" in res.usage_policy


def test_render_surfaces_conda_regulation_and_policy():
    res = cr.ComputeResource(
        id="gpu-node", kind="gpu", gpus=8, gpus_per_run=1,
        conda_env="omicairn-env", conda_path="/opt/miniconda3",
        usage_policy="never delete colleagues' files")
    md = res.render_md()
    assert "at most **1 of the 8**" in md            # regulation surfaced
    assert "conda activate omicairn-env" in md         # create-or-reuse
    assert "REUSE this same env" in md                # disk-saving rule
    assert "never delete colleagues' files" in md     # policy surfaced


def test_render_omits_shared_fields_when_unset():
    """A plain resource with no conda/regulation fields renders none of
    those lines (back-compat with thin catalog entries)."""
    res = cr.ComputeResource(id="g", kind="gpu", gpus=1)
    md = res.render_md()
    assert "conda" not in md.lower()
    assert "regulation" not in md.lower()
    assert "Usage policy" not in md




# ---------------------------------------------------------------------------
# Between-launch GPU scaling (Option B, 2026-06-17): gpus_per_run_max ceiling.
# Scales the EXECUTE arm's claimable GPU count UP toward a configured ceiling
# when the host has idle headroom — NOT live-job resize. Default-OFF.
# ---------------------------------------------------------------------------
def test_gpu_scaling_disabled_by_default():
    """No gpus_per_run_max → scaling OFF, effective ceiling == base cap,
    render byte-for-byte the static regulation line (full back-compat)."""
    res = cr.ComputeResource(id="g", kind="gpu", gpus=8, gpus_per_run=1)
    assert res.gpus_per_run_max == 0
    assert res.gpu_scaling_enabled is False
    assert res.effective_gpu_ceiling == 1
    md = res.render_md()
    assert "at most **1 of the 8**" in md
    assert "SCALE-WHEN-IDLE" not in md


def test_gpu_scaling_ceiling_clamped_to_physical_gpus():
    """gpus_per_run_max above the host's physical gpus is clamped to gpus."""
    res = cr.ComputeResource(
        id="g", kind="gpu", gpus=4, gpus_per_run=1, gpus_per_run_max=99)
    assert res.gpu_scaling_enabled is True
    assert res.effective_gpu_ceiling == 4  # min(gpus, max(1, 99))


def test_gpu_scaling_enabled_renders_scale_line():
    """A ceiling above the base cap turns ON scaling and renders the
    scale-when-idle line naming the base and the ceiling."""
    res = cr.ComputeResource(
        id="g", kind="gpu", gpus=8, gpus_per_run=1, gpus_per_run_max=4)
    assert res.gpu_scaling_enabled is True
    assert res.effective_gpu_ceiling == 4
    md = res.render_md()
    assert "SCALE-WHEN-IDLE" in md
    assert "baseline claim" in md and "1 of the 8" in md
    assert "scale UP to **4 GPUs**" in md
    assert "ACTUALLY idle" in md           # safety gate preserved
    assert "nvidia-smi" in md
    # The static (non-scaling) phrasing must NOT also appear.
    assert "use at most **1 of the 8**" not in md


def test_gpu_scaling_max_equal_or_below_base_is_off():
    """gpus_per_run_max <= base cap is a no-op (OFF) — never scales DOWN."""
    eq = cr.ComputeResource(
        id="g", kind="gpu", gpus=8, gpus_per_run=2, gpus_per_run_max=2)
    assert eq.gpu_scaling_enabled is False
    assert eq.effective_gpu_ceiling == 2
    below = cr.ComputeResource(
        id="g", kind="gpu", gpus=8, gpus_per_run=2, gpus_per_run_max=1)
    assert below.gpu_scaling_enabled is False
    assert below.effective_gpu_ceiling == 2  # base, not the lower ceiling


def test_gpu_scaling_off_for_cpu_resource():
    """A CPU resource never scales (no GPUs); effective ceiling is 0."""
    res = cr.ComputeResource(id="c", kind="cpu", gpus=0, gpus_per_run_max=4)
    assert res.gpu_scaling_enabled is False
    assert res.effective_gpu_ceiling == 0


def test_coerce_parses_gpus_per_run_max():
    """The new ceiling field is parsed from a catalog/config block as int."""
    res = cr._coerce({
        "id": "gpu-node", "kind": "gpu", "gpus": 8,
        "gpus_per_run": 1, "gpus_per_run_max": 3,
    })
    assert res.gpus_per_run == 1 and res.gpus_per_run_max == 3
    assert res.effective_gpu_ceiling == 3


# ---------------------------------------------------------------------------
# GPU SHARING / co-tenancy (raise-compute-envelope, 2026-06-19):
# max_runs_per_gpu lets >1 DISTINCT run co-reside on ONE physical GPU.
# Default 1 = OFF (whole-GPU semantics) — every existing host unchanged.
# ---------------------------------------------------------------------------
def test_gpu_sharing_disabled_by_default():
    """No max_runs_per_gpu → field is 1, sharing OFF, capacity == gpus, and
    the co-tenancy line is NOT rendered (whole-GPU back-compat)."""
    res = cr.ComputeResource(id="g", kind="gpu", gpus=8, gpus_per_run=1)
    assert res.max_runs_per_gpu == 1
    assert res.gpu_sharing_enabled is False
    assert res.run_capacity == 8          # whole-GPU: one run per GPU
    md = res.render_md()
    assert "GPU SHARING" not in md
    assert "co-tenancy" not in md


def test_gpu_sharing_off_for_non_gpu_resource():
    """A CPU/non-GPU resource never shares; capacity is 0 (GPU accounting
    does not bound CPU/theory runs) even if max_runs_per_gpu is set high."""
    res = cr.ComputeResource(id="c", kind="cpu", gpus=0, max_runs_per_gpu=4)
    assert res.gpu_sharing_enabled is False
    assert res.run_capacity == 0


def test_gpu_sharing_enabled_capacity_and_render():
    """max_runs_per_gpu > 1 turns sharing ON; capacity = gpus * max_runs_per_gpu
    and the render surfaces the co-tenancy permission + the VRAM safety check."""
    res = cr.ComputeResource(
        id="g", kind="gpu", gpus=8, gpu_memory_gb=40,
        gpus_per_run=1, max_runs_per_gpu=3)
    assert res.gpu_sharing_enabled is True
    assert res.run_capacity == 24          # 8 GPUs * 3 runs/GPU
    md = res.render_md()
    assert "GPU SHARING" in md
    assert "3 runs to share ONE GPU" in md
    assert "24 runs" in md
    assert "nvidia-smi" in md              # live VRAM gate preserved
    assert "permission, not" in md         # ceiling, not a mandate


def test_gpu_sharing_value_one_is_off():
    """max_runs_per_gpu == 1 is the explicit OFF value (== default)."""
    res = cr.ComputeResource(
        id="g", kind="gpu", gpus=8, gpus_per_run=1, max_runs_per_gpu=1)
    assert res.gpu_sharing_enabled is False
    assert res.run_capacity == 8


def test_coerce_parses_max_runs_per_gpu():
    """The new sharing field is parsed from a catalog/config block as int."""
    res = cr._coerce({
        "id": "gpu-node", "kind": "gpu", "gpus": 8,
        "gpus_per_run": 1, "max_runs_per_gpu": 3,
    })
    assert res.max_runs_per_gpu == 3
    assert res.gpu_sharing_enabled is True
    assert res.run_capacity == 24


def test_default_local_does_not_share_gpu():
    """The built-in CPU fallback keeps whole-GPU-OFF semantics (no sharing)."""
    assert cr.DEFAULT_LOCAL.max_runs_per_gpu == 1
    assert cr.DEFAULT_LOCAL.gpu_sharing_enabled is False
    assert cr.DEFAULT_LOCAL.run_capacity == 0




def test_render_surfaces_ssh_host_for_remote_gpu():
    """A resource with ssh_host must tell the agent to ssh its compute
    there (the orchestrator stays local). Without this the agent has no
    way to know WHERE the GPU lives."""
    res = cr.ComputeResource(
        id="remote-gpu", kind="gpu", gpus=8, gpus_per_run=1,
        ssh_host="gpu-host-1", conda_env="omicairn-env",
        conda_path="/opt/miniconda3")
    md = res.render_md()
    assert "ssh gpu-host-1" in md
    assert "REMOTE host" in md
    assert "omicairn-env" in md


def test_render_no_ssh_line_when_local():
    res = cr.ComputeResource(id="local-gpu", kind="gpu", gpus=1)
    assert "ssh " not in res.render_md().lower()


# ---------------------------------------------------------------------------
# Host-truth fields: scratch_root, hf_home, activation_recipe,
# gated_unavailable — promoted from tribal auto-memory into the catalog so
# they ride {{compute_resources}} deterministically. Round-trip: a YAML
# block carrying them parses into the dataclass and renders; a thin entry
# omitting them parses to "" and renders none of the lines (back-compat).
# ---------------------------------------------------------------------------
def test_host_truth_fields_parse_and_render_round_trip():
    block = """\
```yaml
id: rt-node
kind: gpu
gpus: 8
scratch_root: /scratch/<user>/<run-slug>/
hf_home: /scratch/hf_home
activation_recipe: >
  export PATH="/opt/miniconda3/bin:$PATH" && source activate omicairn-env.
  NO vLLM — use HF generate.
gated_unavailable: >
  No HF_TOKEN on host → gated assets (Llama-Guard) UNAVAILABLE; substitute
  an ungated equivalent or file RESOURCE_REQUEST.md.
```
"""
    res = cr.parse_catalog(block)["rt-node"]
    # parsed onto the dataclass
    assert res.scratch_root == "/scratch/<user>/<run-slug>/"
    assert res.hf_home == "/scratch/hf_home"
    assert "source activate omicairn-env" in res.activation_recipe
    assert "No HF_TOKEN" in res.gated_unavailable
    # rendered into the prompt block, deterministically
    md = res.render_md()
    assert "/scratch/<user>/<run-slug>/" in md
    assert "HF_HOME=/scratch/hf_home" in md
    assert "BY PATH" in md and "source activate omicairn-env" in md
    assert "NO vLLM" in md
    assert "Gated assets UNAVAILABLE" in md and "No HF_TOKEN" in md


def test_host_truth_fields_default_empty_and_render_omitted():
    """A thin entry without the host-truth fields parses to "" and renders
    none of the lines (additive: existing catalog behaviour unchanged)."""
    res = cr.parse_catalog("```yaml\nid: thin\nkind: gpu\ngpus: 1\n```\n")["thin"]
    assert res.scratch_root == "" and res.hf_home == ""
    assert res.activation_recipe == "" and res.gated_unavailable == ""
    md = res.render_md()
    assert "Scratch root" not in md
    assert "HF_HOME" not in md
    assert "Env activation" not in md
    assert "Gated assets" not in md




# ---------------------------------------------------------------------------
# datasets_root: the HOST-side shared dataset root for NON-hub /
# manually-prepared / re-used datasets — keyed by DATASET name (shared,
# prepare-once, reference-many), the remote analogue of {{shared_cache_dir}}.
# Mirrors the host-truth round-trip: a YAML block carrying it parses + renders;
# a thin entry omitting it parses to "" and renders no line (back-compat).
# ---------------------------------------------------------------------------
def test_datasets_root_parses_and_renders_round_trip():
    block = """\
```yaml
id: ds-node
kind: gpu
gpus: 8
datasets_root: /scratch/datasets
```
"""
    res = cr.parse_catalog(block)["ds-node"]
    assert res.datasets_root == "/scratch/datasets"
    md = res.render_md()
    # the rendered {{compute_resources}} block carries the datasets_root line
    assert "Shared dataset root" in md
    assert "/scratch/datasets" in md
    assert "<dataset-name>" in md          # keyed by DATASET, not run-slug
    assert "NOT per run-slug" in md        # the load-bearing convention
    # a trailing slash on the root must not produce a double slash in the
    # keyed-path example
    res2 = cr.parse_catalog(
        "```yaml\nid: t\nkind: gpu\ngpus: 1\ndatasets_root: /data/sets/\n```\n"
    )["t"]
    assert "/data/sets/<dataset-name>/" in res2.render_md()


def test_datasets_root_default_empty_and_render_omitted():
    """A thin entry without datasets_root parses to "" and renders no line
    (additive: existing catalog behaviour unchanged)."""
    res = cr.parse_catalog("```yaml\nid: thin\nkind: gpu\ngpus: 1\n```\n")["thin"]
    assert res.datasets_root == ""
    assert "Shared dataset root" not in res.render_md()




# ---------------------------------------------------------------------------
# SSH-host reachability probe (probe_ssh_host) — preflight diagnostic.
# Mirrors detect_host_gpu's True/False/None contract; never raises; never
# does real ssh in tests (subprocess.run is mocked).
# ---------------------------------------------------------------------------
class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_probe_ssh_empty_host_is_undetermined():
    """Empty / whitespace ssh_host → nothing to probe → (None, ...)."""
    reachable, detail = cr.probe_ssh_host("")
    assert reachable is None
    assert "nothing to probe" in detail
    assert cr.probe_ssh_host("   ")[0] is None


def test_probe_ssh_opt_out_returns_none(monkeypatch):
    """OMICAIRN_NO_SSH_PROBE set → (None, '...skip...') without touching ssh."""
    monkeypatch.setenv(cr.ENV_NO_SSH_PROBE, "1")
    monkeypatch.setattr(cr.shutil, "which", lambda _b: "/usr/bin/ssh")

    def should_not_run(*a, **kw):
        raise AssertionError("subprocess.run called despite opt-out")

    monkeypatch.setattr(cr.subprocess, "run", should_not_run)
    reachable, detail = cr.probe_ssh_host("gpu-host-1")
    assert reachable is None
    assert "skip" in detail.lower()


def test_probe_ssh_missing_binary_is_undetermined(monkeypatch):
    monkeypatch.setattr(cr.shutil, "which", lambda _b: None)
    reachable, detail = cr.probe_ssh_host("gpu-host-1")
    assert reachable is None
    assert "ssh binary" in detail


def test_probe_ssh_rc0_is_reachable(monkeypatch):
    """A mocked ssh rc=0 → (True, ...). Verifies BatchMode + ConnectTimeout
    + the host alias are on the command line (never hangs interactively)."""
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        return _FakeCompleted(returncode=0)

    monkeypatch.setattr(cr.shutil, "which", lambda _b: "/usr/bin/ssh")
    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    reachable, detail = cr.probe_ssh_host("gpu-host-1")
    assert reachable is True
    assert "gpu-host-1" in detail
    cmdline = " ".join(captured["cmd"])
    assert "BatchMode=yes" in cmdline
    assert "ConnectTimeout=" in cmdline
    assert "gpu-host-1" in captured["cmd"]
    assert captured["kw"].get("timeout")  # bounded wall time


def test_probe_ssh_rc_nonzero_is_unreachable_with_diagnosis(monkeypatch):
    """rc!=0 → (False, <last stderr line>) — the actionable diagnosis."""
    monkeypatch.setattr(cr.shutil, "which", lambda _b: "/usr/bin/ssh")
    monkeypatch.setattr(
        cr.subprocess, "run",
        lambda cmd, **kw: _FakeCompleted(
            returncode=255,
            stderr="ssh: Could not resolve hostname bogus: Name or service not known\n"),
    )
    reachable, detail = cr.probe_ssh_host("bogus")
    assert reachable is False
    assert "Could not resolve hostname" in detail


def test_probe_ssh_oserror_never_raises(monkeypatch):
    """An OSError from subprocess.run must be swallowed → (None, ...)."""
    monkeypatch.setattr(cr.shutil, "which", lambda _b: "/usr/bin/ssh")

    def boom(*a, **kw):
        raise OSError("exec format error")

    monkeypatch.setattr(cr.subprocess, "run", boom)
    reachable, detail = cr.probe_ssh_host("gpu-host-1")
    assert reachable is None
    assert "could not run" in detail


def test_probe_ssh_timeout_is_undetermined(monkeypatch):
    """A subprocess timeout is undetermined (None), not a hard failure."""
    monkeypatch.setattr(cr.shutil, "which", lambda _b: "/usr/bin/ssh")

    def slow(cmd, **kw):
        raise cr.subprocess.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout", 15))

    monkeypatch.setattr(cr.subprocess, "run", slow)
    reachable, detail = cr.probe_ssh_host("gpu-host-1")
    assert reachable is None
    assert "within" in detail


def test_probe_ssh_rc_nonzero_empty_stderr_still_unreachable(monkeypatch):
    """rc!=0 with no stderr/stdout → (False, 'ssh exit N') — still a clear
    unreachable signal, never an empty detail."""
    monkeypatch.setattr(cr.shutil, "which", lambda _b: "/usr/bin/ssh")
    monkeypatch.setattr(
        cr.subprocess, "run",
        lambda cmd, **kw: _FakeCompleted(returncode=1, stderr="", stdout=""))
    reachable, detail = cr.probe_ssh_host("h")
    assert reachable is False
    assert "ssh exit 1" in detail
