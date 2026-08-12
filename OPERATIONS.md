# Operations

## Lifecycle boundaries

The repository treats five states independently:

1. Source and config are selected on the physical host.
2. `vagrant up` creates or powers on `core`, `path-a`, and `path-b`.
3. `services-start` stages one config set and starts guest processes.
4. `ml-start` starts five Host ML containers from that same config identity.
5. `subscriptions-start` runs one Core consumer and creates two NWDAF resources.

`experiment-start` and `experiment-stop` are convenience orchestration over
states 3-5. They do not change VM power or reset retained state. A clean start
refuses pre-existing active experiment processes so rollback can stop only the
domains started by that invocation. The three domain lifecycles remain public
for focused operation and debugging.

VM provisioning installs toolchains and builds binaries but leaves the
experiment stack disabled. Vagrant creates only the declared base interfaces;
process aliases are applied when a complete config set is activated. A VM can
remain on while services are restarted, and one service lifetime can contain
multiple subscription/experiment cycles.

## Host preparation

Initialize every pinned component before preflight:

```sh
git submodule update --init --recursive
cp testbed.local.example.yaml testbed.local.yaml
```

Select `virtualbox` after its host driver is known to work.
`testbed.local.yaml` is ignored and may contain the provider name, expected VM
and Docker storage paths, physical ML bind address, and selected complete config
directory. It must not redefine the advertised topology, TAI, UE, NWDAF, or
experiment semantics.

`make experiment-validate CONFIG_DIR=...` is read-only. It runs the Host
preflight plus Compose wiring and Vagrant definition validation. It checks:

- Vagrant/provider and Docker daemon access;
- 10 GiB guest RAM plus a 6 GiB physical-host/ML reserve;
- a warning when free host swap is below 1 GiB; `MemAvailable` remains the hard
  memory gate and unattended long runs are not allowed under memory pressure;
- at least 120 GiB free on the workspace filesystem;
- Host ML bind-address presence and published-port conflicts;
- initialized, clean submodules at all 16 parent gitlinks;
- `components.lock.yaml` equality and native config consistency;
- an already generated PseudoDriver set matching the selected topology and
  effective config.

A failed validation is a stop condition for `make vm-up`; free resources or fix
the provider instead of weakening the topology silently.

## Config sets

`config/default` is a complete native baseline. Create a full editable copy of
the main reference example with:

```sh
make config-create NAME=my-lab
make config-validate CONFIG_DIR=config/local/my-lab
```

Use `FROM=fl-closure-smoke` to select the bounded example instead. The command
writes only `config/local/<name>` and refuses to overwrite an existing set.
Select that complete set explicitly or through the ignored local settings:

```yaml
# testbed.local.yaml
config:
  directory: config/local/my-lab
```

The lower-level renderer remains available for an explicit alternate topology
definition:

```sh
make config-render NAME=my-lab TESTBED=testbed.my-lab.yaml
make config-check TESTBED=testbed.my-lab.yaml CONFIG_DIR=config/generated/my-lab
```

Scenario selection is independent of VM topology. `config/default` is the
`full-core-cat-transition` business example. Create the shorter FL closure
scenario as another complete ignored set:

```sh
make config-create NAME=my-smoke FROM=fl-closure-smoke
make config-validate CONFIG_DIR=config/local/my-smoke
make dataset-generate CONFIG_DIR=config/local/my-smoke
make dataset-show CONFIG_DIR=config/local/my-smoke
```

The generated manifest pins the scenario name, kind, definition hash, and Path
A/B profiles. The renderer changes the coordinated sampling/report periods,
accuracy-policy windows, local epochs, fitting rounds, and preparation window.
It does not change VM resources, networks, NF placement, subscriber identity,
or addresses, so selecting the smoke does not require recreating the VMs.

When running it, use the same complete set in both execution domains:

```sh
make experiment-start CONFIG_DIR=config/local/my-smoke
```

Do not claim the smoke as business acceptance. It keeps the real Consumer/NRF,
Nupf Event Exposure, PyAnLF, ADRF, A/B training, FedAvg, publication, and
reprovision paths, but intentionally shortens monitor timing and local training.

