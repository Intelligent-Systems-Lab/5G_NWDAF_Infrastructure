# Operations

## Lifecycle boundaries

The repository treats four states independently:

1. Source and config are selected on the physical host.
2. `vagrant up` creates or powers on `core`, `path-a`, and `path-b`.
3. `services-start` stages one config set and starts guest processes.
4. `subscriptions-start` runs one Core consumer and creates two NWDAF resources.

VM provisioning installs toolchains and builds binaries but leaves the
experiment stack disabled. A VM can remain on while services are restarted,
and one service lifetime can contain multiple subscription/experiment cycles.

## Host preparation

Initialize every pinned component before preflight:

```sh
git submodule update --init --recursive
cp testbed.local.example.yaml testbed.local.yaml
```

Choose `virtualbox` or `libvirt` only after its host driver is known to work.
`testbed.local.yaml` is ignored and may contain the provider name, expected VM
storage path, and selected complete config directory. It must not redefine TAI,
UE, NWDAF, or experiment semantics.

`make preflight` is read-only. It checks:

- Vagrant/provider commands;
- 12 GiB guest RAM plus a 6 GiB physical-host reserve;
- at least 1 GiB free host swap, so a machine already under memory pressure is
  never used to start the testbed;
- at least 120 GiB free on the workspace filesystem;
- initialized, clean submodules at all 16 parent gitlinks;
- `components.lock.yaml` equality and native config consistency.

A failed preflight is a stop condition for `make vm-up`; free resources or fix
the provider instead of weakening the topology silently.

## Config sets

`config/default` is a complete native baseline. To modify individual component
files manually, copy the whole directory to `config/local/<name>` and select it:

```yaml
# testbed.local.yaml
config:
  directory: config/local/my-lab
```

For an explicit alternate topology definition, render a complete ignored set:

```sh
make config-render NAME=my-lab TESTBED=testbed.my-lab.yaml
make config-check TESTBED=testbed.my-lab.yaml CONFIG_DIR=config/generated/my-lab
```

`services-start` validates the effective set, hashes all native YAML, uploads
one archive to every VM, and extracts it to:

```text
/etc/5g-nwdaf-infrastructure/config-sets/<name>-<hash-prefix>/
```

Each guest verifies the full hash and atomically switches
`/etc/5g-nwdaf-infrastructure/active`. Activation is rejected while any stack
service or the consumer is active. Core, Path A, and Path B can therefore not
accidentally mix config sets.

## Guest builds

The root Vagrant project uses Ubuntu 24.04 and applies these primary disk
budgets:

| VM | RAM | vCPU | Disk | Guest-local build responsibility |
| --- | ---: | ---: | ---: | --- |
| Core | 5120 MiB | 4 | 20 GiB | core NFs, ADRF, NWDAF-C, PyMTLF-C |
| Path A | 3584 MiB | 3 | 25 GiB | gtp5g, UPF, UERANSIM, NWDAF-A, PyAnLF-A, PyMTLF-A |
| Path B | 3584 MiB | 3 | 25 GiB | gtp5g, UPF, UERANSIM, NWDAF-B, PyAnLF-B, PyMTLF-B |

Guests receive the parent-pinned source snapshot and never clone a branch.
Go components use Go 1.26.2; Python components use their own `uv.lock`; gtp5g
is compiled against the running Path kernel; UERANSIM is compiled independently
inside both Path VMs. Provisioning records no experiment run and starts no
experiment unit at boot.

This is a compact functional baseline, not a capacity claim. The current
PyAnLF and PyMTLF locks resolve Linux CUDA runtime packages even though this
scenario configures CPU-only training. Their compressed package footprints are
about 3.8 GiB and 2.8 GiB respectively, so each Path retains a 25 GiB disk
ceiling. Provisioning deliberately uses `uv sync --no-cache`: environments
remain available for services, but the disposable download cache is not kept
inside a guest. Reducing Path disks further requires a separately reviewed,
CPU-only dependency lock in the two source repositories.

The baseline also bounds low-volume runtime growth: MongoDB receives a 256 MiB
WiredTiger cache, Python services use one BLAS/OpenMP thread, each FL Client
accepts one concurrent training job, and model artifacts are limited to 32 MiB
compressed or 128 MiB extracted. The committed seed model is about 0.4 MiB.

## Services and pseudo driver

`make services-start` starts MongoDB and NRF first, then control-plane NFs,
both UPFs, SMF, ADRF/NWDAF/ML, and finally both gNB/UE groups. Failure triggers
reverse-order rollback. `make services-stop` performs the same reverse order
without halting VMs.

Pseudo driver support is required on both paths. It is embedded in each UPF
process and configured by `upfcfg-a.yaml` / `upfcfg-b.yaml`:

- Path A reads committed `pre_data/group1`;
- Path B reads committed `pre_data/group2`;
- both operate in the hybrid historical/live EES path.

These datasets provide reproducible stimulus. They are not evidence of real
application throughput or a user-plane performance benchmark. The selected
remote UPF revision still requires privileged VM revalidation against the
historical unpushed full-core revision described in `COMPONENTS.md`.

## Subscriptions

`make subscriptions-start` requires NWDAF-A and NWDAF-B to be active. The Core
consumer then:

1. binds its callback;
2. queries NRF for `nnwdaf-eventssubscription` providers;
3. selects exactly one unused NWDAF for TAC `000001` and one for `000002`;
4. rejects duplicate NF identities;
5. creates two standard `UE_COMMUNICATION` subscriptions;
6. rolls back the first exact `Location` if the second create fails.

`make subscriptions-stop` deletes both saved locations. If either DELETE fails,
state and callback remain available for retry. It never guesses a resource URI
from an ID.

## Observation

Use compact state without changing processes:

```sh
make vm-status
make services-status
make subscriptions-status
make observe
```

Follow journald directly when detail is needed:

```sh
scripts/host/logs.sh --vm all --service '*' --since '10 minutes ago'
scripts/host/logs.sh --vm path-a --service upf-a --since today
```

Stopping the log follower does not stop any guest. This version does not assign
run IDs, collect logs automatically, or bind VM lifetime to experiment history.

## Initial non-goals

- TLS, certificates, OAuth, and production security hardening;
- 5g-viz integration;
- automatic run archive/replay ownership;
- HA, capacity claims, or public cloud portability;
- automatic destructive VM cleanup.
