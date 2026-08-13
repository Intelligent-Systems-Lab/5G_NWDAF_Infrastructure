# Documentation

This directory is the current user and operator documentation for the
repository. Begin with the root [README](../README.md) for the shortest runnable
path.

## Recommended reading order

| Order | Document | Continue here when |
| --- | --- | --- |
| 1 | [Architecture](architecture.md) | Learning the runtime placement, networks, lifecycle boundaries, and federated-learning flow |
| 2 | [Installation](installation.md) | Preparing Host software, VirtualBox, Docker, optional GPU support, and resource gates |
| 3 | [Configuration](configuration.md) | Selecting `TESTBED`, creating a complete config set, and generating its dataset |
| 4 | [Operations](operations.md) | Creating VMs and starting, observing, stopping, resetting, or destroying the environment |

These four documents form the first-use path. The root
[First experiment](../README.md#first-experiment) keeps the corresponding
commands in one runnable sequence.

## Reference and problem solving

| Document | Use it for |
| --- | --- |
| [Commands](commands.md) | Look up every Make target, parameter, side effect, and intended audience |
| [Components](components.md) | Understand submodules, gitlinks, metadata locks, and Guest builds |
| [Troubleshooting](troubleshooting.md) | Diagnose common Host, VM, network, kernel, Docker, GPU, config, and lifecycle failures |

Historical design decisions and dated experiment reports belong in the
separate `testbed-docs` repository, not in this runtime repository.
