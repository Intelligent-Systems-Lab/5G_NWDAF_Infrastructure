# Operations

## Lifecycle boundaries

The repository treats five states independently:

1. Source and config are selected on the physical host.
2. `vagrant up` creates or powers on `core`, `path-a`, and `path-b`.
3. `services-start` stages one config set and starts guest processes.
4. `ml-start` starts five Host ML containers from that same config identity.
5. `subscriptions-start` runs one Core consumer and creates two NWDAF resources.

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

Choose `virtualbox` or `libvirt` only after its host driver is known to work.
`testbed.local.yaml` is ignored and may contain the provider name, expected VM
and Docker storage paths, physical ML bind address, and selected complete config
directory. It must not redefine the advertised topology, TAI, UE, NWDAF, or
experiment semantics.

`make preflight` is read-only. It checks:

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

A failed preflight is a stop condition for `make vm-up`; free resources or fix
the provider instead of weakening the topology silently.

## Config sets

`config/default` is a complete native baseline. To modify individual component
files manually, copy the whole directory to `config/local/<name>` and select it:

```yaml
# testbed.local.yaml
config:
  directory: config/local/my-lab
```

For an explicit alternate topology definition, render a complete ignored set:

```sh
make config-render NAME=my-lab TESTBED=testbed.my-lab.yaml
make config-check TESTBED=testbed.my-lab.yaml CONFIG_DIR=config/generated/my-lab
```

Scenario selection is independent of VM topology. `config/default` is the
`full-core-cat-transition` business example. Render the shorter FL closure
scenario as another complete ignored set:

```sh
make config-render NAME=fl-closure-smoke \
  SCENARIO=fixtures/full-core/scenarios/fl-closure-smoke.yaml
make config-check CONFIG_DIR=config/generated/fl-closure-smoke
make dataset-generate CONFIG_DIR=config/generated/fl-closure-smoke
make dataset-show CONFIG_DIR=config/generated/fl-closure-smoke
```

The generated manifest pins the scenario name, kind, definition hash, and Path
A/B profiles. The renderer changes the coordinated sampling/report periods,
accuracy-policy windows, local epochs, fitting rounds, and preparation window.
It does not change VM resources, networks, NF placement, subscriber identity,
or addresses, so selecting the smoke does not require recreating the VMs.

When running it, use the same complete set in both execution domains:

```sh
make services-start CONFIG_DIR=config/generated/fl-closure-smoke
make ml-start CONFIG_DIR=config/generated/fl-closure-smoke
make subscriptions-start
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
active set. Previously managed aliases absent from the new set are removed.

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

PseudoDriver Parquet files are generated artifacts, not committed source and
not files inherited from the go-upf submodule. Committed profiles describe
traffic timing and post-boundary behavior; `testbed.yaml` remains authoritative
for UE pools. Generate and inspect the current content-addressed set with:

```sh
make dataset-generate
make dataset-check
make dataset-show
make dataset-stage-plan
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
`make dataset-smoke` performs two independent generations and proves that a
tampered Parquet artifact is rejected.

`make dataset-stage` uploads only the matching Path artifact. The guest checks
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

Validate the resolved production and CPU-only definitions without starting a
container:

```sh
make ml-compose-check
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

Run the bounded CPU-only image/config/health smoke:

```sh
make ml-cpu-smoke
make ml-lifecycle-smoke
```

Each smoke binds only loopback, generates an ignored config set with A/B training
set to CPU, builds each image target once, starts all five services, reports
effective device and container memory, then removes only its own containers,
network, volumes, and generated config. The lifecycle smoke additionally proves
start/status/log/stop, including retention of five stopped containers and five
volumes before its final disposable cleanup. Both retain the two images. They do
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
make vm-status
make services-status
make ml-status
make subscriptions-status
make observe
```

Follow journald directly when detail is needed:

```sh
scripts/host/logs.sh --source all --service '*' --since '10 minutes ago'
scripts/host/logs.sh --source vm --vm path-a --service upf-a --since today
scripts/host/logs.sh --source ml --service pymtlf-a --since '5 minutes ago'
scripts/host/logs.sh --source ml --service pyanlf-a --tail 20 --no-follow
```

`make observe` includes VM, guest service, ML container, and subscription state.
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
