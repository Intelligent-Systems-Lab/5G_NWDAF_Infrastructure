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

`subscriber-data.sh` validates the selected config and invokes the committed
guest `mongosh` projection for scoped `validate`, `plan`, `apply`, `show`, or
`clear`. `services-start.sh` uses only idempotent `apply`; stopping services
does not delete subscriber or Internal Group data.

`config-render.py` resolves one committed scenario contract into a complete
ignored config set. The config manifest records the scenario definition and
canonical hash, so later commands reject a stale or mixed scenario/config set.

`dataset.py` resolves the manifest-selected traffic profiles against
`testbed.yaml`, the effective native config, and the seed model, then builds or
audits ignored, content-addressed Parquet sets. The audit distinguishes
historical inference warm-start samples from the observations available at the
earliest policy trigger. `dataset-stage.sh plan` is read-only; `apply` uploads
only the matching Path artifact. `services-start.sh` calls it before any process
starts.
