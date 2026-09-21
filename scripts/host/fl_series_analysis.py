"""Offline E0–E2b analysis from finalized run records and raw events."""

import csv
import io
import json
import math
import statistics
from datetime import datetime
from pathlib import Path

from matplotlib.figure import Figure


CONDITIONS = ("E0", "E1", "E2a", "E2b")
BOUNDARIES = {"mnist": 12, "cifar10": 20}
T95_DF4 = 2.7764451051977987


def interval(values):
    if len(values) != 5:
        return None
    center = statistics.mean(values)
    half_width = T95_DF4 * statistics.stdev(values) / math.sqrt(5)
    return {"mean": center, "lower": center - half_width, "upper": center + half_width}


def _children(event):
    topology = event.get("payload", {}).get("realizedTopology", {})
    return [child["nfInstanceId"] for child in topology.get("children", [])]


def _seconds_between(start, end):
    first = datetime.fromisoformat(start.replace("Z", "+00:00"))
    second = datetime.fromisoformat(end.replace("Z", "+00:00"))
    return (second - first).total_seconds()


def _load_run(directory):
    run = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    scenario = run["scenario"]
    if scenario.get("experiment", {}).get("series") != "e0-e2b":
        raise ValueError("run is not in the e0-e2b series")
    dataset = scenario["workload"]["dataset"]
    seed = scenario["partition"]["seed"]
    condition = scenario["experiment"]["condition"]
    if dataset not in BOUNDARIES or condition not in CONDITIONS or seed not in range(1, 6):
        raise ValueError("run has an unexpected formal experiment identity")
    identity = (dataset, seed, condition)
    if run.get("status") != "successful" or run.get("finalized") is not True:
        return identity, {"directory": str(directory), "status": run.get("status"), "reason": "not finalized"}
    rounds = run.get("phases", {}).get("rounds", [])
    if (len(rounds) != scenario["training"]["acceptedRounds"]
            or len(rounds) != (24 if dataset == "mnist" else 40)
            or len({item["roundInd"] for item in rounds}) != len(rounds)):
        return identity, {"directory": str(directory), "status": "incomplete", "reason": "accepted rounds"}
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines() if line]
    for source_file in sorted((directory / "observations").glob("*.jsonl")):
        if source_file.stem == "pymtlf-root":
            continue  # Root records are already in events.jsonl.
        for line in source_file.read_text(encoding="utf-8").splitlines():
            if line:
                payload = json.loads(line)
                events.append({"recordedAt": payload["recordedAt"], "source": source_file.stem,
                               "eventType": payload["recordType"], "payload": payload})
    evaluations = {}
    for event in events:
        if event.get("source") != "pymtlf-root" or event.get("eventType") != "MODEL_EVALUATION":
            continue
        payload = event.get("payload", {})
        if payload.get("evaluationStage") == "ROOT_GLOBAL":
            index = payload["roundInd"]
            if index in evaluations:
                raise ValueError("duplicate Root evaluation")
            accuracy, loss = payload["accuracy"], payload["loss"]
            if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (accuracy, loss)):
                raise ValueError("non-finite Root evaluation")
            evaluations[index] = (float(accuracy), float(loss))
    if set(evaluations) != {item["roundInd"] for item in rounds}:
        return identity, {"directory": str(directory), "status": "incomplete", "reason": "Root evaluations"}
    held_out = run.get("heldOutEvaluation", {}).get("accuracy")
    if not isinstance(held_out, (int, float)) or not math.isfinite(held_out):
        return identity, {"directory": str(directory), "status": "incomplete", "reason": "held-out evaluation"}
    return identity, {"directory": str(directory), "status": "complete", "run": run,
                      "rounds": rounds, "evaluations": evaluations, "events": events}


