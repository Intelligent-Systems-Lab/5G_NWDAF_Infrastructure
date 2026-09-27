# Scenario Definition Reference

Protocol image-classification scenarios use schema version `2`. They are
repository-relative YAML files selected by `config-create FROM=...`.

## Common fields

| Field | Meaning |
| --- | --- |
| `schemaVersion` | Must be `2`. |
| `name` | Scenario identity recorded in the generated manifest and run. |
| `kind` | Descriptive scenario category; it is not a runtime selector. |
| `experiment` | Optional retained formal-series metadata. |
| `topology` | Protocol behavior after a Branch failure. |
| `workload` | Dataset, event, model family, and component-native seed artifact identity. |
| `partition` | Dataset identity, split sizes, seed, and optional per-Leaf class allocation. |
| `training` | Accepted rounds, batch size, learning rate, local epochs, and optional FedProx coefficient. |
| `fault` | Optional accepted-round barrier and ordered nodes to stop. |
| `observation` | Required polling and heartbeat policy for a fault scenario. |

## Workload

`workload.profile` is `image-classification`. Supported datasets are `mnist`
and `cifar10`; each has a fixed event, model interoperability identity, model
family, and seed-model ID understood by NWDAF and PyMTLF.

`workload.seedArtifactKey` is the PyMTLF model artifact identity. The committed
scenario contains a valid seed identity. For a formal run,
`config-create SEED=<n>` generates the selected seed model and replaces the
scenario snapshot's seed artifact identity with the generated model's identity.

## Partition

Required fields are:

- `seed`: non-negative partition seed;
- `samplesPerLeaf`: sample count for every selected Leaf;
- `validationSamples` and `heldOutSamples`;
- optional `datasetId`, which allows conditions to share one generated split.

There are three allocation forms:

1. no explicit quota: all ten classes are balanced at every Leaf;
2. `leafLabels`: each listed Leaf receives equal quotas from only its listed
   classes;
3. `leafClassCounts`: each listed Leaf supplies an exact positive quota per
   class, summing to `samplesPerLeaf`.

`leafLabels` and `leafClassCounts` are mutually exclusive. Explicit quotas
require `validationSource: official-train`.

With `validationSource: official-train`, validation is selected from the
official training split and `heldOutSamples` must cover the complete official
test split. With the default `official-test`, balanced validation and held-out
subsets are selected from the official test split.

## Training

`training.acceptedRounds`, `batchSize`, and `localEpochs` are positive
integers. `learningRate` is positive. Optional `proximalMu` is a finite,
non-negative override for a FedProx topology.

The compute device does not belong in the scenario. Select it at render time
with `DEVICE=cpu|gpu`.

## Topology and fault

`topology.onBranchFailure` is one of:

- `replace_branch`;
- `reparent_leaves_to_root`.

A normal scenario omits both `fault` and `observation`. A fault scenario
defines:

- `fault.normalAcceptedRounds`: inject after this many accepted rounds and
  before the final accepted round;
- `fault.stopNodes`: the active highest-priority Branch first, followed by any
  selected Leaves from the same group;
- `observation.pollIntervalMilliseconds`;
- `observation.heartbeatSeconds`.

The runner resolves the named nodes through the selected testbed and records the
actual NF, Guest service, and PyMTLF service identities in run evidence.

## Formal E0–E2b metadata

A retained formal scenario uses:

```yaml
experiment:
  series: e0-e2b
  condition: E0
```

The condition is one of `E0`, `E1`, `E2a`, or `E2b`. It controls formal
run organization and is checked against the selected scenario; it does not by
itself inject a failure. The `fault` section is the actual runner input.

Formal config generation requires `SEED` from 1 through 5 when invoked through
the retained series tool. Rendering replaces:

- `partition.seed` with the selected seed;
- `partition.datasetId` with `<dataset>-formal-s<seed>`;
- `workload.seedArtifactKey` with the generated seed-model identity.

This makes E0–E2b for one workload and seed consume the same dataset and initial
model while retaining different scenario behavior.
