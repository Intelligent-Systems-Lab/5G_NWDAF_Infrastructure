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

Run `make help` for the lifecycle commands. No VM or service is created merely
by cloning this repository. The initial implementation intentionally does not
support TLS/certificates, automatic experiment history, or 5g-viz.

## License status

The parent repository has not yet been assigned an open-source license. See
`LICENSE`. Every submodule remains governed by its own license.

