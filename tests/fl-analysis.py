#!/usr/bin/env python3
"""Check offline comparison alignment and its operator-facing files."""

import csv
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/host/fl-analysis.py"


def write_run(directory, *, phases, accuracies):
    directory.mkdir()
    rounds = [
        {"roundInd": index, "phase": phase, "successfulNfInstanceIds": ["branch-{}".format(phase)]}
        for index, phase in enumerate(phases)
    ]
    run = {
        "runName": directory.name,
        "dataset": "example",
        "status": "successful",
        "finalized": True,
        "phases": {
            "acceptedRoundCount": len(rounds),
            "rounds": rounds,
            "phaseCounts": {
                phase: phases.count(phase) for phase in ("normal", "degraded", "restored")
            },
            "latencies": {
                "failureDetectedSeconds": 3,
                "replacementReadySeconds": 4,
                "firstContributionSeconds": 5,
            },
        },
        "heldOutEvaluation": {
            "accuracy": accuracies[-1], "correct_count": 7, "test_sample_count": 10
        },
    }
    (directory / "run.json").write_text(json.dumps(run), encoding="utf-8")
    events = []
    for index, accuracy in enumerate(accuracies):
        payload = {
            "evaluationStage": "ROOT_INITIAL" if index == 0 else "ROOT_GLOBAL",
            "validationAccuracy": accuracy,
            "validationLoss": 1 - accuracy,
            "sampleCount": 10,
        }
        if index:
            payload["roundInd"] = index - 1
        events.append({
            "source": "pymtlf-root",
            "eventType": "MODEL_EVALUATION",
            "payload": payload,
        })
    (directory / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )


def write_formal_run(directory, *, seed, condition):
    directory.mkdir(parents=True)
    rounds = [{"roundInd": index, "recordedAt": "2026-09-21T00:{:02d}:00Z".format(index),
               "successfulNfInstanceIds": ["branch"]} for index in range(24)]
    run = {
        "runName": directory.name, "dataset": "mnist", "status": "successful", "finalized": True,
        "scenario": {"experiment": {"series": "e0-e2b", "condition": condition},
                     "workload": {"dataset": "mnist", "seedArtifactKey": "model-{}".format(seed)},
                     "partition": {"seed": seed},
                     "training": {"acceptedRounds": 24}},
        "phases": {"rounds": rounds}, "heldOutEvaluation": {"accuracy": 0.6 + seed * 0.01},
    }
    (directory / "run.json").write_text(json.dumps(run), encoding="utf-8")
    events = [{"source": "pymtlf-root", "eventType": "MODEL_EVALUATION",
               "payload": {"evaluationStage": "ROOT_GLOBAL", "roundInd": index,
                           "accuracy": 0.5 + seed * 0.01 + index * 0.005 - (0.02 if condition == "E1" else 0),
                           "loss": 1 - index * 0.01}}
              for index in range(24)]
    (directory / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )


