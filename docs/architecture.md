# Architecture

## Deployment model

`testbed.protocol-hierarchical.yaml` is the single high-level deployment
source. A selected scenario supplies workload, partition, training, failure,
and observation behavior. The renderer combines both inputs into one local
config set consumed by Guest services, Host PyMTLF containers, lifecycle
commands, the experiment runner, and reset tooling.

The reference deployment uses two private Host networks:

- `management` (`192.168.56.0/24`) for provider and Guest management;
- `sbi` (`192.168.57.0/24`) for NRF, ADRF, NWDAF, MongoDB, and Host PyMTLF
  communication.

The Host exposes PyMTLF on `192.168.57.1`. Guest addresses, ports, NF instance
identities, and backend mappings are defined in the testbed file and rendered
into native configs; they should not be maintained separately in documentation.

## Placement

| Location | Guest services | Host PyMTLF counterparts |
| --- | --- | --- |
| Core VM | MongoDB, NRF, ADRF, Root NWDAF | Root PyMTLF |
| Path A VM | primary Branch, replacement Branch, Leaves A1 and A2 | two Branch and two Leaf PyMTLF services |
| Path B VM | Branch B, Leaves B1 and B2 | one Branch and two Leaf PyMTLF services |
| Path C VM | Branch C, Leaves C1 and C2 | one Branch and two Leaf PyMTLF services |

The deployment definition contains eleven possible NWDAF/PyMTLF pairs: one
Root, four Branch candidates, and six Leaves. The generated runtime inventory
contains only the services needed by the selected scenario. In particular, the
Area A replacement Branch is active for a replacement-fault scenario, but is
not started for a normal run or a reparenting scenario.

## Hierarchical training flow

The Root NWDAF owns the training request and global round lifecycle. Its PyMTLF
backend evaluates and aggregates the global model. Each active Branch receives
the model for its group, coordinates its Leaves, aggregates Leaf contributions,
and reports to the Root. Leaves train on their local `.npz` shards through
their own PyMTLF backends.

The retained topology uses priority selection, sample-weighted aggregation,
and FedProx. Branches report after one Branch round; Leaf `report_after` is
derived from the selected scenario's `training.localEpochs`. A scenario may
override the FedProx proximal coefficient through `training.proximalMu`.

## Failure behavior

The scenario selects one of two protocol behaviors:

- `replace_branch`: the next enabled Branch candidate in the affected group is
  selected by priority;
- `reparent_leaves_to_root`: surviving Leaves in the failed Branch group become
  direct Root participants.

A fault scenario also specifies the accepted-round barrier and exact nodes to
stop. The experiment runner performs a confirmed process stop at that barrier;
the protocol, not the runner, determines subsequent participant selection and
accepted rounds. A normal scenario has no injected stop.

## Runtime and state owners

- Vagrant and VirtualBox own the four VM identities and disks.
- systemd template units own Guest MongoDB, NRF, ADRF, and NWDAF processes.
- Docker Compose owns the selected PyMTLF containers and project network.
- Named Docker volumes own PyMTLF runtime state.
- MongoDB owns NRF and ADRF records; ADRF also owns its model-storage directory.
- `config/local/<name>/manifest.yaml` owns the selected generated process and
  reset inventory.
- `.generated/image-datasets` and `.generated/seed-models` own generated
  experiment inputs.
- `runs/protocol-hierarchical` owns raw run evidence and collected final models.

Stopping processes, halting VMs, and resetting experiment state are separate
operations. A stop retains state. A VM halt does not clean experiment state.
Reset is confirmation-gated and uses the generated manifest's exact scope.

## Lifecycle boundaries

1. Render one config set from the testbed and scenario.
2. Generate or reuse the selected dataset and formal seed model.
3. Validate Host, component, config, dataset, provider, and capacity inputs.
4. Create or start the selected VMs.
5. Activate the config and start Guest services and Host PyMTLF containers.
6. Submit and observe training, optionally inject the selected fault, then
   collect the final model, raw observations, and held-out evaluation.
7. Stop processes and reset only the selected experiment state.
8. Run pair or series analysis offline from finalized run records.

Commands that contact Vagrant or VirtualBox always cross the real-provider
boundary, including status and validation. They must run in the approved Host
context and through the shared provider guard.
