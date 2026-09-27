# Testbed Definition Reference

The supported high-level deployment source is
`testbed.protocol-hierarchical.yaml`. Its schema version is `1`.

## Root fields

| Field | Meaning |
| --- | --- |
| `schemaVersion` | Testbed schema selected by the renderer and Vagrantfile. |
| `name` | Stable deployment name. |
| `config.directory` | Default generated config directory used when `CONFIG_DIR` is omitted. |
| `guest` | Guest box, pinned box version, operating system, and release. |
| `hostSafety` | Host RAM, build overhead, GPU memory, swap, and storage admission values. |
| `machines` | Exact VM names, resources, and interface addresses. |
| `networks` | Named private networks and CIDRs. |
| `mobileNetwork` | PLMN and S-NSSAI values rendered into component-native configs. |
| `placement` | Guest units per machine and the complete possible Host-container inventory. |
| `coreServices` | MongoDB, NRF, and ADRF endpoints and ADRF storage ownership. |
| `mlRuntime` | Compose engine, Host bind address, per-service ports, devices, limits, and volumes. |
| `analytics` | NWDAF identities, roles, endpoints, backend mappings, and protocol topology. |
| `security` | Current TLS and OAuth selections. |
| `operations` | Clock, journal, readiness, preparation, data-window, and round timeouts. |

## Machines and networks

Each `machines.<name>` entry contains:

- `resources.memoryMiB`, `resources.cpus`, and `resources.diskGiB`;
- one address for every declared interface under `interfaces`.

Machine names are also provider identities. The current names are `core`,
`path-a`, `path-b`, and `path-c`. The Vagrantfile validates that placement
and machine inventories match and that each interface address belongs to its
declared network.

## Placement and analytics

`placement.<machine>` lists the Guest systemd services owned by that VM.
`placement.host-containers` lists every possible PyMTLF service. The renderer
derives the active subset from the selected scenario.

Each `analytics.nwdaf-*` entry declares:

- `machine` and protocol `role` (`root`, `branch`, or `leaf`);
- a stable lowercase UUIDv4 `nfInstanceId`;
- one SBI network address and port;
- the mapped PyMTLF backend;
- `tai` for Leaf nodes.

Every NWDAF maps one-to-one to one possible PyMTLF service.

## Protocol topology

`analytics.protocolTopology` contains a Root-level `policy` and `strategy`
plus `branchGroups`. Each group declares:

- enabled Branch candidates with integer `priority` and Branch-round
  `reportAfter`;
- enabled Leaves with integer `priority`;
- the group's participant policy and aggregation strategy.

The renderer resolves logical node names to NF instance identities. Leaf report
intervals come from the selected scenario's local epoch count. The scenario
also selects the failure policy and may override the FedProx proximal
coefficient.

## Host PyMTLF services

Each `mlRuntime.services.<name>` entry declares:

- image family, published and container ports;
- source device policy (`cpu` or `cuda:0`);
- CPU and memory limits;
- one named volume and its container target.

On a GPU render, Root and all Leaves retain `cuda:0`; Branches remain on CPU.
On a CPU render, all possible services use CPU. The generated scenario inventory
may omit an inactive replacement Branch and its volume.

## Capacity and operation policy

`hostSafety` is evaluated together with the generated runtime inventory, not
as a documentation estimate. The checks cover total Guest demand, selected
container demand, build overhead, free storage, swap policy, published ports,
and GPU free memory where applicable.

`operations` provides runtime timeouts and tolerances consumed by generated
configs and lifecycle tools:

- `clockSkewToleranceMs`;
- `journalMaxUseMiB`;
- `serviceReadyTimeoutSeconds`;
- `preparationTimeoutSeconds`;
- `preparationDataWindowSeconds`;
- `roundTimeoutSeconds`.

Change these values only as deployment policy. Per-run dataset and training
values belong in the scenario instead.

## Reset ownership

There is no separately maintained reset list in the testbed YAML. The renderer
derives `runtime.resetScope` from placement, component identities, selected
containers, volumes, NRF records, ADRF collections, and ADRF model storage.
Reset tooling consumes that generated scope and refuses unexpected runtime
inventory.
