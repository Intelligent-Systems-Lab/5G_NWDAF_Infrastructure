# Generated Native Configuration Reference

`config-create` writes one complete config set under `config/local/<name>/`.
The directory is generated from the selected testbed and scenario and is
ignored by Git.

## Generated files

| Output | Consumer |
| --- | --- |
| `manifest.yaml` | lifecycle, status, runner, dataset, reset, and evidence tooling |
| `nrfcfg.yaml` | NRF in the Core Guest |
| `adrfcfg.yaml` | ADRF in the Core Guest |
| `nwdafcfg-*.yaml` | each selected Root, Branch, and Leaf NWDAF |
| `pymtlf-*.yaml` | each selected Host PyMTLF service |
| `topology/protocol-hierarchical.yaml` | protocol-aware PyMTLF services |
| `network/<machine>.yaml` | persistent Guest network aliases |
| `compose.yaml` | selected Host PyMTLF services, devices, mounts, ports, and volumes |

The file inventory varies by scenario. A normal or reparenting config omits the
inactive replacement Branch service; a replacement-fault config includes it.

## Manifest

`manifest.yaml` records:

- config-set name;
- a complete snapshot of the rendered scenario;
- selected testbed definition and deployment kind;
- render options, including CPU or GPU device policy;
- exact Guest machine/service and Host container/volume inventory;
- selected NWDAF definitions and resource capacity;
- scoped reset ownership;
- seed-restoration input;
- dataset paths;
- the generated file inventory.

Runtime commands use the scenario snapshot in the manifest. Editing the source
scenario after rendering does not mutate an existing config set; render a new
set, or deliberately replace the old one with `FORCE=true` while it is not
active.

## Guest activation

Guest lifecycle tooling stages the complete config set on every selected VM,
checks required files for that machine, activates the staged directory, and
updates persistent network aliases before starting units. Activation is refused
while experiment services are active.

The selected config tree has one content-derived runtime identity used to fence
selected, staged, active Guest, and Compose state. It is an existing lifecycle
contract: a mismatch stops startup, status-sensitive operations, or reset
instead of guessing which config owns the runtime. Operators do not need to
calculate or pass this value manually.

## Host Compose

Generated Compose builds the local PyMTLF image and starts only the selected
service inventory. Each service receives its native config, role-specific
dataset mount, volume, port, CPU/memory limits, and device policy. GPU services
use the Docker `nvidia` runtime and CDI selection; CPU-owned Branch services do
not receive GPU access.

## Editing boundary

Do not maintain behavior by editing generated files directly. Change:

- deployment ownership in `testbed.protocol-hierarchical.yaml`;
- run behavior in a scenario;
- component-native defaults in `config/templates` or the renderer when that
  is the owning contract.

Then render and validate a new config set. Manual edits can produce a config
that no longer corresponds to either authoritative input and will normally be
rejected by identity or cross-config checks.
