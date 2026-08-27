#!/usr/bin/env python3
"""Resolve generated PseudoDriver datasets from topology and runtime contracts."""

import hashlib
import ipaddress
import json
import math
from pathlib import Path

from configlib import (
    ROOT, load_runtime_manifest, load_yaml, repository_relative_paths, resolve_config_scenario,
    resolve_mobile_identities, resolve_scenario_profile_paths,
)


DATASET_SCHEMA = 2


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_hash(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def load_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def _positive_int(value, label):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("{} must be a positive integer".format(label))
    return value


def _expected_ue_ips(pool, count):
    network = ipaddress.ip_network(pool)
    if network.version != 4:
        raise ValueError("PseudoDriver supports only an IPv4 UE pool")
    if count >= network.num_addresses - 1:
        raise ValueError("UE pool {} has insufficient host addresses".format(pool))
    return [str(network.network_address + index) for index in range(1, count + 1)]


def _minimum_observations(sequence_length, output_length, validation_ratio):
    purge = sequence_length + output_length - 1
    for observations in range(sequence_length + output_length + 1, 100000):
        candidates = observations - sequence_length - output_length + 1
        retained = candidates - purge
        if retained >= 2:
            validation = max(1, math.floor(retained * validation_ratio))
            validation = min(validation, retained - 1)
            training = retained - validation
            return observations, training, validation
    raise ValueError("cannot derive a finite minimum training dataset")


def _sample_counts(observations, sequence_length, output_length, validation_ratio):
    candidates = observations - sequence_length - output_length + 1
    purge = sequence_length + output_length - 1
    retained = candidates - purge
    if retained < 2:
        return 0, 0
    validation = max(1, math.floor(retained * validation_ratio))
    validation = min(validation, retained - 1)
    return retained - validation, validation


def _tool_source_hash(tool_dir):
    digest = hashlib.sha256()
    files = sorted(
        path for path in Path(tool_dir).iterdir()
        if path.is_file() and (path.suffix == ".go" or path.name in ("go.mod", "go.sum"))
    )
    if not files:
        raise ValueError("dataset generator sources are missing: {}".format(tool_dir))
    for path in files:
        digest.update(path.name.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def resolve_dataset_spec(testbed, config_dir, diagnostics=None):
    """Return a canonical dataset specification and optionally collect drift.

    Structural errors that make a dataset impossible to resolve still raise.
    Cross-file policy and timing inconsistencies are appended to ``diagnostics``
    when supplied; they never prevent dataset generation by themselves.
    """
    def diagnose(condition, message):
        if not condition and diagnostics is not None:
            diagnostics.append(message)

    config_dir = Path(config_dir)
    manifest = load_runtime_manifest(config_dir)
    static_deployment = manifest["runtime"]["deploymentKind"] in (
        "static-flat", "static-hierarchical"
    )
    selected_testbed = testbed
    path_supis = resolve_mobile_identities(selected_testbed)["pathSupis"]
    scenario_path, scenario = resolve_config_scenario(config_dir)
    profile_paths = resolve_scenario_profile_paths(scenario_path, scenario)
    profile_sources = repository_relative_paths(profile_paths)
    sampling_contract = _positive_int(
        scenario.get("samplingIntervalSeconds"), "scenario sampling interval"
    )
    monitoring = scenario.get("monitoring", {})
    training = scenario.get("training", {})
    minimum_samples = _positive_int(
        training.get("minimumSamples"), "scenario minimum training samples"
    )
    local_epochs = _positive_int(training.get("localEpochs"), "scenario local epochs")
    fitting_rounds = _positive_int(training.get("fittingRounds"), "scenario fitting rounds")
    preparation_window = _positive_int(
        training.get("preparationDataWindowSeconds"),
        "scenario preparation data window",
    )
    closure_budget = _positive_int(
        training.get("closureBudgetSeconds"),
        "scenario closure budget",
    )
    diagnose(
        training.get("enforcePerformanceGate") is False,
        "scenario must retain final validation with the performance gate disabled",
    )
    warm_start_mode = scenario.get("warmStartMode")
    if warm_start_mode not in ("inference-only", "inference-and-training"):
        raise ValueError("scenario warmStartMode is invalid")
    seed = load_json(ROOT / "ML" / "PyMTLF" / "seed_models" / "initial" / "config.json")
    sequence_length = _positive_int(seed["inference"]["seq_length"], "seed seq_length")
    output_length = _positive_int(seed["inference"]["out_seq_len"], "seed out_seq_len")
    coordinator_name = manifest["runtime"]["coordinatorContainer"]
    coordinator = load_yaml(config_dir / (coordinator_name + ".yaml"))
    monitor_period = _positive_int(
        coordinator["model_monitor"]["report_period_seconds"],
        "model monitor report period",
    )
    minimum_reference = _positive_int(
        coordinator["accuracy_policy"]["min_reference_samples"],
        "minimum reference samples",
    )
    required_hits = _positive_int(
        coordinator["accuracy_policy"]["required_hits"], "required degradation hits"
    )
    decision_window = _positive_int(
        coordinator["accuracy_policy"]["decision_window_size"], "decision window"
    )
    diagnose(
        monitor_period == monitoring.get("reportPeriodSeconds"),
        "coordinator report period differs from the scenario",
    )
    diagnose(
        minimum_reference == monitoring.get("minimumReferenceReports"),
        "coordinator minimum reference count differs from the scenario",
    )
    diagnose(
        required_hits == monitoring.get("requiredHits"),
        "coordinator required hit count differs from the scenario",
    )
    diagnose(
        decision_window == monitoring.get("decisionWindowSize"),
        "coordinator decision window differs from the scenario",
    )
    diagnose(
        required_hits <= decision_window,
        "required degradation hits exceed the decision window",
    )
    server = coordinator["federated_learning"]["server"]
    diagnose(
        server.get("round_count") == fitting_rounds,
        "coordinator fitting rounds differ from the scenario",
    )
    diagnose(
        server.get("client_training", {}).get("epochs") == local_epochs,
        "coordinator client training epochs differ from the scenario",
    )
    diagnose(
        server.get("preparation_data_window_seconds") == preparation_window,
        "coordinator preparation window differs from the scenario",
    )
    diagnose(
        server.get("final_validation", {}).get("enforce_performance_gate") is False,
        "coordinator performance gate must remain disabled",
    )

    resolved_paths = {}
    common_sampling = None
    common_validation = None
    common_min_matched = None
    for path_name in ("a", "b"):
        pseudo = testbed["paths"][path_name]["upf"]["pseudoDriver"]
        diagnose(
            pseudo.get("enabled") is True and pseudo.get("mode") == "hybrid",
            "path {} requires the hybrid PseudoDriver".format(path_name),
        )
        diagnose(
            pseudo.get("dataset", {}).get("file") == "traffic.parquet",
            "path {} dataset file must be traffic.parquet".format(path_name),
        )
        diagnose(
            pseudo["dataset"].get("guestDirectory")
            == "/var/lib/5g-nwdaf-infrastructure/datasets/active",
            "path {} dataset guest directory is not canonical".format(path_name),
        )
        profile_source = profile_sources[path_name]
        profile_path = profile_paths[path_name]
        profile = load_json(profile_path)
        if profile.get("schemaVersion") != DATASET_SCHEMA or profile.get("path") != path_name:
            raise ValueError("{} has an invalid schema or path identity".format(profile_source))
        for field in (
            "windowSeconds", "breakingTimeSeconds", "stableWindows", "degradedWindows",
            "stableUplinkBytes", "stableDownlinkBytes", "degradedUplinkBytes",
            "degradedDownlinkBytes", "degradedJitterScale",
        ):
            _positive_int(profile.get(field), "{} {}".format(profile_source, field))
        if profile["postBoundaryMode"] not in ("stable", "degraded"):
            raise ValueError("{} postBoundaryMode must be stable or degraded".format(profile_source))
        if static_deployment:
            matching_clients = []
            wanted_tac = selected_testbed["paths"][path_name]["tai"]["tac"]
            for service_name in manifest["runtime"]["hostContainers"]:
                candidate = load_yaml(config_dir / (service_name + ".yaml"))
                training_data = (
                    candidate.get("federated_learning", {}).get("client", {})
                    .get("training_data", {})
                )
                profiles = training_data.get("collection_profiles", [])
                tais = [
                    tai
                    for profile_item in profiles
                    for tai in profile_item.get("network_area", {}).get("tais", [])
                ]
                if any(tai.get("tac") == wanted_tac for tai in tais):
                    matching_clients.append(candidate)
            if not matching_clients:
                raise ValueError("path {} has no static private-training client".format(path_name))
            expected_owner_count = sum(
                owner["path"] == path_name
                for owner in manifest["runtime"]["dataOwners"]
            )
            if len(matching_clients) != expected_owner_count:
                raise ValueError(
                    "path {} private-training clients do not match declared data owners".format(
                        path_name
                    )
                )
            sampling = sampling_contract
            min_matched = minimum_reference
            expected_groups = sorted(
                owner["internalGroupId"]
                for owner in manifest["runtime"]["dataOwners"]
                if owner["path"] == path_name
            )
            actual_groups = []
            validation_ratios = {
                candidate["federated_learning"]["client"]["training"]["validation_ratio"]
                for candidate in matching_clients
            }
            if len(validation_ratios) != 1:
                raise ValueError(
                    "path {} private-training clients disagree on validation_ratio".format(
                        path_name
                    )
                )
            validation_ratio = next(iter(validation_ratios))
            for candidate in matching_clients:
                profiles = (
                    candidate["federated_learning"]["client"]["training_data"]
                    .get("collection_profiles", [])
                )
                if len(profiles) != 1:
                    raise ValueError(
                        "path {} data owner must declare exactly one collection profile".format(
                            path_name
                        )
                    )
                groups = profiles[0].get("target_ue", {}).get("intGroupIds", [])
                if len(groups) != 1:
                    raise ValueError(
                        "path {} data owner must select exactly one internal group".format(
                            path_name
                        )
                    )
                actual_groups.append(groups[0])
                diagnose(
                    candidate.get("dataset", {}).get("retrieval_window_seconds")
                    == preparation_window,
                    "path {} data-owner retrieval fallback differs from the scenario".format(
                        path_name
                    ),
                )
            if sorted(actual_groups) != expected_groups:
                raise ValueError(
                    "path {} private-training clients do not exactly cover declared data-owner groups".format(
                        path_name
                    )
                )
        else:
            anlf = load_yaml(config_dir / "pyanlf-{}.yaml".format(path_name))
            mtlf = load_yaml(config_dir / "pymtlf-{}.yaml".format(path_name))
            sampling = _positive_int(
                anlf["analytics"]["ue_communication"]["sampling_interval_seconds"],
                "path {} sampling interval".format(path_name),
            )
            min_matched = _positive_int(
                anlf["accuracy_monitor"]["min_matched_predictions"],
                "path {} minimum matched predictions".format(path_name),
            )
            validation_ratio = mtlf["federated_learning"]["client"]["training"]["validation_ratio"]
        upf = load_yaml(config_dir / "upfcfg-{}.yaml".format(path_name))
        diagnose(
            sampling == sampling_contract,
            "path {} sampling differs from the scenario".format(path_name),
        )
        diagnose(
            monitor_period % sampling == 0,
            "path {} monitor period must align to the sampling interval".format(
                path_name
            ),
        )
        report_capacity = monitor_period // sampling
        diagnose(
            report_capacity >= min_matched,
            "path {} monitor period cannot collect the required matched predictions".format(
                path_name
            ),
        )
        if not isinstance(validation_ratio, (int, float)) or not 0 < validation_ratio < 1:
            raise ValueError("path {} validation_ratio must be between 0 and 1".format(path_name))
        diagnose(
            upf["ees"]["periodSec"] == sampling,
            "path {} UPF period and AnLF sampling interval differ".format(path_name),
        )
        if not static_deployment:
            diagnose(
                mtlf["dataset"].get("retrieval_window_seconds") == preparation_window,
                "path {} retrieval fallback differs from the scenario".format(path_name),
            )
        diagnose(
            not any(
            duration % sampling
            for duration in (
                profile["breakingTimeSeconds"],
                profile["stableWindows"] * profile["windowSeconds"],
                profile["degradedWindows"] * profile["windowSeconds"],
            )
            ),
            "path {} traffic phases must align to the sampling interval".format(path_name),
        )
        diagnose(
            common_sampling is None or sampling == common_sampling,
            "Path A and B sampling intervals differ",
        )
        diagnose(
            common_validation is None or validation_ratio == common_validation,
            "Path A and B validation ratios differ",
        )
        diagnose(
            common_min_matched is None or min_matched == common_min_matched,
            "Path A and B minimum matched predictions differ",
        )
        common_sampling = sampling
        common_validation = validation_ratio
        common_min_matched = min_matched

        historical_observations = profile["breakingTimeSeconds"] // sampling
        minimum_observations, minimum_training, minimum_validation = _minimum_observations(
            sequence_length, output_length, validation_ratio
        )
        historical_training, historical_validation = _sample_counts(
            historical_observations, sequence_length, output_length, validation_ratio
        )
        stable_lead = (
            profile["stableWindows"] * profile["windowSeconds"]
            - profile["breakingTimeSeconds"]
        )
        degraded_tail = profile["degradedWindows"] * profile["windowSeconds"]
        minimum_stable_lead = (
            (minimum_reference + 1) * monitor_period + sampling
        )
        minimum_degraded_tail = (
            (required_hits + 1) * monitor_period + sampling + closure_budget
        )
        diagnose(
            stable_lead >= minimum_stable_lead,
            "path {} stable live lead-in is too short for monitor reference".format(path_name),
        )
        diagnose(
            profile["postBoundaryMode"] != "degraded"
            or degraded_tail >= minimum_degraded_tail,
            "path {} degraded tail is too short for the accuracy policy".format(path_name),
        )
        diagnose(
            path_name != "a" or profile["postBoundaryMode"] == "degraded",
            "Path A must carry the changed traffic profile",
        )
        diagnose(
            path_name != "b" or profile["postBoundaryMode"] == "stable",
            "Path B must remain the stable control",
        )
        earliest_decision = required_hits * monitor_period
        earliest_trigger = stable_lead + earliest_decision
        bounded_trigger = (
            stable_lead + (required_hits + 1) * monitor_period + sampling
        )
        diagnose(
            profile["breakingTimeSeconds"] + bounded_trigger <= preparation_window,
            "path {} preparation window cannot cover warm-start through bounded trigger".format(path_name),
        )
        trigger_observations = historical_observations + earliest_trigger // sampling
        trigger_training, trigger_validation = _sample_counts(
            trigger_observations, sequence_length, output_length, validation_ratio
        )
        diagnose(
            historical_observations >= sequence_length,
            "path {} cannot fill the PyAnLF input window".format(path_name),
        )
        diagnose(
            warm_start_mode != "inference-and-training"
            or (
                historical_training >= minimum_samples
                and historical_validation >= minimum_validation
            ),
            "path {} warm-start cannot prepare training and validation evidence".format(path_name),
        )
        diagnose(
            trigger_training >= minimum_samples
            and trigger_validation >= minimum_validation,
            "path {} earliest trigger lacks training or validation evidence".format(path_name),
        )

        resolved = dict(profile)
        resolved.update({
            "profileSource": profile_source,
            "profileHash": canonical_hash(profile),
            "ueIps": _expected_ue_ips(
                selected_testbed["paths"][path_name]["upf"]["uePool"],
                len(path_supis[path_name]),
            ),
            "artifactFile": pseudo["dataset"]["file"],
            "guestDirectory": pseudo["dataset"]["guestDirectory"],
            "samplingIntervalSeconds": sampling,
            "modelInputWindow": sequence_length,
            "modelOutputWindow": output_length,
            "validationRatio": validation_ratio,
            "historicalObservations": historical_observations,
            "minimumPreparationObservations": minimum_observations,
            "minimumTrainingSamples": minimum_training,
            "minimumValidationSamples": minimum_validation,
            "minimumAdmissionTrainingSamples": minimum_samples,
            "historicalTrainingSamples": historical_training,
            "historicalValidationSamples": historical_validation,
            "earliestTriggerSeconds": earliest_trigger,
            "triggerObservations": trigger_observations,
            "triggerTrainingSamples": trigger_training,
            "triggerValidationSamples": trigger_validation,
            "monitorReportPeriodSeconds": monitor_period,
            "monitorReportCapacity": report_capacity,
            "minimumMatchedPredictions": min_matched,
            "minimumReferenceReports": minimum_reference,
            "requiredDegradationHits": required_hits,
            "stableLeadInSeconds": stable_lead,
            "minimumStableLeadInSeconds": minimum_stable_lead,
            "degradedTailSeconds": degraded_tail,
            "minimumDegradedTailSeconds": minimum_degraded_tail,
            "boundedTriggerSeconds": bounded_trigger,
            "closureBudgetSeconds": closure_budget,
            "boundedClosureSeconds": bounded_trigger + closure_budget,
        })
        resolved_paths["path-" + path_name] = resolved

    spec = {
        "schemaVersion": DATASET_SCHEMA,
        "scenario": {
            "name": scenario["name"],
            "kind": scenario["kind"],
            "definition": scenario_path.relative_to(ROOT).as_posix(),
            "definitionHash": canonical_hash(scenario),
            "warmStartMode": warm_start_mode,
        },
        "generatorSourceHash": _tool_source_hash(ROOT / "tools" / "datasetgen"),
        "paths": resolved_paths,
    }
    spec["datasetSetId"] = canonical_hash(spec)
    return spec
