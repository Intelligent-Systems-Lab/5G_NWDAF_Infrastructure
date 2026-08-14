# Dataset Reference

The dataset pipeline turns scenario traffic profiles and testbed UE pools into
deterministic Path A/B Parquet artifacts for go-upf PseudoDriver replay.
Generated data lives below `.generated/datasets/<dataset-set-id>/` and is not
committed.

## Terms

| Term | Meaning |
| --- | --- |
| raw row | One `(timestamp, UE IP, direction, byte length, action)` Parquet record. Each raw window produces uplink and downlink rows for every UE. |
| raw window | One traffic-profile step separated by `windowSeconds`. |
| warm-start history | Rows before `breakingTimeSeconds`, loaded before live replay. |
| observation | UPF/AnLF aggregation unit, separated by `samplingIntervalSeconds`. |
| prediction input window | Number of observations consumed by the seed model (`seq_length`). |
| prediction output | Future observations predicted by the model (`out_seq_len`). |
| accuracy report | Matched prediction/ground-truth evidence accumulated over `reportPeriodSeconds`. |
| training sample | One retained sequence/target pair after model windowing, purge separation, and validation split. It is not one raw row. |

In the reference full-core experiment:

```text
1-second raw windows
   × 30 windows per observation
= 30-second analytics observations
   × 3 observations per accuracy report
= 90-second accuracy reports
```

The seed model consumes 30 observations and predicts one. A 900-second
warm-start therefore supplies 30 inference observations, not 900 training
samples. Dataset resolution separately derives historical and trigger-time
training/validation counts.

## Lifecycle

```sh
make dataset-generate CONFIG_DIR=config/local/my-experiment
make dataset-show CONFIG_DIR=config/local/my-experiment
make dataset-validate CONFIG_DIR=config/local/my-experiment
make dataset-load CONFIG_DIR=config/local/my-experiment
```

- `generate` resolves the selected scenario, profiles, UE pools, native
  sampling/model settings, and generator source hash. Identical inputs reuse
  the same content-addressed set.
- `show` verifies the artifact and prints readable identity, size, aggregation,
  report capacity, timeline, model-window, trigger, and sample summaries.
- `validate` audits the actual Parquet schema, hashes, rows, UE IPs, timestamps,
  and manifest against the resolved specification.
- `load` uploads only the matching Path artifact and atomically activates it in
  `/var/lib/5g-nwdaf-infrastructure/datasets/active` on each Path VM. Normal
  `services-start` already performs this step.

Each set contains a root `manifest.json` and `resolved-spec.json`, plus
`path-a/` and `path-b/` directories containing `traffic.parquet`, `file.json`,
and a Path manifest. Content hashes detect manual edits or partial staging.

## Timing and capacity

`dataset-show` reports trigger values relative to live experiment start:

- `earliest_after_start` is the first policy timing at which required degrading
  hits can exist after the stable lead;
- `bounded_after_start` adds one report/sampling margin;
- `bounded_closure` adds the scenario closure budget for FL and post-cutover
  evidence.

These are designed timing bounds, not an automatic completion timer. The
experiment is complete only when current-run status observes an evaluated,
non-degrading post-cutover accuracy report.

Cross-file timing and capacity inconsistencies are diagnostics. They appear in
`config-validate` but do not prevent generation or startup. Structural failures
still stop the pipeline: missing/unreadable scenario or profile, unsupported
schema/path identity, non-positive values needed for generation, impossible UE
pool sizing, invalid model dimensions/validation ratio, or a corrupt artifact.
