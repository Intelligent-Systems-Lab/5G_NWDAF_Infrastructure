# Candidate Stage-2 change request text

This document is the candidate Stage-2 text that the paper proposes for
hierarchical federated learning among NWDAFs. The version of the paper
submitted to the free5GC World Forum 2026 for review prints the same text as
Appendix A.

The matching Stage-3 definitions are in
[Candidate Stage-3 OpenAPI fragment](candidate-stage3-openapi.md).
[Candidate specification and implementation](specification-differences.md)
compares the prototype with those Stage-3 definitions.

## Proposed text

The following is a research proposal, not adopted 3GPP text. It is intended
for TS 23.288 Clause 6.2C (horizontal FL).

### Hierarchical FL

An FL server NWDAF may coordinate NWDAFs that act as FL clients toward their
parent and as FL servers toward their children. Roles are relative to each
parent–child relationship. The accepted topology is a rooted tree, with depth
subject to supported capability; each non-root participant has exactly one
parent and leaves may be at different depths. Each relationship is a separate
ML Model Training subscription, and all relationships of the procedure use the
same ML Correlation ID. Flat FL is the case in which every child of the root
is a leaf.

### Formation

OAM may provision eligible instances and policy, or the initiating NWDAF may
use local configuration; this proposal adds no OAM message for individual
training edges. NRF discovery yields candidate NWDAFs, not an agreed
parentage. A parent may include a recursive topology instruction in its
direct-client training subscription, listing authorized descendants and
node-local selection, strategy, and reporting parameters. A subscription
response identifies a direct resource. When preparation is requested, a
subsequent notification identifies the client and its resolved settings, with
the status it records for each direct child; the receiving parent records the
outcome for that client, and an intermediate composes child reports upward. A
candidate does not become accepted because it is listed or because a resource
exists. The root records the connected reported tree as accepted in its local
view, subject to policy; this proposal does not define a distributed commit
acknowledgment. The initiating root supplies one ML Correlation ID across
levels; the first model round on admitted edges activates training without
claiming an atomic distributed commit.

### Topology change

When a parent determines that a direct intermediate client is unavailable, it
may continue with the remaining contributors if its policy permits. It then
either selects a same-level replacement and instructs it to adopt the
surviving descendants, or adopts them directly. New subscriptions are created
only for relationships whose parent changes, and unaffected relationships and
the ML Correlation ID are preserved. The root records the repaired topology as
accepted in its local view, including one with reduced participation,
according to policy before repaired participants contribute. This local
decision is not a distributed commit acknowledgment.

### Termination and unresolved semantics

The initiator can end training by terminating its direct subscriptions; a
reachable intermediate can then terminate its direct-child subscriptions.
Reliable cascading teardown, acknowledgments, unreachable-child cleanup, and
explicit versioned acceptance of one tree across levels require further
specification and were not exercised by the prototype.
