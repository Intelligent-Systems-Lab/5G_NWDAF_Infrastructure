# 5G NWDAF Infrastructure

Infrastructure, deployment configuration, lifecycle tooling, and experiment
assets for the NWDAF testbed. The maintained target is the protocol-driven
hierarchical FL experiment; consult the active plan in `testbed-docs` for its
implementation and verification status rather than treating the older
three-VM workflows below as the current architecture.

No legacy deployment definition is selected implicitly. Config and lifecycle
commands require an explicit `TESTBED=...` unless the Makefile declares a
maintained canonical default. When `CONFIG_DIR` is omitted, it is resolved from
that selected definition's `config.directory`; it never falls back to
`config/default` independently.

## Support boundary and retained assets

The following groups are retained as `legacy` / `unverified` assets. They
preserve prior experiment intent and can be selected explicitly for
best-effort use, but they receive no dedicated maintenance, regression repair,
or execution guarantee. Passing an old test does not change that status.

| Retained group | Included assets and purpose |
| --- | --- |
| Complete deployment definitions | `testbed.yaml`, `testbed.static-flat.yaml`, `testbed.static-hierarchical.yaml`, and the historical generated example in `config/default/` |
| Experiment inputs | UE/CAT scenarios and Path A/B traffic profiles under `experiments/examples/` |
| Dataset tooling | Host/Guest dataset scripts and `tools/datasetgen/` for the former PseudoDriver data path |
| Static FL control | `fl-control.py` and the `fl-collection-*` / `fl-training-*` operator commands |
| Subscription data path | Consumer, subscription commands, subscriber fixtures, Guest units, and their supporting scripts |
| Full 5GC and UE environment | Existing 5GC, UPF, UERANSIM, PyAnLF, gtp5g, component locks, config, provisioning, and build sources |
| Runtime examples and orchestration | Existing Compose sources/generated examples plus full-core, RAN, UPF, status, logs, stop, and reset branches |
| Documentation and tests | Existing detailed workflow documentation and legacy-focused tests, retained as historical behavior and migration evidence |

WebConsole is not part of that legacy classification. It remains an optional,
independently enabled subsystem with its existing source, config, build, and
lifecycle commands. The protocol-driven experiment does not enable or verify
it.

## Start here

Choose the shortest path for the task at hand:

| Situation | Start with |
| --- | --- |
| New Host or first use of the repository | [Installation](docs/installation.md), then [Legacy workflow reference](#legacy-workflow-reference) |
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

## Legacy production topology reference

| Location | Main processes |
| --- | --- |
| Core VM | free5GC control plane, MongoDB, ADRF, NWDAF-C, Consumer, optional WebConsole |
| Path A VM | UPF-A, gNB-A, UE1-3, NWDAF-A |
| Path B VM | UPF-B, gNB-B, UE4-6, NWDAF-B |
| Host | PyAnLF-A/B and PyMTLF-A/B/C, one process per container |

VM power, guest services, Host ML containers, WebConsole, and subscriptions
are separate lifecycles. `experiment-start` composes those domains; `vm-up`
only creates or powers on VMs.

## Legacy workflow reference

Run every command below from the repository root. This section documents the
retained legacy workflow; it is not a maintained acceptance path. Keep the same
explicit `TESTBED` and `CONFIG_DIR` values for the complete lifecycle.

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
  TESTBED=testbed.yaml \
  NAME=my-experiment \
  FROM=experiments/examples/full-core-cat-transition/scenario.yaml \
  DEVICE=gpu
make dataset-generate TESTBED=testbed.yaml CONFIG_DIR=config/local/my-experiment
make experiment-validate TESTBED=testbed.yaml CONFIG_DIR=config/local/my-experiment
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
make vm-up TESTBED=testbed.yaml
make vm-status TESTBED=testbed.yaml
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
make experiment-start TESTBED=testbed.yaml CONFIG_DIR=config/local/my-experiment
make experiment-status TESTBED=testbed.yaml CONFIG_DIR=config/local/my-experiment
```

Use the continuously refreshed overview in another terminal when monitoring the
whole environment:

```sh
make observe TESTBED=testbed.yaml CONFIG_DIR=config/local/my-experiment
```

Follow one component's detailed events when needed:

```sh
make logs TESTBED=testbed.yaml CONFIG_DIR=config/local/my-experiment SERVICE=pymtlf-c
```

The aggregate start stages the selected inputs, starts Guest services and the
optional WebConsole, starts five ML containers, and creates two Consumer
subscriptions. [Operations](docs/operations.md) lists readiness signals and the
full federated-learning closure milestones. Use `make logs TESTBED=testbed.yaml`
without source filters when one combined VM and ML event stream is explicitly
wanted.

### 5. Stop and choose what to retain

Stop experiment processes while retaining VM disks and experiment state:

```sh
make experiment-stop TESTBED=testbed.yaml CONFIG_DIR=config/local/my-experiment
```

Power off the retained VMs when they are no longer needed immediately:

```sh
make vm-halt TESTBED=testbed.yaml
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
