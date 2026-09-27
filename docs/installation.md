# Installation

## Reference Host

The deployment targets a Linux x86-64 Host. Install and configure:

- Git, Go, Python 3, `uv`, `tar`, `sha256sum`, `flock`, `ip`, and `ss`;
- VirtualBox with a working Host driver and Vagrant with the VirtualBox
  provider;
- Docker Engine with Docker Compose v2;
- sufficient access to create private Host-only networks and reach the Guest
  addresses in `testbed.protocol-hierarchical.yaml`.

For GPU runs, also install a compatible NVIDIA driver and NVIDIA Container
Toolkit. `nvidia-smi` must work, Docker must expose the `nvidia` runtime,
`nvidia-ctk cdi list` must contain `nvidia.com/gpu=all`, and the local PyMTLF
image must be able to import Torch with CUDA available.

The Guest platform is Ubuntu 22.04 (`ubuntu/jammy64`). Guest Go and MongoDB
inputs are governed by `provisioning.lock.yaml` and installed during initial
provisioning.

## Capacity budget

The committed testbed definition requests:

| Resource | Selected capacity |
| --- | ---: |
| Core VM | 2 CPUs, 3,072 MiB RAM, 24 GiB disk |
| Each of three Path VMs | 2 CPUs, 2,048 MiB RAM, 20 GiB disk |
| Total Guest capacity | 8 CPUs, 9,216 MiB RAM, 84 GiB logical disk |
| Host memory reserve floor | 6,144 MiB |
| Container-build overhead allowance | 2,048 MiB |
| GPU free-memory floor for GPU config | 8,192 MiB |
| Workspace, Docker, and VirtualBox free-space floor | 120 GiB each |

Container limits are derived from the selected generated runtime inventory.
The Host preflight compares Guest and Host demand with available CPU, RAM,
storage, swap, ports, and GPU memory. The swap policy warns below 1,024 MiB of
free swap; it does not convert insufficient RAM into acceptable capacity.

## Checkout and environments

Clone the repository and initialize the exact retained components:

```sh
git submodule update --init --recursive
```

Prepare the repository tools and optional analysis dependency:

```sh
uv sync --extra analysis
```

Dataset preparation and formal seed-model generation use the PyMTLF project
interpreter at `ML/PyMTLF/.venv/bin/python`. Create it from the component
lockfile:

```sh
uv sync --project ML/PyMTLF
```

The root tools support Python 3.8 or newer. The retained PyMTLF project requires
Python 3.12 or newer; let `uv` select or install a compatible interpreter.

## Host-only preparation check

Before contacting the provider, confirm the repository command surface and
synthetic checks:

```sh
make help
make help-advanced
make test
```

`make test` does not run Vagrant, VirtualBox, VMs, or containers.

## Initial config and provisioning

Create a config before running preflight or provisioning:

```sh
make config-create \
  FROM=experiments/protocol-hierarchical/mnist/smoke.yaml \
  DEVICE=gpu
make dataset-generate
```

The following commands contact the real provider and must run only in the
approved Host context:

```sh
make experiment-validate
make vm-up
```

`vm-up` creates or starts all four selected VMs and runs one-time provisioning
when necessary. Do not run direct `vagrant` or `VBoxManage` commands around the
repository guard. If existing provider state conflicts with the selected VM
inventory, stop and resolve the exact inventory instead of deleting or
recreating machines speculatively.

After provisioning, `make fl-experiment-run RUN_NAME=<name>` performs the
process lifecycle for a bounded experiment. VM destruction is not part of the
normal workflow and is never implied by reset, stop, or halt.