`services-start` validates the effective set, hashes all native YAML, uploads
one archive to every VM, and extracts it to:

```text
/etc/5g-nwdaf-infrastructure/config-sets/<name>-<hash-prefix>/
```

Each guest verifies the full hash and atomically switches
`/etc/5g-nwdaf-infrastructure/active`. Activation is rejected while any stack
service or the consumer is active. Core, Path A, and Path B can therefore not
accidentally mix config sets.

Every complete set also contains `network/core.yaml`, `network/path-a.yaml`,
and `network/path-b.yaml`. The renderer derives these files from the process
endpoints and VM interface anchors in the selected `testbed.yaml`; they are not
an independent topology source. During activation, each guest applies only its
role file before any process starts. A missing anchor, wrong guest role, alias
collision, or network setup failure rejects activation and restores the prior
active set. The Guest renders process aliases into the persistent
`/etc/netplan/60-5g-nwdaf-aliases.yaml`; Vagrant retains ownership of
`50-vagrant.yaml` and its base anchors. Netplan merges both files, so aliases
survive Guest reboot and Vagrant's final network reconfiguration. Previously
managed aliases absent from the new set are removed.

Provisioning disables and masks the base box's `apt-daily` and
`apt-daily-upgrade` units. Guest package changes are explicit provisioning
operations because an unattended upgrade can still interrupt an active
experiment. The topology alias unit is an on-demand reconciler rather than an
enabled boot service. `services-start` verifies all three guests after config
and dataset staging, and only reconciles a missing, stale, or legacy runtime
state before the first NF binds an alias.

## Guest builds

The root Vagrant project pins the cached Ubuntu 22.04 box
`ubuntu/jammy64` `20241002.0.0`, disables automatic box update checks, and
applies these primary disk budgets:

On Linux, `/etc/vbox/networks.conf` must allow the complete isolated address
plan before any VM is created:

```text
* 192.168.33.0/24
* 192.168.56.0/21
```

The first line is an existing site range and is not owned by this repository.
The `/21` is only a VirtualBox host-only allowlist; Vagrant still creates eight
separate `/24` networks. `preflight.sh` fails before VM mutation if any declared
machine interface is outside the Host allowlist.

| VM | RAM | vCPU | Disk | Guest-local build responsibility |
| --- | ---: | ---: | ---: | --- |
| Core | 4096 MiB | 4 | 40 GiB | core NFs, ADRF, NWDAF-C |
| Path A | 3072 MiB | 3 | 40 GiB | gtp5g, UPF, UERANSIM, NWDAF-A |
| Path B | 3072 MiB | 3 | 40 GiB | gtp5g, UPF, UERANSIM, NWDAF-B |

Guests receive the parent-pinned source snapshot and never clone a branch.
Go components use Go 1.26.2; gtp5g is compiled against the running Path kernel;
UERANSIM is compiled independently inside both Path VMs. Provisioning records
no experiment run and starts no experiment unit at boot.

`services-start` checks both Path VMs before starting MongoDB or any NF. The
installed gtp5g module must have vermagic matching the currently running guest
kernel and must load successfully. This catches a guest kernel update that left
only an older module build installed. Repair only that kernel dependency inside
the affected Path VM, then retry service startup:

```sh
sudo /opt/5g-nwdaf-infrastructure/source/scripts/guest/path.sh A kernel
sudo /opt/5g-nwdaf-infrastructure/source/scripts/guest/path.sh B kernel
```

The `kernel` action does not rebuild UPF, NWDAF, or UERANSIM.

This is a compact functional baseline, not a capacity claim. The 40 GiB values
are primary logical capacities because the selected Bento base disk cannot be
shrunk by Vagrant. Dynamically allocated provider disks grow with writes and do
not immediately consume 120 GiB. Moving Python/CUDA environments to Host images
targets about 20 GiB actual use for Core and 18 GiB for each Path; clean-build
measurements must verify those estimates before long-running use.

