# 5G NWDAF Infrastructure

Reproducible three-VM plus Host-container infrastructure for a dual-TAI
free5GC/NWDAF testbed.
The repository owns topology, component revision locks, native configuration,
guest builds, service lifecycle, and a single consumer that discovers and
subscribes to the two path NWDAFs.

The first topology has three independently managed virtual machines and five
independently managed ML containers:

- `core`: free5GC control plane, MongoDB, ADRF, NWDAF-C, and the
  subscription consumer;
- `path-a`: UPF-A, gNB-A, UE1-3, and NWDAF-A;
- `path-b`: UPF-B, gNB-B, UE4-6, and NWDAF-B;
- Host Docker: PyAnLF-A/B and PyMTLF-A/B/C, one process per container.

VM power, guest services, Host ML containers, and NWDAF subscriptions are
separate lifecycles. Bringing up a VM does not automatically start either the
5GC processes or containers, and starting either execution domain does not
automatically create a subscription.

## Repository layout

- `NFs/`: 3GPP network functions, including NWDAF, ADRF, and UPF
- `ML/`: PyAnLF and PyMTLF backends
- `RAN/`: UERANSIM
- `kernel/`: guest kernel dependencies such as gtp5g
- `config/`: complete native config sets plus generated per-VM network aliases;
  `default` is the committed baseline
- `fixtures/full-core/`: scoped six-UE subscriber and Internal Group test data
- `scripts/host/`: host orchestration and read-only checks
- `scripts/guest/`: VM provisioning, build, config activation, and systemd units
- `tools/nwdaf-consumer/`: infrastructure-owned discovery/subscription client

`nwdaf-resources` is not a runtime dependency or submodule. Component source
is fixed by parent gitlinks and is never cloned by guest provisioning.

## Command surface

No VM or service is created merely by cloning this repository. The intended
complete sequence is:

```sh
git submodule update --init --recursive
cp testbed.local.example.yaml testbed.local.yaml
# Select and verify a working provider in testbed.local.yaml.
make preflight
make vm-up
make services-start
make ml-start
make subscriptions-start
make observe
```

Teardown is deliberately split:

```sh
make subscriptions-stop  # delete the two exact NWDAF resources
make ml-stop             # stop ML containers without deleting images/volumes
make services-stop       # leave all three VMs running
make vm-halt             # power off the VMs
```

There is no `vm-destroy` target. Destruction must be an explicit Vagrant action
after its exact targets have been reviewed. See [OPERATIONS.md](OPERATIONS.md)
for config selection, status, log filtering, and guest build details.

## Current validation boundary

The repository has passed source-lock, YAML, config-render/check, consumer
discovery, Python native-settings, shell syntax, Host preflight, Vagrant
definition validation, three-VM provisioning, config activation, and the full
guest service lifecycle. The topology
now allocates 10 GiB across the guests and reserves 6 GiB of available Host RAM
for the physical host and ML containers. Each VM declares a 40 GiB dynamically
allocated primary-disk ceiling; this does not allocate 120 GiB immediately.
Low free swap is reported as a warning while RAM and 120 GiB free workspace
storage remain hard gates.

The committed config publishes all five ML endpoints on the isolated Host SBI
candidate `192.168.57.1`, using ports `9091`-`9094` and `9292`. Container
listeners bind their own `0.0.0.0` interfaces; callbacks and Go NWDAF clients
use only the advertised Host endpoints. A pinned Python 3.12 image definition,
PyAnLF/PyMTLF targets, five-service Compose topology, production NVIDIA runtime
CDI selection, and a CPU-only validation override are implemented. Bounded CPU image and
lifecycle smokes started all five services, verified status/log/stop behavior,
and removed their disposable containers and volumes afterward. The production
`ml-start`/`ml-status`/`ml-stop` lifecycle is implemented. Host toolkit-base,
CDI inventory, NVIDIA runtime registration by daemon reload, a disposable
container GPU probe, and VM-to-Host SBI reachability have passed without
interrupting the eight shared containers.
Production GPU activation does not change the default runtime or require a
shared-daemon restart. Guest source sync, provisioning, and service dispatch no
longer include PyAnLF or PyMTLF; ML source and environments remain Host-container
responsibilities.

The guest lifecycle now applies six scoped subscriber records and one Internal
Group idempotently before starting UERANSIM. A complete stop/start regression
kept all 23 guest units active and established six registrations and six PDU
Sessions: Path A received `10.60.0.1`-`10.60.0.3`, and Path B received
`10.61.0.1`-`10.61.0.3`. Host ML containers, NWDAF subscriptions, N6 traffic,
and PseudoDriver replay remain separate validation stages.

The initial implementation intentionally does not support TLS/certificates,
automatic experiment history, or 5g-viz.

## License status

The parent repository has not yet been assigned an open-source license. See
`LICENSE`. Every submodule remains governed by its own license.
