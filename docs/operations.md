# Operations

## 1. Prepare the checkout

```sh
git submodule update --init --recursive
uv sync --extra analysis
uv sync --project ML/PyMTLF
```

Use `make test` for the repository-local synthetic suite. It is not a
substitute for a real provider, container, or distributed training run.

## 2. Run the bounded MNIST smoke

Render a GPU config and generate the selected data:

```sh
make config-create \
  FROM=experiments/protocol-hierarchical/mnist/smoke.yaml \
  NAME=protocol-hierarchical \
  DEVICE=gpu
make dataset-generate
make config-validate
```

In the approved Host context, run preflight and start the selected VMs:

```sh
make experiment-validate
make vm-up
```

Do not start experiment processes separately. The runner requires a clean
process start:

```sh
make fl-experiment-run RUN_NAME=mnist-smoke-01
```

A successful runner invocation:

1. validates config, dataset, component, provider, Docker, and GPU inputs;
2. starts selected Guest services and Host PyMTLF containers;
3. confirms runtime, registration, image, device, and active-config state;
4. submits one Root training request;
5. observes accepted rounds and injects the selected fault when present;
6. records the final Root model checkpoint;
7. stops experiment processes;
8. collects raw PyMTLF observations and runs the held-out evaluation;
9. performs and verifies the scenario-scoped reset.

VMs remain running after the experiment.

## 3. Run one formal condition

Formal config generation requires a seed:

```sh
make config-create \
  FROM=experiments/protocol-hierarchical/mnist/formal-replacement.yaml \
  NAME=paper-mnist-s1-e1 \
  DEVICE=gpu \
  SEED=1
make dataset-generate CONFIG_DIR=config/local/paper-mnist-s1-e1
make fl-experiment-run \
  CONFIG_DIR=config/local/paper-mnist-s1-e1 \
  RUN_NAME=paper-mnist-s1-e1
```

The same pattern applies to CIFAR-10 and the other retained conditions. Use a
unique run name; an existing run directory is never overwritten.

## 4. Run selected formal slots

```sh
make fl-series-run \
  WORKLOAD=cifar10 \
  SEEDS=1,2,3,4,5 \
  CONDITIONS=E0,E1,E2a,E2b \
  RUN_PREFIX=paper
```

For each seed and condition, the series tool generates a named GPU config,
generates or reuses the per-seed dataset and seed model, then calls the same
single-run pipeline. Execution is sequential and stops on the first failure.
Rerun only the intended remaining slots rather than silently skipping a failed
slot.

## 5. Run evidence

A non-formal run is written under:

```text
runs/protocol-hierarchical/<dataset>/<run-name>/
```

A formal run is written under:

```text
runs/protocol-hierarchical/e0-e2b/<dataset>/seed-<n>/<condition>/<run-name>/
```

The evidence directory can contain:

- `run.json`: selection, revisions, runtime identity, workload, deadlines,
  fault, accepted-round phases, held-out result, cleanup, and final status;
- `events.jsonl`: chronological controller and Root observation events;
- `observations/*.jsonl`: raw records collected from every active PyMTLF
  service;
- `final-root-model.tar.gz`: the collected final model;
- `diagnostics/`: bounded logs and status captured after a failure.

`run.json` is checkpointed during execution. `successful` with
`finalized: true` means collection and cleanup evidence passed. A
`collection-pending` or `collection-failed` status with completed training is
eligible for collection-only recovery.

## 6. Collection recovery

If training completed and the final model checkpoint exists but post-processing
failed, keep the same generated config and Root volume state, then run:

```sh
make fl-experiment-collect \
  CONFIG_DIR=config/local/<name> \
  RUN_NAME=<same-run-name>
```

The command verifies that the checkpoint, scenario, config selection, final
model source, and PyMTLF image still match before copying the artifact,
collecting observations, evaluating held-out data, and resetting state. It does
not retrain.

For a run that failed before a retryable final checkpoint, collection-only can
preserve available raw observations but cannot turn it into a successful run.
Use its diagnostics to fix the cause and start a new run name.

## 7. Observation and logs

For a one-shot full state report:

```sh
make experiment-status
```

For repeated snapshots:

```sh
make observe
```

For bounded logs:

```sh
make logs SOURCE=all SERVICE=all SINCE='10 minutes ago' TAIL=200 FOLLOW=false
```

Narrow to a Guest or Host service when diagnosing a specific owner:

```sh
make logs SOURCE=vm VM=path-a SERVICE='nwdaf-*' FOLLOW=false
make logs SOURCE=ml SERVICE=pymtlf-root FOLLOW=false
```

Guest logs come from systemd journal; Host ML logs come from Docker. The log
command does not transform them into experiment evidence.

## 8. Manual process lifecycle

For diagnostics that do not use the complete experiment runner:

```sh
make experiment-start
make fl-training-start RUN_ID=<uuid-v4>
make fl-training-status RUN_ID=<same-uuid-v4>
make experiment-stop
```

This path is useful for component inspection, but it does not automatically
produce the complete single-run evidence contract.

## 9. Stop, reset, and halt

Stop processes while retaining state:

```sh
make experiment-stop
```

Review the exact reset scope:

```sh
make reset-show CONFIG_DIR=config/local/<name>
```

The output prints the required scenario confirmation. After confirming the
selection and with all experiment processes stopped:

```sh
make reset \
  CONFIG_DIR=config/local/<name> \
  RESET_CONFIRM=<exact-scenario-name>
```

Reset clears selected NRF/ADRF records, ADRF model storage, and selected PyMTLF
volume contents, then verifies the result. It retains the VMs, containers,
images, volumes, networks, datasets, generated config, and run records.

Finally, halt VMs separately when they are no longer needed:

```sh
make vm-halt
```

None of these normal commands destroys a VM.

## 10. Offline analysis

Compare two finalized runs:

```sh
make fl-analysis \
  BASELINE_RUN=<e0-run-directory> \
  TREATMENT_RUN=<treatment-run-directory> \
  OUTPUT_DIR=<new-analysis-directory>
```

Aggregate a formal series:

```sh
make fl-series-analysis \
  SERIES_ROOT=runs/protocol-hierarchical/e0-e2b \
  OUTPUT_DIR=<new-analysis-directory>
```

Analysis is independent of training and can be repeated from preserved
`run.json` and `events.jsonl` records without starting the testbed.
