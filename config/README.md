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
