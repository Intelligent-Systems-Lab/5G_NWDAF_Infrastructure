# Configuration

## Two explicit inputs

Every command resolves two inputs:

- `TESTBED` selects one complete topology definition. It defaults to the
  committed `testbed.yaml`.
- `CONFIG_DIR` selects one complete native config set. When omitted, the
  command uses only `TESTBED:config.directory`, which defaults to
  `config/default`.

There is no implicit `testbed.local.yaml` overlay. To use a different topology,
copy `testbed.yaml` to an ignored local path, edit the complete definition, and
pass that same path explicitly:

```sh
cp testbed.yaml testbed.lab.yaml
make config-create TESTBED=testbed.lab.yaml NAME=lab DEVICE=cpu
make experiment-validate TESTBED=testbed.lab.yaml CONFIG_DIR=config/local/lab
```

Do not mix a config rendered from one topology with another `TESTBED`.

## Creating a complete config set

The normal interface is:

```sh
make config-create \
  NAME=my-experiment \
  FROM=full-core-cat-transition \
  DEVICE=gpu \
  WEBCONSOLE=false
```

- `NAME` names the ignored output directory below `config/local/`.
- `FROM` selects the committed example contract. Supported values are
  `full-core-cat-transition` and `fl-closure-smoke`.
- `DEVICE` is the explicit `gpu` or `cpu` ML policy.
- `WEBCONSOLE` decides whether aggregate experiment startup includes the
  optional Core service.

The renderer writes a complete set of native NF, RAN, Consumer, PyAnLF,
PyMTLF, subscriber, and generated network files. `manifest.yaml` records the
scenario, runtime policy, referenced inputs, and config identity. Always pass
the directory as a unit; copying individual YAML files between sets defeats
the consistency checks.

`config/default` is the committed full-core baseline. `config/local` is for
user-managed generated sets. `config/generated` is used by repository tests.

## Ownership of values

`testbed.yaml` owns placement, VM resources, networks, service endpoints,
mobile-network identity, UE pools, and the config directory selection. The
scenario owns experiment timing, traffic profile choice, preparation window,
monitor policy, training epochs/rounds, and closure budget. Native NF config is
renderer output from those two sources.

`mobileNetwork.plmn` is the single MCC/MNC source. The renderer derives:

- NF and UERANSIM PLMNs and TAIs
- 15-digit IMSI SUPIs for all six subscribers
- Internal Group ID
- Consumer path targets
- subscriber and group fixtures

`subscriberNumbers` are stable local ordinals that are padded into the MSIN.
GPSI/MSISDN is a separate identity and is intentionally not derived from PLMN;
edit it explicitly when telephone-number identities must change.

## Scenarios

`full-core-cat-transition` is the reference business experiment. Its historical
warm-start fills PyAnLF's prediction input before monitoring and its Path A
profile transitions to degradation while Path B stays stable.

`fl-closure-smoke` shortens the bounded closure path and includes enough
warm-start evidence for training as well as inference. It exercises the same
three-VM architecture and process contracts; it is not a different VM
topology.

Sampling and policy windows are validated as a relationship. The committed
profiles use 30-second reports and a 90-second monitor window, so a normal
window contains three observations. Validation rejects a scenario that lacks
the historical prediction input, trigger-time training/validation evidence,
startup margin, or post-trigger closure budget required by its native config.

## Dataset lifecycle

PseudoDriver Parquet files are generated, ignored artifacts. They are not
committed and are not inherited from the go-upf submodule.

```sh
make dataset-generate CONFIG_DIR=config/local/my-experiment
make dataset-validate CONFIG_DIR=config/local/my-experiment
make dataset-show CONFIG_DIR=config/local/my-experiment
```

Generation derives UE addresses from each `uePool`, resolves the scenario's
traffic profiles, and writes a content-addressed Path A/B set below
`.generated/datasets/`. Its manifest fixes schema, rows, IPs, timestamps,
breaking time, and content hashes. `services-start` stages the matching Path
artifact and atomically activates it in each guest; UPF startup rejects an
absent or incomplete active set.

## Validation and activation

`make config-validate CONFIG_DIR=...` checks the static config contract only.
`make experiment-validate CONFIG_DIR=...` additionally checks source locks,
Host prerequisites, the generated dataset, Compose policy, resource gates,
and the Vagrant definition without starting anything.

At service startup, the chosen config is hashed, staged to each VM, and made
active below `/etc/5g-nwdaf-infrastructure/config-sets/`; the
`/etc/5g-nwdaf-infrastructure/active` symlink selects the complete set. Network
aliases are rendered into a persistent Netplan fragment. Existing VMs receive
a hash-verified copy of current runtime helpers before activation, so ordinary
script changes do not require reprovisioning.