def _recovery(item, baseline_ci):
    if item["run"]["scenario"]["experiment"]["condition"] == "E0":
        return {"status": "not applicable"}
    stops = item["run"].get("faultStops", [])
    accepted_topology = [event for event in item["events"]
                         if event.get("source") == "pymtlf-root"
                         and event.get("eventType") == "TOPOLOGY_ACCEPTANCE"
                         and event.get("payload", {}).get("accepted") is True]
    if not stops or not accepted_topology:
        return {"status": "undetermined", "reason": "fault or topology event missing"}
    stopped_at = min(stop["effectiveAt"] for stop in stops)
    if accepted_topology[0]["recordedAt"] >= stopped_at:
        return {"status": "undetermined", "reason": "initial accepted topology missing"}
    initial = set(_children(accepted_topology[0]))
    repaired = set()
    for event in accepted_topology[1:]:
        if event["recordedAt"] >= stopped_at:
            repaired.update(set(_children(event)) - initial)
    if not repaired:
        return {"status": "not recovered", "reason": "no new accepted Root child"}
    first = next((position for position, round_item in enumerate(item["rounds"], 1)
                  if round_item["recordedAt"] >= stopped_at
                  and repaired.intersection(round_item["successfulNfInstanceIds"])), None)
    if first is None:
        return {"status": "not recovered", "reason": "repair did not contribute"}
    result = {
        "firstRepairedRound": first,
        "failureToFirstContributionSeconds": _seconds_between(stopped_at, item["rounds"][first - 1]["recordedAt"]),
        "repairedNfInstanceIds": sorted(repaired),
    }
    if not baseline_ci:
        return {**result, "status": "undetermined", "reason": "five-seed E0 CI unavailable"}
    metrics = item["evaluations"]
    rounds = item["rounds"]
    for position in range(first, len(rounds)):
        current = baseline_ci.get(position)
        following = baseline_ci.get(position + 1)
        if current is None or following is None:
            return {**result, "status": "undetermined", "reason": "corresponding E0 CI missing"}
        first_accuracy = metrics[rounds[position - 1]["roundInd"]][0]
        next_accuracy = metrics[rounds[position]["roundInd"]][0]
        if (current["lower"] <= first_accuracy <= current["upper"] and
                following["lower"] <= next_accuracy <= following["upper"]):
            return {**result, "status": "recovered", "recoveryRound": position,
                    "confirmationRound": position + 1,
                    "roundsToRecovery": position - item["run"]["scenario"]["fault"]["normalAcceptedRounds"],
                    "failureToRecoverySeconds": _seconds_between(stopped_at, rounds[position - 1]["recordedAt"])}
    return {**result, "status": "not recovered"}


def _run_details(item, baseline_ci):
    run = item["run"]
    events = item["events"]
    nodes = run.get("nodeIdentities", {})
    leaves = run.get("leafSplitSummary", {})
    branch_aggregations = [event for event in events
                           if event.get("source") != "pymtlf-root"
                           and event.get("eventType") == "ROUND_AGGREGATION"
                           and event.get("payload", {}).get("accepted") is True]
    coverage = []
    previous_at = None
    for position, round_item in enumerate(item["rounds"], 1):
        observed_leaf_ids = set()
        unobserved_branches = []
        for identifier in round_item["successfulNfInstanceIds"]:
            role = nodes.get(identifier, {}).get("role")
            if role == "leaf":
                observed_leaf_ids.add(identifier)
            elif role == "branch":
                matching = [event for event in branch_aggregations
                            if event["payload"].get("nfInstanceId") == identifier
                            and (previous_at is None or event["recordedAt"] > previous_at)
                            and event["recordedAt"] <= round_item["recordedAt"]]
                if matching:
                    observed_leaf_ids.update(matching[-1]["payload"].get("successfulNfInstanceIds", []))
                else:
                    unobserved_branches.append(identifier)
        observed_leaves = sorted(nodes[identifier]["unit"] for identifier in observed_leaf_ids
                                 if identifier in nodes and nodes[identifier]["role"] == "leaf")
        histogram = {}
        for leaf in observed_leaves:
            for label, count in leaves.get(leaf, {}).get("classHistogram", {}).items():
                histogram[int(label)] = histogram.get(int(label), 0) + count
        coverage.append({"acceptedRound": position, "observedLeaves": observed_leaves,
                         "unobservedBranchNfInstanceIds": unobserved_branches,
                         "observedLeafSampleCount": sum(histogram.values()),
                         "observedLeafClassHistogram": histogram})
        previous_at = round_item["recordedAt"]
    operations = {}
    notifications_sent = {}
    timeline = []
    relevant = {"NODE_PROCESS_STOPPED", "REPAIR_SELECTION", "EDGE_CONFIRMED",
                "EDGE_UNAVAILABLE", "TOPOLOGY_ACCEPTANCE", "ROUND_AGGREGATION",
                "MODEL_TRAINING_OPERATION"}
    for event in events:
        event_type = event.get("eventType")
        payload = event.get("payload", {})
        if event_type == "MODEL_TRAINING_OPERATION" and payload.get("direction") == "SENT":
            key = payload.get("operation", "unknown")
            source = event["source"]
            if key in ("CREATE", "PUT", "PATCH", "DELETE"):
                operations.setdefault(source, {})[key] = operations.get(source, {}).get(key, 0) + 1
            elif key == "NOTIFY":
                notifications_sent[source] = notifications_sent.get(source, 0) + 1
        if event_type in relevant:
            entry = {"at": event.get("recordedAt"), "source": event.get("source"), "type": event_type}
            for field in ("roundInd", "childNfInstanceId", "selectedNfInstanceIds",
                          "successfulNfInstanceIds", "failedNfInstanceIds", "unit",
                          "operation", "direction", "outcome", "targetNfInstanceId",
                          "subscriptionId", "startedAt"):
                if field in payload:
                    entry[field] = payload[field]
            if event_type == "TOPOLOGY_ACCEPTANCE":
                entry["directChildren"] = _children(event)
            timeline.append(entry)
    timeline.sort(key=lambda entry: entry["at"])
    return {"recovery": _recovery(item, baseline_ci),
            "subscriptionResourceOperationsSent": operations,
            "notificationsSent": notifications_sent,
            "timeline": timeline, "observedLeafCoverage": coverage,
            "faultStops": run.get("faultStops", [])}


