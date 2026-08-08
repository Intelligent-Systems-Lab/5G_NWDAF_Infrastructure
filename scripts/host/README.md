# Host scripts

These scripts coordinate Vagrant, configuration, guest services, Host ML
containers, subscriptions, and terminal observability. They do not compile
network functions on the host.

`ml-start.sh`, `ml-status.sh`, and `ml-stop.sh` own only the Compose project
`5g-nwdaf-infrastructure`. Stop retains containers, named volumes, and images.
`ml-lifecycle-smoke.sh` uses a separate disposable project and CPU-only config.
Production `ml-start.sh` requires the Host CDI inventory to contain
`nvidia.com/gpu=all` and probes that device before starting the project. It does
not configure a Docker runtime or restart the shared daemon. `ml-status.sh`
reports the configured application device, actual CDI request, and in-container
CUDA visibility separately.
