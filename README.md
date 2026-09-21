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
| Complete deployment definitions | `testbed.yaml`, `testbed.static-flat.yaml`, `testbed.static-hierarchical.yaml`, and the historical generated example in `config/default/`. The production definition remains a repository fixture. Both static definitions can still be parsed into bounded machine/runtime inventories, but current PyMTLF native validation rejects their obsolete private seed-import contract; lifecycle therefore fails closed. None has a real-run guarantee. |
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

## Maintained protocol-driven workflow

The canonical definition is `testbed.protocol-hierarchical.yaml`. It declares
four VMs (`core` plus three Path VMs), eleven Guest NWDAFs, eleven one-to-one
Host PyMTLF containers, and only MongoDB, NRF, and ADRF as supporting Guest
services. The Make defaults select this definition and its generated
`config/local/protocol-hierarchical` directory.

Scenario definitions live under `experiments/protocol-hierarchical/mnist/` and
`experiments/protocol-hierarchical/cifar10/`. Filenames such as `smoke.yaml`,
`formal-baseline.yaml`, and `all-class-skew-replacement.yaml` help select the
input; formal condition identity is saved explicitly in the selected scenario.
Generated configs created before selected scenario snapshots continue to read
their original scenario definition. Keep that definition available for older
config checks and reset validation. Create a new config to select a formal seed;
do not replace a generated config while its experiment processes are running.
Historical run records retain their original paths.

Create either dataset-specific config, acquire and partition the dataset, and
then create the VMs:

```sh
make config-create FROM=experiments/protocol-hierarchical/mnist/smoke.yaml
make dataset-generate
make vm-up
```

Run `make experiment-validate` explicitly before `vm-up` to review the complete
input, capacity, selected component-revision, dataset, Compose, and provider
diagnostics. `vm-up` itself starts with the exact provider process/metadata/state
preflight, and all real Vagrant and VirtualBox operations still require the
approved Host execution context. Guest provisioning builds only the services
selected for each machine and records their Git revisions in the Guest
provisioning manifest; service startup rejects a revision mismatch.

Start the minimal runtime and submit the two-round normal-topology request:

```sh
make experiment-start
make fl-training-start RUN_ID=<uuid-v4>
make fl-training-status RUN_ID=<same-uuid-v4>
make experiment-status RUN_ID=<same-uuid-v4>
```

The MNIST and CIFAR-10 scenarios each create six balanced 100-sample Leaf
shards, a 200-sample Root validation set, and a separate 200-sample held-out
set. Raw archives are cached under ignored `.cache/image-datasets/`; generated
partitions are kept under ignored `.generated/image-datasets/`. Dataset
validation uses the current PyMTLF native loader.

To switch datasets, stop and reset the current run first, then explicitly
replace the canonical generated config and generate the other partition:

```sh
make experiment-stop
make reset-show
make reset RESET_CONFIRM=<current-scenario-name>
make config-create FROM=experiments/protocol-hierarchical/cifar10/smoke.yaml FORCE=true
make dataset-generate
```

The config identity guard prevents the new generated config from silently
operating an old active runtime. As an alternative to the manual
`experiment-start` / `fl-training-start` sequence above, a selected normal
scenario can use the same checkpointed experiment lifecycle as replacement.
For a fresh environment, select the GPU config and run:

```sh
make config-create FROM=experiments/protocol-hierarchical/mnist/smoke.yaml DEVICE=gpu
make dataset-generate
make experiment-validate
make vm-up
make fl-experiment-run RUN_NAME=mnist-normal-1
```

If training completes but collection fails, retry with
`make fl-experiment-collect RUN_NAME=mnist-normal-1`; it does not retrain.
The short normal scenarios are flow checks, not paired formal-comparison data.

For a bounded GPU fault-lifecycle flow, select one of the MNIST smoke scenarios:
`healthy-comparison-smoke.yaml` (no stop), `replacement-smoke.yaml` (stop the
primary Branch), `reparent-leaves-smoke.yaml` (stop the primary Branch and
reparent its Leaves), or `partial-reparent-smoke.yaml` (stop the primary Branch
then one Leaf). They share one generated dataset partition. The foreground
runner starts the selected processes, submits the request, stops the exact
scenario-selected Guest NWDAF and Host PyMTLF pairs in order after the
accepted-round barrier, collects the final model, held-out result, and raw
records, then stops and resets the selected experiment state:

```sh
make config-create \
  FROM=experiments/protocol-hierarchical/mnist/replacement-smoke.yaml \
  DEVICE=gpu FORCE=true
make dataset-generate
make vm-up
make fl-experiment-run RUN_NAME=123
```

Use the same `fl-experiment-run` command for all four scenarios. The retained
`fl-branch-replacement-*` aliases use the same runner but accept only a
replacement-fault scenario, not a healthy or reparenting scenario.

The runner creates and checkpoints the UUIDv4 request identity before submitting
training. If training completed but final collection or held-out evaluation
failed, retry only that stage without retraining:

```sh
make fl-experiment-collect RUN_NAME=123
```

Switch scenarios only after the previous runner has reported successful cleanup.
Each of these four smoke runs targets eight accepted rounds; repair success and actual
participant contributions are determined from the saved records afterward, not
used as a runner completion gate. Evidence is kept under ignored
`runs/protocol-hierarchical/<dataset>/<run-name>/` as `events.jsonl`, `run.json`,
`final-root-model.tar.gz`, and per-process JSONL under `observations/`; bounded diagnostics
are added only on failure. The runner does not control replacement preparation
timing or silently fall back to CPU. VMs remain running after the selected
process and data reset.

The E0–E2b paper scenarios use an explicit `SEED` when generating a config.
For each workload and seed, all four conditions select one dataset partition
and one PyMTLF-native initial model; different seeds receive separate inputs.
The selected source is mounted read-only into the Root container. A formal run
keeps its raw evidence under
`runs/protocol-hierarchical/e0-e2b/<workload>/seed-<seed>/<condition>/<run-name>/`.
The separate MNIST `seeded-smoke.yaml` uses the same seed preparation with two
accepted rounds; its runs remain outside the formal series and its inputs have
separate dataset directories.
With VMs already running in the approved Host context, select only the runs
intended for the current batch:

```sh
make fl-series-run WORKLOAD=mnist SEEDS=1 CONDITIONS=E0,E1 RUN_PREFIX=trial
```

This calls the existing config-create and single-run lifecycle in order. It
stops on the first failure; use the failed run's selected `CONFIG_DIR` and
`RUN_NAME` with `fl-experiment-collect` before switching configs if collection
needs a retry. It does not automatically execute the full paper matrix.
After runs are saved, offline analysis can be repeated without training:

```sh
make fl-series-analysis OUTPUT_DIR=runs/protocol-hierarchical/e0-e2b/analysis/first-pass
```

The analysis keeps missing or incomplete runs visible and reports a five-seed
confidence interval only when all five relevant runs are available. Paired
effects additionally require matching E0 inputs. Its output is
derived data; `run.json` and raw JSONL remain unchanged.

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
