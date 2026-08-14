# Installation

Install only the sections that the Host is missing. Each section begins with
read-only checks and ends with the condition that must be true for this
repository. Installing VirtualBox kernel modules, Docker, or NVIDIA support is
a Host-wide administration task; coordinate it before changing the shared
laboratory machine.

## Platform boundary

The Host must be Linux x86-64 with working VirtualBox, Vagrant, Docker Compose
v2, and Python 3. This guide intentionally does not prescribe one laboratory
Host OS or package-version snapshot; use versions supported together by their
current vendors.

The Vagrant Guests are separate and pinned by `testbed.yaml` to Ubuntu 22.04
(`ubuntu/jammy64` `20241002.0.0`). Automatic box update checks are disabled.
Changing the Guest release requires an explicit provisioning and kernel-module
compatibility review.

## 1. Base command-line tools

Check the tools used directly by repository scripts:

```sh
git --version
python3 --version
curl --version
tar --version
sha256sum --version
```

On Ubuntu, install only missing base packages:

```sh
sudo apt-get update
sudo apt-get install -y git python3 curl ca-certificates gnupg tar coreutils
```

Python project dependencies are managed with `uv` where required. If `uv` is
missing, use its official standalone installer:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Start a new shell if the installer updated `PATH`, then verify:

```sh
uv --version
```

See the [official uv installation guide](https://docs.astral.sh/uv/getting-started/installation/)
for alternate or version-specific installation methods.

## 2. VirtualBox and Vagrant

Both commands and the VirtualBox Host driver must be usable:

```sh
VBoxManage --version
vagrant --version
test -c /dev/vboxdrv
```

If VirtualBox or its driver is missing on the reference Ubuntu Host, install
the distribution packages matching the running kernel:

```sh
sudo apt-get update
sudo apt-get install -y virtualbox virtualbox-dkms "linux-headers-$(uname -r)"
sudo modprobe vboxdrv
```

If Vagrant is missing, install the HashiCorp package repository and Vagrant:

```sh
curl -fsSL https://apt.releases.hashicorp.com/gpg |
  sudo gpg --dearmor --yes -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(. /etc/os-release && echo "$VERSION_CODENAME") main" |
  sudo tee /etc/apt/sources.list.d/hashicorp.list >/dev/null
sudo apt-get update
sudo apt-get install -y vagrant
```

Rerun the three checks above after installation. This repository does not
require an additional Vagrant plugin.

### VirtualBox network allowlist

On Linux, `/etc/vbox/networks.conf` must allow every declared host-only
address. The reference Host keeps its existing laboratory range and allows the
testbed range with:

```text
* 192.168.33.0/24
* 192.168.56.0/21
```

The `/21` is only an example allowlist covering the committed topology. The
topology still creates separate `/24` networks. `experiment-validate` reports
whether the declared interfaces are allowed; `vm-up` may still fail at the
provider if the Host configuration is incompatible.

## 3. Docker Engine and Compose

Docker runs the five Host ML services. Check the CLI, Compose plugin, daemon,
and current-user access separately:

```sh
docker --version
docker compose version
docker info
```

If Docker is absent on a new Host, use the
[official Docker Engine installation guide for a supported Ubuntu release](https://docs.docker.com/engine/install/ubuntu/).
The required package set is Docker Engine, the Docker CLI, containerd, Buildx,
and the Compose v2 plugin. Do not replace the working shared installation or
remove conflicting packages merely as part of repository setup.

To grant an existing user non-root access:

```sh
sudo usermod -aG docker "$USER"
```

The new group membership takes effect after a fresh login session. You may use
`newgrp docker` to open a temporary shell instead. This permission change does
not require restarting Docker. The final `docker info` check must succeed
without `sudo`.

Do not run global Docker prune commands on this shared Host. PyTorch/CUDA image
layers are intentionally shared, while stopped project containers and named
volumes retain experiment state.

## 4. NVIDIA GPU support (optional)

Skip this section when every intended config uses `DEVICE=cpu`. GPU configs
assign only PyMTLF-A/B to `cuda:0`; PyAnLF-A/B and PyMTLF-C remain on CPU.

Check the driver, toolkit, Docker runtime, and CDI inventory independently:

```sh
nvidia-smi
nvidia-ctk --version
nvidia-ctk cdi list
docker info --format '{{json .Runtimes}}'
```

The required CDI selector is `nvidia.com/gpu=all`, and Docker must report an
`nvidia` runtime. If the driver or toolkit is missing, follow NVIDIA's current
[Ubuntu driver](https://documentation.ubuntu.com/server/how-to/graphics/install-nvidia-drivers/)
and [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
guides for the Host OS.

The standard Docker runtime configuration is a Host-wide operation:

```sh
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Do not run those commands on the shared Host without coordination: they modify
Docker daemon configuration and restart the daemon. Repository commands never
perform that configuration, restart Docker, change its default runtime, or
silently fall back from GPU to CPU. `experiment-validate` reports CDI/runtime
readiness; `ml-start` performs the actual image-level CUDA visibility probe
before starting the production containers.

## 5. Source initialization

After cloning the parent repository, initialize exactly the revisions fixed by
its gitlinks:

```sh
git submodule update --init --recursive
git submodule status --recursive
```

All committed submodule URLs use HTTPS. A leading `-` in status means a
submodule is not initialized; a leading `+` means its checkout differs from the
parent gitlink. Do not use `git submodule update --remote` for an experiment
checkout. Provisioning never clones or selects branches inside a VM.

## 6. Resource budget

The default VMs use:

| VM | RAM | vCPU | Primary disk ceiling |
| --- | ---: | ---: | ---: |
| Core | 4096 MiB | 4 | 40 GiB |
| Path A | 3072 MiB | 3 | 40 GiB |
| Path B | 3072 MiB | 3 | 40 GiB |

VirtualBox disks are dynamically allocated, so the three 40 GiB ceilings do
not immediately consume 120 GiB. The committed testbed recommends 120 GiB of
free storage and 6 GiB of available RAM outside the guest allocation for the
Host and containers. Low swap follows the configured warning policy rather
than causing memory to be preallocated at VM startup.

Use the repository preflight to inspect current headroom rather than estimating
it from total RAM or logical disk ceilings. Findings are advisory and do not
reserve resources or authorize startup:

```sh
make experiment-validate CONFIG_DIR=config/local/my-experiment
```

## 7. First provisioning

Create and validate a config before creating VMs:

```sh
make config-create \
  NAME=my-experiment \
  FROM=experiments/examples/full-core-cat-transition/scenario.yaml \
  DEVICE=gpu
make dataset-generate CONFIG_DIR=config/local/my-experiment
make experiment-validate CONFIG_DIR=config/local/my-experiment
make vm-up
```

Use `DEVICE=cpu` if section 4 was intentionally skipped. The first `vm-up`
provisions each guest and builds its assigned Go, RAN, and kernel components.
UERANSIM and gtp5g are built independently inside both Path VMs against the
guest environment. Go and MongoDB resolution follows
`provisioning.lock.yaml`; the resolved guest identity is recorded in
`/etc/5g-nwdaf-infrastructure/provisioning-manifest.yaml`.

Continue with the [standard experiment workflow](operations.md#standard-experiment-workflow).
