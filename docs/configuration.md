# Configuration

An experiment is built from three layers. `TESTBED` describes where software
runs, a scenario describes what experiment should happen, and `config-create`
renders the native files consumed by each process. Traffic profiles belong to
the scenario and generated Parquet belongs to neither source tree.

## Create one experiment

Use the committed `testbed.yaml`, or copy it to an ignored local file when the
Host topology differs. There is no implicit `testbed.local.yaml` overlay.

### Use a committed example unchanged

```sh
cp testbed.yaml testbed.lab.yaml       # only when topology changes are needed
make config-create \
  TESTBED=testbed.lab.yaml \
  NAME=my-experiment \
  FROM=experiments/examples/full-core-cat-transition/scenario.yaml \
  DEVICE=gpu \
  WEBCONSOLE=false
```

`FROM` is always an explicit repository-relative YAML path. It has no default.
`NAME` creates `config/local/NAME`, `DEVICE` is `cpu` or `gpu`, and
`WEBCONSOLE` is `true` or `false`. The renderer refuses to overwrite an
existing directory; choose a new name or remove an unwanted local set yourself.

### Customize the dataset pattern

Do not edit a committed directory under `experiments/examples/`. Copy the whole
example, including its scenario and both Path traffic profiles, into ignored
user space:

```sh
cp -a \
  experiments/examples/full-core-cat-transition \
  experiments/local/my-pattern
```

The copied inputs are:

```text
experiments/local/my-pattern/
├── scenario.yaml
└── traffic/
    ├── path-a.json
    └── path-b.json
```

Change `name` in `scenario.yaml` to a unique experiment identity. Edit
`traffic/path-a.json` and `traffic/path-b.json` to change raw stable/degraded
traffic values, phase lengths, jitter, or the raw window interval. The existing
`trafficProfiles` paths remain valid because they are relative to
`scenario.yaml`. Sampling, accuracy-report policy, training rounds, and closure
timing belong in `scenario.yaml`, not in the traffic JSON files.

Render a new complete config from the copied scenario:

```sh
make config-create \
  NAME=my-pattern \
  FROM=experiments/local/my-pattern/scenario.yaml \
  DEVICE=gpu \
  WEBCONSOLE=false
```

Use `DEVICE=cpu` when CUDA is not wanted. Do not edit generated Parquet files;
`dataset-generate` derives them from the copied scenario, traffic profiles, and
the UE pools in the selected testbed.

### Generate and inspect the dataset

Generate and inspect the dataset described by that complete config. The commands
below continue the unchanged example as `config/local/my-experiment`; use
`config/local/my-pattern` instead after following the customization workflow.

```sh
make config-validate TESTBED=testbed.lab.yaml CONFIG_DIR=config/local/my-experiment
make dataset-generate TESTBED=testbed.lab.yaml CONFIG_DIR=config/local/my-experiment
make dataset-show TESTBED=testbed.lab.yaml CONFIG_DIR=config/local/my-experiment
```

`config-validate` and `experiment-validate` are explicit diagnostics. They
report inconsistent, risky, or unsupported combinations and return non-zero
when findings exist, but neither is invoked by `experiment-start`. Startup
still stops for conditions that make execution impossible or unsafe, such as
missing inputs, unreadable YAML/JSON, invalid artifacts, inactive VMs, process
collisions, unavailable bind addresses or GPU runtime, and component startup
failures.

Keep the same `TESTBED` and `CONFIG_DIR` pair for generation, startup, status,
and reset. `CONFIG_DIR` overrides `TESTBED:config.directory`; when it is omitted,
the selected testbed's directory is used.

## Where to edit

| Desired change | Authoritative input |
| --- | --- |
| VM size, network, IP, NF placement, PLMN, UE identity, service endpoint | `testbed.yaml` or a complete local testbed YAML |
| Sampling, monitoring policy, FL rounds, preparation/closure budget | selected `scenario.yaml` |
| Raw traffic rows, warm-start boundary, stable/degraded stimulus | scenario-relative `traffic/*.json` |
| CPU/GPU choice or optional WebConsole | `DEVICE` and `WEBCONSOLE` at render time |
| Component-native behavior not modeled by the renderer | a complete generated config under `config/local/` |

Prefer changing the higher-level source and rendering a new config. Native
edits are supported for advanced experiments, but the user then owns their
cross-file consistency; diagnostics explain drift without silently rewriting
it.

## Reference documents

- [Testbed reference](configuration/testbed-reference.md) — topology, resources,
  identity, endpoint, placement, runtime, and operations fields.
- [Scenario reference](configuration/scenario-reference.md) — experiment timing,
  monitoring, and training policy.
- [Traffic profile reference](configuration/traffic-profile-reference.md) — raw
  row generation and stable/degraded phases.
- [Native config reference](configuration/native-config-reference.md) — every
  rendered file, its consumer, and safe editing boundary.
- [Dataset reference](configuration/dataset-reference.md) — row/window/
  observation/report/sample terminology, artifacts, and lifecycle.

`config/default` is the committed reference output. `config/local/` is ignored
user space. `experiments/examples/` contains committed examples, while
`experiments/local/` is ignored space for new scenarios and their traffic
profiles.
