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
| `make experiment-validate CONFIG_DIR=...` | Run source, config, dataset, Host resource, Compose, GPU-if-requested, and Vagrant diagnostics without starting runtime state. Findings return non-zero but do not gate a later start. |
| `make experiment-start CONFIG_DIR=...` | Generate/reuse data, then start Guest services, enabled WebConsole, ML containers, Consumer, and two subscriptions. Requires running VMs and stopped domains; it does not invoke diagnostic validation. |
| `make experiment-status CONFIG_DIR=...` | Show config identity, Host headroom, VM, guest service, ML, FL result, and subscription state. Read-only; rejects an incomplete running-backend snapshot while powered-off services appear as `not-running` and Core-owned saved state appears as `not-readable`. |
| `make experiment-stop` | Delete exact subscriptions, leave the NWDAFs and ML backends available for a fixed 40-second asynchronous cleanup grace, then stop process domains while retaining state and VMs. |
| `make observe` | Continuously display the active VM/service/container/subscription state without requiring `CONFIG_DIR`. It keeps the previous complete screen while collecting the next bounded parallel snapshot, then replaces it once; failed sections retain their error and are marked unavailable while later intervals continue. Read-only with respect to experiment state. |
| `make logs [SOURCE=...] [VM=...] [SERVICE=...] [SINCE=...] [TAIL=...] [FOLLOW=...]` | Follow filtered owned VM journals and project ML container logs with UTC timestamps. Source selectors default to all components and the time window defaults to the last ten minutes; `FOLLOW=false` prints a bounded snapshot. Read-only. |

## Configuration and datasets

| Command | Function and effect |
| --- | --- |
| `make config-create NAME=... FROM=experiments/.../scenario.yaml DEVICE=... WEBCONSOLE=...` | Render a complete ignored config under `config/local/NAME`. `FROM` is a required repository-relative scenario YAML path; existing output is not overwritten. |
| `make config-validate CONFIG_DIR=...` | Diagnose native fields plus cross-file topology, endpoint, identity, timing, fixture, and manifest relationships against `TESTBED`. Read-only; findings do not block startup. |
| `make dataset-generate CONFIG_DIR=...` | Generate or reuse the content-addressed Path A/B Parquet set described by the selected config. Writes ignored artifacts. |
| `make dataset-validate CONFIG_DIR=...` | Audit actual Parquet files and their manifest, schema, hashes, rows, IPs, timestamps, and scenario capacity. Read-only. |
| `make dataset-show CONFIG_DIR=...` | Verify and print dataset identity plus per-Path artifact, aggregation, reporting, timeline, model-window, trigger, and sample summaries. Read-only. |
| `make dataset-load CONFIG_DIR=...` | Upload, verify, and atomically activate only the matching dataset on each Path VM. Changes guest dataset state. |

## VM and process domains

| Command | Function and effect |
| --- | --- |
| `make vm-up` | After a fail-closed Host process/Vagrant UUID and state preflight, create/provision missing VMs or power on existing Core/Path VMs. Does not start experiment processes. |
| `make vm-status` | Show Vagrant VM power state from an approved Host context; this provider query does not prove that no orphan process exists. |
| `make vm-halt` | Gracefully power off all three VMs without deleting them. |
| `make services-start CONFIG_DIR=...` | Sync helpers, stage config/data, apply subscriber fixtures, and start guest units in dependency order. Does not start ML or subscriptions. |
| `make services-status` | Show all 23 guest unit states plus current-invocation Registration/PDU readiness for six UEs. Read-only; readiness is `inactive`, `pending`, `successful`, or `failed`. |
| `make services-stop` | Stop guest experiment units in reverse order without halting VMs or deleting persistent data. |
| `make ml-start CONFIG_DIR=...` | Resolve the selected config, enforce actual bind-address and CPU/GPU runtime requirements, build/reuse images, and start the five production containers. |
| `make ml-status` | Show container/device/image identity plus a `StartedAt`-scoped FL milestone summary and result from matching-config PyMTLF-A/B/C logs. Read-only; unseen milestones remain `not-seen`. |
| `make ml-stop` | Stop only the production Compose project's running containers and retain containers, images, and volumes. It does not modify VM or subscription state. |
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
| `make test` | Run shell/Python/YAML, lock, config, production Flat ownership, network, dataset, Compose, Consumer, and Vagrant definition checks. It does not start the production stack. |
| `make test-containers` | Run the disposable five-container CPU lifecycle test, load the rendered configs with the pinned ML components, then remove only its own containers, network, volumes, and generated config. |

The disposable container test does not start the three containing Go NWDAFs.
Its test-only PyMTLF health override accepts either a fully verified ready
context or an explicit `503 unavailable` context while still requiring process
artifacts to be ready and rejecting a capability mismatch. That override is
selected only by the isolated `cpu-smoke` project; production readiness and
`ml-start` are not relaxed.

`validate`, `status`, `show`, and `observe` are read-only. `create`, `generate`,
`load`, `apply`, `start`, `stop`, `reset`, `clear`, `up`, and `halt` change only
the scope described by their row. Internal validation and regression helpers
are not separate Make commands.

`make observe` accepts two per-invocation environment settings:

| Variable | Default | Meaning |
| --- | --- | --- |
| `OBSERVE_INTERVAL` | `5` | Seconds to retain a completed screen before beginning the next collection. Collection time is additional; rounds never overlap. |
| `OBSERVE_SECTION_TIMEOUT` | `30` | Positive integer seconds allowed for each bounded status collector before that section becomes `unavailable`. |

For example, `OBSERVE_INTERVAL=30 make observe` retains each completed screen
for 30 seconds. These values apply only to that command invocation.

`make logs` accepts the following per-invocation selectors:

| Variable | Default | Meaning |
| --- | --- | --- |
| `SOURCE` | `all` | Select `vm`, `ml`, or both sources. |
| `VM` | `all` | Select `core`, `path-a`, `path-b`, or all VM journals. This selector affects only the VM source. |
| `SERVICE` | `all` | Select one logical service, a glob such as `pymtlf-*`, or all services. An empty value also means all. |
| `SINCE` | `10 minutes ago` | Resolve one Host time expression and apply the same UTC instant to VM and container logs. |
| `TAIL` | `all` | Select a non-negative line count or all available lines after `SINCE`. |
| `FOLLOW` | `true` | Continue following logs; `false` returns after the current snapshot. |

The selectors are combined. For example, `SOURCE=vm VM=path-a` shows all Path
A Guest logs, while `SERVICE=pymtlf-c` is sufficient to find that unique ML
service across the default `SOURCE=all`. With `SOURCE=all`, `VM` narrows only
the VM portion and does not remove matching Host ML logs. Make command-line
values apply to that invocation only and are not retained by the next command.
