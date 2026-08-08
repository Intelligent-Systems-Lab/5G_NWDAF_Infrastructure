# Host scripts

These scripts coordinate Vagrant, configuration, guest services, Host ML
containers, subscriptions, and terminal observability. They do not compile
network functions on the host.

`ml-start.sh`, `ml-status.sh`, and `ml-stop.sh` own only the Compose project
`5g-nwdaf-infrastructure`. Stop retains containers, named volumes, and images.
`ml-lifecycle-smoke.sh` uses a separate disposable project and CPU-only config.
Production `ml-start.sh` requires the Host CDI inventory to contain
`nvidia.com/gpu=all` and Docker to expose the `nvidia` runtime, then probes that
CDI-qualified device through NVIDIA runtime CDI mode before starting the
project. It does not configure or restart the shared daemon. `ml-status.sh`
reports the configured application device, OCI runtime, CDI selector, and
in-container CUDA visibility separately.
