# Documentation

This directory is the current user and operator documentation for the
repository. Begin with the root [README](../README.md) for the shortest runnable
path.

| Document | Use it for |
| --- | --- |
| [Architecture](architecture.md) | VM/container placement, networks, lifecycle boundaries, and the federated-learning flow |
| [Installation](installation.md) | Host software, optional GPU support, network allowlist, resource gates, and first provisioning |
| [Configuration](configuration.md) | `TESTBED`, complete config sets, scenarios, datasets, identities, and activation |
| [Operations](operations.md) | Starting, observing, stopping, resetting, and operating WebConsole |
| [Commands](commands.md) | Every Make target, parameters, side effects, and intended audience |
| [Components](components.md) | Submodules, gitlinks, metadata locks, and guest builds |
| [Validation](validation.md) | What has been checked on the reference environment and what each test proves |
| [Troubleshooting](troubleshooting.md) | Common Host, VM, network, kernel, Docker, GPU, config, and lifecycle failures |

Historical design decisions and dated experiment reports belong in the
separate `testbed-docs` repository, not in this runtime repository.
