"""Compute-resource catalog parsing + per-case resolution.

A OmiCairn case declares, in its ``omicairn_config.yaml``, an optional
``compute:`` block naming a resource (by ``id``) plus optional inline
overrides::

    compute:
      resource: gpu-host-1
      # optional inline overrides:
      # max_runtime_hours: 12

The named resource is resolved against a ``COMPUTATION_RESOURCES.md``
catalog found by walking up from the case directory. The catalog is a
human-readable markdown file whose resources live in fenced ```yaml
blocks (one block per resource); prose around the blocks is ignored.

The resolved resource is exposed on ``ResearchMd.compute`` and rendered
into the EXECUTE/PLAN prompts via the ``{{compute_resources}}`` token, so
the agent knows the real hardware envelope (CPU/RAM/GPU) instead of
assuming "no GPU locally".

Design notes
------------
- Domain-agnostic (design principles): no hardcoded research-domain terms. The
  only fixed strings are hardware field names and the built-in
  ``default-local`` CPU fallback used when a case names no resource and no
  catalog is found.
- Fail loud on a *named-but-unknown* resource: a typo must not silently
  degrade to "no compute info". A case with no ``compute:`` block at all
  falls back to ``default-local`` (historical behaviour).
"""
from __future__ import annotations

import dataclasses
import os
import pathlib
import re
import shutil
import subprocess
from typing import Any

import yaml

CATALOG_FILENAME = "COMPUTATION_RESOURCES.md"

# Opt-out for the actual-host GPU probe (set to 1/true to skip detection
# and trust the declared resource verbatim — e.g. a scheduler that knows
# the GPU is attached even when a probe transiently fails).
ENV_NO_HOST_PROBE: str = "OMICAIRN_NO_HOST_GPU_PROBE"

# Opt-out for the SSH-host reachability probe (sibling of ENV_NO_HOST_PROBE):
# set to 1/true to skip probing a remote compute host's ssh alias and treat
# reachability as undetermined (None). Useful when running offline or behind
# a jump-host the probe cannot traverse non-interactively.
ENV_NO_SSH_PROBE: str = "OMICAIRN_NO_SSH_PROBE"

# ConnectTimeout passed to ssh (per-connection) + the overall subprocess
# timeout (a hair longer so the subprocess timeout never fires before ssh's
# own clean failure). Short on purpose — a preflight diagnostic must not hang.
_SSH_CONNECT_TIMEOUT_S: int = 10
_SSH_SUBPROCESS_TIMEOUT_S: int = 15
_TRUTHY = ("1", "true", "yes", "on")


def detect_host_gpu() -> bool | None:
    """Best-effort probe of whether THIS host has a usable GPU.

    Returns True (GPU present), False (definitively none), or None
    (could not determine — be conservative and do NOT contradict the
    declared resource). Probe order: `nvidia-smi -L` (authoritative when
    the binary exists), else `torch.cuda.is_available()`. Never raises.

    Opt out via OMICAIRN_NO_HOST_GPU_PROBE=1 (returns None = trust the
    declaration). This is provider-agnostic plumbing; it inspects the
    machine, never research content."""
    if os.environ.get(ENV_NO_HOST_PROBE, "").strip().lower() in _TRUTHY:
        return None
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run(
                [smi, "-L"], capture_output=True, timeout=10, text=True,
            )
            if out.returncode == 0:
                # `nvidia-smi -L` prints one "GPU 0: ..." line per device;
                # empty stdout with rc 0 means no devices.
                return any(line.strip().startswith("GPU ")
                           for line in out.stdout.splitlines())
        except (OSError, subprocess.SubprocessError):
            pass  # fall through to torch
    try:
        import torch  # type: ignore
        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 — torch missing/broken → undetermined
        return None


