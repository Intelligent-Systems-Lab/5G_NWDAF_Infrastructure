# Candidate Stage-3 OpenAPI fragment

This document is the candidate Stage-3 fragment that the paper proposes for
the `Nnwdaf_MLModelTraining` API. The version of the paper submitted to the
free5GC World Forum 2026 for review prints the same text and listing as
Appendix B. The matching Stage-2 text is in
[Candidate Stage-2 change request text](candidate-stage2-text.md).

The fragment lists only the members and schemas that the proposal adds.
[`candidate-openapi.yaml`](candidate-openapi.yaml) is an OpenAPI document of
the same additions, with further rules that OpenAPI cannot express and with
examples. It is not a superset of this fragment.
[Candidate specification and implementation](specification-differences.md)
lists how the two differ and where the prototype differs from each.

## Proposed fragment

The following is a *candidate merge fragment* for the TS 29.520 OpenAPI
definition of `Nnwdaf_MLModelTraining`; existing schemas gain only the listed
members, and no path or operation changes. Its member names and core data
model correspond to the Go NWDAF and Python MTLF wire models used by the
prototype; implementation-specific differences are documented in [Candidate
specification and implementation](specification-differences.md). The fragment
is a proposed design, not a complete description of the prototype. The
validation of a merged, normative TS 29.520 artifact through the 3GPP
toolchain and an independent on-wire packet capture remain pending.
Constraints that OpenAPI alone cannot express follow the listing. The listing
and accompanying constraints document the proposed merge surface; a versioned
full OpenAPI artifact should accompany a future Stage-3 submission.

```yaml
components:
  schemas:
    NwdafMLModelTrainSubsc:            # added members only
      properties:
        flTopology: {$ref: '#/components/schemas/FlTopologyNode'}
        retainedResultReq: {type: boolean}
    NwdafMLModelTrainSubscPatch:       # added members; RFC 7396 merge
      properties:
        flTopology: {$ref: '#/components/schemas/FlTopologyNodePatch'}
        retainedResultReq: {type: boolean, nullable: true}
    NwdafMLModelTrainNotif:            # added members only
      properties:
        flTopologyReport: {$ref: '#/components/schemas/FlTopologyReport'}
        retainedResultStatus: {$ref: '#/components/schemas/RetainedResultStatus'}
    FlTopologyNode:
      type: object
      additionalProperties: false
      required: [nfInstanceId]
      properties:
        nfInstanceId: {$ref: 'TS29571_CommonData.yaml#/components/schemas/NfInstanceId'}
        enabled: {type: boolean}              # absent = enabled
        priority: {$ref: 'TS29571_CommonData.yaml#/components/schemas/Uinteger'}
        policy: {$ref: '#/components/schemas/FlPolicy'}
        strategy: {$ref: '#/components/schemas/FlStrategy'}
        reportAfter: {$ref: '#/components/schemas/FlReportAfter'}
        retainedResultReq: {type: boolean}
        children: {type: array, minItems: 1, items: {$ref: '#/components/schemas/FlTopologyNode'}}
    FlTopologyNodePatch:
      type: object
      nullable: true                          # null removes flTopology
      additionalProperties: false
      properties:
        nfInstanceId: {$ref: 'TS29571_CommonData.yaml#/components/schemas/NfInstanceId'}
        enabled: {type: boolean, nullable: true}
        priority: {type: integer, minimum: 0, nullable: true}
        policy: {$ref: '#/components/schemas/FlPolicyPatch'}
        strategy: {$ref: '#/components/schemas/FlStrategyPatch'}
        reportAfter: {$ref: '#/components/schemas/FlReportAfterPatch'}
        retainedResultReq: {type: boolean, nullable: true}
        children:                             # replaces the stored array
          type: array
          minItems: 1
          nullable: true
          items: {$ref: '#/components/schemas/FlTopologyNode'}
    FlPolicy:
      type: object
      additionalProperties: false
      properties:
        allowAdditionalCandidates: {type: boolean}
        additionalCandidatePriority: {$ref: 'TS29571_CommonData.yaml#/components/schemas/Uinteger'}
        selectionMethod: {type: string}       # executable: priority, random
        minAvailableNodes: {type: integer, minimum: 1}
        fractionTrain: {type: number, minimum: 0, exclusiveMinimum: true, maximum: 1}
        minTrainNodes: {type: integer, minimum: 1}
        acceptFailures: {type: boolean}
        minCompletionRate: {type: number, minimum: 0, exclusiveMinimum: true, maximum: 1}
    FlPolicyPatch:                            # FlPolicy members, each nullable
      type: object
      nullable: true
      additionalProperties: false
      properties:
        allowAdditionalCandidates: {type: boolean, nullable: true}
        additionalCandidatePriority: {type: integer, minimum: 0, nullable: true}
        selectionMethod: {type: string, nullable: true}
        minAvailableNodes: {type: integer, minimum: 1, nullable: true}
        fractionTrain: {type: number, minimum: 0, exclusiveMinimum: true, maximum: 1, nullable: true}
        minTrainNodes: {type: integer, minimum: 1, nullable: true}
        acceptFailures: {type: boolean, nullable: true}
        minCompletionRate: {type: number, minimum: 0, exclusiveMinimum: true, maximum: 1, nullable: true}
    FlStrategy:
      type: object
      additionalProperties: false
      required: [method, aggregation, methodParameters]
      properties:
        method: {type: string, enum: [fedProx]}
        aggregation: {type: string, minLength: 1}   # executable: sampleWeighted
        methodParameters: {$ref: '#/components/schemas/FedProxParameters'}
    FedProxParameters:
      type: object
      additionalProperties: false
      required: [proximalMu]
      properties:
        proximalMu: {type: number, minimum: 0}
    FlStrategyPatch:
      type: object
      nullable: true
      additionalProperties: false
      properties:
        method: {type: string, enum: [fedProx], nullable: true}
        aggregation: {type: string, minLength: 1, nullable: true}
        methodParameters:
          type: object
          nullable: true
          additionalProperties: false
          properties:
            proximalMu: {type: number, minimum: 0, nullable: true}
    FlReportAfter:
      type: object
      additionalProperties: false
      required: [count, unit]
      properties:
        count: {type: integer, minimum: 1}
        unit: {type: string, minLength: 1}    # executable: epoch (leaf), round (intermediate)
    FlReportAfterPatch:
      type: object
      nullable: true
      additionalProperties: false
      properties:
        count: {type: integer, minimum: 1, nullable: true}
        unit: {type: string, minLength: 1, nullable: true}
    FlTopologyReport:                         # rooted at the reporting NWDAF
      type: object
      additionalProperties: false
      required: [nfInstanceId]
      properties:
        nfInstanceId: {$ref: 'TS29571_CommonData.yaml#/components/schemas/NfInstanceId'}
        policy: {$ref: '#/components/schemas/FlPolicy'}
        strategy: {$ref: '#/components/schemas/FlStrategy'}
        reportAfter: {$ref: '#/components/schemas/FlReportAfter'}
        children: {type: array, minItems: 1, items: {$ref: '#/components/schemas/FlTopologyReportNode'}}
    FlTopologyReportNode:
      type: object
      additionalProperties: false
      required: [nfInstanceId, status, statusTimestamp]
      properties:
        nfInstanceId: {$ref: 'TS29571_CommonData.yaml#/components/schemas/NfInstanceId'}
        status: {$ref: '#/components/schemas/FlTopologyStatus'}
        statusTimestamp: {$ref: 'TS29571_CommonData.yaml#/components/schemas/DateTime'}
        statusCause: {$ref: '#/components/schemas/FlTopologyStatusCause'}
        policy: {$ref: '#/components/schemas/FlPolicy'}
        strategy: {$ref: '#/components/schemas/FlStrategy'}
        reportAfter: {$ref: '#/components/schemas/FlReportAfter'}
        children: {type: array, minItems: 1, items: {$ref: '#/components/schemas/FlTopologyReportNode'}}
    FlTopologyStatus:
      type: string
      enum: [UNCONFIRMED, DEPLOYING, ACTIVE, FAILED, INACTIVE]
    FlTopologyStatusCause:
      type: string
      enum: [RESPONSE_TIMEOUT, COMMUNICATION_FAILURE, FEATURE_NOT_SUPPORTED,
             REQUIREMENTS_NOT_MET, REMOVED_BY_POLICY, OTHER]
    RetainedResultStatus:
      type: string
      enum: [FOUND, NOT_FOUND, FAILED]
```

