# Documentation

This directory documents the supported protocol-driven hierarchical FL
testbed. The deployment source, scenario definitions, generated artifacts, and
runtime state have distinct ownership; read the configuration and operation
guides before changing or running the environment.

## Guides

- [Architecture](architecture.md): topology, placement, networks, protocol
  behavior, lifecycle, and state owners.
- [Components](components.md): retained repositories, revision pins, and
  Guest/Host build boundaries.
- [Installation](installation.md): Host prerequisites, resource budget,
  submodules, Python environments, and initial provisioning.
- [Configuration](configuration.md): deployment and scenario inputs, rendering,
  local config sets, and artifact boundaries.
- [Commands](commands.md): Make targets, required parameters, execution domains,
  and effects.
- [Operations](operations.md): smoke, single-run, series, observation, recovery,
  stop, reset, and offline analysis procedures.
- [Troubleshooting](troubleshooting.md): current failure modes and safe recovery
  paths.

## Configuration references

- [Testbed definition](configuration/testbed-reference.md)
- [Scenario definition](configuration/scenario-reference.md)
- [Generated native configuration](configuration/native-config-reference.md)
- [Image datasets](configuration/dataset-reference.md)

The source files and `make help` output remain authoritative when a command or
field changes. These documents do not track active development status, local
runtime identity, or experiment results.
