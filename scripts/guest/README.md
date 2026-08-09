# Guest scripts

Idempotent VM setup, guest-local builds, config activation, and disabled-by-
default systemd units live here. Guests never clone component branches.

`subscriber-data.js` runs under Core's installed `mongosh`. It projects the
committed compact full-core fixtures into the free5GC collections and limits
apply/show/clear operations to the six declared SUPIs and one Internal Group.

`dataset-activate.sh` accepts a Host-audited Path archive, rechecks role, set
identity, hash, bytes, and breaking-time metadata, then atomically switches the
guest-local `datasets/active` symlink. It never reads generated artifacts from
the rsynced source tree.
