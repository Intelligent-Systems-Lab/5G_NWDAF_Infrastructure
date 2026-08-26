# Native Config Reference

`config-create` renders a complete directory because the processes do not read
`testbed.yaml` or `scenario.yaml` themselves. Each service receives its own
native YAML/JSON, while `manifest.yaml` records provenance and runtime choices.

## File map

| Files | Consumer | Primary rendered source |
| --- | --- | --- |
| `nrfcfg.yaml` | NRF | NRF SBI, MongoDB, PLMN |
| `nssfcfg.yaml` | NSSF | PLMN, S-NSSAI, both TAIs |
| `udrcfg.yaml`, `udmcfg.yaml`, `ausfcfg.yaml`, `pcfcfg.yaml` | free5GC NFs | SBI/NRF/MongoDB, identity and slice |
| `amfcfg.yaml` | AMF | SBI, N2, PLMN, S-NSSAI, TAIs |
| `smfcfg.yaml` | SMF | SBI/N4, DNN, UPFs, TAI routes, sampling/URR period |
| `uerouting.yaml` | SMF routing | Derived Path SUPIs and selected UPF routes |
| `upfcfg-a.yaml`, `upfcfg-b.yaml` | go-upf | N3/N4/N6, UE pool, GTP interface, Event Exposure, PseudoDriver |
| `nwdafcfg-a.yaml`, `-b.yaml`, `-c.yaml` | three NWDAFs | NF identity/role, SBI, NRF/ADRF, ML endpoints, FL contracts |
| `adrfcfg.yaml` | ADRF | stable NF identity, SBI, MongoDB, model storage and retrieval |
| `pyanlf-a.yaml`, `-b.yaml` | PyAnLF containers | sampling, analytics/accuracy delivery, model device and endpoints |
| `pymtlf-a.yaml`, `-b.yaml` | FL clients | collection trigger, local fitting runtime parameters, ADRF retrieval, artifacts and device |
| `pymtlf-c.yaml` | FL server | Flat orchestration, participant source, training trigger, Server-owned client epochs, rounds, Model Provision/Monitor, validation and publication |
| `consumer.yaml` | Consumer/callback server | NRF discovery, Path scope, Internal Group, callback and reporting |
| `webuicfg.yaml` | optional WebConsole | management HTTP, NRF, MongoDB, loopback billing compatibility |
| `ueransim/gnb-*.yaml` | two gNBs | PLMN/TAI, N2/N3, AMF and S-NSSAI |
| `ueransim/ue1.yaml` … `ue6.yaml` | six UEs | derived SUPI, authentication, gNB, DNN and S-NSSAI |
| `subscriber/*.json` | MongoDB fixture loader | same derived SUPIs, authentication, DNN, slice and Internal Group |
| `network/*.yaml` | Guest network reconciler | VM base interfaces and service aliases |
| `manifest.yaml` | Host tooling | topology/scenario provenance, runtime policy, fixture and dataset paths |

## Production Flat PyMTLF ownership

All three PyMTLF processes use `runtime.mode: federated`, but their configured
engines and policy ownership differ:

- A/B configure the Client engine and
  `training_data.collection_trigger: consumer_subscription`. Their `training`
  section owns device, batch size, learning rate, validation ratio, and random
  seed; it must not contain `epochs`.
- C configures the Server engine, `orchestration.mode: flat`,
  `participant_source: monitor_scopes`, degradation-triggered training, and a
  disabled private trigger. It owns `round_count` and
  `client_training.epochs`.

For each round, C writes its client-training directive into the typed
`ROUND_INPUT` artifact manifest and sends A/B a training PATCH containing that
artifact's `mLModelUrl`. A/B validate the artifact and use the embedded epochs;
epochs are not an extra ad-hoc field in the public training request. Adding a
Client-local epochs value is therefore a schema error, not an override.

## Renderer-owned and advanced values

Topology, address, identity, scenario timing, dataset references, CPU/GPU
policy, and WebConsole enablement are renderer-owned. Change their upstream
input and create a new set whenever possible. This keeps NF, RAN, ML, Consumer,
fixture, and network values synchronized.

A local native file may be edited when an experiment needs a component option
the renderer does not model. Treat the directory as one versioned unit:

1. create a new `config/local/NAME` rather than editing `config/default`;
2. change all affected native files deliberately;
3. run `make config-validate CONFIG_DIR=...` and review every finding;
4. record intentional deviations with the experiment results.

Diagnostics compare native values with `TESTBED`, scenario, component locks,
seed-model dimensions, fixtures, and other native files. They do not merge
files, repair drift, or gate `experiment-start`. A syntactically valid but
inconsistent set may start and then fail at the responsible component.

## Activation

Guest startup hashes and stages the whole directory below
`/etc/5g-nwdaf-infrastructure/config-sets/<name>-<hash-prefix>/`. The Guest
`active` symlink selects that immutable staged copy. Host ML containers bind
the selected native files read-only and carry config name/hash labels. Validation,
Guest activation, container labels, logs, and status all use the same canonical
tree SHA-256 for that directory.

Do not edit an already staged Guest copy. Create or edit the Host config and
restart the relevant lifecycle so identity, logs, and status remain traceable.
