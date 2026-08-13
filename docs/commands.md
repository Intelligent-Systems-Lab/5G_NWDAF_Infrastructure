# Command Reference

The Make interface is layered so normal operators can use a small workflow
while advanced and developer commands remain available. `TESTBED` defaults to
`testbed.yaml`; commands accepting `CONFIG_DIR` otherwise use that testbed's
`config.directory`.

## Help and aggregate experiment lifecycle

| Command | Function and effect |
| --- | --- |
| `make help` | Show the primary experiment workflow. Read-only. |
| `make help-advanced` | Show configuration and independently managed runtime domains. Read-only. |
| `make help-dev` | Show repository and disposable-container tests. Read-only. |
| `make help-all` | Print all three help layers. Read-only. |
| `make experiment-validate CONFIG_DIR=...` | Run source, config, dataset, Host resource, Compose, GPU-if-requested, and Vagrant checks without starting runtime state. |
| `make experiment-start CONFIG_DIR=...` | Generate/validate data, then start Guest services, enabled WebConsole, ML containers, Consumer, and two subscriptions. Requires running VMs and stopped domains. |
| `make experiment-status CONFIG_DIR=...` | Show config identity, Host headroom, VM, guest service, ML, and subscription state. Read-only; rejects an incomplete running-backend snapshot while powered-off VMs appear as `not-running`. |
| `make experiment-stop` | Delete exact subscriptions, leave the NWDAFs and ML backends available for a fixed 40-second asynchronous cleanup grace, then stop process domains while retaining state and VMs. |
| `make observe` | Continuously display VM/service/container/subscription state. Read-only; failed sections retain their error and are marked unavailable while later intervals continue. |
| `make logs` | Follow all owned VM journals—including dedicated Consumer/Network units—and project ML container logs with UTC timestamps. Read-only; use `scripts/host/logs.sh` for filters. |

## Configuration and datasets

| Command | Function and effect |
| --- | --- |
| `make config-create NAME=... FROM=... DEVICE=... WEBCONSOLE=...` | Render a complete ignored config under `config/local/NAME`. `NAME` is the output name; `FROM` is `full-core-cat-transition` or `fl-closure-smoke`. |
| `make config-validate CONFIG_DIR=...` | Validate native fields plus cross-file topology, endpoint, identity, timing, fixture, and manifest relationships against `TESTBED`. Read-only. |
| `make dataset-generate CONFIG_DIR=...` | Generate or reuse the content-addressed Path A/B Parquet set described by the selected config. Writes ignored artifacts. |
| `make dataset-validate CONFIG_DIR=...` | Audit actual Parquet files and their manifest, schema, hashes, rows, IPs, timestamps, and scenario capacity. Read-only. |
| `make dataset-show CONFIG_DIR=...` | Print the resolved dataset identity and Path summaries. Read-only. |
| `make dataset-load CONFIG_DIR=...` | Upload, verify, and atomically activate only the matching dataset on each Path VM. Changes guest dataset state. |

## VM and process domains

| Command | Function and effect |
| --- | --- |
| `make vm-up` | Create/provision missing VMs or power on existing Core/Path VMs. Does not start experiment processes. |
| `make vm-status` | Show Vagrant VM power state. Read-only. |
| `make vm-halt` | Gracefully power off all three VMs without deleting them. |
| `make services-start CONFIG_DIR=...` | Sync helpers, stage config/data, apply subscriber fixtures, and start guest units in dependency order. Does not start ML or subscriptions. |
| `make services-status` | Show all 23 guest unit states plus current-invocation Registration/PDU readiness for six UEs. Read-only; readiness is `inactive`, `pending`, `successful`, or `failed`. |
| `make services-stop` | Stop guest experiment units in reverse order without halting VMs or deleting persistent data. |
| `make ml-start CONFIG_DIR=...` | Validate, build/reuse images, enforce CPU/GPU policy, and start the five production containers. |
| `make ml-status` | Show container/device/image identity plus a `StartedAt`-scoped FL milestone summary from matching-config PyMTLF-A/B/C logs. Read-only; unseen milestones remain `not-seen`. |
| `make ml-stop` | Stop only the production Compose project's running containers; retain containers, images, and volumes. |
| `make webconsole-start CONFIG_DIR=...` | If enabled, prepare/reuse the Core artifact and start WebConsole. MongoDB and NRF must be active. |
| `make webconsole-status` | Show the WebConsole unit and endpoint state. Read-only. |
| `make webconsole-stop` | Stop only WebConsole and retain its build artifacts and other services. |
| `make subscriptions-start` | Start the Core Consumer, discover two distinct path NWDAFs through NRF, and create two subscriptions. |
| `make subscriptions-status` | Show the Consumer service, local saved resource state, selected providers, exact locations, and independent Path A/B callback request counts/times. Read-only; it does not claim remote GET verification. |
| `make subscriptions-stop` | Delete the exact saved resources and stop the callback only after successful cleanup. |

## Subscriber and retained state

| Command | Function and effect |
| --- | --- |
| `make subscriber-data-show CONFIG_DIR=...` | Compare expected fixtures with current scoped records and display matching/missing/different/extra. Read-only. |
| `make subscriber-data-apply CONFIG_DIR=...` | Idempotently upsert the selected six subscriber and one Internal Group records. |
| `make subscriber-data-clear CONFIG_DIR=...` | Delete only the selected subscriber/group scope. |
| `make reset-show CONFIG_DIR=...` | Display retained experiment state, reset scope, and the required scenario confirmation. Read-only. |
| `make reset CONFIG_DIR=... RESET_CONFIRM=...` | Apply the scoped reset and immediately verify it. `RESET_CONFIRM` must equal the selected scenario name, such as `full-core-cat-transition`. |

## Repository and container tests

| Command | Function and effect |
| --- | --- |
| `make test` | Run shell/Python/YAML, lock, config, network, dataset, Compose, Consumer, and Vagrant definition checks. It does not start the production stack. |
| `make test-containers` | Run the disposable five-container CPU lifecycle test, then remove only its own containers, network, volumes, and generated config. |

`validate`, `status`, `show`, and `observe` are read-only. `create`, `generate`,
`load`, `apply`, `start`, `stop`, `reset`, `clear`, `up`, and `halt` change only
the scope described by their row. Internal validation and regression helpers
are not separate Make commands.
