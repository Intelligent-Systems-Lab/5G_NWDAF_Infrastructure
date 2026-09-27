# Configuration

## Configuration ownership

The runtime is produced from two human-maintained inputs:

1. `testbed.protocol-hierarchical.yaml` owns deployment topology, identity,
   placement, networks, capacity, operation policy, and reset ownership.
2. One scenario under `experiments/protocol-hierarchical` or
   `experiments/local` owns workload, data partition, training, fault, and
   observation behavior.

`config-create` combines these inputs into `config/local/<name>/`. Generated
native configs, Compose, topology, network aliases, and the runtime manifest are
outputs, not additional configuration sources.

## Create a config set

The default output name is `protocol-hierarchical`:

```sh
make config-create \
  FROM=experiments/protocol-hierarchical/mnist/smoke.yaml \
  DEVICE=gpu
```

Use a distinct name when retaining multiple selections:

```sh
make config-create \
  FROM=experiments/protocol-hierarchical/mnist/formal-baseline.yaml \
  NAME=paper-mnist-s1-e0 \
  DEVICE=gpu \
  SEED=1
```

The render arguments are:

- `FROM`: required repository-relative scenario YAML path;
- `NAME`: output directory name, defaulting to `protocol-hierarchical`;
- `DEVICE`: `cpu` or `gpu`, defaulting to `cpu`;
- `SEED`: required for an E0–E2b formal scenario and rejected for other
  scenarios;
- `FORCE=true`: replace an existing output directory. The default is to stop
  rather than overwrite it.

The experiment runner requires a GPU config. CPU rendering is supported for
configuration, dataset, repository, and independent lifecycle work.

## Select a generated config

The testbed definition selects `config/local/protocol-hierarchical` by default.
When `NAME` differs, pass the same directory to subsequent commands:

```sh
make dataset-generate CONFIG_DIR=config/local/paper-mnist-s1-e0
make config-validate CONFIG_DIR=config/local/paper-mnist-s1-e0
make fl-experiment-run \
  CONFIG_DIR=config/local/paper-mnist-s1-e0 \
  RUN_NAME=paper-mnist-s1-e0
```

Do not switch an active deployment by silently changing the default directory.
Stop the selected processes, inspect the reset scope, and select the new config
explicitly.

## Validate inputs

```sh
make config-validate CONFIG_DIR=config/local/<name>
make dataset-validate CONFIG_DIR=config/local/<name>
make dataset-show CONFIG_DIR=config/local/<name>
```

`config-validate` checks the generated cross-component contract without
starting VMs or containers. `experiment-validate` additionally checks Host
capacity, component checkouts, dataset, Docker, GPU policy, and the real
provider definition; it therefore belongs to the approved Host provider
boundary.

## Local scenarios and artifacts

Use `experiments/local/` for uncommitted scenario variants. A local scenario
must use the same schema and renderer; it is not a separate execution path.

The following paths are intentionally local and ignored:

- `config/local/`: generated runtime config sets;
- `.cache/image-datasets/`: downloaded official archives;
- `.generated/image-datasets/`: generated Leaf, validation, and held-out data;
- `.generated/seed-models/`: generated formal initial models;
- `runs/`: experiment evidence, raw observations, and final models;
- `.vagrant/` and Docker state: provider and runtime state.

Rebuild these artifacts from the deployment, scenario, component lockfiles, and
documented commands. Do not commit a generated config set as a substitute for
its authoritative inputs.

## References

- [Testbed definition](configuration/testbed-reference.md)
- [Scenario definition](configuration/scenario-reference.md)
- [Generated native configuration](configuration/native-config-reference.md)
- [Image datasets](configuration/dataset-reference.md)
