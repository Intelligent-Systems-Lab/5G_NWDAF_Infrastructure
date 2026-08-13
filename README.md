# 5G NWDAF Infrastructure

Reproducible infrastructure for a three-NWDAF, dual-TAI federated-learning
experiment. The reference environment uses three Ubuntu 22.04 VirtualBox VMs
for the 5G core, two paths, and six UEs, plus five independently managed Docker
containers for PyAnLF and PyMTLF.

The repository owns topology, component revision locks, native configuration,
guest provisioning, process lifecycle, deterministic PseudoDriver datasets,
and the Consumer that discovers and subscribes to both path NWDAFs.

## Topology

| Location | Main processes |
| --- | --- |
| Core VM | free5GC control plane, MongoDB, ADRF, NWDAF-C, Consumer, optional WebConsole |
| Path A VM | UPF-A, gNB-A, UE1-3, NWDAF-A |
| Path B VM | UPF-B, gNB-B, UE4-6, NWDAF-B |
| Host | PyAnLF-A/B and PyMTLF-A/B/C, one process per container |

VM power, guest services, Host ML containers, WebConsole, and subscriptions
are separate lifecycles. `experiment-start` composes those domains; `vm-up`
only creates or powers on VMs.

## First experiment

Install the prerequisites and Host settings in
[Installation](docs/installation.md), then initialize the pinned sources:

```sh
git submodule update --init --recursive
```

Create one complete local config, generate its deterministic dataset, and
validate everything without changing runtime state:

```sh
make config-create NAME=my-experiment DEVICE=gpu
make dataset-generate CONFIG_DIR=config/local/my-experiment
make experiment-validate CONFIG_DIR=config/local/my-experiment
```

Use `DEVICE=cpu` when CUDA is not wanted. The choice is explicit; the runtime
does not silently fall back from GPU to CPU.

Start the VMs and experiment processes, then inspect status and logs:

```sh
make vm-up
make experiment-start CONFIG_DIR=config/local/my-experiment
make experiment-status CONFIG_DIR=config/local/my-experiment
make logs
```

Stop the process domains while retaining their state, then optionally power off
the VMs:

```sh
make experiment-stop
make vm-halt
```

Use the same `TESTBED` and `CONFIG_DIR` values throughout a lifecycle. The
standard workflow, clean-run reset, success signals, and WebConsole option are
described in [Operations](docs/operations.md).

## Documentation

- [Documentation map](docs/README.md)
- [Architecture and experiment flow](docs/architecture.md)
- [Installation](docs/installation.md)
- [Configuration](docs/configuration.md)
- [Operations](docs/operations.md)
- [Command reference](docs/commands.md)
- [Components and source locks](docs/components.md)
- [Validated behavior](docs/validation.md)
- [Troubleshooting](docs/troubleshooting.md)

Run `make help`, `make help-advanced`, or `make help-dev` for the layered command
surface. Run `make test` for the Host-only repository checks.
