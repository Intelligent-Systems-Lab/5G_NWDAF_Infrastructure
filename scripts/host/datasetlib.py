#!/usr/bin/env python3
"""Resolve generated PseudoDriver datasets from topology and runtime contracts."""

import hashlib
import ipaddress
import json
import math
from pathlib import Path

from configlib import (
    ROOT, load_yaml, repository_relative_paths, resolve_config_scenario,
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


def resolve_dataset_spec(testbed, config_dir):
    """Return a canonical, fully resolved dataset set specification."""
    config_dir = Path(config_dir)
    path_supis = resolve_mobile_identities(testbed)["pathSupis"]
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
    if training.get("enforcePerformanceGate") is not False:
        raise ValueError("scenario must retain final validation with the performance gate disabled")
    warm_start_mode = scenario.get("warmStartMode")
    if warm_start_mode not in ("inference-only", "inference-and-training"):
        raise ValueError("scenario warmStartMode is invalid")
    seed = load_json(ROOT / "ML" / "PyMTLF" / "seed_models" / "initial" / "config.json")
    sequence_length = _positive_int(seed["inference"]["seq_length"], "seed seq_length")
    output_length = _positive_int(seed["inference"]["out_seq_len"], "seed out_seq_len")
    coordinator = load_yaml(config_dir / "pymtlf-c.yaml")
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
    if monitor_period != monitoring.get("reportPeriodSeconds"):
        raise ValueError("coordinator report period differs from the scenario")
    if minimum_reference != monitoring.get("minimumReferenceReports"):
        raise ValueError("coordinator minimum reference count differs from the scenario")
    if required_hits != monitoring.get("requiredHits"):
        raise ValueError("coordinator required hit count differs from the scenario")
    if decision_window != monitoring.get("decisionWindowSize"):
        raise ValueError("coordinator decision window differs from the scenario")
    if required_hits > decision_window:
        raise ValueError("required degradation hits exceed the decision window")
    server = coordinator["federated_learning"]["server"]
    if server.get("round_count") != fitting_rounds:
        raise ValueError("coordinator fitting rounds differ from the scenario")
    if server.get("preparation_data_window_seconds") != preparation_window:
        raise ValueError("coordinator preparation window differs from the scenario")
    if server.get("final_validation", {}).get("enforce_performance_gate") is not False:
        raise ValueError("coordinator performance gate must remain disabled")

    resolved_paths = {}
    common_sampling = None
    common_validation = None
    common_min_matched = None
    for path_name in ("a", "b"):
        pseudo = testbed["paths"][path_name]["upf"]["pseudoDriver"]
        if pseudo.get("enabled") is not True or pseudo.get("mode") != "hybrid":
            raise ValueError("path {} requires the hybrid PseudoDriver".format(path_name))
        if pseudo.get("dataset", {}).get("file") != "traffic.parquet":
            raise ValueError("path {} dataset file must be traffic.parquet".format(path_name))
        if pseudo["dataset"].get("guestDirectory") != "/var/lib/5g-nwdaf-infrastructure/datasets/active":
            raise ValueError("path {} dataset guest directory is not canonical".format(path_name))
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
        anlf = load_yaml(config_dir / "pyanlf-{}.yaml".format(path_name))
        mtlf = load_yaml(config_dir / "pymtlf-{}.yaml".format(path_name))
        upf = load_yaml(config_dir / "upfcfg-{}.yaml".format(path_name))
        sampling = _positive_int(
            anlf["analytics"]["ue_communication"]["sampling_interval_seconds"],
            "path {} sampling interval".format(path_name),
        )
        min_matched = _positive_int(
            anlf["accuracy_monitor"]["min_matched_predictions"],
            "path {} minimum matched predictions".format(path_name),
        )
        if sampling != sampling_contract:
            raise ValueError("path {} sampling differs from the scenario".format(path_name))
        if monitor_period % sampling:
            raise ValueError(
                "path {} monitor period must align to the sampling interval".format(
                    path_name
                )
            )
        report_capacity = monitor_period // sampling
        if report_capacity < min_matched:
            raise ValueError(
                "path {} monitor period cannot collect the required matched predictions".format(
                    path_name
                )
            )
        validation_ratio = mtlf["federated_learning"]["client"]["training"]["validation_ratio"]
        if not isinstance(validation_ratio, (int, float)) or not 0 < validation_ratio < 1:
            raise ValueError("path {} validation_ratio must be between 0 and 1".format(path_name))
        if upf["ees"]["periodSec"] != sampling:
            raise ValueError("path {} UPF period and AnLF sampling interval differ".format(path_name))
        if mtlf["federated_learning"]["client"]["training"].get("epochs") != local_epochs:
            raise ValueError("path {} local epochs differ from the scenario".format(path_name))
        if mtlf["dataset"].get("retrieval_window_seconds") != preparation_window:
            raise ValueError("path {} retrieval fallback differs from the scenario".format(path_name))
        if any(
            duration % sampling
            for duration in (
                profile["breakingTimeSeconds"],
                profile["stableWindows"] * profile["windowSeconds"],
                profile["degradedWindows"] * profile["windowSeconds"],
            )
        ):
            raise ValueError("path {} traffic phases must align to the sampling interval".format(path_name))
        if common_sampling is not None and sampling != common_sampling:
            raise ValueError("Path A and B sampling intervals differ")
        if common_validation is not None and validation_ratio != common_validation:
            raise ValueError("Path A and B validation ratios differ")
        if common_min_matched is not None and min_matched != common_min_matched:
            raise ValueError("Path A and B minimum matched predictions differ")
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
        if stable_lead < minimum_stable_lead:
            raise ValueError("path {} stable live lead-in is too short for monitor reference".format(path_name))
        if (
            profile["postBoundaryMode"] == "degraded"
            and degraded_tail < minimum_degraded_tail
        ):
            raise ValueError("path {} degraded tail is too short for the accuracy policy".format(path_name))
        if path_name == "a" and profile["postBoundaryMode"] != "degraded":
            raise ValueError("Path A must carry the changed traffic profile")
        if path_name == "b" and profile["postBoundaryMode"] != "stable":
            raise ValueError("Path B must remain the stable control")
        earliest_decision = required_hits * monitor_period
        earliest_trigger = stable_lead + earliest_decision
        bounded_trigger = (
            stable_lead + (required_hits + 1) * monitor_period + sampling
        )
        if profile["breakingTimeSeconds"] + bounded_trigger > preparation_window:
            raise ValueError(
                "path {} preparation window cannot cover warm-start through bounded trigger".format(path_name)
            )
        trigger_observations = historical_observations + earliest_trigger // sampling
        trigger_training, trigger_validation = _sample_counts(
            trigger_observations, sequence_length, output_length, validation_ratio
        )
        if historical_observations < sequence_length:
            raise ValueError("path {} cannot fill the PyAnLF input window".format(path_name))
        if warm_start_mode == "inference-and-training" and (
            historical_training < minimum_samples or historical_validation < minimum_validation
        ):
            raise ValueError("path {} warm-start cannot prepare training and validation evidence".format(path_name))
        if trigger_training < minimum_samples or trigger_validation < minimum_validation:
            raise ValueError("path {} earliest trigger lacks training or validation evidence".format(path_name))

        resolved = dict(profile)
        resolved.update({
            "profileSource": profile_source,
            "profileHash": canonical_hash(profile),
            "ueIps": _expected_ue_ips(
                testbed["paths"][path_name]["upf"]["uePool"],
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
