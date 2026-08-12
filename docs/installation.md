# Installation

## Reference Host

The supported reference path is a Linux Host running VirtualBox and Vagrant.
The Vagrant project pins Ubuntu 22.04 (`ubuntu/jammy64` `20241002.0.0`) and
disables automatic box update checks. Docker runs the Host ML services. Python
repository tools use `python3`; Python project dependencies are managed with
`uv` where required.

Install these Host tools before cloning:

- Git, Git LFS if required by a component, and GitHub credentials for private
  Intelligent-Systems-Lab repositories
- VirtualBox and Vagrant
- Docker Engine with Compose v2
- Python 3 and `uv`
- NVIDIA driver, NVIDIA Container Toolkit, and CDI support when using GPU mode

Adding a user to the Docker group takes effect after a fresh login session (or
`newgrp docker`); it does not by itself require restarting the shared Docker
daemon. Confirm access with `docker info`.

## Clone and source initialization

All committed submodule URLs use HTTPS. Configure GitHub authentication before
initializing private sources, then run:

```sh
git submodule update --init --recursive
```

Preflight confirms that every installed component matches the parent-pinned
commit in `components.lock.yaml`. Provisioning never clones branches inside a
VM.

## VirtualBox network allowlist

On Linux, `/etc/vbox/networks.conf` must allow every declared host-only address.
The reference Host keeps its existing laboratory range and allows the testbed
range with:

```text
* 192.168.33.0/24
* 192.168.56.0/21
```

The `/21` is only a VirtualBox allowlist. The topology still creates separate
`/24` networks. `experiment-validate` checks the declared interfaces before VM
mutation.

## Resource budget

The default VMs use:

| VM | RAM | vCPU | Primary disk ceiling |
| --- | ---: | ---: | ---: |
| Core | 4096 MiB | 4 | 40 GiB |
| Path A | 3072 MiB | 3 | 40 GiB |
| Path B | 3072 MiB | 3 | 40 GiB |

VirtualBox disks are dynamically allocated, so the three 40 GiB ceilings do
not immediately consume 120 GiB. The Host resource gate nevertheless requires
120 GiB of free workspace storage before creation and keeps 6 GiB of available
RAM outside the guest allocation for the Host and containers. Low swap follows
the configured warning policy rather than causing memory to be preallocated at
VM startup.

Do not run global Docker prune commands on this shared Host. PyTorch/CUDA image
layers are intentionally shared, while stopped project containers and named
volumes retain experiment state.

## CPU and GPU modes

`DEVICE=cpu` uses the normal OCI runtime. `DEVICE=gpu` assigns PyMTLF-A/B to
`cuda:0`; the other ML services remain on CPU. GPU startup checks the NVIDIA
runtime, CDI selector `nvidia.com/gpu=all`, and an actual CUDA visibility probe.
It does not change the Docker default runtime or restart the daemon. A failed
GPU prerequisite stops startup rather than changing the requested policy.

## First provisioning

Create and validate a config before creating VMs:

```sh
make config-create NAME=my-experiment DEVICE=gpu
make dataset-generate CONFIG_DIR=config/local/my-experiment
make experiment-validate CONFIG_DIR=config/local/my-experiment
make vm-up
```

The first `vm-up` provisions each guest and builds its assigned Go, RAN, and
kernel components. UERANSIM and gtp5g are built independently inside both Path
VMs against the guest environment. Go and MongoDB resolution follows
`provisioning.lock.yaml`; the resolved guest identity is recorded in
`/etc/5g-nwdaf-infrastructure/provisioning-manifest.yaml`.

Continue with the [standard experiment workflow](operations.md#standard-experiment-workflow).
