# 5G NWDAF Infrastructure

Reproducible three-VM infrastructure for a dual-TAI free5GC/NWDAF testbed.
The repository owns topology, component revision locks, native configuration,
guest builds, service lifecycle, and a single consumer that discovers and
subscribes to the two path NWDAFs.

The first topology has three independently managed virtual machines:

- `core`: free5GC control plane, MongoDB, ADRF, NWDAF-C, PyMTLF-C, and the
  subscription consumer;
- `path-a`: UPF-A, gNB-A, UE1-3, NWDAF-A, PyAnLF-A, and PyMTLF-A;
- `path-b`: UPF-B, gNB-B, UE4-6, NWDAF-B, PyAnLF-B, and PyMTLF-B.

VM power, experiment services, and NWDAF subscriptions are separate
lifecycles. Bringing up a VM does not automatically start the 5GC processes,
and starting services does not automatically create a subscription.

## Repository layout

- `NFs/`: 3GPP network functions, including NWDAF, ADRF, and UPF
- `ML/`: PyAnLF and PyMTLF backends
- `RAN/`: UERANSIM
- `kernel/`: guest kernel dependencies such as gtp5g
- `config/`: complete native config sets; `default` is the committed baseline
- `scripts/host/`: host orchestration and read-only checks
- `scripts/guest/`: VM provisioning, build, config activation, and systemd units
- `tools/nwdaf-consumer/`: infrastructure-owned discovery/subscription client

`nwdaf-resources` is not a runtime dependency or submodule. Component source
is fixed by parent gitlinks and is never cloned by guest provisioning.

## Command surface

No VM or service is created merely by cloning this repository. The normal
sequence is:

```sh
git submodule update --init --recursive
cp testbed.local.example.yaml testbed.local.yaml
# Select and verify a working provider in testbed.local.yaml.
make preflight
make vm-up
make services-start
make subscriptions-start
make observe
```

Teardown is deliberately split:

```sh
make subscriptions-stop  # delete the two exact NWDAF resources
make services-stop       # leave all three VMs running
make vm-halt             # power off the VMs
```

There is no `vm-destroy` target. Destruction must be an explicit Vagrant action
after its exact targets have been reviewed. See [OPERATIONS.md](OPERATIONS.md)
for config selection, status, log filtering, and guest build details.

## Current validation boundary

The repository has passed source-lock, YAML, config-render/check, consumer
discovery, Python native-settings, shell syntax, and provider-independent
Vagrant validation. The three new VMs and privileged full scenario have not yet
been created or executed. The compact baseline allocates 12 GiB across the
guests; preflight additionally requires a 6 GiB host reserve, 1 GiB free swap,
and 120 GiB free workspace storage. Current PyTorch locks still include CUDA
runtime packages despite CPU-only training, so the two Path disks remain 25 GiB
until their upstream dependency locks are made CPU-only.

The initial implementation intentionally does not support TLS/certificates,
automatic experiment history, or 5g-viz.

## License status

The parent repository has not yet been assigned an open-source license. See
`LICENSE`. Every submodule remains governed by its own license.
