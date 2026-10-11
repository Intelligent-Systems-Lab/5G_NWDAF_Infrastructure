# Paper companion: free5GC World Forum 2026

This directory relates the repository to the paper

> *Toward Resilient Hierarchical Federated Learning for 6G Networks: NWDAF
> Protocol Extensions and a free5GC-Based Realization*

prepared for the free5GC World Forum 2026. The paper cites this repository at
the release tag `free5gc-world-forum-2026`. Use that tag to study or rerun the
prototype described by the paper. Commits after the tag are continued
development and are not described by the paper.

## Documents

- This file: how the paper's experiment conditions, tables, and figures map to
  scenarios, commands, and analysis outputs.
- [Candidate Stage-2 change request text](candidate-stage2-text.md): the
  Stage-2 text that the paper proposes.
- [Candidate Stage-3 OpenAPI fragment](candidate-stage3-openapi.md): the
  added members and schemas that the paper proposes, with the constraints
  that OpenAPI cannot express.
- [`candidate-openapi.yaml`](candidate-openapi.yaml): an OpenAPI document of
  the same additions, with further rules and examples.
- [Candidate specification and implementation](specification-differences.md):
  where the prototype matches, differs from, or goes beyond the candidate
  specification, and how the fragment and the OpenAPI document differ.
- [Relation to upstream free5GC](free5gc-requirements.md): which components
  come from upstream free5GC, which do not, and what the prototype needs
  beyond the upstream release.
- [Reproduction notes](reproduction-notes.md): how to obtain the tagged
  source, the software versions used for the paper's experiments, and what is
  not published.

## What the tag contains

The tag identifies one commit of this repository. That commit selects the
exact NWDAF, NRF, ADRF, and PyMTLF commits through Git submodules and
[`components.lock.yaml`](../../components.lock.yaml); see
[Components](../components.md). The component repositories are not tagged
separately.

Two facts relate the tagged revision to the one that produced the paper's
results:

- The four components are at the commits used for the paper's experiments or
  at later commits that change only documentation and one code-generation
  comment. Their runtime source is the same.
- The testbed tooling in this repository was reorganized for public release
  after the experiments. The reorganized revision was validated with the
  bounded MNIST smoke run on freshly provisioned VMs. The full experiment
  matrix was not rerun on it.

## Terminology

The paper and this repository name the same roles differently.

| Paper | Repository |
| --- | --- |
| intermediate NWDAF (A, B, C) | Branch |
| standby or replacement intermediate (A*) | replacement Branch |
| leaf NWDAF | Leaf |
| root NWDAF | Root |

## Experiment conditions

Each condition in the paper is one scenario file per workload. The condition
is recorded in the `experiment.condition` field of each file.

| Condition | MNIST scenario | CIFAR-10 scenario |
| --- | --- | --- |
| E0 | `mnist/formal-baseline.yaml` | `cifar10/all-class-skew-baseline.yaml` |
| E1 | `mnist/formal-replacement.yaml` | `cifar10/all-class-skew-replacement.yaml` |
| E2a | `mnist/formal-reparent.yaml` | `cifar10/all-class-skew-reparent.yaml` |
| E2b | `mnist/formal-partial-reparent.yaml` | `cifar10/all-class-skew-partial-reparent.yaml` |

Paths are relative to `experiments/protocol-hierarchical/`. Round counts, fault
timing, data partitions, and training settings are defined in these files and
in [`testbed.protocol-hierarchical.yaml`](../../testbed.protocol-hierarchical.yaml);
see the [scenario reference](../configuration/scenario-reference.md).
`mnist/smoke.yaml` is a bounded check of the deployment and is not one of the
paper's conditions.

The paper uses seeds 1 through 5 for every condition and workload, which gives
40 runs.

## Commands

The paper's runs and their aggregation use two commands, described in
[Operations](../operations.md):

```sh
make fl-series-run \
  WORKLOAD=mnist \
  SEEDS=1,2,3,4,5 \
  CONDITIONS=E0,E1,E2a,E2b \
  RUN_PREFIX=<prefix>

make fl-series-analysis OUTPUT_DIR=<new-directory>
```

Repeat the series command with `WORKLOAD=cifar10`. No separate script
collection exists for the paper. The commands above call these sources:

| Purpose | Source |
| --- | --- |
| Scenario definitions | `experiments/protocol-hierarchical/` |
| Series and single-run execution, fault injection, collection | `scripts/host/fl-series-run.py`, `scripts/host/fl-experiment-run.py`, `scripts/host/fl_experiment.py` |
| Dataset partitions and seed models | `scripts/host/dataset.py`, `scripts/host/image_dataset.py`, `scripts/host/seed-model.py` |
| Offline aggregation | `scripts/host/fl-analysis.py`, `scripts/host/fl_series_analysis.py` |

## Paper results and analysis outputs

`fl-series-analysis` reads finalized run records and writes `summary.csv`,
`rounds.csv`, `details.json`, and `curves.svg`. Accuracy values are fractions;
the paper reports percentages and percentage points. Intervals are two-sided
95% Student-t intervals over the five seeds.

The tool produces some of the paper's values directly. Others must be computed
from the per-run records that the tool reads or exports.

| Paper content | Available from the analysis outputs |
| --- | --- |
| Learning-outcome table: validation accuracy, held-out test accuracy, and validation loss as five-seed means with intervals | Directly, in `summary.csv` (`endpointValidationAccuracy*`, `heldOutAccuracy*`, `endpointValidationLoss*`) |
| Held-out endpoint difference from E0, paired by seed | Not computed. `summary.csv` gives each condition's own held-out mean and interval. Compute the paired difference from `heldOutEvaluation.accuracy` in each run's `run.json`. |
| Timing table: the segments between failure, detection, repair start, last confirmed edge, and first repaired contribution | Only the total from failure to first repaired contribution is computed, as `recovery.failureToFirstContributionSeconds` per run in `details.json`. The segments must be computed from the per-run `timeline` in `details.json`, which lists the fault stop, `EDGE_UNAVAILABLE`, each CREATE, `EDGE_CONFIRMED`, and `ROUND_AGGREGATION` records with timestamps. |
| Repair timeline figure | Same per-run `timeline` records. |
| Accepted training paths in each condition | `successfulDirectParticipants` per accepted round in `rounds.csv`, and the `TOPOLOGY_ACCEPTANCE` and `EDGE_CONFIRMED` records in the per-run `timeline`. |
| Trajectory figure: per-round validation accuracy difference from E0, paired by seed | Not plotted. `rounds.csv` has the per-seed, per-round accuracy from which the differences are computed, and `summary.csv` gives the last-round difference (`pairedEndpointDeltaVsE0*`). `curves.svg` plots absolute mean curves with intervals. |
| Relative L2 difference between the final models of matched E1 and E2a runs | Not computed. It is calculated from the final models collected in the two run directories. |
| Content of the topology reports, including the reported child status | Not summarized. The raw records are in each run's `observations/*.jsonl`. |
| Trace-defined protocol-success judgement of each fault run | Not computed. It is judged from the per-run `timeline` and the raw observation records. |

`details.json` also reports a `recovery.status` for each fault run, with a
`recovery.recoveryRound` when the run is classified as recovered, and
`summary.csv` counts the runs in each class. The paper does not use this
classification.
