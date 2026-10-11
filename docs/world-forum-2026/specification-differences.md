# Candidate specification and implementation

The candidate specification that the paper proposes is in three documents in
this directory:

- [Candidate Stage-2 change request text](candidate-stage2-text.md);
- [Candidate Stage-3 OpenAPI fragment](candidate-stage3-openapi.md), which
  lists the added members and schemas and the constraints that OpenAPI cannot
  express;
- [`candidate-openapi.yaml`](candidate-openapi.yaml), an OpenAPI document of
  the same additions, with further rules and examples.

This document states where the pinned Go NWDAF and PyMTLF sources match the
fragment, where they differ, which specified members the prototype never uses,
and which rules the prototype enforces beyond the fragment. [The OpenAPI
document](#the-openapi-document) then states how `candidate-openapi.yaml`
differs from the fragment and from the prototype.

Every statement below was checked against the component sources at the pinned
commits. Paths are relative to `NFs/nwdaf/internal` for the Go NWDAF and to
`ML/PyMTLF/src/py_mtlf` for PyMTLF. Behaviour that was not read directly from
source is not listed.

## Division of work between the two components

The Go NWDAF serves the `Nnwdaf_MLModelTraining` resource routes
(`sbi/api_ml_model_training.go`). It parses and validates the candidate
members and applies merge patches (`compat/mlmodeltraining/`), and it relays
requests to its private PyMTLF backend. PyMTLF decides whether a subscription
that carries a topology instruction is accepted (`core/fl_client.py`), runs
preparation and training, and produces and reads the topology reports
(`core/fl_candidate_orchestration.py`, `core/fl_root.py`,
`core/fl_branch.py`).

## Matches

- **Members.** All 28 member names in the fragment's listing exist with the
  same names in both wire models (`compat/mlmodeltraining/candidate.go`,
  `compat/mlmodeltraining/models.go`, `wire/ml_model_training.py`). Both
  reject unknown members in the added objects.
- **Feature.** The prototype feature is number 3, `HierarchicalFLOrch`, whose
  `suppFeats` mask is `"4"` (`compat/mlmodeltraining/candidate.go`,
  `wire/features.py`).
- **Size bounds.** An instruction or report tree is limited to 16 levels and
  1,024 nodes in both components (`compat/mlmodeltraining/candidate.go`,
  `wire/ml_model_training.py`).
- **Identity.** An instruction must address its receiver, and a report must
  come from the participant bound to the subscription
  (`compat/mlmodeltraining/candidate_validation.go`,
  `wire/ml_model_training.py`).
- **Correlation.** `mlCorreId` is required with `flTopology` in both
  components (`compat/mlmodeltraining/validation.go`,
  `wire/ml_model_training.py`).
- **Report status.** A `FAILED` or `INACTIVE` report node requires
  `statusCause` in both components
  (`compat/mlmodeltraining/candidate_validation.go`,
  `wire/ml_model_training.py`).
- **PATCH.** Both components merge a PATCH body into the stored representation
  and then validate the merged result
  (`compat/mlmodeltraining/validation.go`, `wire/ml_model_training.py`).
- **Retained results.** The retained-result members are validated but not
  executed, and, as the paper states, PyMTLF rejects a true
  `retainedResultReq`. The prototype returns HTTP 403 for it
  (`_reject_retained_instruction` in `core/fl_client.py`).

## Differences

### Feature negotiation on CREATE

The fragment makes three statements. The members are accepted only on a
resource that negotiated the feature, and a later PUT or PATCH that carries
them on a resource without the feature returns HTTP 403 with cause
`ML_MODEL_TRAINING_REQS_NOT_MET`. On CREATE, a receiver that does not accept
the instruction omits the feature from the returned `suppFeats`. A receiver
rejects contracts it cannot execute.

The prototype follows the first two. For PUT and PATCH on an existing
resource it returns HTTP 403, and the Go NWDAF also applies the check to
notifications (`candidateTrainingOperationProblem` in
`sbi/processor/ml_model_training.go`). On CREATE, when PyMTLF does not accept
a subscription that carries `flTopology`, it creates the resource, returns
HTTP 201, and omits the feature from the returned `suppFeats`. The resource
stays idle. The sender inspects the returned `suppFeats`, deletes the
resource, and records `FEATURE_NOT_SUPPORTED` (`create()` in
`core/fl_client.py`; the sender side is in `core/fl_server.py`). Other
requirement failures on CREATE, such as a true `retainedResultReq`, return
HTTP 403.

The difference is in the third statement: on CREATE the prototype does not
reject a contract that it cannot execute. PyMTLF parses and validates the
members first, so a malformed instruction is rejected with HTTP 400. It then
omits the feature, as described above, in two situations: this NWDAF is not
configured or capable for the requested role, or it is capable but cannot
execute this particular request. `_can_accept_protocol_resource()` in
`core/fl_client.py` declines the feature when any of the following holds:

- `mLPreFlag` is not true, or the request carries `mLModelInfos`;
- the NWDAF has no round-model distribution service;
- the ML event and model interoperability identifier are not a supported
  image-training contract;
- the NWDAF does not advertise an FL capability that matches the requested
  role for the ML event;
- for a leaf: the NWDAF is not configured for local image classification, the
  local dataset or model interoperability identifier does not match,
  `strategy` or `reportAfter` is absent, or the `reportAfter` unit is not
  `epoch`;
- for an intermediate: the NWDAF has no intermediate coordinator, or no
  executable contract can be resolved from the instruction.

Returning the supported subset of features in the creation response follows
the feature negotiation of 3GPP TS 29.500 clause 6.6.2, and a receiver that
does not implement the extension behaves the same way. The second of the two
situations is closer to the existing `ML_MODEL_TRAINING_REQS_NOT_MET` error of
3GPP TS 29.520. The prototype does not distinguish the two, so a sender cannot
tell from the response which one occurred.

### Status and cause values are open strings

The fragment lists `FlTopologyStatus`, `FlTopologyStatusCause`, and
`RetainedResultStatus` as closed enumerations. Both wire models type these
members as strings and accept values outside the lists; both test suites
check that vendor-specific values are accepted. Only the listed values drive
the validation rules. `FlStrategy.method` is a closed enumeration in the
prototype, as in the listing.

### Patch schemas

The fragment defines separate `FlTopologyNodePatch`, `FlPolicyPatch`,
`FlStrategyPatch`, and `FlReportAfterPatch` schemas. PyMTLF has corresponding
models, and additionally names the patch form of `FedProxParameters`. The Go
NWDAF has no separate patch types for these objects: its PATCH body reuses
`FlTopologyNode`, and it validates the merged result against the full schema.

## Specified but not exercised by the prototype

- **`flTopology` in PATCH.** Both components accept and process it. No sender
  in the prototype issues it: the PATCH requests that PyMTLF builds carry
  preparation-flag, round, model, validation, and deadline members only
  (`core/fl_server.py`). Tree formation and repair use CREATE, as the paper
  states.
- **`INACTIVE`.** PyMTLF sets this status when a later instruction removes or
  disables a child that was already being established or active
  (`core/fl_candidate_orchestration.py`). The evaluated flows do not send
  such an instruction, and the paper reports that every recorded direct-child
  status was `ACTIVE`.
- **`OTHER` and `RESPONSE_TIMEOUT` causes.** `OTHER` is not produced by any
  flow. `RESPONSE_TIMEOUT` is set only by the root for its own direct
  children (`core/fl_root.py`), and the root reports to no parent.
- **`statusCause` as input.** Reports carry it, but no receiver reads it. The
  root decides acceptance from the direct children whose `status` is `ACTIVE`
  (`_active_report_children` in `core/fl_root.py`).
- **Depth.** The validators admit 16 levels. The root in this prototype plans
  a root, intermediate, and leaf tree; leaves at different depths arise only
  after the root adopts leaves directly during repair. The paper states the
  same limit.

## Enforced by the prototype but not listed in the fragment

The first four rules are enforced by both components
(`compat/mlmodeltraining/candidate_validation.go`,
`wire/ml_model_training.py`). The OpenAPI document states all four among its
`x-stage3-rules`.

- `statusCause` is rejected when `status` is `UNCONFIRMED`, `DEPLOYING`, or
  `ACTIVE`.
- `minAvailableNodes` must be at least `minTrainNodes`.
- When `selectionMethod` is `priority`, every enabled explicit child must
  carry `priority`.
- `retainedResultReq` cannot be true on a disabled node.
- The acceptance conditions for CREATE listed under
  [Feature negotiation on CREATE](#feature-negotiation-on-create), beyond the
  executable profile that the fragment names.
- Deadlines, retry counts, and capacity limits are implementation settings.
  For example, the preparation and round deadlines default to 300 seconds in
  PyMTLF (`config.py`) and are set per deployment, and the three DELETE
  attempts before repair that the paper describes are a cleanup setting.

## The OpenAPI document

[`candidate-openapi.yaml`](candidate-openapi.yaml) is an OpenAPI document that
contains what the proposal adds: the added members and schemas, the feature,
the rules that OpenAPI cannot express (`x-stage3-rules`), and examples. It
defines no paths. It gives each of the three extended schemas as the schema of
the same name in the OpenAPI definition of 3GPP TS 29.520 V18.14.0 together
with the added members, so it refers to the 3GPP files instead of repeating
their content. It is not a superset of the fragment; the two differ as listed
below. Like the fragment, it has not been validated with the 3GPP toolchain.

### Differences from the fragment

- **Patch schemas.** The OpenAPI document defines no separate patch schemas.
  The `flTopology` member that it adds to the PATCH body refers to
  `FlTopologyNode`, which requires `nfInstanceId` and has no nullable members.
  The fragment's patch schemas make every member optional and every member
  except `nfInstanceId` nullable.
- **Extensible enumerations.** `FlTopologyStatus`, `FlTopologyStatusCause`,
  and `RetainedResultStatus` are a listed set of values or any other string,
  the form that 3GPP OpenAPI uses for enumerations that may grow. The
  fragment lists the same values as closed enumerations.
- **Named value sets.** The OpenAPI document defines `FlSelectionMethod`
  (`priority`, `random`), `FlAggregation` (`sampleWeighted`), and
  `FlReportUnit` (`epoch`, `round`) as extensible enumerations. The fragment
  types these members as strings, requires `aggregation` and `unit` to be
  non-empty, and names the executable values in comments.
- **Strategy.** In the OpenAPI document `FlStrategy` is a `oneOf` with a
  discriminator on `method` and one alternative, `FedProxStrategy`. The
  fragment gives the same three members directly in `FlStrategy`.
- **Defaults and annotations.** The OpenAPI document states defaults for
  `enabled` (true), `allowAdditionalCandidates` (false), and
  `additionalCandidatePriority` (0), marks `retainedResultReq` as write-only,
  and gives the format `float` for `fractionTrain`, `minCompletionRate`, and
  `proximalMu`. The fragment states only that an absent `enabled` means
  enabled.
- **Rules that only the OpenAPI document states.** The scope of `policy`,
  `strategy`, and `reportAfter`; the priority and node-count rules; the
  required content of a notification; the correlation rules for
  retained-result requests, for PATCH, and for notifications; the
  retained-result rules; and the rule that `statusCause` is absent for a
  status other than `FAILED` or `INACTIVE`.
- **Rules that only the fragment states.** The bounds of 16 levels and 1,024
  nodes; the HTTP status and cause returned for a resource without the feature
  and for a schema violation; the omission of the feature on CREATE; the
  validation of the merged result of a PATCH against the full schema; and the
  rejection of contracts that the receiver cannot execute, with the profile
  that the evaluated MTLF executes.

### Relation to the prototype

- **Types.** The prototype accepts values outside the lists for the status,
  cause, and retained-result status members, as described under
  [Status and cause values are open strings](#status-and-cause-values-are-open-strings),
  which agrees with the extensible enumerations of the OpenAPI document.
- **Patch.** The Go NWDAF has no separate patch types, as in the OpenAPI
  document, and PyMTLF has patch models, as in the fragment. Both accept what
  the fragment's patch schemas describe: a PATCH may omit `nfInstanceId` and
  may carry null members
  (`compat/mlmodeltraining/candidate_validation.go`,
  `wire/ml_model_training.py`). The OpenAPI document's schema permits
  neither.
- **Cause values.** Both documents list the six values that PyMTLF defines
  (`CandidateStatusCause` in `core/fl_candidate_orchestration.py`). The Go
  NWDAF defines no list and treats the member as a string.
- **Rules enforced by both components**
  (`compat/mlmodeltraining/candidate_validation.go`,
  `compat/mlmodeltraining/validation.go`, `wire/ml_model_training.py`):
  - an instruction addresses its receiver, and a report comes from the bound
    direct participant;
  - node identifiers are unique within an instruction or report;
  - with `selectionMethod` `priority`, every enabled explicit child carries
    `priority`;
  - `minAvailableNodes` is at least `minTrainNodes`;
  - a notification carries at least one detailed member, and
    `delayEventNotif` does not coexist with `mLModelInfos` or `termTrainReq`;
  - `mlCorreId` is required with `flTopology` or a true `retainedResultReq`
    and in a notification that carries `flTopologyReport` or
    `retainedResultStatus`, and a PATCH that uses either request member is
    accepted only on a resource that already carries `mlCorreId`;
  - `retainedResultStatus` `FOUND` requires `roundInd` and `mLModelInfos`, and
    `NOT_FOUND` or `FAILED` excludes them;
  - `statusCause` is required for `FAILED` or `INACTIVE` and rejected for the
    other listed statuses;
  - `retainedResultReq` cannot be true on a disabled node.
- **Retained-result lookup.** The OpenAPI document specifies the lookup: it
  is scoped to one operation, is not stored as subscription state, and a
  subscription has at most one outstanding lookup. Neither component stores
  `retainedResultReq`. PyMTLF removes it on every write
  (`split_candidate_operations` in `wire/ml_model_training.py`). The Go NWDAF
  removes it on PUT and PATCH (`StripCandidateOperations` in
  `compat/mlmodeltraining/candidate.go`); on CREATE it stores the returned
  representation and refuses one that still carries the member
  (`validateTrainingCreateResponse` in `sbi/processor/ml_model_training.go`).
  The prototype does not execute a lookup, and PyMTLF rejects a true
  `retainedResultReq` (see [Matches](#matches)).
- **Feature negotiation.** The OpenAPI document requires the feature to be
  negotiated on every parent-to-child subscription resource. It does not say
  how a receiver signals that it does not accept the feature. The fragment
  does, and the prototype follows it as described under
  [Feature negotiation on CREATE](#feature-negotiation-on-create).
- **Bounds.** The OpenAPI document sets no limit on depth or node count. The
  prototype applies the limits of 16 levels and 1,024 nodes that the fragment
  states.
- **Examples.** The examples in the OpenAPI document are illustrative. The
  subscription example sets `mLPreFlag` without `mLModelTrainInfos`, which
  both components reject as an unmet training requirement
  (`compat/mlmodeltraining/validation.go`, `wire/ml_model_training.py`).
