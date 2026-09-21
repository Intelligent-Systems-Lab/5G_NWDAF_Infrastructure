#!/usr/bin/env python3
"""Compare the saved validation curves of two completed FL runs offline."""

import argparse
import csv
import io
import json
import math
from pathlib import Path

from matplotlib.backends.backend_svg import FigureCanvasSVG
from matplotlib.figure import Figure


class AnalysisError(ValueError):
    pass


def load_run(directory):
    with (directory / "run.json").open(encoding="utf-8") as stream:
        run = json.load(stream)
    if run.get("status") != "successful" or run.get("finalized") is not True:
        raise AnalysisError("{} is not a completed successful run".format(directory))
    phases = run.get("phases") or {}
    rounds = phases.get("rounds") or []
    if not rounds or len(rounds) != phases.get("acceptedRoundCount"):
        raise AnalysisError("{} has incomplete accepted rounds".format(directory))

    evaluations = {}
    with (directory / "events.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            if event.get("source") != "pymtlf-root" or event.get("eventType") != "MODEL_EVALUATION":
                continue
            payload = event["payload"]
            stage = payload.get("evaluationStage")
            if stage == "ROOT_INITIAL":
                key = None
            elif stage == "ROOT_GLOBAL":
                key = payload.get("roundInd")
            else:
                continue
            if key in evaluations:
                raise AnalysisError(
                    "{} has duplicate Root validation for {}".format(directory, key)
                )
            accuracy = payload.get("validationAccuracy")
            loss = payload.get("validationLoss")
            if not all(
                isinstance(value, (int, float)) and math.isfinite(value)
                for value in (accuracy, loss)
            ):
                raise AnalysisError("{} has missing Root validation metrics".format(directory))
            evaluations[key] = (accuracy * 100, loss, payload.get("sampleCount"))

    indices = [item.get("roundInd") for item in rounds]
    if len(set(indices)) != len(indices) or set(evaluations) != {None, *indices}:
        raise AnalysisError(
            "{} has unaligned accepted rounds and Root validation".format(directory)
        )
    held_out = run.get("heldOutEvaluation") or {}
    for field in ("accuracy", "correct_count", "test_sample_count"):
        if field not in held_out:
            raise AnalysisError("{} has no final test {}".format(directory, field))
    return run, [evaluations[None]] + [evaluations[index] for index in indices]


def build_rows(baseline, treatment):
    base_run, base_metrics = baseline
    treat_run, treat_metrics = treatment
    if base_run.get("dataset") != treat_run.get("dataset"):
        raise AnalysisError("baseline and treatment datasets differ")
    if len(base_metrics) != len(treat_metrics):
        raise AnalysisError("baseline and treatment accepted round counts differ")
    base_rounds = base_run["phases"]["rounds"]
    treat_rounds = treat_run["phases"]["rounds"]
    rows = []
    for index, (base, treated) in enumerate(zip(base_metrics, treat_metrics)):
        base_round = base_rounds[index - 1] if index else None
        treat_round = treat_rounds[index - 1] if index else None
        rows.append({
            "accepted_round": index,
            "baseline_validation_accuracy_pct": base[0],
            "treatment_validation_accuracy_pct": treated[0],
            "accuracy_delta_pp": treated[0] - base[0],
            "baseline_validation_loss": base[1],
            "treatment_validation_loss": treated[1],
            "baseline_validation_samples": base[2],
            "treatment_validation_samples": treated[2],
            "treatment_phase": treat_round["phase"] if treat_round else "initial",
            "baseline_successful_branches": (
                ";".join(base_round["successfulNfInstanceIds"]) if base_round else ""
            ),
            "treatment_successful_branches": (
                ";".join(treat_round["successfulNfInstanceIds"]) if treat_round else ""
            ),
        })
    return rows


def summary_row(baseline, treatment):
    base_run, _ = baseline
    treat_run, _ = treatment
    base_test = base_run["heldOutEvaluation"]
    treat_test = treat_run["heldOutEvaluation"]
    counts = treat_run["phases"]["phaseCounts"]
    latencies = treat_run["phases"]["latencies"]
    return {
        "baseline_run": base_run["runName"],
        "treatment_run": treat_run["runName"],
        "dataset": base_run["dataset"],
        "baseline_final_test_accuracy_pct": base_test["accuracy"] * 100,
        "baseline_final_test_correct": base_test["correct_count"],
        "baseline_final_test_samples": base_test["test_sample_count"],
        "treatment_final_test_accuracy_pct": treat_test["accuracy"] * 100,
        "treatment_final_test_correct": treat_test["correct_count"],
        "treatment_final_test_samples": treat_test["test_sample_count"],
        "treatment_normal_rounds": counts["normal"],
        "treatment_degraded_rounds": counts["degraded"],
        "treatment_restored_rounds": counts["restored"],
        "fault_to_detection_seconds": latencies.get("failureDetectedSeconds", ""),
        "fault_to_ready_seconds": latencies.get("replacementReadySeconds", ""),
        "fault_to_first_contribution_seconds": latencies.get("firstContributionSeconds", ""),
    }


def csv_text(rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    for row in rows:
        formatted = dict(row)
        for key, value in row.items():
            if isinstance(value, float):
                digits = 2 if key.endswith(("accuracy_pct", "delta_pp")) else 6
                formatted[key] = "{:.{}f}".format(value, digits)
        writer.writerow(formatted)
    return stream.getvalue()


def svg_text(rows):
    rounds = [row["accepted_round"] for row in rows]
    degraded = [row["accepted_round"] for row in rows if row["treatment_phase"] == "degraded"]
    restored = next(
        (row["accepted_round"] for row in rows if row["treatment_phase"] == "restored"),
        None,
    )
    figure = Figure(figsize=(7.2, 5.1))
    FigureCanvasSVG(figure)
    axes = figure.subplots(2, 1, sharex=True)
    figure.patch.set_facecolor("white")
    for axis, title, label, keys in (
        (
            axes[0],
            "(a) Validation accuracy",
            "Accuracy (%)",
            ("baseline_validation_accuracy_pct", "treatment_validation_accuracy_pct"),
        ),
        (
            axes[1],
            "(b) Validation loss",
            "Loss",
            ("baseline_validation_loss", "treatment_validation_loss"),
        ),
    ):
        if degraded:
            axis.axvline(degraded[0] - 0.5, color="0.4", linewidth=0.9,
                         linestyle="-.", label="Primary branch stopped")
        if restored is not None:
            axis.axvline(restored, color="0.4", linewidth=0.9,
                         linestyle=":", label="Replacement first contributes")
        for key, color, style, marker, name in zip(
            keys,
            ("#1f4e79", "#b34d2e"),
            ("-", "--"),
            ("o", "s"),
            ("No-failure baseline", "Branch failure + replacement"),
        ):
            axis.plot(rounds, [row[key] for row in rows], color=color, linewidth=1.4,
                      linestyle=style, marker=marker, markevery=max(1, len(rounds) // 10),
                      markersize=3, label=name)
        axis.set_title(title, loc="left", fontsize=8.5, fontweight="bold", pad=3)
        axis.set_ylabel(label, fontsize=8)
        axis.tick_params(direction="out", labelsize=7.5, length=3, width=0.8)
        axis.spines[["top", "right"]].set_visible(False)
        axis.set_xlim(0, rounds[-1])
    max_accuracy = max(
        row[key]
        for row in rows
        for key in ("baseline_validation_accuracy_pct", "treatment_validation_accuracy_pct")
    )
    axes[0].set_ylim(0, min(100, max_accuracy + 5))
    axes[1].set_ylim(bottom=0)
    axes[1].set_xlabel("Accepted round (0 = initial)", fontsize=8)
    handles, labels = axes[0].get_legend_handles_labels()
    legend = dict(zip(labels, handles))
    labels = [
        name
        for name in (
            "No-failure baseline",
            "Branch failure + replacement",
            "Primary branch stopped",
            "Replacement first contributes",
        )
        if name in legend
    ]
    figure.legend([legend[name] for name in labels], labels, loc="upper center", ncol=2,
                  bbox_to_anchor=(0.5, 0.985), frameon=False, fontsize=7.5,
                  handlelength=2.5, columnspacing=1.5)
    figure.subplots_adjust(left=0.12, right=0.98, top=0.82, bottom=0.11, hspace=0.26)
    stream = io.StringIO()
    figure.savefig(stream, format="svg")
    return stream.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-run")
    parser.add_argument("--treatment-run")
    parser.add_argument("--series-root")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    if args.series_root:
        if args.baseline_run or args.treatment_run:
            parser.error("series analysis does not use baseline/treatment run options")
        output = Path(args.output_dir).resolve()
        if any((directory / "run.json").exists() for directory in (output, *output.parents)):
            parser.error("analysis output must remain separate from a raw run directory")
        from fl_series_analysis import analyze
        try:
            count, issues = analyze(Path(args.series_root).resolve(), output)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
        print("Analyzed {} completed formal runs; {} missing or incomplete slots".format(count, issues))
        return
    if not all((args.baseline_run, args.treatment_run, args.output_dir)):
        parser.error("all directories must be specified")
    baseline = Path(args.baseline_run).resolve()
    treatment = Path(args.treatment_run).resolve()
    output = Path(args.output_dir).resolve()
    if (baseline == treatment or output == baseline or output == treatment
            or baseline in output.parents or treatment in output.parents
            or output in baseline.parents or output in treatment.parents):
        parser.error("run and output directories must be separate")
    try:
        base_data = load_run(baseline)
        treat_data = load_run(treatment)
        rows = build_rows(base_data, treat_data)
        summary = summary_row(base_data, treat_data)
        content = {
            "rounds.csv": csv_text(rows),
            "summary.csv": csv_text([summary]),
            "comparison.svg": svg_text(rows),
        }
        if any((output / name).exists() for name in content):
            raise AnalysisError("output already contains analysis files: {}".format(output))
        output.mkdir(parents=True, exist_ok=True)
        for name, value in content.items():
            (output / name).write_text(value, encoding="utf-8")
    except (AnalysisError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print("Wrote rounds.csv, summary.csv, comparison.svg to {}".format(output))


if __name__ == "__main__":
    main()
