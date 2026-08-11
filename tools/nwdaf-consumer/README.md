# NWDAF consumer

This infrastructure-owned Core process binds the configured callback first,
queries `Nnrf_NFDiscovery`, and selects exactly one unused
`nnwdaf-eventssubscription` provider for each configured TAI. Both selected
`nfInstanceId` values must differ.

For each path it creates a standard `UE_COMMUNICATION` subscription containing
the common Internal Group ID and that path's `networkArea`. It accepts only a
`201` response with `Location`, normalizes relative locations against the
discovered service origin, and rejects cross-origin locations.

The state file stores the discovered NF identity, service API root, correlation
ID, exact resource location, and latest callback summary. If the second create
fails, the first resource is deleted. During teardown, any failed DELETE leaves
the callback process and state available so `make subscriptions-stop` can be
retried safely.

The normal user interface is:

```sh
make subscriptions-start
make subscriptions-status
make subscriptions-stop
```

These commands do not start or stop VMs and do not own the 5GC service stack.

Validate a config without contacting NRF or creating runtime state with:

```sh
python3 tools/nwdaf-consumer/consumer.py \
  --config config/default/consumer.yaml validate
```

Validation rejects missing and unknown fields, malformed PLMN/TAI values,
non-HTTP endpoints, non-IP bind addresses, duplicate path names, unsupported
reporting methods, and relative state paths.
