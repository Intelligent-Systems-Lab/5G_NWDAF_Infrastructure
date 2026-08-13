# Components and Source Locks

## Repository model

Network functions, ML services, RAN, gtp5g, and WebConsole remain independent
repositories mounted as submodules:

- `NFs/`: free5GC NFs plus the project NWDAF, ADRF, SMF, UDM, UDR, NRF, and UPF
- `ML/`: PyAnLF and PyMTLF
- `RAN/`: UERANSIM
- `kernel/`: gtp5g
- `webconsole/`: optional free5GC WebConsole

`nwdaf-resources` is not a runtime dependency. The infrastructure owns the
Consumer and dataset generator under `tools/`, while generated Parquet data
remains ignored.

## What fixes a version

The parent gitlink commit is the executable source lock. A branch name is only
a maintenance hint and cannot make an installed checkout move. Three pieces of
metadata are kept consistent:

1. `.gitmodules` defines each submodule path, HTTPS clone URL, and branch hint.
2. The parent Git index records the exact gitlink commit.
3. `components.lock.yaml` repeats path, URL, branch or tag, and commit metadata
   for readable validation and inventory.

Initialize exactly those revisions with:

```sh
git submodule update --init --recursive
```

Do not use `git submodule update --remote` for an experiment checkout. The
branch tip may advance while the parent gitlink intentionally remains fixed.

Preflight verifies each installed HEAD against the readable lock and rejects a
dirty submodule worktree. It does not require a local submodule's `origin` URL
to match because the checked-out commit, not the local fetch configuration, is
the executable source identity.

## Guest build boundary

Vagrant syncs the parent-pinned source snapshot into each VM. Guests never
clone or select branches:

- Core builds its assigned free5GC NFs, ADRF, and NWDAF-C.
- Each Path builds UPF, UERANSIM, and its path NWDAF.
- Each Path builds gtp5g against its own running kernel.
- PyAnLF and PyMTLF are built into Host images, not guest Python environments.

Runtime helper scripts and systemd definitions are hash-synced before config
activation, so helper changes can reach existing VMs without rebuilding every
guest component.

Go and MongoDB provisioning inputs are separately owned by
`provisioning.lock.yaml`. Go archive identity and SHA-256 are strict. MongoDB
uses the declared compatible package family and records the actually resolved
patch version with visible drift evidence. Each guest writes its resolved
identity to `/etc/5g-nwdaf-infrastructure/provisioning-manifest.yaml`.

## UPF and gtp5g

The pinned UPF contains the Release 18 Nupf Event Exposure contract, hybrid
Parquet PseudoDriver, and distinct configured GTP interface support for the two
paths. The gtp5g gitlink is pinned to the compatible `v0.9.16` tag. Before any
guest service starts, each Path checks that the installed kernel module's
vermagic matches the running kernel and that the module can load.

If a guest kernel changes, rebuild only that dependency:

```sh
vagrant ssh path-a -c 'sudo /opt/5g-nwdaf-infrastructure/source/scripts/guest/path.sh A kernel'
vagrant ssh path-b -c 'sudo /opt/5g-nwdaf-infrastructure/source/scripts/guest/path.sh B kernel'
```
