# Guest scripts

Idempotent VM setup, guest-local builds, config activation, and disabled-by-
default systemd units live here. Guests never clone component branches.
Core provisioning creates the absolute ADRF model-storage directory owned by
the unprivileged `5g-nwdaf` runtime account before services can start.

`subscriber-data.js` runs under Core's installed `mongosh`. It projects the
committed compact full-core fixtures into the free5GC collections and limits
apply/show/clear operations to the six declared SUPIs and one Internal Group.

`experiment-reset.sh` and `experiment-reset.js` implement the Core side of the
guarded Host reset. They delete only ADRF record documents, ADRF model files,
and ADRF-type NRF profile/URI-index records; MongoDB databases and collections
are retained.

`gtp5g-check.sh` verifies that the installed module targets the running Path
kernel and can be loaded. `path.sh A|B kernel` is the explicit kernel-only
rebuild used after a guest kernel update.

`dataset-activate.sh` accepts a Host-audited Path archive, rechecks role, set
identity, hash, bytes, and breaking-time metadata, then atomically switches the
guest-local `datasets/active` symlink. It never reads generated artifacts from
the rsynced source tree.