The baseline also bounds low-volume runtime growth: MongoDB receives a 256 MiB
WiredTiger cache, Python services use one BLAS/OpenMP thread, each FL Client
accepts one concurrent training job, and model artifacts are limited to 32 MiB
compressed or 128 MiB extracted. The committed seed model is about 0.4 MiB.
PyMTLF-C packages that pinned seed source into its named artifact volume before
starting the server, verifies the deterministic artifact SHA-256, and then
opens the configured seed catalog. The import is content-addressed and
idempotent, so an ordinary stop/start does not create duplicate artifacts.
Both PyAnLF artifact-origin allowlists include this coordinator endpoint in
addition to their local FL Client and ADRF endpoints; otherwise a valid Model
Provision notification would be rejected before downloading the seed model.
Changing the seed catalog is a state migration: an existing coordinator model
state with a different family set must not be silently reused.

## Services and pseudo driver

`make services-start` first stages the selected config and one generated
role-specific dataset to each Path. It then starts MongoDB, NRF and the
control-plane NFs, idempotently provisions the six full-core subscribers and
their one Internal Group, then starts both UPFs, SMF, ADRF/NWDAF, and finally
both gNB/UE groups.
It does not start ML containers. Failure triggers reverse-order rollback.
`make services-stop` performs the same reverse order without halting VMs; it
also defensively stops any retained legacy guest ML units.

Subscriber data is experiment input rather than process state, so it persists
across `services-stop` and VM restart. Inspect or manage only the committed
scope with:

```sh
make subscriber-data-validate
make subscriber-data-plan
make subscriber-data-show
make subscriber-data-apply
make subscriber-data-clear
```

`apply` uses MongoDB `replaceOne(..., upsert=true)` for 48 documents belonging
to the six named SUPIs and one named Internal Group. `clear` deletes only those
SUPIs and that group; it never drops a database or collection. The fixtures in
`fixtures/full-core/` were selected from
`nwdaf-resources@d2634b84e8790a6b696e5b21ec1a0f660b683948`, but the runtime
uses Core's installed `mongosh` and does not depend on that repository or a
Host Python environment. `config-check` rejects fixture PLMN, SUPI, group,
K/OPc, AMF, S-NSSAI, or DNN mismatches before mutation.

## Experiment-state reset

ADRF uses one stable, topology-owned lowercase UUIDv4 from `testbed.yaml`.
Generated config sets copy that value into `adrfcfg.yaml`, and config validation
rejects a missing, malformed, or mismatched value. The UUID is only a globally
unique NF instance identity; its digits do not encode the ADRF role, site, TAI,
or scenario.

A service stop deliberately retains experiment artifacts. Before a clean E2E
run, inspect the reset scope while all VMs may remain powered off:

```sh
make reset-show CONFIG_DIR=config/local/my-experiment
```

The plan reports the five retained Compose containers and named volumes. If
Core is running, it also reads the two ADRF record collections, ADRF model
directory, and the ADRF-only `NfProfile` and `urilist` records in NRF. When Core
is off, guest counts are explicitly unavailable; the command never powers it on
and never deletes state.

After config activation has created the persistent Netplan fragment, a fresh
`vagrant up core` applies the Vagrant anchors and the managed aliases together.
If the plan still reports that the MongoDB bind address is inactive, first run
`sudo /usr/local/libexec/5g-nwdaf-infrastructure/network-setup --verify` inside
Core. A migration or drift failure can then be repaired with
`sudo systemctl restart 5g-nwdaf-network.service`; the reset does not change
network state implicitly.

An apply requires all five Host ML containers and every guest experiment
service to be stopped, Core to be running, and an exact scenario-name
confirmation:

```sh
make reset \
  CONFIG_DIR=config/local/my-experiment \
  RESET_CONFIRM=full-core-cat-transition
```

`reset` runs the post-delete verification automatically. It fails before
deletion unless `RESET_CONFIRM` exactly matches the scenario name shown by
`reset-show`.

