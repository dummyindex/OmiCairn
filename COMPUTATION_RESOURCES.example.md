# Computation Resources

Copy this file to `COMPUTATION_RESOURCES.md` at the repository root and edit
it to describe the machines OmiCairn may use. A research case selects one in
its `omicairn_config.yaml`:

   ```yaml
   compute:
     resource: gpu-node
   ```

When a case names no resource, or no catalog is found, OmiCairn uses the
local machine. Field reference: `docs/references/computation-resources-schema.md`.

## default-local

```yaml
id: default-local
display_name: "Local workstation (CPU only)"
kind: cpu
gpus: 0
cpus: 8
memory_gb: 32
max_runtime_hours: 6
notes: CPU-only fallback for small analyses.
```

## gpu-node

```yaml
id: gpu-node
display_name: "Remote GPU server"
kind: gpu
gpus: 1
gpu_model: "NVIDIA A100 80GB"
gpu_memory_gb: 80
cpus: 32
memory_gb: 256
max_runtime_hours: 24
ssh_host: my-gpu-server          # an alias from ~/.ssh/config
conda_path: /opt/miniconda3
conda_env: omicairn-env
scratch_root: /scratch/<user>/<run-slug>/
notes: >
  Shared GPU server. Check reachability with
  `omicairn preflight --check-remote my-gpu-server`.
```