def main():
    with tempfile.TemporaryDirectory() as root:
        root = Path(root)
        baseline, treatment, output = (
            root / name for name in ("baseline", "treatment", "analysis")
        )
        write_run(baseline, phases=["normal", "normal"], accuracies=[0.2, 0.4, 0.6])
        write_run(treatment, phases=["degraded", "restored"], accuracies=[0.2, 0.3, 0.5])
        subprocess.run(
            [
                sys.executable, str(SCRIPT), "--baseline-run", str(baseline),
                "--treatment-run", str(treatment), "--output-dir", str(output),
            ],
            check=True, capture_output=True, text=True,
        )
        with (output / "rounds.csv").open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        assert [row["accepted_round"] for row in rows] == ["0", "1", "2"]
        assert [row["treatment_phase"] for row in rows] == ["initial", "degraded", "restored"]
        assert float(rows[1]["accuracy_delta_pp"]) == -10
        assert rows[1]["treatment_successful_branches"] == "branch-degraded"
        with (output / "summary.csv").open(newline="", encoding="utf-8") as stream:
            summary = next(csv.DictReader(stream))
        assert summary["treatment_restored_rounds"] == "1"
        assert summary["fault_to_first_contribution_seconds"] == "5"
        ET.parse(output / "comparison.svg")
        series = root / "series"
        for seed in range(1, 6):
            for condition in ("E0", "E1"):
                write_formal_run(series / "mnist" / "seed-{}".format(seed)
                                 / condition.lower() / "run-{}-{}".format(seed, condition),
                                 seed=seed, condition=condition)
        failed = series / "mnist/seed-1/e1/failed-attempt"
        write_formal_run(failed, seed=1, condition="E1")
        failed_record = json.loads((failed / "run.json").read_text(encoding="utf-8"))
        failed_record["status"] = "failed"
        (failed / "run.json").write_text(json.dumps(failed_record), encoding="utf-8")
        selected_run = series / "mnist/seed-1/e1/run-1-E1"
        selected_record = json.loads((selected_run / "run.json").read_text(encoding="utf-8"))
        selected_record["nodeIdentities"] = {
            "branch": {"unit": "branch", "role": "branch"},
            "leaf": {"unit": "leaf", "role": "leaf"},
        }
        selected_record["leafSplitSummary"] = {"leaf": {"count": 8000, "classHistogram": {0: 8000}}}
        (selected_run / "run.json").write_text(json.dumps(selected_record), encoding="utf-8")
        observation = selected_run / "observations"
        observation.mkdir()
        (observation / "pymtlf-leaf-a1.jsonl").write_text(
            "\n".join(json.dumps({"recordedAt": "2026-09-21T00:01:00Z",
                                   "recordType": "MODEL_TRAINING_OPERATION", "operation": operation,
                                   "direction": direction, "outcome": "SUCCESS"})
                      for operation, direction in (("CREATE", "SENT"), ("CREATE", "RECEIVED"),
                                                   ("NOTIFY", "SENT"))) + "\n", encoding="utf-8"
        )
        (observation / "pymtlf-branch.jsonl").write_text(
            json.dumps({"recordedAt": "2026-09-21T00:00:30Z", "recordType": "ROUND_AGGREGATION",
                        "nfInstanceId": "branch", "roundInd": 0, "accepted": True,
                        "successfulNfInstanceIds": ["leaf"]}) + "\n", encoding="utf-8"
        )
        formal_output = root / "formal-analysis"
        subprocess.run(
            [sys.executable, str(SCRIPT), "--series-root", str(series),
             "--output-dir", str(formal_output)],
            check=True, capture_output=True, text=True,
        )
        with (formal_output / "summary.csv").open(newline="", encoding="utf-8") as stream:
            formal = next(row for row in csv.DictReader(stream)
                          if row["workload"] == "mnist" and row["condition"] == "E1")
        assert formal["completeSeeds"] == "5"
        assert abs(float(formal["pairedEndpointDeltaVsE0Mean"]) + 0.02) < 1e-10
        assert abs(float(formal["pairedAucDeltaVsE0Mean"]) + 0.24) < 1e-10
        details = json.loads((formal_output / "details.json").read_text(encoding="utf-8"))
        assert any(issue.get("directory") == str(failed) for issue in details["issues"])
        assert details["runs"]["mnist/seed-1/E1/run-1-E1"]["subscriptionResourceOperationsSent"] == {
            "pymtlf-leaf-a1": {"CREATE": 1}
        }
        assert details["runs"]["mnist/seed-1/E1/run-1-E1"]["notificationsSent"] == {
            "pymtlf-leaf-a1": 1
        }
        assert details["runs"]["mnist/seed-1/E1/run-1-E1"]["observedLeafCoverage"][1][
            "observedLeafSampleCount"
        ] == 8000
        with (formal_output / "rounds.csv").open(newline="", encoding="utf-8") as stream:
            assert any(row["rowType"] == "five-seed CI" for row in csv.DictReader(stream))
        ET.parse(formal_output / "curves.svg")
    print("PASS offline FL analysis")


if __name__ == "__main__":
    main()