The reset preserves VMs, container objects, images, the Compose network, named
volume objects, generated datasets, activated config sets, and full-core
subscriber/group fixtures. It clears only:

- contents of the five project-owned PyAnLF/PyMTLF state volumes;
- all documents in ADRF `data_store_records` and `mlmodel_store_records`;
- files below the configured ADRF model-storage directory;
- NRF `NfProfile` and `urilist` documents whose `nfType` is `ADRF`.

It never runs Docker prune, removes a container or volume, drops a database, or
deletes another NF type. MongoDB is started temporarily by the guarded guest
operation and returned to its prior stopped state. An ordinary stop/start does
not require this reset; use it only when the next experiment must not inherit a
previous model or report history.

PseudoDriver Parquet files are generated artifacts, not committed source and
not files inherited from the go-upf submodule. Committed profiles describe
traffic timing and post-boundary behavior; `testbed.yaml` remains authoritative
for UE pools. Generate and inspect the current content-addressed set with:

```sh
make dataset-generate
make dataset-validate
make dataset-show
make dataset-load
```

Generation resolves the manifest-selected profiles against effective
PyAnLF/PyMTLF, UPF, seed model, and monitor settings. The business example
requires its 30 historical observations only to fill the PyAnLF input; its
earliest policy trigger is separately checked to provide 69 observations and
8 training/1 validation samples. The smoke requires its 100-observation
warm-start itself to provide 36 training/4 validation samples; its earliest
trigger has 107 observations and 43 training/4 validation samples. Both reject
insufficient stable reference lead-in, degradation tail, retrieval lookback, or
trigger-time evidence. Generation derives `.1` through `.3` from each UE pool
and writes below `.generated/datasets/<dataset-set-id>/`.
`make test` performs two independent generations and proves that a tampered
Parquet artifact is rejected as part of the repository test suite.

`make dataset-load` uploads only the matching Path artifact. The guest checks
its set ID, role, SHA-256, bytes, and breaking time before atomically switching
`/var/lib/5g-nwdaf-infrastructure/datasets/active`. `services-start` performs
this automatically before starting any process; UPF startup rejects an absent
or incomplete active dataset. Existing guest set directories are retained for
reversible activation and are not experiment-run records.

## Host ML endpoints

Five Python processes run as five Docker services while sharing two image
types. PyAnLF-A/B and PyMTLF-C use CPU; PyMTLF-A/B request `cuda:0`. The public
baseline advertises `192.168.57.1` with these fixed mappings:

| Service | Host port | Container port |
| --- | ---: | ---: |
| PyAnLF-A | 9093 | 9093 |
| PyAnLF-B | 9094 | 9093 |
| PyMTLF-A | 9092 | 9092 |
| PyMTLF-B | 9091 | 9092 |
| PyMTLF-C | 9292 | 9292 |

The physical bind address may be overridden in `testbed.local.yaml`, but all
three VMs must still route to the advertised address. Container-native configs
listen on `0.0.0.0`; public URLs and callbacks use the advertised Host endpoint.
`compose.yaml` defines the five production services. PyMTLF-A/B request one
NVIDIA GPU and keep `cuda:0` in their native config; the other services are CPU
placed. Every service has a non-root user, read-only root filesystem, bounded
log rotation, health check, memory/CPU limit, read-only config bind, and its own
writable named volume. The three PyMTLF volumes contain artifact storage, model
state, publication journal, and FL workspaces under one service-specific root.

Repository-wide static, config, network, dataset, Compose, and Vagrant
definition checks run without starting a container or VM:

```sh
make test
```

Operate the long-lived production project independently from VM services:

```sh
make ml-start
make ml-status
make ml-stop
```

`ml-start` validates and hashes the selected complete config set, builds each
image target once, verifies the Host bind address, requires the configured Host
RAM reserve and Docker free-space threshold, and performs an actual CUDA
visibility probe before starting the production GPU services. Low swap follows
the configured warn/require policy. Missing NVIDIA runtime support therefore
fails before Compose service creation instead of silently falling back to CPU.
A failed Compose startup stops only this ML project and retains images and named
volumes.

