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
| `make experiment-start CONFIG_DIR=...` | Generate/reuse data, then start the manifest-selected Guest and ML inventories. Production Flat also starts enabled WebConsole, Consumer, and two subscriptions; static deployments skip that chain. |
| `make experiment-status CONFIG_DIR=...` | Show config identity, Host headroom, VM, guest service, ML, FL result, and subscription state. Read-only; rejects an incomplete running-backend snapshot while powered-off services appear as `not-running` and Core-owned saved state appears as `not-readable`. |
| `make experiment-stop CONFIG_DIR=...` | For production Flat, delete exact subscriptions and allow asynchronous cleanup; then stop the selected process inventories while retaining state and VMs. |
| `make observe CONFIG_DIR=...` | Continuously display the selected VM/service/container/subscription state. It keeps the previous complete screen while collecting the next bounded parallel snapshot. Read-only. |
| `make logs [SOURCE=...] [VM=...] [SERVICE=...] [SINCE=...] [TAIL=...] [FOLLOW=...]` | Follow filtered owned VM journals and project ML container logs with UTC timestamps. Source selectors default to all components and the time window defaults to the last ten minutes; `FOLLOW=false` prints a bounded snapshot. Read-only. |

## Configuration and datasets

| Command | Function and effect |
| --- | --- |
| `make config-create TESTBED=... NAME=... FROM=experiments/.../scenario.yaml DEVICE=... WEBCONSOLE=...` | Render an ignored config under `config/local/NAME`. `TESTBED` selects one complete production Flat, static Flat, or static Hierarchical definition; static definitions require `WEBCONSOLE=false`. |
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
| `make services-status CONFIG_DIR=...` | Show manifest-selected guest units plus current-invocation Registration/PDU readiness for six or eight UEs. Read-only. |
| `make services-stop CONFIG_DIR=...` | Stop manifest-selected guest experiment units in reverse order without halting VMs or deleting persistent data. |
| `make ml-start CONFIG_DIR=...` | Enforce bind-address and CPU/GPU requirements, build/reuse images, and start the selected five or seven containers. |
| `make ml-status CONFIG_DIR=...` | Show selected container/device/image identity. Production Flat renders the A/B/C milestone summary, static Flat renders the Server/four-Client publication and exact cleanup summary, and static Hierarchical remains explicitly unevaluated until its execution phase. |
| `make ml-stop CONFIG_DIR=...` | Stop only the Compose project's running containers and retain containers, images, and selected volumes. |
| `make webconsole-start CONFIG_DIR=...` | If enabled, prepare/reuse the Core artifact and start WebConsole. MongoDB and NRF must be active. |
| `make webconsole-status` | Show the WebConsole unit and endpoint state. Read-only. |
| `make webconsole-stop` | Stop only WebConsole and retain its build artifacts and other services. |
| `make subscriptions-start` | Start the Core Consumer, discover two distinct path NWDAFs through NRF, and create two subscriptions. |
| `make subscriptions-status` | Show the Consumer service, local saved resource state, selected providers, exact locations, and independent Path A/B callback request counts/times. Read-only; it does not claim remote GET verification. |
| `make subscriptions-stop` | Delete the exact saved resources and stop the callback only after successful cleanup. |

## Static Flat controlled flow

These commands accept only a selected `static-flat` deployment and require one
operator-retained canonical lowercase UUIDv4 `RUN_ID`. They derive the Server,
four Clients, collection profiles, endpoints, model family, and ownership from
the selected manifest and native configs. A non-static-Flat topology, a
selected/active config mismatch, an unhealthy selected container, or a stale
or contradictory resource fails closed.

| Command | Function and effect |
| --- | --- |
| `make fl-collection-start TESTBED=... CONFIG_DIR=... RUN_ID=...` | Create or idempotently recover the four exact private collection requests, wait for all four to collect, and perform bounded exact rollback if a partial create fails. |
| `make fl-collection-status TESTBED=... CONFIG_DIR=... RUN_ID=...` | Show each owner's request/profile identity, state, resolved UE and active peer counts, stored records/observations, descriptor state, and cleanup state. Read-only. |
| `make fl-collection-stop TESTBED=... CONFIG_DIR=... RUN_ID=...` | Delete the four exact peer collection resources and succeed only after every request retains its descriptor with zero active/pending peer resources and no cleanup pending. |
| `make fl-training-start TESTBED=... CONFIG_DIR=... RUN_ID=... [MODEL_FAMILY_ID=...]` | Require four current retained descriptors, resolve the selected Server family, and create or idempotently recover one manual static Flat training request. |
| `make fl-training-status TESTBED=... CONFIG_DIR=... RUN_ID=... [MODEL_FAMILY_ID=...]` | Show the exact top-level request identity, family, mode, participant source, state, rounds, candidate digest, and bounded failure detail. Read-only. |

Collection stop retains the stored descriptor and data for the configured
retention window; it is not a data reset. If more than one retained collection
group matches a Client's training request, dataset preparation rejects the
ambiguity. Preserve the evidence, then wait for descriptor expiry or use the
selected guarded reset before starting a fresh run. The commands do not create
a Host run ledger, automatically generate `RUN_ID`, start traffic, stop the
experiment, or reset retained state.

## Subscriber and retained state

| Command | Function and effect |
| --- | --- |
| `make subscriber-data-show CONFIG_DIR=...` | Compare expected fixtures with current scoped records and display matching/missing/different/extra. Read-only. |
| `make subscriber-data-apply CONFIG_DIR=...` | Idempotently upsert selected subscriber/Internal Group records (6/1 production or 8/4 static). |
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
