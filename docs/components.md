# Components

## Retained repositories

| Path | Runtime responsibility | Build location |
| --- | --- | --- |
| `NFs/nwdaf` | Root, Branch, replacement Branch, and Leaf NWDAF processes | selected Guests |
| `NFs/nrf` | NF registration and discovery | Core Guest |
| `NFs/adrf` | analytics data and ML-model storage | Core Guest |
| `ML/PyMTLF` | Root aggregation, Branch aggregation, Leaf training, evaluation, and model artifacts | Host container image |

MongoDB is installed in the Core Guest from the repository and package policy
recorded in `provisioning.lock.yaml`; it is not a Git submodule.

## Revision identity

Three sources have different roles:

- Gitlinks select the exact component commits checked out by this repository.
- `.gitmodules` records canonical clone URLs and default-branch hints.
- `components.lock.yaml` records the exact commits expected by provisioning,
  preflight, runtime evidence, and image labels.

The gitlink and lock entry for each retained component must agree. Updating a
branch hint does not update the selected source. Updating a working tree without
updating the owning testbed revision also does not produce a release candidate.

Check the current identities with ordinary Git commands:

```sh
git submodule status
git -C NFs/nwdaf rev-parse HEAD
git -C NFs/nrf rev-parse HEAD
git -C NFs/adrf rev-parse HEAD
git -C ML/PyMTLF rev-parse HEAD
```

## Guest build boundary

Vagrant rsyncs the repository source into each Guest without local caches,
generated data, local config sets, or Python environments. Provisioning stages
only the selected Go sources, builds the required binaries, installs them under
the testbed runtime prefix, and records the expected component revisions in a
Guest provisioning manifest.

`services-start` checks provisioning identity before activating and starting
the selected systemd units. A changed component commit therefore requires the
Guest build/provisioning path to run again; changing the Host checkout alone is
not sufficient.

## Host PyMTLF image boundary

`containers/ml/Dockerfile` builds one local PyMTLF image from the pinned
`ML/PyMTLF` source and its lockfile. Generated Compose creates role-specific
containers from that common image, with separate native configs and named
volumes. Root and Leaves use the selected GPU policy in a GPU config; Branch
containers remain CPU-owned. A CPU render assigns CPU to all participants.

The experiment runner records source revisions, dirty flags, selected image
revision, and actual container devices as run evidence. Those observations do
not replace the Git and lockfile ownership described above.
