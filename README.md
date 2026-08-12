# 5G NWDAF Infrastructure

Reproducible three-VM plus Host-container infrastructure for a dual-TAI
free5GC/NWDAF testbed.
The repository owns topology, component revision locks, native configuration,
guest builds, service lifecycle, and a single consumer that discovers and
subscribes to the two path NWDAFs.

The first topology has three independently managed virtual machines and five
independently managed ML containers:

- `core`: free5GC control plane, MongoDB, ADRF, NWDAF-C, and the
  subscription consumer;
- `path-a`: UPF-A, gNB-A, UE1-3, and NWDAF-A;
- `path-b`: UPF-B, gNB-B, UE4-6, and NWDAF-B;
- Host Docker: PyAnLF-A/B and PyMTLF-A/B/C, one process per container.

VM power, guest services, Host ML containers, and NWDAF subscriptions are
separate lifecycles. Bringing up a VM does not automatically start either the
5GC processes or containers, and starting either execution domain does not
automatically create a subscription.

## Repository layout

- `NFs/`: 3GPP network functions, including NWDAF, ADRF, and UPF
- `ML/`: PyAnLF and PyMTLF backends
- `RAN/`: UERANSIM
- `kernel/`: guest kernel dependencies such as gtp5g
- `config/`: complete native config sets, config-specific subscriber/group input,
  and generated per-VM network aliases; `default` is the committed baseline
- `fixtures/full-core/`: named scenario contracts and traffic profiles
- `scripts/host/`: host orchestration and read-only checks
- `scripts/guest/`: VM provisioning, build, config activation, and systemd units
- `tools/nwdaf-consumer/`: infrastructure-owned discovery/subscription client
- `tools/datasetgen/`: infrastructure-owned deterministic Parquet generator/auditor

`nwdaf-resources` is not a runtime dependency or submodule. Component source
is fixed by parent gitlinks and is never cloned by guest provisioning.
All committed submodule URLs use HTTPS. The Intelligent-Systems-Lab sources
still require GitHub credentials while those repositories remain private;
making this parent repository public alone would not make a recursive clone
anonymous. URL changes never replace the pinned gitlink revisions.

## Command surface

No VM or service is created merely by cloning this repository. The intended
complete sequence is:

```sh
git submodule update --init --recursive
cp testbed.local.example.yaml testbed.local.yaml
# Select VirtualBox in testbed.local.yaml.
make config-create NAME=my-experiment DEVICE=gpu
make dataset-generate CONFIG_DIR=config/local/my-experiment
make experiment-validate CONFIG_DIR=config/local/my-experiment
make vm-up
make experiment-start CONFIG_DIR=config/local/my-experiment
make experiment-status CONFIG_DIR=config/local/my-experiment
make logs
```

`experiment-validate` is deliberately read-only and rejects a missing dataset;
`experiment-start` regenerates and revalidates that dataset before activating
the process domains. `DEVICE` is an explicit config policy: choose `gpu` or
`cpu`; no Host detection silently changes it. The default is the recommended
`gpu` policy. The default example is `full-core-cat-transition`. To
create the bounded `fl-closure-smoke` instead, without changing VM topology or
rebuilding a VM:

```sh
make config-create NAME=my-smoke FROM=fl-closure-smoke DEVICE=cpu
make config-validate CONFIG_DIR=config/local/my-smoke
make dataset-generate CONFIG_DIR=config/local/my-smoke
make dataset-show CONFIG_DIR=config/local/my-smoke
```

Use the same explicit `CONFIG_DIR` throughout one lifecycle. The
activated config manifest fixes the scenario definition and traffic-profile
paths; the generated dataset manifest fixes their content hashes.
For an alternate mobile network, change the single
`testbed.yaml:mobileNetwork.plmn` MCC/MNC source in a local topology and render
a complete set. NF/RAN PLMNs, TAIs, 15-digit IMSI SUPIs, Internal Group IDs,
Consumer targets, and subscriber fixtures are derived together; GPSI/MSISDN
remains an independent fixture identity. See [OPERATIONS.md](OPERATIONS.md).
The advanced `services-*`, `ml-*`, and `subscriptions-*` targets remain
available when an execution domain must be operated independently. Run
`make help`, `make help-advanced`, or `make help-dev` for the layered command
surface.

WebConsole is optional at both build and runtime. Add `WEBCONSOLE=true` when
creating a config set to include it in `experiment-start`, or operate it
independently after Guest services are active:

```sh
make config-create NAME=my-lab DEVICE=gpu WEBCONSOLE=true
make webconsole-start CONFIG_DIR=config/local/my-lab
make webconsole-status
make webconsole-stop
```

The first enabled start installs Node.js 20 in Core and builds the pinned
frontend and Go server. Later starts reuse the content-addressed artifact.
Disabled configs do not install, build, or start WebConsole. The HTTP endpoint
is `http://192.168.56.10:5000` on the isolated management network. The pinned
upstream revision cannot validate `billingServer.enable: false`, so its unused
FTP server remains enabled but is restricted to Core loopback
`127.0.0.1:2121`; billing transfers, TLS, and certificates are not supported.
The upstream startup path also recreates its own `admin` tenant/account and
resets the credentials to `admin/free5gc` each time; it does not replace the
config-owned subscriber records.

PyAnLF-A/B retrieve analytics data through ADRF. Their optional direct MongoDB
fallback is disabled in the default and generated E2E config sets; MongoDB still
runs in the Core VM for the 5GC NFs and ADRF itself. PyMTLF configuration is not
changed by this policy. PyMTLF-C uses an explicit 300-second Model Monitor
watchdog grace so initial prediction maturation cannot collide with the first
periodic-report deadline. Scenario validation also checks report sample
capacity, startup margin, bounded trigger timing, and an explicit post-trigger
closure budget before a dataset can be generated.

