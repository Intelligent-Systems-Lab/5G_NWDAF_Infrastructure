# 5G NWDAF Infrastructure

Infrastructure, deployment configuration, lifecycle tooling, and experiment
assets for protocol-driven hierarchical federated learning with NWDAF.

The supported deployment is defined by
[`testbed.protocol-hierarchical.yaml`](testbed.protocol-hierarchical.yaml). It
contains four VirtualBox/Vagrant guests, eleven possible Host PyMTLF services,
and the Root–Branch–Leaf protocol topology used by the retained MNIST and
CIFAR-10 experiments. Generated runtime configuration is derived from this
deployment definition and one selected scenario.

## Repository layout

- `NFs/nwdaf`, `NFs/nrf`, `NFs/adrf`: Go components deployed in the guests.
- `ML/PyMTLF`: the Python training backend built into Host containers.
- `experiments/protocol-hierarchical`: one bounded MNIST smoke scenario and the
  E0, E1, E2a, and E2b experiment scenarios.
- `config/templates`: source templates used by the renderer.
- `scripts/host` and `scripts/guest`: lifecycle, rendering, dataset, runner,
  observation, and reset tooling.
- `docs`: architecture, installation, configuration, operation, and reference
  documentation.

The exact component revisions are the matching Git submodule commits recorded
in [`components.lock.yaml`](components.lock.yaml). `.gitmodules` provides the
canonical repository URLs and branch hints; it does not replace the exact
revision pins.

## Supported experiment conditions

The retained formal series has four conditions:

- **E0**: no injected node stop;
- **E1**: stop the primary Area A Branch and use priority-based replacement;
- **E2a**: stop the primary Area A Branch and reparent its Leaves to the Root;
- **E2b**: stop the primary Area A Branch and one Area A Leaf, then reparent the
  surviving Leaf to the Root.

Formal scenarios use seeds 1 through 5. All four conditions for one workload
and seed resolve to the same generated dataset and seed model.

## Prepare the repository

The reference platform is a Linux x86-64 Host with VirtualBox, Vagrant,
Docker Compose v2, Git, Go, Python 3, and `uv`. GPU experiments additionally
require a working NVIDIA driver, the NVIDIA Container Toolkit, the Docker
`nvidia` runtime, and the `nvidia.com/gpu=all` CDI device. See
[`docs/installation.md`](docs/installation.md) for the complete prerequisites
and capacity budget.

```sh
git submodule update --init --recursive
uv sync --extra analysis
uv sync --project ML/PyMTLF
```

## Run the bounded MNIST smoke

Generate the selected config and its dataset first:

```sh
make config-create \
  FROM=experiments/protocol-hierarchical/mnist/smoke.yaml \
  DEVICE=gpu
make dataset-generate
make config-validate
```

All commands that contact Vagrant or VirtualBox, including apparently
read-only status and validation commands, must run in an approved Host context.
The common provider guard intentionally refuses them when the Host VirtualBox
device namespace is unavailable.

```sh
make experiment-validate
make vm-up
make fl-experiment-run RUN_NAME=mnist-smoke-01
```

The runner starts the selected Guest and Host processes, submits one Root
training request, observes accepted rounds, collects the final model and
held-out evaluation, stops experiment processes, and performs the selected
scenario reset. It does not halt the VMs. If training completed but collection
failed, retry only post-processing with the same config and run name:

```sh
make fl-experiment-collect RUN_NAME=mnist-smoke-01
```

`fl-experiment-run` requires a generated GPU config. A CPU config remains
useful for rendering, dataset preparation, repository checks, and manual
Host-only component work, but is not accepted by the experiment runner.

## Run a formal series

With the selected VMs already running:

```sh
make fl-series-run \
  WORKLOAD=mnist \
  SEEDS=1,2,3,4,5 \
  CONDITIONS=E0,E1,E2a,E2b \
  RUN_PREFIX=paper
```

The series command calls the same config, dataset, and single-run pipeline
sequentially and stops on the first failure. It always selects the GPU path.
Run offline aggregation separately:

```sh
make fl-series-analysis \
  OUTPUT_DIR=runs/protocol-hierarchical/e0-e2b/analysis/paper
```

## Observe and stop the deployment

```sh
make experiment-status
make observe
make logs SOURCE=all SERVICE=all FOLLOW=false
make experiment-stop
make vm-halt
```

`experiment-stop` stops Guest and Host experiment processes while retaining
VMs, containers, images, volumes, and other state. `vm-halt` separately powers
off the selected VMs. For scoped state deletion, review `make reset-show`
before running the confirmation-gated `make reset` command.

## Local artifact boundary

The public source repository does not include generated configuration,
downloaded datasets, generated partitions and seed models, raw runs, final
models, VM state, container volumes, logs, or exchange archives. These remain
under ignored local paths such as `config/local/`, `.cache/`, `.generated/`,
`runs/`, and `.vagrant/` and can be rebuilt through the documented workflow.

## Documentation and checks

Start with the [documentation index](docs/README.md). The current command
surface is also available through:

```sh
make help
make help-advanced
make help-dev
```

`make test` runs repository-local syntax, contract, renderer, runner, analysis,
and synthetic provider-safety checks. It does not invoke Vagrant or
VirtualBox and does not start VMs or containers.

This repository is licensed under the Apache License 2.0; see
[`LICENSE`](LICENSE).
