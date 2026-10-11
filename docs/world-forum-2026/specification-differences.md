# Candidate specification and implementation

The paper's Appendix A gives candidate Stage-2 text, and Appendix B gives a
candidate Stage-3 OpenAPI fragment with the constraints that OpenAPI cannot
express. This document states where the pinned Go NWDAF and PyMTLF sources
match that text, where they differ, which specified members the prototype
never uses, and which rules the prototype enforces beyond the listing.

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

- **Members.** All 28 member names in the Appendix B listing exist with the
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

Appendix B makes three statements. The members are accepted only on a
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

Appendix B lists `FlTopologyStatus`, `FlTopologyStatusCause`, and
`RetainedResultStatus` as closed enumerations. Both wire models type these
members as strings and accept values outside the lists; both test suites
check that vendor-specific values are accepted. Only the listed values drive
the validation rules. `FlStrategy.method` is a closed enumeration in the
prototype, as in the listing.

### Patch schemas

Appendix B defines separate `FlTopologyNodePatch`, `FlPolicyPatch`,
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

## Enforced by the prototype but not listed in the appendix

The first four rules are enforced by both components
(`compat/mlmodeltraining/candidate_validation.go`,
`wire/ml_model_training.py`).

- `statusCause` is rejected when `status` is `UNCONFIRMED`, `DEPLOYING`, or
  `ACTIVE`.
- `minAvailableNodes` must be at least `minTrainNodes`.
- When `selectionMethod` is `priority`, every enabled explicit child must
  carry `priority`.
- `retainedResultReq` cannot be true on a disabled node.
- The acceptance conditions for CREATE listed under
  [Feature negotiation on CREATE](#feature-negotiation-on-create).
- Deadlines, retry counts, and capacity limits are implementation settings.
  For example, the preparation and round deadlines default to 300 seconds in
  PyMTLF (`config.py`) and are set per deployment, and the three DELETE
  attempts before repair that the paper describes are a cleanup setting.
