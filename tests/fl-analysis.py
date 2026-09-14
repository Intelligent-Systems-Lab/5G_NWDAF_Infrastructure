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
    print("PASS offline FL analysis")


if __name__ == "__main__":
    main()
