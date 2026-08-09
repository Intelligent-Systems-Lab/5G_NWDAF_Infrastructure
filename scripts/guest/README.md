# Guest scripts

Idempotent VM setup, guest-local builds, config activation, and disabled-by-
default systemd units live here. Guests never clone component branches.

`subscriber-data.js` runs under Core's installed `mongosh`. It projects the
committed compact full-core fixtures into the free5GC collections and limits
apply/show/clear operations to the six declared SUPIs and one Internal Group.
