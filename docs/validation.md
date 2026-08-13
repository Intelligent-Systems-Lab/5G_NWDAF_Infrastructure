# Validated Behavior

This document states what the repository checks and what has been demonstrated
on the reference Host. It is evidence for the pinned source/config combination,
not a general capacity or traffic-performance claim.

## Repository checks

`make test` runs without starting the production VM or container stack. It
checks:

- shell syntax and Python compilation;
- YAML and testbed schema consistency;
- installed component revisions against the readable source lock;
- provisioning lock and resolved-version rules;
- native config rendering and representative cross-file drift rejection;
- PLMN-derived NF, RAN, SUPI, group, Consumer, and subscriber relationships;
- Netplan alias rendering and stale-address migration in temporary roots;
- deterministic PseudoDriver generation and tamper rejection;
- production and CPU-smoke Compose contracts;
- Consumer schema and discovery/subscription behavior in controlled tests;
- atomic Consumer state updates across callback threads/processes, retained-state
  compatibility, create/delete overlap, and independent Path callback accounting;
- current-invocation UE Registration/PDU readiness parsing, including pending,
  rejection, recovery, and inactive service cases;
- aggregate snapshot failure propagation and valid powered-off `not-running`
  behavior for Guest, WebConsole, and Subscription domains;
- Vagrant definition validation.

`make experiment-validate CONFIG_DIR=...` adds checks against the current Host:
submodule worktree state, required tools, VirtualBox allowlist, generated data,
available RAM/storage/swap policy, Docker access, Host SBI bind address, and the
requested CPU/GPU runtime. It remains read-only.

## Provisioning and network

The three pinned Ubuntu 22.04 VMs have been provisioned with the default 4/3/3
GiB memory allocation and 40 GiB dynamically allocated disk ceilings. Config
activation has been exercised with Vagrant-owned base addresses and persistent
repository-owned Netplan aliases. Guest runtime helpers are content-verified
before activation, and the gtp5g running-kernel check executes before any NF
binds its interface.

## 5GC and subscription path

The full guest lifecycle has brought up all 23 experiment units. Six UEs have
registered and established six PDU Sessions, with Path A receiving
`10.60.0.1`-`10.60.0.3` and Path B receiving
`10.61.0.1`-`10.61.0.3`.

The Consumer has discovered two distinct NWDAF instances through NRF, created
one TAI-specific subscription on each, retained their exact `Location` values,
and received analytics callbacks from both paths. Both NWDAFs created Nupf
Event Exposure resources and both PseudoDrivers replayed their generated input
through the normal report path.

## Host ML runtime

The production five-container lifecycle has been started concurrently with the
guest stack. All five application readiness endpoints returned HTTP 200 from
their owning VMs. GPU activation on the reference RTX 3080 made CUDA visible to
PyMTLF-A/B while PyAnLF-A/B and PyMTLF-C remained on CPU. CPU mode has also
passed a disposable five-container build, health, status, log, stop, and scoped
cleanup smoke.

An empty five-service startup used approximately 1.3 GiB of container RSS. That
measurement excludes active datasets, training tensors, model growth, and GPU
memory, so the configured resource gates remain the operational baseline.

## Federated-learning closure

With the currently pinned business-E2E component revisions and the full-core
scenario, a bounded run completed:

- Path A degradation detection;
- two rounds of A/B local training;
- sample-count-weighted FedAvg at C;
- model publication to ADRF;
- A/B reprovisioning;
- model generation cutover; and
- a post-cutover accuracy report.

An active-runtime teardown regression also established both Model Monitor
paths and verified that the default 40-second grace allowed their asynchronous
DELETE operations to return `204` before the first ML container stopped.

## Interpreting results

The PseudoDriver makes experiment timing and traffic features reproducible. It
does not turn these checks into a real application-traffic benchmark. The
validated result belongs to the recorded parent gitlinks, config hash, dataset
manifest, and runtime artifact identities; preserve those identities when
reporting a new run.
