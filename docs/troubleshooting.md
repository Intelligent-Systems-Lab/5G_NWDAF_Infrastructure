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

HTTPS initialization of private team repositories requires GitHub credentials.
`gh auth status` can confirm GitHub CLI authentication, but Git itself must also
have the credential helper configured by the chosen login flow.

## Config selection or stale files

There is no local overlay. If an old `testbed.local.yaml` exists from an earlier
workflow, it is ignored unless explicitly passed as `TESTBED=...`. Confirm the
effective inputs in the first line of `experiment-status` and always pass the
same pair:

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
scenario contract no longer match. Generated artifacts are content-addressed;
do not edit a Parquet file in place. `services-start` automatically stages a
valid matching set.

## Host RAM, storage, or swap

The default guest allocation is not reserved in full before use, and all three
VirtualBox disks grow dynamically. Validation nevertheless blocks when
available RAM would fall below the 6 GiB Host reserve or workspace free space
is below 120 GiB. Identify other users' processes, VMs, containers, images,
volumes, and caches before changing those safety values.

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
the disposable CUDA probe reported by `experiment-validate`. The runtime does
not restart Docker, alter its default runtime, or silently fall back to CPU.
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

During aggregate stop, log-correlated cleanup verification keeps those backends
alive while the PyMTLF-C reconciler retries asynchronous Model Monitor deletion.
The verification reconstructs active IDs from PyMTLF-C's own `active` and
`removed` records; it does not correlate PyAnLF's public registration IDs with
different backend resource IDs. A timeout warning lists any unresolved
subscription IDs. Diagnose a late DELETE failure before setting
`ML_CLEANUP_TIMEOUT_SECONDS=0`.

## Start says a domain is already active

Aggregate startup deliberately requires a clean process state. Inspect
`services-status`, `ml-status`, and `subscriptions-status`, then either continue
with independent domain commands or run `make experiment-stop`. Do not reset
retained experiment data merely to resolve an active process.