The normal teardown keeps VM power and experiment process state separate:

```sh
make experiment-stop  # subscriptions, consumer, ML, and Guest services
make vm-halt          # optional: power off the VMs
```

After exact subscription deletion, `experiment-stop` keeps every ML and Guest
backend available for a 40-second asynchronous cleanup grace before stopping
the processes. `ML_CLEANUP_GRACE_SECONDS` may override this non-negative value.

Stopping retains experiment state. For a deliberately clean run, first review
`make reset-show`, then use the scenario-confirmed `make reset` documented in
[OPERATIONS.md](OPERATIONS.md). Reset performs its own post-delete verification.
It keeps the existing containers and named volumes; it clears only their scoped
contents plus ADRF-owned database, model, and NRF registration state.

There is no `vm-destroy` target. Destruction must be an explicit Vagrant action
after its exact targets have been reviewed. See [OPERATIONS.md](OPERATIONS.md)
for config selection, status, log filtering, and guest build details.

## Current validation boundary

The repository has passed source-lock, YAML, config-render/check, consumer
discovery, Python native-settings, shell syntax, Host preflight, Vagrant
definition validation, three-VM provisioning, config activation, and the full
guest service lifecycle. The topology
now allocates 10 GiB across the guests and reserves 6 GiB of available Host RAM
for the physical host and ML containers. Each VM declares a 40 GiB dynamically
allocated primary-disk ceiling; this does not allocate 120 GiB immediately.
Low free swap is reported as a warning while RAM and 120 GiB free workspace
storage remain hard gates.

The committed config publishes all five ML endpoints on the isolated Host SBI
candidate `192.168.57.1`, using ports `9091`-`9094` and `9292`. Container
listeners bind their own `0.0.0.0` interfaces; callbacks and Go NWDAF clients
use only the advertised Host endpoints. A pinned Python 3.12 image definition,
PyAnLF/PyMTLF targets, five-service Compose topology, config-owned CPU/GPU
policy, and production NVIDIA runtime CDI selection are implemented. Bounded CPU image and
lifecycle smokes started all five services, verified status/log/stop behavior,
and removed their disposable containers and volumes afterward. The production
`ml-start`/`ml-status`/`ml-stop` lifecycle is implemented and has also started
all five services concurrently with the three-VM guest stack. Host toolkit-base,
CDI inventory, NVIDIA runtime registration by daemon reload, a disposable
container GPU probe, and VM-to-Host SBI reachability have passed without
interrupting the eight shared containers.
Production GPU activation does not change the default runtime or require a
shared-daemon restart. Guest source sync, provisioning, and service dispatch no
longer include PyAnLF or PyMTLF; ML source and environments remain Host-container
responsibilities.

In the bounded production integration smoke, PyMTLF-A/B reported CUDA available
on the RTX 3080, all five application readiness endpoints returned HTTP 200 from
their owning VMs. That historical smoke used the then-pinned full-state backend
synchronization contract. The current component pins instead use ready-only
backend generation tracking and stateless, on-demand containing-NWDAF context
lookup; `/health/live` and `/internal/v1/sync` are no longer part of the
integration contract. Each ML config points at its owning NWDAF AnLF or MTLF
internal server, and config rendering derives those endpoints from the native
NWDAF configs. Empty-stack container RSS was about 1.38 GiB total. The earlier
result proves activation and transport for its recorded revisions, not the
current lifecycle contract or concurrent training capacity.

The dataset lifecycle derives expected UE addresses from each topology
`uePool`, resolves the scenario selected by the complete config set, generates
ignored Path A/B Parquet artifacts, and audits schema, hash, rows, IPs,
timestamps, historical warm-start responsibility, and trigger-time
training/validation evidence. The default artifacts are about 1 MiB and 32,580
rows per Path; the smoke artifacts are about 0.7 MiB and 22,680 rows per Path.
They are not source assets and are excluded from Vagrant rsync.

The guest lifecycle now applies six scoped subscriber records and one Internal
Group idempotently before starting UERANSIM. A complete stop/start regression
kept all 23 guest units active and established six registrations and six PDU
Sessions: Path A received `10.60.0.1`-`10.60.0.3`, and Path B received
`10.61.0.1`-`10.61.0.3`. A bounded follow-up also proved two NRF-discovered
subscriptions, both Nupf Event Exposure resources, PseudoDriver replay into
PyAnLF, and two consumer analytics callbacks. A later GPU bounded run completed
A-only degradation detection, two rounds of A/B local training and C FedAvg,
ADRF publication, A/B reprovision, generation cutover, and a post-cutover
accuracy report. Existing VMs receive a hash-verified runtime helper bundle
before config activation, so this lifecycle no longer depends on provision-time
helper copies. Continuous degradation is expected to trigger later FL processes
while the experiment remains active. Model Monitor cleanup remains asynchronous.
The aggregate stop therefore waits before signaling any ML container; merely
stopping PyMTLF-C before A/B was shown not to preserve the cleanup path. An
active-runtime regression verified that the default grace drains both A/B
Model Monitor paths with `204` responses before container shutdown.

The initial implementation intentionally does not support TLS/certificates,
automatic experiment history, or 5g-viz.

PyMTLF-C imports the pinned initial model into its persistent artifact volume
before application startup and rejects a bundle whose deterministic digest
differs from the configured seed catalog.

## License status

The parent repository has not yet been assigned an open-source license. See
`LICENSE`. Every submodule remains governed by its own license.
