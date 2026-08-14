# Traffic Profile Reference

Each Path profile is a JSON input to the deterministic dataset generator. It
creates reproducible UPF PseudoDriver stimulus; it is not a claim of real
application traffic or a performance benchmark.

## Fields

| Field | Type/unit | Meaning |
| --- | --- | --- |
| `schemaVersion` | integer | Dataset/profile contract; currently `2`. |
| `path` | `a` or `b` | Prevents a profile from being silently used for the wrong Path. |
| `windowSeconds` | positive seconds | Timestamp interval between generated raw traffic windows. |
| `breakingTimeSeconds` | positive seconds | Replay boundary: rows before it form historical warm-start; live replay continues from it. |
| `stableWindows` | positive count | Number of raw windows generated with stable traffic before the traffic-transition boundary. |
| `degradedWindows` | positive count | Number of raw windows generated after that boundary. |
| `postBoundaryMode` | `stable` or `degraded` | Whether post-boundary rows retain stable values or use degraded values. |
| `stableUplinkBytes`, `stableDownlinkBytes` | positive bytes/window | Base lengths before the traffic boundary. |
| `degradedUplinkBytes`, `degradedDownlinkBytes` | positive bytes/window | Base lengths after a degrading boundary. |
| `degradedJitterScale` | positive integer | Multiplier for deterministic post-boundary byte variation. |

The generator derives UE IPs from `testbed.yaml`; profiles must not contain
site-specific IP addresses.

## Timeline and row count

For a profile:

```text
traffic transition = stableWindows × windowSeconds
total duration      = (stableWindows + degradedWindows) × windowSeconds
live stable lead    = traffic transition − breakingTimeSeconds
rows                = total windows × UE count × 2 directions
```

The committed full-core Path A profile has 1-second raw windows, a 900-second
warm-start boundary, a transition at 1800 seconds, and a degraded tail of 3630
seconds. Path B has the same timeline but `postBoundaryMode: stable`.

`windowSeconds` is not the analytics sampling period. go-upf aggregates or
replays the raw windows over the native UPF/AnLF sampling interval. With
`windowSeconds: 1` and `samplingIntervalSeconds: 30`, thirty raw windows form
one observation. A 90-second accuracy report can therefore contain three
observations. See [Dataset reference](dataset-reference.md) for the full term
mapping.

Keep phase boundaries aligned to sampling when predictable closure timing is
important. `config-validate` reports non-integral alignment, insufficient
warm-start, too-short stable/degraded phases, and Path role inconsistencies.
These are experiment diagnostics rather than an automatic startup veto.