`ml-status` reports state, application health, effective configured device,
actual CUDA visibility, live memory, image ID, component revision, config-set
name, and config hash. `ml-stop` stops only running containers labeled as the
`5g-nwdaf-infrastructure` project; stopped containers, named volumes, images,
VMs, guest processes, and subscriptions remain intact.

Run the bounded CPU-only image/config/health/lifecycle test:

```sh
make test-containers
```

The test binds only loopback, generates an ignored config set with A/B training
set to CPU, builds each image target once, starts all five services, verifies
non-root identity, effective device, status/log/stop, and retention of five
stopped containers and volumes, then removes only its own containers, network,
volumes, and generated config. It retains the two images. It does
not exercise CUDA, modify the NVIDIA driver/toolkit, create a VM, or prove
VM-to-Host reachability.

The first successful empty-service smoke observed about 230 MiB RSS per PyAnLF
and 283 MiB per PyMTLF, about 1.28 GiB total. These figures exclude model
training, datasets, tensor growth, GPU memory, and full-stack traffic, so they
are startup measurements rather than capacity requirements. Each image has a
5.42 GB virtual size because it includes the shared CUDA-enabled PyTorch
runtime; the common runtime layers are shared by both image targets. Do not run
global Docker prune on this shared Host.

The production lifecycle has passed bounded GPU activation and concurrent guest
stack integration. PyMTLF-A/B reported CUDA available, all five readiness
endpoints returned HTTP 200 from their owning VMs, and periodic backend sync
crossed the VM/Host boundary in both directions. This does not prove training
capacity: concurrent A/B training, peak VRAM/RAM, traffic callbacks, and
PseudoDriver replay remain separate gates.

Pseudo driver support is required on both paths. It is embedded in each UPF
process and configured by `upfcfg-a.yaml` / `upfcfg-b.yaml`. Path A uses a
generated post-boundary degradation profile, Path B remains stable, and both
read the guest-local active dataset through the hybrid historical/live EES
path.

These datasets provide reproducible stimulus. They are not evidence of real
application throughput or a user-plane performance benchmark. The selected
remote UPF revision still requires privileged VM revalidation against the
historical unpushed full-core revision described in `COMPONENTS.md`.

## Subscriptions

`make subscriptions-start` requires NWDAF-A and NWDAF-B to be active. The Core
consumer then:

1. binds its callback;
2. queries NRF for `nnwdaf-eventssubscription` providers;
3. selects exactly one unused NWDAF for TAC `000001` and one for `000002`;
4. rejects duplicate NF identities;
5. creates two standard `UE_COMMUNICATION` subscriptions;
6. rolls back the first exact `Location` if the second create fails.

`make subscriptions-stop` deletes both saved locations. If either DELETE fails,
state and callback remain available for retry. It never guesses a resource URI
from an ID.

## Observation

Use compact state without changing processes:

```sh
make experiment-status CONFIG_DIR=config/local/my-experiment
```

The individual `vm-status`, `services-status`, `ml-status`, and
`subscriptions-status` targets remain available through `make help-advanced`.

Follow journald directly when detail is needed:

```sh
scripts/host/logs.sh --source all --service '*' --since '10 minutes ago'
scripts/host/logs.sh --source vm --vm path-a --service upf-a --since today
scripts/host/logs.sh --source ml --service pymtlf-a --since '5 minutes ago'
scripts/host/logs.sh --source ml --service pyanlf-a --tail 20 --no-follow
```

`experiment-status` adds config identity, configured ML devices, and Host
resource headroom to the VM, Guest service, ML container, and subscription
state previously shown by `observe`.
Log selection is label-scoped for ML containers and supports VM, ML, or combined
sources. Stopping the log follower does not stop any process. This version does
not assign run IDs, collect logs automatically, or bind VM lifetime to experiment
history.

## Initial non-goals

- TLS, certificates, OAuth, and production security hardening;
- 5g-viz integration;
- automatic run archive/replay ownership;
- HA, capacity claims, or public cloud portability;
- automatic destructive VM cleanup.
