# Reproduction notes

The existing guides describe how to build and operate the testbed. This
document adds what a reader who starts from the paper needs in addition: how
to obtain the tagged source, in which order to read the guides, what
"approved Host context" means, which software versions the paper's experiments
used, and what is not published.

## Obtain the tagged source

```sh
git clone https://github.com/Intelligent-Systems-Lab/5G_NWDAF_Infrastructure.git
cd 5G_NWDAF_Infrastructure
git checkout free5gc-world-forum-2026
git submodule update --init --recursive
```

The last command checks out the exact NWDAF, NRF, ADRF, and PyMTLF commits
that the tag selects.

## Order of steps

1. [Installation](../installation.md): Host prerequisites, capacity budget,
   Python environments, and the Host-only check with `make test`.
2. [Configuration](../configuration.md): create a config set from a scenario
   and generate its dataset.
3. [Operations](../operations.md), sections 2 to 4: provision the VMs, run the
   bounded MNIST smoke, then run one condition or a series.
4. [Operations](../operations.md), section 10: aggregate finished runs
   offline.
5. [Troubleshooting](../troubleshooting.md) when a preflight, readiness, or
   collection step fails.

The mapping from the paper's conditions and results to scenarios, commands,
and output files is in [the companion overview](README.md).

The deployment uses four VirtualBox VMs and Host PyMTLF containers on one
machine. The experiment runner accepts only a config generated with
`DEVICE=gpu`, so reproducing the paper's runs requires an NVIDIA GPU that
meets the requirements in [Installation](../installation.md).

## Approved Host context

The guides require commands that contact Vagrant or VirtualBox to run "in an
approved Host context". This means a shell directly on the machine where
VirtualBox is installed, with the VirtualBox kernel driver available. The
repository guard checks that `/dev/vboxdrv` is visible as a character device
and refuses the command otherwise. Do not run these commands from inside a
container, a restricted sandbox, or another environment that hides the Host
devices, and do not call `vagrant` or `VBoxManage` directly around the Make
targets.

## Software used for the paper's experiments

The repository pins the Guest and container software. The Host software is
not pinned; the table lists what the experiment Host had installed.

| Item | Version | Source of the value |
| --- | --- | --- |
| Guest box | `ubuntu/jammy64` `20241002.0.0` (Ubuntu 22.04) | `testbed.protocol-hierarchical.yaml` |
| Guest Go | 1.26.2 | `provisioning.lock.yaml` |
| Guest MongoDB | 8.0 series | `provisioning.lock.yaml` |
| PyTorch in the PyMTLF image | 2.5.1 with CUDA 12.1 | `ML/PyMTLF/pyproject.toml` and the run records |
| GPU | NVIDIA GeForce RTX 3080, 10 GiB, driver 535.183.01 | Run records |
| Host operating system | Ubuntu 20.04 | Experiment Host |
| VirtualBox | 6.1.50 | Experiment Host package database |
| Vagrant | 2.4.3 | Experiment Host package database |
| Docker Engine and Compose | 27.4.1 and v2.32.1 | Experiment Host package database |
| NVIDIA Container Toolkit (base package) | 1.19.1 | Experiment Host package database |

Other versions that satisfy [Installation](../installation.md) may work but
were not used for the paper.

## What is not published

The repository contains the source and the procedure to rebuild the inputs.
It does not contain the paper's raw run records, final models, generated
dataset partitions and seed models, or local configuration sets; see the
artifact boundary in the [root README](../../README.md). The official MNIST
and CIFAR-10 sources are downloaded by `make dataset-generate`.

## Expected variation

A rerun uses the same scenario definitions and seeds as the paper, and the
data partitions and initial models are generated from them. It is not expected
to reproduce the paper's numbers digit for digit. Repair timing depends on
Host load and scheduling, and trained accuracy can differ across GPU models,
drivers, and library builds. Results should be compared with the paper's
intervals rather than with its point values.
