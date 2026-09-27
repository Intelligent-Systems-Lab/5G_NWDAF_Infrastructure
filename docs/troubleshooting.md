# Troubleshooting

## Provider commands are refused

Every real Vagrant or VirtualBox operation must see the approved Host device
namespace and pass the shared provider guard. This applies to `validate`,
`status`, and inventory commands as well as start and halt.

Do not bypass the guard with direct `vagrant`, `VBoxManage`, absolute binary
paths, or wrapper scripts. Move the command to the approved Host context. If
provider processes and Vagrant metadata disagree, stop and inspect the exact
inventory; do not destroy broad or guessed targets.

## A config output already exists

`config-create` refuses to overwrite `config/local/<name>`. Prefer a new
`NAME` when preserving the old selection. Use `FORCE=true` only after
confirming the old config is not active and its generated output is disposable.

## The selected config does not match runtime

Lifecycle and reset commands compare the selected config with staged Guest and
Compose identity. A mismatch usually means a different config was previously
activated.

Stop the current Guest and Host processes using their matching config, inspect
`make reset-show CONFIG_DIR=<matching-config>`, complete any required reset,
then start the new selection. Do not edit `manifest.yaml`, Guest
`active.sha256`, or Compose labels to suppress the mismatch.

## PyMTLF environment is missing

Dataset and formal seed preparation require
`ML/PyMTLF/.venv/bin/python`. Recreate the component environment:

```sh
uv sync --project ML/PyMTLF
```

The PyMTLF project requires Python 3.12 or newer.

## Dataset generation or validation fails

Run:

```sh
make dataset-show CONFIG_DIR=config/local/<name>
make dataset-validate CONFIG_DIR=config/local/<name>
```

Common causes are an incomplete official download, insufficient class samples
for an explicit quota, a scenario changed after the config was rendered, or a
previous generated directory belonging to a different seed or partition.
Select the intended config first. Preserve evidence still used by runs before
removing any local generated dataset.

## Component lock mismatch or dirty component

`experiment-validate` requires each selected submodule checkout to match
`components.lock.yaml` and be clean. Inspect:

```sh
git submodule status
git -C <component-path> status --short
git -C <component-path> rev-parse HEAD
```

Resolve the checkout through the repository's approved revision workflow. Do
not hide local component edits or change the lock merely to pass preflight.

## VM inventory is incomplete or unexpected

`vm-up`, status, startup, halt, and reset compare OS provider processes,
Vagrant metadata, and selected machine state. An omitted, duplicate, or
unexpected provider process fails closed.

Use the approved Host context to inspect exact selected state. If cleanup or VM
destruction is actually required, determine exact targets and obtain separate
authorization; normal lifecycle commands should not infer accident cleanup.

## Host ports or resources are unavailable

`experiment-validate` reports the selected Guest/container CPU and memory
budget, free storage, swap, Host bind address, and published-port conflicts.
Close the actual conflicting process or select adequate capacity. Do not lower
resource or safety thresholds merely to make preflight pass.

The Host ML address `192.168.57.1` normally appears after the provider creates
the Host-only network. A warning before VM creation can therefore become ready
after `vm-up`; `ml-start` still requires the address to exist.

## GPU admission fails

Check, in order:

1. `nvidia-smi` reports a GPU with enough free memory;
2. `nvidia-ctk cdi list` contains `nvidia.com/gpu=all`;
3. Docker reports the `nvidia` runtime;
4. the selected manifest says `mlDevicePolicy: gpu`;
5. the local PyMTLF image passes the CUDA probe.

Branch containers intentionally remain CPU-owned. Root and all selected Leaves
must expose CUDA for a GPU run. The complete experiment runner rejects a CPU
config.

## Experiment startup finds active processes

The complete runner requires a clean process start. Inspect:

```sh
make experiment-status CONFIG_DIR=config/local/<name>
```

If the state belongs to the same selection, stop it with
`make experiment-stop CONFIG_DIR=...`. If it belongs to another selection,
use that config for stop and reset. Do not start a second experiment over active
Guest units or project containers.

## Guest service or registration readiness fails

Use bounded logs for the owning domain:

```sh
make logs SOURCE=vm VM=core SERVICE=nwdaf-root FOLLOW=false
make logs SOURCE=vm VM=path-a SERVICE='nwdaf-*' FOLLOW=false
```

Shell globs are accepted by the service filter. Issue separate commands when
selecting unrelated names. Also inspect `make services-status` and the
provisioning identity. A source update without rebuilding the Guest binary can
produce a revision mismatch.

## PyMTLF container readiness fails

Inspect:

```sh
make ml-status CONFIG_DIR=config/local/<name>
make logs SOURCE=ml SERVICE=pymtlf-root FOLLOW=false
```

The selected service inventory, config set, image revision, device assignment,
volume, and health must all agree. Unexpected project containers or volumes
block lifecycle and reset rather than being silently adopted.

## Training completed but collection failed

If `run.json` is `collection-pending` or `collection-failed` and contains a
completed terminal checkpoint, retain the selected config, PyMTLF image, and
Root volume, then run:

```sh
make fl-experiment-collect \
  CONFIG_DIR=config/local/<name> \
  RUN_NAME=<same-run-name>
```

This retries collection and held-out evaluation without training again. If the
run failed before a complete final checkpoint, preserve its diagnostics and use
a new run name after fixing the cause.

## Reset is refused

Reset requires:

- the selected VMs running;
- no active Guest experiment units;
- no running selected ML containers;
- selected and active config identity matching;
- exact selected project container and volume inventory;
- the exact scenario name in `RESET_CONFIRM`.

Run `make reset-show CONFIG_DIR=...` first. A refusal is a scope or lifecycle
problem to resolve, not a reason to broaden deletion or bypass confirmation.
