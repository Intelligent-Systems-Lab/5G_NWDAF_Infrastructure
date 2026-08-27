# Operations

## Standard experiment workflow

Run all commands from the repository root and keep the same `TESTBED` and
`CONFIG_DIR` for the entire lifecycle.

### 1. Prepare inputs

The commands below use the committed traffic pattern unchanged. To create a
different dataset pattern, first follow
[Customize the dataset pattern](configuration.md#customize-the-dataset-pattern),
then use that local scenario and its generated `CONFIG_DIR` throughout this
workflow.

```sh
git submodule update --init --recursive
make config-create \
  NAME=my-experiment \
  FROM=experiments/examples/full-core-cat-transition/scenario.yaml \
  DEVICE=gpu
make dataset-generate CONFIG_DIR=config/local/my-experiment
make experiment-validate CONFIG_DIR=config/local/my-experiment
```

Use `DEVICE=cpu` when GPU execution is not required. Validation is read-only
and reports all known findings without changing state. It is recommended before
a run but is not called by startup and is not an execution gate.

### 2. Start or resume the VMs

```sh
make vm-up
make vm-status
```

`vm-up` creates or powers on Core, Path A, and Path B. It does not start the
experiment processes. First creation provisions and builds the guest-owned
components; a later invocation is an ordinary VM boot.

Before its first provider query, `vm-up` verifies the approved Host context,
parses the current user's `VBoxHeadless --startvm <UUID>` processes, and checks
the exact Vagrant machine UUID metadata. It then compares a second process
snapshot with Vagrant's live state. Duplicate UUIDs, missing or unexpected
metadata, process/state mismatches, inventory changes, or parse failures stop
the command before `vagrant up`. Do not bypass this preflight with a direct
Vagrant or VirtualBox command.

`vm-status` does not request a power transition, but it still starts a provider
client and therefore requires the same approved Host context. Its reported
`poweroff` or `not created` state is not, by itself, proof that no VM process
exists.

If the parent component pins changed after those VMs were provisioned, do not
infer binary freshness from `vm-up`, source rsync, or a passing Host submodule
check. Follow the [existing VM binary boundary](components.md#existing-vm-binary-boundary),
rebuild the affected Guest artifacts explicitly, and record their identity
before the experiment. Resetting retained data does not rebuild software.

For a first run or a run that should retain previous state, continue directly.
For a deliberately clean subsequent run, stop all experiment processes and use
the guarded reset described under [Clean-run reset](#clean-run-reset).

### 3. Start the experiment

```sh
make experiment-start CONFIG_DIR=config/local/my-experiment
```

The aggregate command requires all three VMs to be running and the experiment
domains to be stopped. It generates or reuses the dataset, stages the
config and dataset, starts Guest services, optionally starts WebConsole, starts
the five ML containers, and finally starts the Consumer and its two
subscriptions. If startup fails, it rolls back only domains started by that
invocation; a failed exact subscription cleanup intentionally leaves its
backends available for retry.

Useful success signals are:

- all 23 guest units active;
- six UE registrations and six PDU Sessions;
- five healthy ML containers with the requested device policy;
- two distinct NRF-discovered NWDAF subscriptions;
- Consumer callbacks from both paths.

Container health proves process, artifact, and containing-NWDAF capability
checks, but it is not by itself proof that the complete Model Provision and
Model Monitor business chain has converged. For FL readiness, also confirm
successful Model Provision subscription creation and active monitor scopes.
See [Model Provision returns 503 during startup](troubleshooting.md#model-provision-returns-503-during-startup)
when early reconciliation retries appear.

The full-core scenario then continues through accuracy monitoring, A/B local
training, C FedAvg, ADRF publication, A/B reprovisioning, generation cutover,
and a post-cutover accuracy report. These later transitions are asynchronous;
observe logs as well as the compact state view.

### 4. Observe progress

```sh
make experiment-status CONFIG_DIR=config/local/my-experiment
make services-status
make observe
make logs SERVICE=pymtlf-c
```

`experiment-status` is a snapshot covering config identity, Host headroom, VM
power, guest units, container health/device state, and subscriptions.
`services-status` keeps the 23-unit process table and adds a six-UE readiness
table. Each UE row distinguishes service state, Registration, and PDU Session;
only journal records carrying the service's current systemd invocation ID are
considered. An active UE without the corresponding success evidence is
`pending`, so a previous run cannot make a fresh process appear ready. `make
observe` does not require `CONFIG_DIR`: it reads the active Guest config,
container identity/mounts, and saved Consumer state. Use `experiment-status`
when a selected config must be compared with that runtime.

Continuous observation keeps the previous complete screen visible while the
next VM, Guest, Docker, and subscription snapshot is collected in parallel.
Only a complete new snapshot replaces it. The header reports collection start,
completion, and elapsed time; the default five-second interval begins after a
snapshot is rendered, so a slow backend never creates overlapping rounds. CUDA
probe results are cached by container identity and PyMTLF log reads continue
from the preceding cursor for the lifetime of that `make observe` process. The
temporary cache is removed on normal exit or Ctrl-C and does not alter
experiment state. Stop observation with Ctrl-C without affecting the
experiment. Use focused `make logs` calls for detailed component events. A bare
`make logs` still follows all owned VM journals and project ML container logs,
including template Guest units, the Core-only Consumer, and each Network unit.

To keep each runtime domain readable, open four terminals from the repository
root and run one follower in each:

```sh
# Core VM
make logs SOURCE=vm VM=core

# Path A VM
make logs SOURCE=vm VM=path-a

# Path B VM
make logs SOURCE=vm VM=path-b

# Host Docker ML services
make logs SOURCE=ml
```

Each command first prints the selected domain's recent logs and then follows new
events. Ctrl-C stops only that terminal's follower; it does not stop any VM,
Guest service, container, or experiment.

Powered-off or not-yet-created VMs are valid observable states. Guest and
WebConsole services report `not-running` without attempting SSH. For the Core
Consumer, the service is `not-running` and its saved subscription state is
`not-readable`: the state may remain on the VM disk, but the Host cannot inspect
it while Core is off. A running VM that cannot be queried, a Docker failure, or
malformed status data is different: `experiment-status` and the single snapshot
return non-zero and preserve the backend error. Continuous `observe` labels the
failed section `unavailable` and continues with the next interval so recovery
remains visible.

The ML section also provides a current-container FL milestone table. It scopes
Docker logs with each PyMTLF container's current `StartedAt`, requires matching
config-set/hash labels, and reports monitor lifecycle, degradation, process,
preparation, A/B local rounds, aggregation, validation, publication, adoption,
cutover, and post-cutover evaluated accuracy. `not-seen` means the current
container logs do not yet contain that evidence; it is not inferred to be a
component failure. The separate failure row only changes when an explicit FL
failure signature is observed.

The table ends with one current-run result:

- `not-started` means no coordinator container exists;
- `in-progress` means the coordinator is running without a terminal outcome;
- `incomplete` means the coordinator stopped before terminal evidence appeared;
- `failed` means the newest terminal evidence is an explicit FL failure;
- `complete` means the newest terminal evidence is an evaluated,
  non-degrading post-cutover accuracy report.

If a failure is followed by a later successful cutover report, the newer
success wins; a newer failure supersedes an earlier success. A `complete`
result is retained after containers stop because it describes the observed
business outcome, not process state. It never stops the experiment
automatically; use `make experiment-stop` when observation is complete.

Focused examples are:

```sh
make logs SOURCE=vm VM=path-a SERVICE=upf-a SINCE=today
make logs SOURCE=ml SERVICE=pymtlf-a SINCE='10 minutes ago'
make logs SOURCE=vm VM=core SERVICE=nwdaf-c TAIL=100 FOLLOW=false
make logs SOURCE=vm VM=core SERVICE=consumer FOLLOW=false
make logs SOURCE=vm SERVICE=network FOLLOW=false
```

Filters accept `SOURCE=vm|ml|all`, `VM=core|path-a|path-b|all`, a `SERVICE` name,
glob, `all`, or empty value, `SINCE`, `TAIL`, and `FOLLOW=true|false`. Selectors
are combined. `VM` affects only VM journals, so use `SOURCE=vm VM=path-a` when
the intended scope excludes all Host ML logs. Logical service names map to
their actual systemd units: regular services and WebConsole use
`5g-nwdaf@<name>.service`, while `consumer` and `network` select their dedicated
units. A VM that does not own a matching service is skipped.

The Host resolves `--since` once and passes the same absolute instant to Guest
journald and Docker. VM and container output includes UTC timestamps, so lines
from different runtime domains can be compared directly. Relative values such
as `today` are interpreted in the Host timezone before conversion to UTC;
invalid values fail before any log follower starts.

### 5. Stop without deleting state

```sh
make experiment-stop
make vm-halt
```

`experiment-stop` deletes the two saved Consumer subscription locations, then
keeps the NWDAFs and ML backends available for a fixed 40-second grace before
stopping containers and Guest services. PyAnLF and PyMTLF perform the dependent
Model Provision and Model Monitor cleanup asynchronously, so immediate shutdown
would still be unsafe even though cross-node NWDAF deletion no longer holds a
route mutex during network I/O. The grace is deliberately not inferred from
application log wording; use `make logs` when teardown diagnostics are needed.

The stop retains VMs, stopped containers, images, named volumes, generated
datasets, active config, subscriber/Internal Group records, and ADRF/model
state. `vm-halt` is optional and only powers off the three VMs.

## Clean-run reset

Reset is not part of every start. Use it only when the next experiment must not
inherit prior ML, ADRF, model, or ADRF-registration state.

First inspect the exact scope:

```sh
make reset-show CONFIG_DIR=config/local/my-experiment
```

The plan prints the expected scenario and a copyable confirmation command. All
five ML containers and guest experiment services must be stopped; Core must be
running for deletion. Apply and verify with the exact scenario name:

```sh
make reset \
  CONFIG_DIR=config/local/my-experiment \
  RESET_CONFIRM=full-core-cat-transition
```

The reset empties the five project-owned ML state volumes, ADRF record
collections and model directory, and only ADRF-type NRF registration records.
It retains container and volume objects, images, networks, VMs, datasets,
configs, and the six subscriber/one Internal Group fixtures. It never invokes a
global Docker cleanup or drops an entire database.

## Independent domain operations

Use the advanced targets when diagnosing one domain rather than running an
aggregate experiment:

```sh
make services-start CONFIG_DIR=config/local/my-experiment
make ml-start CONFIG_DIR=config/local/my-experiment
make subscriptions-start
make subscriptions-status
make subscriptions-stop
make ml-stop
make services-stop
```

The order matters: Guest NWDAFs must exist before the Consumer subscribes, and
subscription resources should be removed while the NWDAFs and ML backends are
still available. The aggregate commands encode that order and are preferred
for normal experiments. `ml-stop` only changes the ML process domain; it does
not claim that subscription resources currently exist or change them itself.

`subscriptions-status` reports the Consumer service separately from its saved
resource state. The resource state is local ownership evidence, not a remote
GET verification. Each Path row includes provider, TAC, correlation, exact
Location, callback HTTP request count, and last callback UTC time. Counts are
for the current subscription pair and reset before a new pair is created. They
count callback HTTP requests, not the number of analytics items inside one payload.
Retained state from an older version remains readable and shows `unknown` until
a new callback establishes per-Path evidence. Unknown correlation IDs are
reported separately rather than attributed to Path A or B. An unreachable Core,
Consumer CLI error, or malformed state makes the command fail explicitly.

## Subscriber data

Subscriber and Internal Group fixtures belong to the selected config and
persist across service stops:

```sh
make subscriber-data-show CONFIG_DIR=config/local/my-experiment
make subscriber-data-apply CONFIG_DIR=config/local/my-experiment
make subscriber-data-clear CONFIG_DIR=config/local/my-experiment
```

`show` compares expected fixtures with the scoped MongoDB records and reports
matching, missing, different, and extra records. `apply` performs idempotent
upserts. `clear` deletes only the six declared SUPIs and one declared Internal
Group; it does not drop collections. Normal `services-start` already applies
the selected records.

## Optional WebConsole

Create the config with WebConsole enabled to include it in aggregate startup:

```sh
make config-create \
  NAME=my-webconsole \
  FROM=experiments/examples/full-core-cat-transition/scenario.yaml \
  DEVICE=cpu WEBCONSOLE=true
```

After Guest services are active it can also be managed independently with
`make webconsole-start`, `make webconsole-status`, and `make webconsole-stop`.
Browse `http://192.168.56.10:5000` from the Host and sign in with
`admin/free5gc`.

The pinned upstream startup recreates that WebConsole-owned admin account and
resets its credentials on every start. Its billing FTP compatibility service
remains enabled but binds only Core loopback at `127.0.0.1:2121`; it is not
reachable on the management or laboratory network. WebConsole does not replace
the config-owned subscriber records.

## VM destruction

VM destruction is a separate, destructive maintenance operation. There is
deliberately no `vm-destroy` Make target, so ordinary experiment cleanup cannot
discard a VM by mistake. Stop a running experiment first, then verify the exact
three targets:

```sh
make experiment-stop
vagrant status core path-a path-b
```

When `core`, `path-a`, and `path-b` are the intended environment, permanently
delete their VMs and virtual disks with:

```sh
vagrant destroy -f core path-a path-b
```

This removes Guest-installed components, MongoDB and ADRF/model state, staged
active config and datasets, system journals, and every other file stored only
on the three VM disks. It does not remove repository files, local config sets,
Host-generated datasets, the Vagrant box cache, or Host Docker images,
containers, volumes, and networks. Use the guarded [Clean-run reset](#clean-run-reset)
instead when only retained experiment state should be cleared while keeping the
VMs.

Confirm destruction and rebuild the complete environment from the selected
testbed definition when needed:

```sh
vagrant status core path-a path-b
make vm-up TESTBED=testbed.yaml
```

The status must report all three machines as `not created`. The next `vm-up`
creates, provisions, and builds all three Guests from scratch. If a non-default
testbed definition was used to create the VMs, pass that same file through the
`TESTBED` environment variable to both direct Vagrant commands as well as the
Make command.
