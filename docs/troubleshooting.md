# Troubleshooting

Start with a read-only diagnosis:

```sh
make experiment-status CONFIG_DIR=config/local/my-experiment
make experiment-validate CONFIG_DIR=config/local/my-experiment
```

Then narrow logs with `scripts/host/logs.sh`; see
[Operations](operations.md#4-observe-progress).

## Source or submodule mismatch

Run:

```sh
git submodule status --recursive
```

A leading `+` means the installed checkout differs from the parent gitlink; a
leading `-` means it has not been initialized. Compare an unexpected revision
with `components.lock.yaml`. Do not repair it with `--remote`; first determine
whether the parent pin or local checkout is intended.

## Guest source and installed binary mismatch

A clean Host submodule checkout and a current source tree below
`/opt/5g-nwdaf-infrastructure/source` do not prove that an existing VM is
executing a binary built from that source. `vm-up` does not reprovision an
existing VM, and `services-start` synchronizes helpers, config, and dataset but
does not rebuild guest-owned components.

Suspect a stale artifact when a Guest endpoint exposes an older contract even
though the Host pin and synced source agree, or when PyMTLF capability
verification reports fields missing from the containing NWDAF context. Stop
the experiment, identify the affected Core or Path build boundary, and rebuild
the affected artifact explicitly. Record hashes before and after the rebuild;
do not use `reset` for this problem, because reset changes retained data rather
than installed software. See [Components](components.md#existing-vm-binary-boundary).

## Model Provision returns 503 during startup

PyAnLF reconciliation may begin while the processes are healthy but the
NWDAF-C to PyMTLF-C Model Provision chain is still converging. A bounded series
of create `503` responses is recoverable only when retry is followed by a `201`
Model Provision subscription, successful Model Monitor registration, and
active monitor scopes.

Do not treat container health alone as that business-level evidence, and do not
disable production readiness or create replacement resources manually. If the
503 responses persist, the later `201`/monitor evidence never appears, or a
reconciler reaches its terminal failure, inspect NWDAF-C, PyMTLF-C, and
PyAnLF-A/B logs together with UTC timestamps and treat startup as failed.

## Config selection or stale files

There is no local overlay. A stale repository-root `testbed.local.yaml` from an
earlier workflow is rejected so it cannot appear to affect only some commands;
move its intended values into one complete testbed file with a different name.
Confirm the effective inputs in the first line of `experiment-status` and
always pass the same pair:

```sh
make config-validate TESTBED=testbed.lab.yaml CONFIG_DIR=config/local/lab
```

Regenerate a set instead of copying selected NF YAML files into it. A manifest
or config hash mismatch indicates the directory was mixed or edited after
rendering.

## Dataset rejection

Use:

```sh
make dataset-show CONFIG_DIR=config/local/my-experiment
make dataset-validate CONFIG_DIR=config/local/my-experiment
```

Regenerate when a file is missing or its content hash, UE IPs, timestamps, or
resolved specification no longer match. Generated artifacts are
content-addressed; do not edit a Parquet file in place. `services-start`
automatically stages the matching integrity-checked set.

## Host RAM, storage, or swap

The default guest allocation is not reserved in full before use, and all three
VirtualBox disks grow dynamically. Validation reports when available RAM falls
below the 6 GiB Host reserve or free space falls below the 120 GiB recommendation;
it no longer blocks startup. Identify other users' processes, VMs, containers,
images, volumes, and caches before changing those values or running for long
periods.

Low free swap is a warning under the committed `swapPolicy`. With no usable
swap, Linux has less room to move inactive pages and an abrupt workload peak is
more likely to invoke the OOM killer. Do not create, clear, or resize Host swap
during a shared experiment without coordinating with other users.

Use project-scoped inspection such as `docker compose ps` and `docker system
df`; never use a global prune on the shared Host.

## VirtualBox host-only address failure

Confirm `/etc/vbox/networks.conf` permits all addresses in `testbed.yaml`. The
reference range requires `192.168.56.0/21`. After editing the Host file, rerun
`make experiment-validate` before `make vm-up`.

Inside a VM, Vagrant owns `50-vagrant.yaml` and the repository owns
`60-5g-nwdaf-aliases.yaml`. Verify the managed state with:

```sh
sudo /usr/local/libexec/5g-nwdaf-infrastructure/network-setup --verify
```

If an older VM has missing or stale aliases, reconcile its persistent fragment
with `sudo systemctl restart 5g-nwdaf-network.service`. The unit is an explicit
reconciler, not a second always-enabled boot configuration pass.

## gtp5g failure after a kernel change

`services-start` stops before launching NFs if the module vermagic does not
match the running Path kernel or cannot load. Rebuild only the affected Path's
kernel dependency using the commands in [Components](components.md#upf-and-gtp5g),
then retry startup. This does not rebuild UPF, NWDAF, or UERANSIM.

## Docker or GPU failure

First verify non-root access with `docker info`. A new Docker-group membership
requires a new login session or `newgrp docker`.

For GPU mode, check `nvidia-smi`, Docker's `nvidia` runtime, CDI inventory, and
the disposable image-level CUDA probe run by `ml-start`. The runtime does not
restart Docker, alter its default runtime, or silently fall back to CPU.
If GPU is not required, create a separate config with `DEVICE=cpu`; do not edit
only the Compose file or native PyMTLF config.

## WebConsole does not start

WebConsole must be enabled in the selected config, and Core MongoDB and NRF must
already be active. Its first start installs/builds the pinned source in Core and
may take longer; later starts reuse the content-addressed release. Inspect:

```sh
make webconsole-status
scripts/host/logs.sh --source vm --vm core --service webconsole --no-follow
```

The expected endpoint is `http://192.168.56.10:5000`. The billing compatibility
listener must remain on Core loopback; do not expose it to fix an HTTP problem.

## Subscription cleanup failure

The Consumer stores exact resource `Location` values. If deletion fails,
`subscriptions-stop` leaves its state and callback process available for retry;
do not delete the state file or guess a resource URI. Keep the NWDAFs and ML
backends running, inspect Core/path logs, and retry `make subscriptions-stop`.

During aggregate stop, a fixed 40-second grace keeps those backends alive while
PyAnLF and PyMTLF perform dependent Model Provision and Model Monitor cleanup.
The stop command does not parse application logs or claim that every internal
resource was remotely queried. If cleanup is suspect, inspect the current-run
PyAnLF, PyMTLF-C, and three NWDAF logs for DELETE status and reconciler errors
before starting another experiment.

After Core is powered off, `subscriptions-status` reports the Consumer as
`not-running` and its saved resource state as `not-readable`. This is an
expected power state, not evidence that resource state was lost. Power Core on
before inspecting saved locations or retrying an exact deletion.

## Start says a domain is already active

Aggregate startup deliberately requires a clean process state. Inspect
`services-status`, `ml-status`, and `subscriptions-status`, then either continue
with independent domain commands or run `make experiment-stop`. Do not reset
retained experiment data merely to resolve an active process.
