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

`dataset.py` resolves committed traffic profiles against `testbed.yaml` and the
effective native config, then builds or audits ignored, content-addressed
Parquet sets. `dataset-stage.sh plan` is read-only; `apply` uploads only the
matching Path artifact. `services-start.sh` calls it before any process starts.
