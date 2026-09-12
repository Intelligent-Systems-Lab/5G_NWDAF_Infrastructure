# Testbed Reference

`TESTBED` selects one complete topology document. The committed
`testbed.yaml` is the production Flat reference and the source used to render
`config/default`. `testbed.static-flat.yaml` and
`testbed.static-hierarchical.yaml` are complete static definitions using the
same schema. Copy an entire file for a site-specific environment; partial
overlays and a second deployment selector are not supported.

## Root and Guest identity

| Field | Meaning |
| --- | --- |
| `schemaVersion` | Testbed contract version; currently `1`. |
| `name` | Human-readable topology identity shown in generated metadata. |
| `config.directory` | Config selected when a command does not receive `CONFIG_DIR`. |
| `guest.box`, `guest.boxVersion` | Exact Vagrant box and pinned version used for new VMs. |
| `guest.os`, `guest.release` | Declared Guest identity checked against the pinned box and provisioning contract. |

The reference Guests are Ubuntu 22.04. Changing these fields is a platform
migration, not an experiment knob, because gtp5g, UERANSIM, MongoDB, and Guest
build locks are tied to the Guest environment.

## Host guidance and VM resources

`machines.<name>.resources` controls VirtualBox RAM in MiB, vCPU count, and the
dynamically growing primary disk ceiling in GiB. The production reference uses
`core`, `path-a`, and `path-b`; the protocol Hierarchical definition adds
`path-c`.

`hostSafety` contains recommendations reported by explicit validation and
status commands:

| Field | Unit and effect |
| --- | --- |
| `reserveMemoryMiB` | Desired Host memory left outside the VM allocation. |
| `containerBuildOverheadMemoryMiB` | Additional selected-runtime memory budget for image build and Docker overhead. |
| `minimumGpuMemoryMiB` | Minimum free accelerator memory when the selected inventory contains GPU participants. |
| `minimumFreeStorageGiB` | Desired free space on workspace, VirtualBox, and Docker filesystems. |
| `minimumFreeSwapMiB` | Desired free swap. |
| `swapPolicy` | `warn` or `require` classification in validation output. |

Validation combines the VM allocation with the selected Compose CPU/memory
limits, build overhead, GPU participant count, accelerator memory, and the Host
reserve floor. `ml-start` repeats the available Host/GPU check before starting
containers. Insufficient selected capacity is a hard safety failure.

## Networks and interfaces

`networks.<name>.cidr` defines each private `/24`. `mode: private` maps it to a
VirtualBox host-only network. `egress: nat` on N6 declares the retained egress
role; N6 is not used by the reference PseudoDriver flow but remains part of the
topology.

`machines.<name>.interfaces` gives each VM one base address on every network it
joins. Service addresses are separate aliases on those same interfaces. The
renderer produces `network/core.yaml`, `network/path-a.yaml`, and
`network/path-b.yaml`; the Guest network reconciler makes those aliases
persistent.

Network roles are:

| Network | Traffic |
| --- | --- |
| `management` | SSH/Vagrant and optional WebConsole access. |
| `sbi` | NF SBI, UPF Event Exposure, Host ML endpoints, and Consumer callback. |
| `n2` | gNB to AMF NGAP. |
| `n3-a`, `n3-b` | Per-path gNB to UPF GTP-U. |
| `n4` | SMF to both UPFs PFCP. |
| `n6-a`, `n6-b` | Retained per-path data-network egress. |

Every address must belong to its named CIDR and be unique. The Host ML bind
address must exist on the Host, normally as the SBI host-only adapter address.

## Mobile identity

| Field | Meaning and derivation |
| --- | --- |
| `mobileNetwork.plmn.mcc`, `.mnc` | Canonical MCC/MNC strings. MNC may contain two or three digits. |
| `mobileNetwork.snssai.sst`, `.sd` | S-NSSAI rendered into 5GC and UERANSIM formats. |
| `mobileNetwork.dnn` | DNN/APN used by SMF, subscriber records, and UEs. |
| `mobileNetwork.internalGroup.serviceId`, `.localId` | Combined with PLMN into the Internal Group ID. |
| `paths.<a|b>.subscriberNumbers` | Stable numeric ordinals padded into 15-digit IMSI SUPIs. |

