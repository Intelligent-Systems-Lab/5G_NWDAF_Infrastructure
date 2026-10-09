# Relation to upstream free5GC

The paper describes a free5GC-based realization. This document states which
parts of the prototype come from upstream free5GC, which do not, and what is
needed beyond the upstream release to run it.

The statements about upstream were checked on 2026-10-09 against the public
`free5gc` GitHub organization. Upstream may have changed since.

## What the prototype runs

The testbed runs NRF, ADRF, the Go NWDAF, MongoDB, and the PyMTLF backend
containers; see [Architecture](../architecture.md) and
[Components](../components.md). It does not run AMF, SMF, UPF, or the other
5G core network functions, and it attaches no UE or RAN. The hierarchy that
the paper evaluates is formed among NWDAF instances, with NRF used for
registration and discovery and ADRF for data and model storage.

## What upstream free5GC provides

The upstream `free5gc/free5gc` repository selects these components as
submodules: AMF, AUSF, BSF, CHF, N3IWF, NEF, NRF, NSSF, PCF, SCP, SMF, TNGF,
UDM, UDR, UPF, and WebConsole. It includes no NWDAF and no ADRF, and the
`free5gc` organization publishes no repository for either.

Of the components that the prototype runs, only NRF originates from upstream.

## Components and their origin

| Component | Repository | Relation to upstream free5GC |
| --- | --- | --- |
| NRF | `Intelligent-Systems-Lab/nrf` | Fork of `free5gc/nrf` with two additional commits, described below |
| NWDAF | `Intelligent-Systems-Lab/NWDAF` | Not part of upstream; its Go module path uses the `github.com/free5gc/nwdaf` name, but no such upstream repository exists |
| ADRF | `Intelligent-Systems-Lab/adrf` | Not part of upstream; follows the free5GC application structure |
| PyMTLF | `Intelligent-Systems-Lab/PyMTLF` | Not part of upstream; a private Python backend of the NWDAF, not a 3GPP network function |
| MongoDB | Installed in the Core Guest according to `provisioning.lock.yaml` | Third-party dependency |

### NRF

The pinned NRF commit is two commits ahead of the upstream commit it was
forked from. Upstream has continued to change after that point, and the fork
does not include those later upstream commits.

- **Preserve NWDAF federated capabilities** (required by the prototype). The
  NRF stores and returns the Release 18 NWDAF and ADRF profile members without
  loss, and it validates and matches the extended discovery filters. The
  prototype's hierarchy discovery queries NRF for NWDAFs by ML analytics
  information and FL capability
  (`core/fl_hierarchy_discovery.py` in PyMTLF), which depends on these members
  and filters.
- **Preserve internal group identifier ranges** (not exercised by the
  prototype). The NRF keeps the UDM internal group identifier ranges in its
  stored representation, so UDM discovery filtered by internal group returns
  the registered capability. This path serves UE data collection. The testbed
  runs no UDM, and the paper's experiments do not use it.

The prototype has been run only with the pinned fork. It has not been tested
with an unmodified upstream NRF.

### NWDAF

The Go NWDAF implements NWDAF services based on 3GPP TS 29.520, including the
`Nnwdaf_MLModelTraining` service that the paper extends. It owns the SBI
resources, NRF registration and discovery, and transport between NWDAF peers,
and it delegates model training to PyMTLF. The hierarchical topology members
are a project extension and are not defined by 3GPP; see
[Candidate specification and implementation](specification-differences.md).

### ADRF

The ADRF implements the Data Management and ML Model Management services. It
stores metadata in MongoDB and model artifacts on the local filesystem, and it
registers with NRF.

### PyMTLF

PyMTLF is the Model Training Logical Function backend of one NWDAF process.
It performs root aggregation, intermediate aggregation, leaf training,
evaluation, and model artifact handling. Other network functions reach it
only through its NWDAF.

## What is needed beyond an upstream deployment

To run the prototype, a deployment based on upstream free5GC needs:

1. the first NRF change above, or equivalent support for the Release 18 NWDAF
   and ADRF profile members and discovery filters;
2. the NWDAF, ADRF, and PyMTLF components at the pinned commits;
3. MongoDB for the NRF and ADRF records.

This repository provides all three for the topology in
[`testbed.protocol-hierarchical.yaml`](../../testbed.protocol-hierarchical.yaml).
It does not integrate the prototype with a full upstream 5G core, and the
paper's experiments do not depend on one.
