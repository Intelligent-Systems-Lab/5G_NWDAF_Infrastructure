# 5G NWDAF Infrastructure

Reproducible infrastructure for production Flat and controlled static
Flat/Hierarchical federated-learning deployments. All deployments reuse three
Ubuntu 22.04 VirtualBox VMs; the selected complete config owns the exact NWDAF,
UE, PyAnLF/PyMTLF, volume, and subscription inventory.

The repository owns topology, component revision locks, native configuration,
guest provisioning, process lifecycle, deterministic PseudoDriver datasets,
and the Consumer that discovers and subscribes to both path NWDAFs.

## Start here

Choose the shortest path for the task at hand:

| Situation | Start with |
| --- | --- |
| New Host or first use of the repository | [Installation](docs/installation.md), then [First experiment](#first-experiment) |
| Understand what runs where and why | [Architecture and experiment flow](docs/architecture.md) |
| Create or change an experiment | [Configuration](docs/configuration.md), then [Operations](docs/operations.md) |
| Run an already prepared environment | [Operations](docs/operations.md) |
| Look up a command or its side effects | [Command reference](docs/commands.md) |
| Diagnose a failure | [Troubleshooting](docs/troubleshooting.md) |

For a first complete reading, use this order:

1. [Architecture](docs/architecture.md) — understand VM/container placement,
   networks, lifecycle boundaries, and the reference experiment.
2. [Installation](docs/installation.md) — prepare the Host, VirtualBox, Docker,
   and optional NVIDIA GPU support.
3. [Configuration](docs/configuration.md) — create one complete config set and
   its deterministic dataset.
4. [Operations](docs/operations.md) — create the VMs, run and observe the
   experiment, then stop or reset it safely.

[Components](docs/components.md) explains pinned source revisions and Guest
build ownership. It is useful when updating or diagnosing a component, but is
not required before the first run.

## Default production topology

| Location | Main processes |
| --- | --- |
| Core VM | free5GC control plane, MongoDB, ADRF, NWDAF-C, Consumer, optional WebConsole |
| Path A VM | UPF-A, gNB-A, UE1-3, NWDAF-A |
| Path B VM | UPF-B, gNB-B, UE4-6, NWDAF-B |
| Host | PyAnLF-A/B and PyMTLF-A/B/C, one process per container |

VM power, guest services, Host ML containers, WebConsole, and subscriptions
are separate lifecycles. `experiment-start` composes those domains; `vm-up`
only creates or powers on VMs.

## First experiment

Run every command below from the repository root. Keep the same `TESTBED` and
`CONFIG_DIR` values for the complete lifecycle; the examples use the committed
`testbed.yaml` implicitly.

### 1. Prepare the Host and sources

Complete [Installation](docs/installation.md), then initialize the revisions
pinned by this repository:

```sh
git submodule update --init --recursive
```

### 2. Create and validate experiment inputs

Create one complete local config and generate its deterministic PseudoDriver
dataset:

```sh
make config-create \
  NAME=my-experiment \
  FROM=experiments/examples/full-core-cat-transition/scenario.yaml \
  DEVICE=gpu
make dataset-generate CONFIG_DIR=config/local/my-experiment
make experiment-validate CONFIG_DIR=config/local/my-experiment
```

This runs the committed traffic pattern unchanged. To define different stable
or degraded traffic, phase lengths, or raw window timing, first follow
[Customize the dataset pattern](docs/configuration.md#customize-the-dataset-pattern)
and render the copied local scenario instead.

Use `DEVICE=cpu` when CUDA is not wanted. The choice is explicit; the runtime
does not silently fall back from GPU to CPU. Validation is a recommended,
read-only diagnostic: review its findings, but startup does not invoke or
require it to pass. See [Configuration](docs/configuration.md) for scenario,
identity, topology, native config, and dataset choices.

To prepare a controlled comparison layout, select the complete
`TESTBED=testbed.static-flat.yaml` or
`TESTBED=testbed.static-hierarchical.yaml` definition when rendering and on
every later lifecycle command. They keep the same three VMs, use eight UEs
and four two-UE data owners, and render five or seven independent NWDAF/PyMTLF
pairs. Static configs do not start the production Consumer subscription chain.

### 3. Create the three VMs

```sh
make vm-up
make vm-status
```

The first `vm-up` creates, provisions, and builds Core, Path A, and Path B. It
does not start 5GC, RAN, NWDAF, ML, or Consumer processes. Later invocations
only boot existing VMs. Before Vagrant can start a VM, the repository guard
checks the Host context and exact `VBoxHeadless` process/Vagrant UUID inventory;
duplicate, orphaned, changing, or unparseable state fails closed. `vm-status`
still starts a provider client and must only run from the approved Host context.

### 4. Start and observe the experiment

Start all process domains and inspect their combined status:

```sh
make experiment-start CONFIG_DIR=config/local/my-experiment
make experiment-status CONFIG_DIR=config/local/my-experiment
```

Use the continuously refreshed overview in another terminal when monitoring the
whole environment:

```sh
make observe CONFIG_DIR=config/local/my-experiment
```

Follow one component's detailed events when needed:

```sh
make logs CONFIG_DIR=config/local/my-experiment SERVICE=pymtlf-c
```

The aggregate start stages the selected inputs, starts Guest services and the
optional WebConsole, starts five ML containers, and creates two Consumer
subscriptions. [Operations](docs/operations.md) lists readiness signals and the
full federated-learning closure milestones. A bare `make logs` remains available
when one combined VM and ML event stream is explicitly wanted.

### 5. Stop and choose what to retain

Stop experiment processes while retaining VM disks and experiment state:

```sh
make experiment-stop CONFIG_DIR=config/local/my-experiment
```

Power off the retained VMs when they are no longer needed immediately:

```sh
make vm-halt
```

For the next run, choose the appropriate operation instead of deleting data by
default:

- restart with retained state using the standard workflow;
- use the guarded [clean-run reset](docs/operations.md#clean-run-reset) to clear
  experiment state while retaining the VMs;
- use explicit [VM destruction](docs/operations.md#vm-destruction) only when
  the complete Guest environment and its virtual disks must be rebuilt.

## Documentation

- [Documentation map and recommended order](docs/README.md)
- [Command reference](docs/commands.md) for exact parameters and side effects
- [Troubleshooting](docs/troubleshooting.md) for common failure paths

Run `make help`, `make help-advanced`, or `make help-dev` for the layered command
surface. Run `make test` for the Host-only repository checks.
