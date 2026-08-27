# Architecture

## Runtime placement

The testbed separates 5G network functions from GPU-capable ML runtimes without
making the topology look like a single Host process tree. One selected complete
`TESTBED` defines the logical NWDAF, UE, and Host ML inventory, while every
committed topology reuses the same three VMs.

| Domain | Placement | Responsibility |
| --- | --- | --- |
| Core | Core VM | Control-plane NFs, MongoDB, ADRF, selected Core NWDAF instances, and the production Consumer |
| Path A | Path A VM | UPF-A, gNB-A, selected Path A UEs, and selected Path A NWDAF instances |
| Path B | Path B VM | UPF-B, gNB-B, selected Path B UEs, and selected Path B NWDAF instances |
| Analytics ML | Host containers | Production PyAnLF-A and PyAnLF-B |
| Model training ML | Host containers | One independently configured PyMTLF container for each selected NWDAF |

The three committed definitions place their logical inventory as follows:

| Selected topology | Core VM | Path A VM | Path B VM | Host ML runtime |
| --- | --- | --- | --- | --- |
| Production Flat | NWDAF-C | NWDAF-A, UE1-3 | NWDAF-B, UE4-6 | PyAnLF-A/B and PyMTLF-A/B/C |
| Static Flat | Server | Clients 1-2, four UEs | Clients 3-4, four UEs | five PyMTLF containers |
| Static Hierarchical | Root and Branches 1-2 | Leaves 1-2, four UEs | Leaves 3-4, four UEs | seven PyMTLF containers |

The VMs keep the 5GC paths intuitive and provide the Linux kernel environment
needed by gtp5g. Each Guest installs one NWDAF binary and the selected manifest
starts the required independent systemd instances with separate identity,
config, endpoint, runtime directory, and log stream. Host containers give the
selected GPU participants access to the physical GPU without PCI passthrough
and isolate each Python service, dependency set, state volume, health check,
and log stream. Static deployments do not start PyAnLF or the production
Consumer subscription chain.

## Networks

The committed complete `TESTBED` definitions share eight isolated `/24`
VirtualBox networks. The first three octets identify the function of a plane;
the final octet identifies the Host or VM endpoint.

| Plane | Subnet | Purpose |
| --- | --- | --- |
| Management | `192.168.56.0/24` | Vagrant/Host administration and WebConsole |
| SBI | `192.168.57.0/24` | HTTP service-based interfaces and Host ML endpoints |
| N2 | `192.168.58.0/24` | gNB to AMF signaling |
| N3-A / N3-B | `192.168.59.0/24`, `192.168.60.0/24` | Each gNB to its UPF |
| N4 | `192.168.61.0/24` | SMF to both UPFs |
| N6-A / N6-B | `192.168.62.0/24`, `192.168.63.0/24` | Reserved path-specific data-network plane and UPF aliases |

Vagrant owns the base interface addresses in `50-vagrant.yaml`. The repository
renders process aliases from the active config into
`60-5g-nwdaf-aliases.yaml`; Netplan merges both files. The N6 planes are kept
in the topology even though the reference experiment obtains reproducible
traffic stimulus from the UPF PseudoDrivers.

## Independent lifecycles

Six domains can be inspected and operated independently:

1. VM power: `vm-*`
2. Guest 5GC/RAN/NWDAF services: `services-*`
3. Host PyAnLF/PyMTLF containers: `ml-*`
4. Optional Core WebConsole: `webconsole-*`
5. Consumer and its two exact subscription resources: `subscriptions-*`
6. Retained experiment data: `reset-*` and `subscriber-data-*`

`experiment-start`, `experiment-status`, and `experiment-stop` are aggregate
operator commands over the domains declared by the selected manifest.
Production Flat includes WebConsole and subscription handling when enabled;
static deployments omit those domains. Starting an aggregate experiment does
not tie the experiment lifetime to VM creation, and stopping it does not delete
VMs, containers, volumes, generated datasets, subscriber fixtures, or retained
model/data state.

## Production reference experiment flow

The full-core example exercises the following closed loop:

1. Six UEs register and create PDU Sessions through the normal 5GC control
   path. SMF selects the Path A or Path B UPF for the UE's TAI.
2. The Core Consumer queries NRF for NWDAFs supporting
   `nnwdaf-eventssubscription`, selects distinct providers for TAC `000001` and
   `000002`, and creates one `UE_COMMUNICATION` subscription on each.
3. NWDAF-A/B create Nupf Event Exposure subscriptions. Each UPF replays its
   generated Parquet input through the PseudoDriver and delivers reports to its
   path NWDAF.
4. PyAnLF-A/B obtain their data through ADRF, produce analytics, and the path
   NWDAFs deliver notifications to the Consumer callback.
5. PyMTLF-C monitors A/B accuracy reports. The example degrades Path A while
   Path B remains stable, causing C to coordinate both FL Clients.
6. A/B retrieve local training data from ADRF and train. C combines their model
   updates with sample-count-weighted FedAvg and publishes the resulting model
   to ADRF.
7. C provisions the new model to A/B. Both paths cut over to the new model
   generation and continue reporting post-cutover accuracy.

The reference experiment reaches its business completion criterion only after
the cutover is followed by an evaluated accuracy report for the new generation
that does not trigger degradation. Reaching that criterion does not stop any
process; the operator still chooses when to run `experiment-stop`.

The registration, PDU Session, service discovery, subscriptions, and Event
Exposure control paths are real. PseudoDriver input is deterministic stimulus,
not a claim of real application throughput or a user-plane benchmark.

## State ownership

Native config and generated dataset manifests are repository-owned inputs.
The selected subscriber/Internal Group records, ADRF records and files, NRF
ADRF records, and five or seven selected ML state volumes persist across
ordinary stops. Production Consumer subscription locations persist only so
exact resources can be retried and deleted. Reset derives its exact scope from
the selected manifest and retains the other topology's state. See
[Operations](operations.md) before deliberately clearing retained state.
