# Command Reference

The default deployment is `testbed.protocol-hierarchical.yaml`; the default
generated config directory comes from that file. Pass `TESTBED=...` and
`CONFIG_DIR=...` explicitly when using another selection.

## Help and repository checks

| Target | Effect |
| --- | --- |
| `make help` | Show the primary experiment lifecycle. |
| `make help-advanced` | Show config, dataset, domain, runner, observation, reset, and analysis commands. |
| `make help-dev` | Show repository checks. |
| `make test` | Run host-only and synthetic checks without a real provider, VM, or container. |

## Configuration and datasets

| Target | Required or important variables | Effect |
| --- | --- | --- |
| `config-create` | `FROM`; optional `NAME`, `DEVICE`, `SEED`, `FORCE` | Render one local config set. |
| `config-validate` | optional `CONFIG_DIR` | Check the generated cross-component contract. |
| `dataset-generate` | optional `CONFIG_DIR` | Download/cache official sources and generate or reuse the selected split. |
| `dataset-validate` | optional `CONFIG_DIR` | Validate the selected generated dataset. |
| `dataset-show` | optional `CONFIG_DIR` | Print its identity and sample summary. |

`FROM` must be a repository-relative YAML path. `DEVICE` is `cpu` or `gpu`.
`SEED` is used only with an E0–E2b scenario. `FORCE=true` removes and
rerenders an existing config output, so use it only when that config is not
active.

These commands are Host-side and do not contact Vagrant or VirtualBox.

## Provider and VM lifecycle

| Target | Effect |
| --- | --- |
| `experiment-validate` | Check Host, component, config, dataset, Compose, capacity, and Vagrant definition prerequisites. |
| `vm-up` | Create or start and provision the exact selected VMs. |
| `vm-status` | Read the exact selected provider state. |
| `vm-halt` | Gracefully halt the exact selected VMs. |

All four targets contact the real provider, including validation and status.
Run them only through the repository entrypoints in the approved Host context.

## Process lifecycle

| Target | Domain | Effect |
| --- | --- | --- |
| `services-start` | Guests/provider | Activate selected config and start selected Guest services. |
| `services-status` | Guests/provider | Show selected Guest unit state. |
| `services-stop` | Guests/provider | Stop selected Guest units and retain Guest state. |
| `ml-start` | Host Docker | Build and start selected PyMTLF containers. |
| `ml-status` | Host Docker | Show exact selected container, image, device, config, and health state. |
| `ml-stop` | Host Docker | Stop selected containers and retain containers, images, and volumes. |
| `experiment-start` | Guests and Host | Generate/reuse the dataset, then start both process domains. |
| `experiment-status` | Guests and Host | Show VM, Guest, container, registration, backend, and optional training state. |
| `experiment-stop` | Guests and Host | Stop both process domains and retain state and VMs. |

`experiment-start` requires already-running selected VMs and refuses an
already-active experiment. The single-run experiment runner invokes this
lifecycle itself; do not pre-start processes before calling the runner.

## Training and experiment runners

| Target | Required variables | Effect |
| --- | --- | --- |
| `fl-training-start` | `RUN_ID=<uuid-v4>`; optional `MODEL_FAMILY_ID` | Submit a training request to an already-running Root. |
| `fl-training-status` | same `RUN_ID`; optional `MODEL_FAMILY_ID` | Read the training resource state. |
| `fl-experiment-run` | `RUN_NAME=<safe-name>` | Run the complete GPU single-experiment lifecycle and evidence collection. |
| `fl-experiment-collect` | the same `RUN_NAME` | Resume collection from a compatible existing checkpoint without retraining. |
| `fl-series-run` | `WORKLOAD`, `SEEDS`, `CONDITIONS`, `RUN_PREFIX` | Sequentially invoke config, dataset, and single-run flow for formal slots. |

`RUN_NAME` must match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`. The single-run and
series runners require GPU-generated config. They contact the running provider
environment and must use the approved Host context.

`fl-series-run` accepts `WORKLOAD=mnist|cifar10`, comma-separated seeds from
1 through 5, and comma-separated conditions from `E0,E1,E2a,E2b`. It stops on
the first unsuccessful slot.

## Observation

| Target | Important variables | Effect |
| --- | --- | --- |
| `observe` | optional `CONFIG_DIR`; `OBSERVE_INTERVAL` for repeated snapshots | Show parallel VM, Guest-service, and Host-container snapshots. |
| `logs` | `SOURCE`, `VM`, `SERVICE`, `SINCE`, `TAIL`, `FOLLOW` | Read/follow Guest journal and/or Host container logs. |

`SOURCE` is `vm`, `ml`, or `all`. `FOLLOW=false` returns a bounded
snapshot. `SOURCE=ml` stays in Host Docker; `vm` and `all` contact Guests
through the provider boundary.

## Reset

| Target | Required variables | Effect |
| --- | --- | --- |
| `reset-show` | optional `CONFIG_DIR` | Show the selected destructive scope without deleting state. |
| `reset` | `RESET_CONFIRM=<scenario-name>` | Clear only selected NRF/ADRF/model and PyMTLF volume contents, then verify. |

Both reset targets inspect real provider state. Reset requires the selected VMs
running, experiment services and containers stopped, the active config identity
matching the selection, and no unexpected project containers or volumes.
Containers, images, volumes, networks, VMs, source, datasets, and run records
are retained.

## Offline analysis

| Target | Required variables | Output |
| --- | --- | --- |
| `fl-analysis` | `BASELINE_RUN`, `TREATMENT_RUN`, `OUTPUT_DIR` | `rounds.csv`, `summary.csv`, `comparison.svg` |
| `fl-series-analysis` | optional `SERIES_ROOT`; required `OUTPUT_DIR` | `rounds.csv`, `summary.csv`, `details.json`, `curves.svg` |

Analysis reads finalized run evidence and does not require VMs, containers, or
the provider. Output must be separate from raw run directories and existing
analysis files are not overwritten.
