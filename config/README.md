# Configuration sets

`default/` is the committed complete baseline. `generated/` contains renderer
output and `local/` contains complete user-managed sets; the latter two are
ignored except for their directory markers.

Every complete set selects exactly one committed scenario through
`manifest.yaml:scenario`. The default set selects
`full-core-cat-transition`. Render the bounded smoke set with:

```sh
make config-render NAME=fl-closure-smoke \
  SCENARIO=fixtures/full-core/scenarios/fl-closure-smoke.yaml
```

The renderer applies only the declared scenario-owned sampling, monitor policy,
training epochs/rounds, preparation window, and traffic profile identities.
Topology and component endpoints still come from `testbed.yaml`. Generated sets
remain ignored and must be selected as one complete `CONFIG_DIR`; do not mix
individual files from different scenarios.

`testbed.yaml:mobileNetwork.plmn` is the only MCC/MNC source. The renderer
derives NF and RAN PLMN fields, Path TAIs, 15-digit IMSI SUPIs, the Internal
Group ID, UE routing, Consumer targets, and subscriber/group fixtures from it.
Each Path declares only a TAC and three stable `subscriberNumbers`; the
renderer pads the MSIN according to whether the MNC has two or three digits.
The Internal Group declares only its eight-hex `serviceId` and octet-aligned
`localId`. Do not restore full SUPIs, TAI PLMNs, or a complete group ID to the
topology because those would become competing sources.

GPSI/MSISDN is an independent subscriber identity, not a PLMN-derived value.
Changing MCC/MNC deliberately leaves the six GPSIs in
`subscriber/ue-subscribers.json` unchanged. Edit those fixture records
explicitly only when the experiment also needs different telephone-number
identities.

`webuicfg.yaml` is the native configuration for the optional WebConsole. Its
MongoDB, NRF, management endpoint, and billing compatibility fields are part of
the validated config contract. WebConsole remains disabled by default through
`manifest.yaml`; when enabled, the pinned upstream revision requires
`billingServer.enable: true`. The otherwise-unused FTP listener is restricted to
Core VM loopback. Billing transfers, TLS, and certificates are not qualified.