def probe_ssh_host(ssh_host: str) -> tuple[bool | None, str]:
    """Best-effort probe of whether a remote compute host's ssh alias is
    reachable RIGHT NOW (non-interactively), so a missing/rotated alias
    surfaces at preflight instead of failing silently mid-EXECUTE.

    Mirrors detect_host_gpu's contract — inspects the machine/network, never
    research content, and NEVER raises:

    - ``(True, detail)``  — ``ssh -o BatchMode=yes <host> true`` returned 0.
    - ``(False, detail)`` — ssh definitively failed (host not in ssh config,
      connection refused, auth/permission denied, or connect timeout). A GPU
      run targeting this host WILL fail at EXECUTE.
    - ``(None, detail)``  — cannot determine: empty ``ssh_host`` (nothing to
      probe), the ``ssh`` binary is missing, the probe was opted out via
      ``OMICAIRN_NO_SSH_PROBE``, or the subprocess itself timed out. Be
      conservative — an undetermined probe is NOT a failure signal.

    ``BatchMode=yes`` guarantees ssh never hangs on an interactive password
    prompt; a short ConnectTimeout + an overall subprocess timeout bound the
    wall time. Takes the alias as an argument — no hardcoded hostname."""
    if not ssh_host or not ssh_host.strip():
        return None, "no ssh_host set — nothing to probe (local compute)"
    if os.environ.get(ENV_NO_SSH_PROBE, "").strip().lower() in _TRUTHY:
        return None, f"probe skipped ({ENV_NO_SSH_PROBE} set)"
    host = ssh_host.strip()
    ssh = shutil.which("ssh")
    if not ssh:
        return None, "ssh binary not found on PATH — cannot probe"
    try:
        out = subprocess.run(
            [
                ssh,
                "-o", "BatchMode=yes",
                "-o", f"ConnectTimeout={_SSH_CONNECT_TIMEOUT_S}",
                host, "true",
            ],
            capture_output=True,
            timeout=_SSH_SUBPROCESS_TIMEOUT_S,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return None, (
            f"ssh probe of {host!r} did not return within "
            f"{_SSH_SUBPROCESS_TIMEOUT_S}s — undetermined"
        )
    except (OSError, subprocess.SubprocessError) as exc:  # never raises
        return None, f"ssh probe of {host!r} could not run: {exc}"
    if out.returncode == 0:
        return True, f"ssh {host} OK"
    detail = (out.stderr or out.stdout or "").strip()
    detail = detail.splitlines()[-1] if detail else f"ssh exit {out.returncode}"
    return False, detail


# Fenced ```yaml blocks in the catalog markdown. Anchored to line starts
# (MULTILINE) and tolerant of leading indentation so an *indented* fence
# inside a prose/list snippet pairs with its OWN closing fence instead of
# swallowing later blocks. DOTALL lets a block span lines; non-greedy so
# each opening fence binds to its nearest closing fence.
_YAML_BLOCK_RE = re.compile(
    r"^[ \t]*```yaml[ \t]*\n(.*?)\n[ \t]*```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)

# Recognised resource fields and their coercion. id is required; every
# other field has a conservative default so a thin catalog entry parses.
_INT_FIELDS = ("gpus", "cpus", "gpus_per_run", "gpus_per_run_max",
               "max_runs_per_gpu")
_NUM_FIELDS = ("gpu_memory_gb", "memory_gb", "storage_gb", "max_runtime_hours")
_STR_FIELDS = (
    "display_name", "kind", "gpu_model", "interconnect", "notes",
    "conda_env", "conda_path", "usage_policy", "ssh_host",
    "scratch_root", "hf_home", "activation_recipe", "gated_unavailable",
    "datasets_root",
)


@dataclasses.dataclass
class ComputeResource:
    """One named hardware envelope from the catalog (or the built-in
    fallback). Only ``id`` is guaranteed meaningful; unspecified numeric
    fields are 0 and string fields empty."""

    id: str
    display_name: str = ""
    kind: str = "cpu"                 # gpu | cpu | cluster
    gpus: int = 0
    gpu_model: str = ""
    gpu_memory_gb: float = 0.0
    cpus: int = 0
    memory_gb: float = 0.0
    storage_gb: float = 0.0
    interconnect: str = ""
    max_runtime_hours: float = 0.0
    notes: str = ""
    # Optional execution-environment + regulation knobs (for real shared
    # GPU hosts). conda_env: a named conda env the run must create-or-reuse
    # (reuse across runs saves disk when many share one stack). conda_path:
    # the conda install prefix to activate from. gpus_per_run: regulation
    # cap — how many of `gpus` a single run may claim (leave the rest for
    # colleagues on a shared host); 0 = unset. usage_policy: free-text rules
    # surfaced to the agent (shared host, never delete others' files/envs).
    conda_env: str = ""
    conda_path: str = ""
    gpus_per_run: int = 0
    # OPTIONAL between-launch GPU scaling CEILING (Option B, 2026-06-17). The
    # base `gpus_per_run` is the conservative cap a run claims by default; when
    # `gpus_per_run_max > gpus_per_run`, an EXECUTE arm MAY scale UP toward this
    # ceiling (bounded by the host's physical `gpus`) WHEN the host has idle
    # headroom — i.e. dynamic-between-units-of-work scaling, NOT live-job
    # resize (a detached torch process with fixed CUDA_VISIBLE_DEVICES cannot
    # absorb new GPUs; that path is infeasible without elastic-training code,
    # see DYNAMIC_GPU_RESIZE_DESIGN). 0 (or <= gpus_per_run) = OFF: behaviour is
    # byte-for-byte the static cap. The agent's live `nvidia-smi` idle-check at
    # launch is the safety gate — the ceiling only says "you MAY use up to N",
    # the agent still claims ONLY actually-idle devices (no OOM collision, full
    # shared-host etiquette preserved). Mirrors the existing GPU wall/inactivity
    # FLOOR pattern (orchestrator `_resolve_*`): an effective value resolved
    # from a base + a bound, never mutating the declaration.
    gpus_per_run_max: int = 0
    # OPTIONAL GPU-SHARING / co-tenancy permission (2026-06-19, raise-compute-
    # envelope). How many DISTINCT runs MAY co-reside on a SINGLE physical GPU.
    # 1 (or 0) = OFF, the default → today's whole-GPU semantics (a run that
    # claims a GPU has it to itself), so every existing host is byte-for-byte
    # unchanged. > 1 = the host PERMITS up to that many runs to share one GPU,
    # appropriate when each run's model is small relative to the card's VRAM
    # (e.g. a ≤7B VLM at ~14-16 GB on a 40 GB A100 → 2 fit with headroom). This
    # is ORTHOGONAL to `gpus_per_run` (how many GPUs ONE run claims) and to
    # `gpus_per_run_max` (between-launch scale-UP of one run): sharing is about
    # how many runs co-tenant ONE GPU, scaling is about how many GPUs one run
    # spans. The host's run-level co-scheduling capacity is therefore
    # `gpus * max_runs_per_gpu` (e.g. 8 * 3 = 24). Like the GPU wall/inactivity
    # FLOORs and the scaling ceiling, this is a CEILING/PERMISSION, never a
    # mandate — the agent's live `nvidia-smi` VRAM check at launch still decides
    # whether a card actually has room for another tenant (no OOM collision).
    max_runs_per_gpu: int = 1
    usage_policy: str = ""
    # SSH alias of the host to run compute ON, when it differs from the
    # box the OmiCairn orchestrator runs on (a remote GPU node). Empty = run
    # locally. When set, EXECUTE ssh-es its compute there (activates
    # conda_env, claims gpus_per_run idle GPUs) and copies results back.
    ssh_host: str = ""
    # Host-truth that previously lived ONLY in tribal auto-memory, promoted
    # into the catalog so it rides {{compute_resources}} deterministically.
    # scratch_root: remote scratch/working root convention (one dir per run);
    # where EXECUTE writes code + caches activations/weights + harvests
    # results, plus the cleanup convention. hf_home: shared HuggingFace cache
    # path (download-once across runs). activation_recipe: how to activate the
    # env BY PATH (not `conda activate <name>` — the real host quirk) plus any
    # runtime caveat (e.g. no vLLM → use HF generate). gated_unavailable:
    # gated assets unreachable due to missing creds (e.g. no HF_TOKEN), with
    # the ungated substitution / RESOURCE_REQUEST guidance.
    scratch_root: str = ""
    hf_home: str = ""
    activation_recipe: str = ""
    gated_unavailable: str = ""
    # datasets_root: the HOST-side shared dataset root for NON-hub /
    # manually-prepared / re-used-derived datasets — keyed by DATASET name
    # (``datasets_root/<dataset-name>/``), shared across runs (prepare once,
    # reference many). This is the remote-host analogue of the orchestrator-
    # local ``{{shared_cache_dir}}`` (which already covers on-the-way
    # accession fetches under the project tree). The boundary is deliberate
    # and non-overlapping: HF-hub models/datasets live under ``hf_home``;
    # per-run ephemeral scratch lives under ``scratch_root``; only persistent,
    # non-hub, cross-run-shared datasets live under ``datasets_root``. Empty =
    # the host has no dedicated shared dataset root (use hf_home / the local
    # shared cache as applicable).
    datasets_root: str = ""

    @property
    def has_gpu(self) -> bool:
        return self.gpus > 0

    @property
    def gpu_scaling_enabled(self) -> bool:
        """True when between-launch GPU scaling is ON for this resource — i.e.
        a GPU host whose `gpus_per_run_max` ceiling is strictly above the base
        `gpus_per_run` cap. OFF (False) by default and for any non-GPU resource,
        so existing cases are byte-for-byte unchanged."""
        return (
            self.has_gpu
            and self.gpus_per_run_max > 0
            and self.gpus_per_run_max > self.gpus_per_run
        )

    @property
    def gpu_sharing_enabled(self) -> bool:
        """True when GPU co-tenancy is ON for this resource — i.e. a GPU host
        that PERMITS more than one run per physical GPU (`max_runs_per_gpu >
        1`). OFF (False) by default and for any non-GPU resource, so existing
        hosts keep whole-GPU semantics byte-for-byte."""
        return self.has_gpu and self.max_runs_per_gpu > 1

    @property
    def run_capacity(self) -> int:
        """How many DISTINCT runs MAY co-schedule on this resource by GPU
        accounting — `gpus * max_runs_per_gpu` when sharing is ON, else the
        whole-GPU count `gpus`. Returns 0 for a non-GPU resource (GPU
        accounting does not bound CPU/theory runs). This is a CEILING the
        operator/loop uses to size the concurrent-run target; it is NOT the
        whole story (the shared Claude API and per-phase heaviness also bind)
        and it is never enforced in code — sharing is a permission."""
        if not self.has_gpu:
            return 0
        per_gpu = self.max_runs_per_gpu if self.max_runs_per_gpu > 1 else 1
        return self.gpus * per_gpu

    @property
    def effective_gpu_ceiling(self) -> int:
        """The MAX number of GPUs an EXECUTE arm MAY claim on this resource.

        - Scaling OFF (`gpus_per_run_max` unset/<= base): the static
          `gpus_per_run` cap, unchanged.
        - Scaling ON: `min(gpus, max(gpus_per_run, gpus_per_run_max))` — scale
          up toward the configured ceiling but NEVER above the host's physical
          GPU count. This is only a CEILING ("you MAY use up to N idle GPUs");
          the agent's live `nvidia-smi` check still decides how many are
          actually idle and claimable at launch.

        Returns 0 for a non-GPU resource (no GPUs to cap)."""
        if not self.has_gpu:
            return 0
        base = self.gpus_per_run if self.gpus_per_run > 0 else self.gpus
        if not self.gpu_scaling_enabled:
            return base
        return min(self.gpus, max(base, self.gpus_per_run_max))

    def render_md(self, probe_host: bool = False) -> str:
        """Render as the ``{{compute_resources}}`` prompt block — a short,
        unambiguous statement of the hardware the agent may assume.

        When ``probe_host`` is True, the actual host is probed for a GPU
        (``detect_host_gpu``) and a reconciliation note is appended if the
        DECLARED resource claims a GPU the host does not actually have (or,
        more rarely, a GPU is present that the declaration omits). The
        declaration is NOT mutated — the note tells the agent to expect a
        RESOURCE_REQUEST / re-scope rather than silently trusting an
        unsatisfiable envelope. An undetermined probe (None) appends
        nothing, so a transient probe failure never contradicts a
        legitimate declaration."""
        lines = [
            f"- **Resource:** {self.display_name or self.id} (`{self.id}`)",
            f"- **Class:** {self.kind}",
        ]
        if self.has_gpu:
            gpu = self.gpu_model or "GPU"
            vram = (f", {self.gpu_memory_gb:g} GB VRAM each"
                    if self.gpu_memory_gb else "")
            lines.append(
                f"- **GPU:** AVAILABLE — {self.gpus}× {gpu}{vram}. "
                f"GPU-bound methods are in scope; size them to this VRAM."
            )
        else:
            lines.append(
                "- **GPU:** NOT available on this resource. A GPU-bound "
                "analysis must be requested via `RESOURCE_REQUEST.md` — do "
                "NOT silently subsample to fit a CPU budget."
            )
        if self.cpus:
            lines.append(f"- **CPUs:** {self.cpus} logical cores")
        if self.memory_gb:
            lines.append(f"- **Host RAM:** {self.memory_gb:g} GB")
        if self.storage_gb:
            lines.append(f"- **Scratch storage:** {self.storage_gb:g} GB")
        if self.interconnect:
            lines.append(f"- **Interconnect:** {self.interconnect}")
        if self.max_runtime_hours:
            lines.append(
                f"- **Max runtime:** ~{self.max_runtime_hours:g} h "
                f"(soft hardware ceiling)"
            )
        if self.gpus_per_run and self.gpus:
            if self.gpu_scaling_enabled:
                ceiling = self.effective_gpu_ceiling
                lines.append(
                    f"- **GPU regulation (SHARED HOST, SCALE-WHEN-IDLE):** the "
                    f"baseline claim for this run is **{self.gpus_per_run} of "
                    f"the {self.gpus}** GPUs, but you MAY scale UP to **{ceiling} "
                    f"GPUs** WHEN the host has idle headroom. Run `nvidia-smi` "
                    f"first; claim ONLY device id(s) that are ACTUALLY idle, up "
                    f"to {ceiling} — if fewer than {ceiling} are idle, take only "
                    f"the idle ones (never collide with a busy GPU). Set "
                    f"`CUDA_VISIBLE_DEVICES` to those id(s). Colleagues share "
                    f"this host — the ceiling is a permission, not a mandate."
                )
            else:
                lines.append(
                    f"- **GPU regulation (SHARED HOST):** use at most "
                    f"**{self.gpus_per_run} of the {self.gpus}** GPUs for this "
                    f"run (e.g. set `CUDA_VISIBLE_DEVICES` to {self.gpus_per_run} "
                    f"free device id(s)). Check `nvidia-smi` first and pick "
                    f"idle GPUs — colleagues share this host."
                )
        if self.gpu_sharing_enabled:
            lines.append(
                f"- **GPU SHARING (co-tenancy ALLOWED):** this host PERMITS up "
                f"to **{self.max_runs_per_gpu} runs to share ONE GPU** "
                f"(~{self.run_capacity} runs across the {self.gpus} GPUs). A "
                f"small model (a ≤7B VLM is ~14-16 GB in bf16/fp16) leaves "
                f"ample headroom on a {self.gpu_memory_gb:g} GB card for "
                f"co-tenants. You MAY therefore pick a device that ALREADY has "
                f"another run on it, PROVIDED `nvidia-smi` shows enough FREE "
                f"VRAM for your model plus its activations/KV-cache "
                f"(rule of thumb: only co-reside if the target device has "
                f">= your model's footprint + ~20% spare). Prefer the device "
                f"with the LOWEST current memory use; expect co-tenants and "
                f"never assume a GPU is yours alone. This is a permission, not "
                f"a mandate — if an empty GPU is free, take it."
            )
        if self.ssh_host:
            lines.append(
                f"- **Run compute ON host (SSH):** this GPU lives on a "
                f"REMOTE host, not the box you are on. Run the heavy "
                f"analysis there via `ssh {self.ssh_host} '<cmd>'` (and "
                f"`scp`/`rsync` results back into the stage dir). The "
                f"OmiCairn orchestrator stays local; only the compute goes to "
                f"`{self.ssh_host}`."
            )
        if self.conda_env:
            act = (f"source {self.conda_path}/bin/activate && "
                   if self.conda_path else "")
            host = f"ssh {self.ssh_host} " if self.ssh_host else ""
            lines.append(
                f"- **Conda env (create-or-reuse):** run inside the conda "
                f"env `{self.conda_env}` (on the {self.ssh_host or 'local'} "
                f"host). {act}`conda activate {self.conda_env}` if it "
                f"exists; otherwise create it once and install deps into "
                f"it. REUSE this same env across runs in this research "
                f"round — do NOT make a new env per run (saves disk)."
            )
        if self.activation_recipe:
            lines.append(
                f"- **Env activation (BY PATH, not `conda activate`):** "
                f"{self.activation_recipe.strip()}"
            )
        if self.scratch_root:
            lines.append(
                f"- **Scratch root (per-run workdir):** write code, cache "
                f"activations/weights, and harvest results under "
                f"`{self.scratch_root.strip()}`."
            )
        if self.hf_home:
            lines.append(
                f"- **Shared HuggingFace cache (download-once):** set "
                f"`HF_HOME={self.hf_home.strip()}` so big models/datasets "
                f"download ONCE and every later run reuses them — do NOT use "
                f"a per-run cache."
            )
        if self.datasets_root:
            lines.append(
                f"- **Shared dataset root (prepare-once, reference-many):** "
                f"NON-hub / manually-prepared / re-used datasets live under "
                f"`{self.datasets_root.strip()}`, keyed by DATASET name "
                f"(`{self.datasets_root.strip().rstrip('/')}/<dataset-name>/`) — "
                f"NOT per run-slug. CHECK there before fetching/preparing a "
                f"dataset, and REFERENCE the shared copy instead of "
                f"re-downloading it per run. (HF-hub assets still go via "
                f"`HF_HOME`; per-run ephemeral artifacts stay under the "
                f"scratch root.)"
            )
        if self.gated_unavailable:
            lines.append(
                f"- **Gated assets UNAVAILABLE:** {self.gated_unavailable.strip()}"
            )
        if self.usage_policy:
            lines.append(f"- **Usage policy:** {self.usage_policy.strip()}")
        if self.notes:
            lines.append(f"- **Notes:** {self.notes.strip()}")
        if probe_host:
            note = self._host_reconciliation_note()
            if note:
                lines.append(note)
        return "\n".join(lines)

    def _host_reconciliation_note(self) -> str:
        """Compare the DECLARED resource against an actual-host GPU probe;
        return a reconciliation note string when they disagree, else ""."""
        host_gpu = detect_host_gpu()
        if host_gpu is None:
            return ""  # undetermined — never contradict the declaration
        if self.has_gpu and not host_gpu:
            return (
                "- **⚠ HOST CHECK:** the declared resource advertises a GPU, "
                "but THIS host has none (probe found no CUDA device). "
                "GPU-bound analyses cannot run here — EXECUTE should file "
                "`RESOURCE_REQUEST.md` (not simulate/subsample), and REVIEW "
                "should re-scope rather than loop `retry_execute`. Run on the "
                "declared GPU host to complete the deferred arms."
            )
        if (not self.has_gpu) and host_gpu:
            return (
                "- **HOST CHECK:** a GPU was detected on this host that the "
                "declared CPU-only resource omits; GPU-bound work is in fact "
                "runnable here if the analysis benefits."
            )
        return ""


# The implicit fallback when a case names no resource and no catalog entry
# applies: CPU-only, GPU must be requested (historical OmiCairn assumption).
DEFAULT_LOCAL = ComputeResource(
    id="default-local",
    display_name="Local CPU (no GPU)",
    kind="cpu",
    gpus=0,
    cpus=8,
    memory_gb=32.0,
    storage_gb=200.0,
    interconnect="single-node",
    max_runtime_hours=6.0,
    notes=(
        "Default fallback. CPU-only — a GPU is NOT available and any "
        "GPU-bound analysis MUST be requested via RESOURCE_REQUEST.md "
        "rather than silently degraded."
    ),
)


def _coerce(entry: dict[str, Any]) -> ComputeResource:
    """Build a ComputeResource from a parsed catalog dict, coercing field
    types and ignoring unknown keys (forward-compat)."""
    rid = entry.get("id")
    if not rid or not str(rid).strip():
        raise ValueError("compute resource entry is missing required `id`")
    kwargs: dict[str, Any] = {"id": str(rid).strip()}
    for f in _STR_FIELDS:
        if entry.get(f) is not None:
            kwargs[f] = str(entry[f])
    for f in _INT_FIELDS:
        if entry.get(f) is not None:
            kwargs[f] = int(entry[f])
    for f in _NUM_FIELDS:
        if entry.get(f) is not None:
            kwargs[f] = float(entry[f])
    return ComputeResource(**kwargs)


def parse_catalog(text: str) -> dict[str, ComputeResource]:
    """Parse all fenced ```yaml resource blocks from catalog markdown into
    an ``{id: ComputeResource}`` map. Blocks that don't parse as a mapping
    with an ``id`` are skipped (the file also documents the schema in
    example blocks — but every real entry carries an ``id``)."""
    out: dict[str, ComputeResource] = {}
    for block in _YAML_BLOCK_RE.findall(text):
        try:
            loaded = yaml.safe_load(block)
        except yaml.YAMLError:
            continue
        if not isinstance(loaded, dict) or not loaded.get("id"):
            continue
        res = _coerce(loaded)
        out[res.id] = res
    return out


def find_catalog(start: pathlib.Path) -> pathlib.Path | None:
    """Walk up from ``start`` (a file or dir) to find the nearest
    ``COMPUTATION_RESOURCES.md``. Returns None if none is found before the
    filesystem root."""
    cur = start if start.is_dir() else start.parent
    cur = cur.resolve()
    for d in (cur, *cur.parents):
        cand = d / CATALOG_FILENAME
        if cand.is_file():
            return cand
    return None


def load_catalog(start: pathlib.Path) -> dict[str, ComputeResource]:
    """Find + parse the nearest catalog from ``start``. Empty dict when no
    catalog file exists (callers fall back to DEFAULT_LOCAL)."""
    cat = find_catalog(start)
    if cat is None:
        return {}
    return parse_catalog(cat.read_text())


def resolve(
    compute_block: dict[str, Any] | None,
    case_dir: pathlib.Path,
) -> ComputeResource:
    """Resolve a case's ``compute:`` block to a ComputeResource.

    - No block (None / empty) → ``DEFAULT_LOCAL`` (historical CPU-only).
    - ``resource: <id>`` → look the id up in the nearest catalog; raise
      ValueError if the catalog lacks that id (a typo must fail loud).
    - Inline keys (other than ``resource``) override catalog fields, so a
      case can tweak e.g. ``max_runtime_hours`` without a new catalog
      entry. An inline block with NO ``resource`` builds an ad-hoc
      resource straight from the inline fields (must still carry an ``id``).
    """
    if not compute_block:
        return DEFAULT_LOCAL
    if not isinstance(compute_block, dict):
        raise ValueError(
            f"`compute:` must be a mapping; got {type(compute_block).__name__}"
        )

    name = compute_block.get("resource")
    inline = {k: v for k, v in compute_block.items() if k != "resource"}

    if name:
        catalog = load_catalog(case_dir)
        if not catalog:
            raise ValueError(
                f"compute.resource={name!r} was requested but no "
                f"{CATALOG_FILENAME} catalog was found at or above "
                f"{case_dir}. Add the catalog or remove the compute block."
            )
        base = catalog.get(str(name))
        if base is None:
            raise ValueError(
                f"compute.resource={name!r} is not in the catalog "
                f"({CATALOG_FILENAME}). Known ids: "
                f"{sorted(catalog)}"
            )
        if not inline:
            return base
        # Overlay inline overrides onto the catalog entry.
        merged = dataclasses.asdict(base)
        merged.update(inline)
        return _coerce(merged)

    # Inline-only ad-hoc resource (no catalog reference).
    if "id" not in inline:
        inline = {"id": "inline", **inline}
    return _coerce(inline)