def _csv_text(rows):
    if not rows:
        return ""
    output = io.StringIO()
    fields = list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _add_interval(row, name, values):
    estimate = interval(values)
    row[name + "Mean"] = estimate["mean"] if estimate else ""
    row[name + "CiLower"] = estimate["lower"] if estimate else ""
    row[name + "CiUpper"] = estimate["upper"] if estimate else ""


def _paired_inputs_match(left, right):
    first = left["run"]["scenario"]
    second = right["run"]["scenario"]
    return (
        first["partition"] == second["partition"]
        and first["training"] == second["training"]
        and first["workload"]["seedArtifactKey"] == second["workload"]["seedArtifactKey"]
    )


def analyze(series_root, output):
    discovered = {}
    issues = []
    for path in sorted(series_root.rglob("run.json")):
        try:
            identity, item = _load_run(path.parent)
            discovered.setdefault(identity, []).append(item)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            issues.append({"directory": str(path.parent), "reason": str(error)})
    selected = {}
    for dataset in BOUNDARIES:
        for seed in range(1, 6):
            for condition in CONDITIONS:
                identity = (dataset, seed, condition)
                candidates = discovered.get(identity, [])
                completed = [candidate for candidate in candidates if candidate["status"] == "complete"]
                for candidate in candidates:
                    if candidate["status"] != "complete":
                        issues.append({"workload": dataset, "seed": seed, "condition": condition,
                                       "reason": candidate["reason"], "directory": candidate["directory"]})
                if len(completed) == 1:
                    selected[identity] = completed[0]
                elif len(completed) > 1 or not candidates:
                    issues.append({"workload": dataset, "seed": seed, "condition": condition,
                                   "reason": "multiple successful attempts" if completed else "missing",
                                   "runs": [candidate["directory"] for candidate in candidates]})
    curves = {}
    round_rows = []
    summary_rows = []
    details = {}
    for dataset, boundary in BOUNDARIES.items():
        expected_rounds = 24 if dataset == "mnist" else 40
        baseline_ci = {}
        for position in range(1, expected_rounds + 1):
            values = [selected[(dataset, seed, "E0")]["evaluations"][
                selected[(dataset, seed, "E0")]["rounds"][position - 1]["roundInd"]][0]
                for seed in range(1, 6) if (dataset, seed, "E0") in selected]
            baseline_ci[position] = interval(values)
        for condition in CONDITIONS:
            group = [(seed, selected[(dataset, seed, condition)]) for seed in range(1, 6)
                     if (dataset, seed, condition) in selected]
            endpoints = []
            endpoint_losses = []
            held_out_accuracies = []
            auc_values = []
            paired_endpoints = []
            paired_aucs = []
            recoveries = []
            condition_curves = []
            for seed, item in group:
                rounds = item["rounds"]
                values = [item["evaluations"][entry["roundInd"]] for entry in rounds]
                endpoints.append(values[-1][0])
                endpoint_losses.append(values[-1][1])
                held_out_accuracies.append(item["run"].get("heldOutEvaluation", {}).get("accuracy"))
                auc = sum(value[0] for value in values[boundary:boundary + 12]) if len(values) >= boundary + 12 else None
                if auc is not None:
                    auc_values.append(auc)
                condition_curves.append(values)
                baseline = selected.get((dataset, seed, "E0"))
                if baseline and _paired_inputs_match(baseline, item):
                    baseline_values = [baseline["evaluations"][entry["roundInd"]] for entry in baseline["rounds"]]
                    paired_endpoints.append(values[-1][0] - baseline_values[-1][0])
                    if auc is not None:
                        paired_aucs.append(auc - sum(value[0] for value in baseline_values[boundary:boundary + 12]))
                elif baseline:
                    issues.append({"workload": dataset, "seed": seed, "condition": condition,
                                   "reason": "paired input differs from E0"})
                for position, (round_item, (accuracy, loss)) in enumerate(zip(rounds, values), 1):
                    round_rows.append({"rowType": "seed", "workload": dataset, "condition": condition, "seed": seed,
                                       "acceptedRound": position, "roundInd": round_item["roundInd"],
                                       "validationAccuracy": accuracy, "validationLoss": loss,
                                       "successfulDirectParticipants": ";".join(round_item["successfulNfInstanceIds"])})
                run_details = _run_details(item, baseline_ci)
                recoveries.append(run_details["recovery"]["status"])
                details["{}/seed-{}/{}/{}".format(dataset, seed, condition, item["run"]["runName"])] = {
                    "workload": dataset, "seed": seed, "condition": condition,
                    "directory": item["directory"], "endpointAccuracy": values[-1][0],
                    "postFaultAuc12": auc, **run_details,
                }
            summary = {"workload": dataset, "condition": condition, "completeSeeds": len(group)}
            summary.update({
                "recoveredRuns": recoveries.count("recovered"),
                "notRecoveredRuns": recoveries.count("not recovered"),
                "undeterminedRecoveryRuns": recoveries.count("undetermined"),
            })
            for name, values in (
                ("endpointValidationAccuracy", endpoints),
                ("endpointValidationLoss", endpoint_losses),
                ("heldOutAccuracy", held_out_accuracies),
                ("postFaultAuc12", auc_values),
                ("pairedEndpointDeltaVsE0", paired_endpoints),
                ("pairedAucDeltaVsE0", paired_aucs),
            ):
                _add_interval(summary, name, values)
            summary_rows.append(summary)
            if len(condition_curves) == 5:
                curves[(dataset, condition)] = {
                    "accuracy": [interval([curve[position][0] for curve in condition_curves])
                                 for position in range(expected_rounds)],
                    "loss": [interval([curve[position][1] for curve in condition_curves])
                             for position in range(expected_rounds)],
                }
                for position in range(expected_rounds):
                    accuracy = curves[(dataset, condition)]["accuracy"][position]
                    loss = curves[(dataset, condition)]["loss"][position]
                    round_rows.append({"rowType": "five-seed CI", "workload": dataset,
                                       "condition": condition, "seed": "", "acceptedRound": position + 1,
                                       "validationAccuracy": accuracy["mean"],
                                       "accuracyCiLower": accuracy["lower"], "accuracyCiUpper": accuracy["upper"],
                                       "validationLoss": loss["mean"],
                                       "lossCiLower": loss["lower"], "lossCiUpper": loss["upper"]})
    figure = Figure(figsize=(10, 7))
    colors = {"E0": "#222222", "E1": "#0072B2", "E2a": "#D55E00", "E2b": "#009E73"}
    for row, dataset in enumerate(BOUNDARIES):
        for column, (metric, label) in enumerate((("accuracy", "Validation accuracy"), ("loss", "Validation loss"))):
            axis = figure.add_subplot(2, 2, row * 2 + column + 1)
            for condition in CONDITIONS:
                curve = curves.get((dataset, condition))
                if curve is None:
                    continue
                values = curve[metric]
                positions = list(range(1, len(values) + 1))
                axis.plot(positions, [point["mean"] for point in values], label=condition, color=colors[condition])
                axis.fill_between(positions, [point["lower"] for point in values],
                                  [point["upper"] for point in values], alpha=0.15, color=colors[condition])
            axis.axvline(BOUNDARIES[dataset] + 0.5, color="#999999", linestyle="--", linewidth=0.8)
            axis.set_title("{} — {}".format(dataset.upper(), label))
            axis.set_xlabel("Accepted round")
            if any((dataset, condition) in curves for condition in CONDITIONS):
                axis.legend(loc="best", fontsize=8)
    figure.tight_layout()
    image = io.StringIO()
    figure.savefig(image, format="svg")
    output.mkdir(parents=True, exist_ok=True)
    for name in ("rounds.csv", "summary.csv", "details.json", "curves.svg"):
        if (output / name).exists():
            raise ValueError("analysis output already exists: " + str(output / name))
    (output / "rounds.csv").write_text(_csv_text(round_rows), encoding="utf-8")
    (output / "summary.csv").write_text(_csv_text(summary_rows), encoding="utf-8")
    (output / "details.json").write_text(json.dumps({
        "definitions": {
            "accuracyUnit": "fraction",
            "confidenceInterval": "two-sided 95% Student-t, five paired seeds, df=4",
            "postFaultAuc": "sum of 12 accepted-round validation accuracies after the nominal boundary",
            "nominalFaultAfterRound": BOUNDARIES,
            "pairedEffect": "condition minus E0 at the same workload and seed",
            "recoveryRound": "first of two consecutive accepted rounds inside the corresponding E0 CI",
            "elapsedTimes": "UTC event timestamp differences; interpret with recorded clock uncertainty",
        },
        "issues": issues, "runs": details,
    }, indent=2) + "\n", encoding="utf-8")
    (output / "curves.svg").write_text(image.getvalue(), encoding="utf-8")
    return len(selected), len(issues)