The renderer derives every NF/RAN PLMN, TAI, SUPI, Consumer group target, and
subscriber/group fixture. GPSI/MSISDN is a separate subscriber identity and is
not derived from PLMN.

## Placement and endpoints

`placement` is the exact ownership inventory: Guest units for every selected
machine and the selected Host containers. The protocol Hierarchical definition
uses eleven one-to-one NWDAF/PyMTLF pairs. The generated manifest must be exactly
reconstructible from placement and the other `TESTBED` fields.

Endpoint objects use `network`, `address`, and usually `port`:

- `coreServices` owns NRF, NSSF, UDR, UDM, AUSF, PCF, AMF, SMF, MongoDB, and
  ADRF endpoints and persistence identity.
- `paths.<a|b>.gnb` owns N2/N3 addresses.
- `paths.<a|b>.upf` owns N3/N4/N6, Event Exposure, GTP interface name, and UE
  pool.
- `analytics.topology` selects `production-flat`, `static-flat`,
  `static-hierarchical`, or `protocol-hierarchical` inside the complete
  definition.
- `analytics.nwdaf-*` owns stable lowercase UUIDv4 NF instance IDs, SBI
  addresses, FL role, Host backend mapping, and static data-owner/Branch edges
  where applicable.
- `analytics.dataOwners` assigns the four static logical positions to disjoint
  two-SUPI partitions. Production Flat continues to use `analytics.backends`
  for its legacy A/B/C native endpoint mapping.

Within `analytics.protocolTopology`, Branch candidates define a positive
`reportAfter.count` with `unit: round`. Leaf candidates must omit
`reportAfter`: the renderer combines the Leaf role with the selected image
scenario's required `training.localEpochs` to generate the complete native
`report_after: {count: <localEpochs>, unit: epoch}` instruction. This keeps the
Leaf epoch count in one operator-authored source.

ADRF's `nfInstanceId` is stable so retained NRF and model state can be scoped
exactly. `modelStorage.localDirectory` must remain an absolute directory below
`/var/lib/5g-nwdaf-infrastructure/adrf`.

## PseudoDriver and ML runtime

Each `paths.<a|b>.upf.pseudoDriver` declares whether replay is enabled, its
`hybrid` mode, expected `traffic.parquet` name, Guest activation directory, and
minimum replay-memory headroom. The scenario selects the profile content; this
section selects how the matching artifact is used by go-upf.

`mlRuntime` fields are:

| Field | Meaning |
| --- | --- |
| `engine` | Required Compose v2 engine identity. |
| `networkMode` | Compose network contract; currently `bridge`. |
| `bindAddress` | Host address used for published container ports. |
| `advertisedAddress` | Address written into NF/ML callback and public URLs. |
| `services.<name>.image` | `pyanlf` or `pymtlf` build target. |
| `publishedPort`, `containerPort` | Host-side and container-side service ports. |
| `device` | Native desired device (`cpu` or `cuda:0`); render-time `DEVICE=cpu` creates the coordinated CPU override. |
| `cpus`, `memoryMiB` | Per-container limits included in the selected capacity gate. |
| `volume.name`, `volume.target` | Exact project volume identity and its writable container mount. |

`optionalServices.webconsole` owns its Core placement and management endpoint;
whether it starts is stored in the generated manifest from `WEBCONSOLE`.

## Consumer, security, and operations

`consumer` selects the Core placement, requester NF type, NRF discovery service
and event, target Paths, callback bind/advertised endpoint, and periodic report
request. Its `reporting.periodSeconds` is the analytics notification request,
not the PyAnLF accuracy-monitor period.

`security.tls` and `security.oauth` must remain false because certificates and
OAuth are not supported by this repository yet.

`operations.clockSkewToleranceMs` is the accepted cross-process clock margin;
`journalMaxUseMiB` sizes retained Guest journals. `operations.pyanlfDelivery`
provides request and worker-stop timeouts for analytics reports, accuracy
reports, and runtime-completion delivery. They bound network/application waits,
not artificial transmission delays.

Run `make config-validate TESTBED=... CONFIG_DIR=...` after editing. Findings
identify cross-file drift but do not rewrite the selected values or prevent an
operator from attempting startup.
