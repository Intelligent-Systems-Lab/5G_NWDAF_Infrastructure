# Scenario Reference

A scenario is a repository-relative YAML document that describes experiment
semantics independent of Host topology. Pass its full path to `config-create`:

```sh
make config-create \
  NAME=my-experiment \
  FROM=experiments/local/my-experiment/scenario.yaml \
  DEVICE=cpu WEBCONSOLE=false
```

Keep its traffic profiles beside it. Paths in `trafficProfiles` are resolved
relative to the scenario file, not the repository root or current shell.

## Fields

| Field | Type/unit | Meaning |
| --- | --- | --- |
| `schemaVersion` | integer | Scenario contract; currently `2`. |
| `name` | string | Stable scenario identity recorded in config and dataset manifests and used by guarded reset confirmation. |
| `kind` | enum | `business-acceptance` or `bounded-smoke`; descriptive intent, not a different topology. |
| `warmStartMode` | enum | `inference-only` fills prediction history; `inference-and-training` also expects usable training/validation evidence before live replay. |
| `samplingIntervalSeconds` | positive seconds | One analytics observation interval expected from UPF/AnLF. |
| `trafficProfiles.a`, `.b` | relative JSON paths | Raw Path A/B stimulus definitions. |

### `monitoring`

| Field | Meaning |
| --- | --- |
| `reportPeriodSeconds` | Time covered by one accuracy report from each Path. |
| `minimumReferenceReports` | Stable evaluated reports required before degradation decisions. |
| `decisionWindowSize` | Number of recent evaluated reports kept by the policy. |
| `requiredHits` | Degrading reports required inside the decision window to trigger FL. |

Normally `requiredHits <= decisionWindowSize`, the report period is an integer
multiple of sampling, and one report can contain at least the native
`min_matched_predictions`.

### `training`

| Field | Meaning |
| --- | --- |
| `minimumSamples` | Minimum local training samples admitted per client. Small positive datasets remain valid. |
| `localEpochs` | Local dataset passes the coordinator requires from every Client in each federated round. The renderer writes this Server-owned value to `pymtlf-c.yaml`, not to the A/B Client configs. |
| `fittingRounds` | Coordinator round count. |
| `preparationDataWindowSeconds` | ADRF history requested for client preparation/fallback. |
| `closureBudgetSeconds` | Time reserved after the bounded degradation trigger for training, publication, adoption, cutover, and post-cutover evidence. |
| `enforcePerformanceGate` | Whether candidate metrics can veto publication; reference experiments use `false` while still performing final validation. |

The renderer transfers these values into PyAnLF/PyMTLF and related 5GC timing
fields. A native edit can intentionally differ; `config-validate` reports the
difference without changing or blocking the run.

`localEpochs` is orchestration policy. PyMTLF-C embeds it as
`fl_metadata.client_training.epochs` in each typed `ROUND_INPUT` artifact; the
round PATCH sent to A/B carries the artifact URL. Each Client validates that
artifact and uses the supplied value for local fitting. A/B still own local
runtime choices such as device, batch size, learning rate, validation ratio,
and random seed, but a federated Client config must not declare its own
`epochs`.

### Protocol image-classification scenarios

An image scenario uses `workload.profile: image-classification` and selects
MNIST or CIFAR-10 plus its component-native model identity. `partition` owns the
seed and Leaf/validation/held-out sample counts. `training` owns accepted rounds,
batch size, learning rate, and the required positive `localEpochs`; it must not
contain a device. Physical device assignment belongs to
`TESTBED.mlRuntime.services`, while `DEVICE=cpu` remains the coordinated
render-time fallback.

For protocol Hierarchical image scenarios, `training.localEpochs` is the only
operator-authored Leaf epoch value. Normal scenarios set it to `1`, and the
Branch-replacement scenarios set it to `32`. The renderer uses that value and
the Leaf role to generate each native
`report_after: {count: <localEpochs>, unit: epoch}` instruction. It does not use
a default when the field is absent.

The retained normal scenarios contain two accepted rounds and no fault block.
A Branch-replacement scenario contains eight accepted rounds, 8,000 samples per
Leaf, 32 local epochs, plus:

```yaml
fault:
  mode: branch-replacement
  branchGroup: area-a
  normalAcceptedRounds: 2
  restoredAcceptedRounds: 1
observation:
  pollIntervalMilliseconds: 250
  heartbeatSeconds: 30
```

The group name is resolved against the selected protocol topology. The runner
stops its current highest-priority Branch pair, but the Root remains responsible
for selecting and preparing the next candidate. No scenario field fixes a
degraded-round count or gates replacement timing.

## Committed examples

- `experiments/examples/full-core-cat-transition/scenario.yaml` is the business
  E2E: warm-start fills inference history, Path A degrades, Path B remains the
  control, and C coordinates the complete FL closure.
- `experiments/examples/fl-closure-smoke/scenario.yaml` uses a shorter live
  path and supplies training evidence during warm-start for integration work.

Copy an entire example directory to `experiments/local/` before modifying it.
The local directory is ignored; generated configs record the selected path and
validated scenario semantics.