## Constraints beyond the schema

The members are accepted only on a resource that negotiated `suppFeats`
feature 3 (`HierarchicalFLOrch`, prototype value). A later PUT or PATCH that
carries them on a resource without the feature returns HTTP 403 with cause
`ML_MODEL_TRAINING_REQS_NOT_MET`; on CREATE, a receiver that does not accept
the instruction omits the feature from the returned `suppFeats`. Schema
violations return HTTP 400 with `INVALID_MSG_FORMAT`. `mlCorreId` is mandatory
with `flTopology`. An instruction must address its receiver and a report its
bound direct participant, and node identifiers must be unique across each full
instruction or report tree, bounded by 16 levels and 1,024 nodes. A `FAILED`
or `INACTIVE` report requires a `statusCause`. A PATCH is merged into the
stored representation ([RFC 7396](https://www.rfc-editor.org/rfc/rfc7396)),
and the result must satisfy the full schema. A receiver rejects contracts it
cannot execute: the evaluated MTLF executes `fedProx` with `sampleWeighted`
aggregation, `priority` or `random` selection, and `reportAfter` in epochs for
leaves and rounds for intermediates. The fragment also lists
`retainedResultReq` and `retainedResultStatus`, with which a new parent can
ask a surviving leaf for its latest completed local result instead of waiting
for retraining. The prototype validates these members but does not execute the
lookup, so the MTLF rejects a true `retainedResultReq`, and no experiment uses
them. The prototype enforces these rules together with implementation-specific
checks, and [Candidate specification and
implementation](specification-differences.md) documents where it differs from
this fragment.
