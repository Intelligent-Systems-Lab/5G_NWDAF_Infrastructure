# Component source policy

The parent gitlinks are the authoritative source lock. Branches in
`.gitmodules` and `components.lock.yaml` are maintenance hints only; lifecycle
scripts must not run `git submodule update --remote`.

Initialize a checkout with:

```sh
git submodule update --init --recursive
```

`components.lock.yaml` duplicates each gitlink commit so preflight can report a
human-readable source and license inventory. A lock mismatch is an error.

## UPF and pseudo driver

`NFs/upf` is intentionally pinned to
`fix/r18-nupf-event-exposure-contract@234bae0`, not the repository's `main`.
This commit builds on `test-EES-with-pseudodriver@9a4d95c`, retains the hybrid
Parquet pseudo driver, shared reader/batching behavior, and network-clock
synchronization, and aligns `ueIpAddress` and `repPeriod` with the Release 18
Nupf Event Exposure contract. Its forwarder
accepts gtp5g versions from 0.9.5 up to, but not including, 0.10.0; therefore
`kernel/gtp5g` is fixed to `v0.9.16` rather than current upstream `master`.

The previously recorded full-core FL revision `c69051b` was a local unpushed
go-upf commit and is not fetchable from the team remote. This lock selects the
reproducible Release 18 contract fix on top of the newest remotely reproducible
pseudo-driver revision. A bounded three-VM runtime smoke has verified Nupf
subscription creation, replay callbacks, and one analytics notification per
Path. It does not establish equivalence to the historical full federated-
learning run; degradation, training, FedAvg, and reprovisioning remain separate
validation gates.

## Release blockers

The current NWDAF, ADRF, PyAnLF, and PyMTLF revisions do not contain a license
file. The parent repository also has no selected open-source license. Public
release is blocked until ownership and compatible licenses are explicitly
resolved. UERANSIM is AGPL-3.0-only and gtp5g is GPL-2.0-only; they remain
separate submodules and retain their upstream terms.
