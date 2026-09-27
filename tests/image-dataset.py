#!/usr/bin/env python3
"""Verify deterministic image partitions through the PyMTLF native loader."""

from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np
import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "host"))

import image_dataset as dataset_module  # noqa: E402
from configlib import image_scenario_contract, nwdaf_definitions  # noqa: E402
from image_dataset import DatasetError, build_split, validate_output  # noqa: E402


def source(shape: tuple[int, ...], per_class: int) -> tuple[np.ndarray, np.ndarray]:
    labels = np.repeat(np.arange(10, dtype=np.uint8), per_class)
    images = np.arange(labels.size * int(np.prod(shape)), dtype=np.uint8).reshape(
        labels.size, *shape
    )
    return images, labels


def main() -> int:
    testbed = yaml.safe_load(
        (ROOT / "testbed.protocol-hierarchical.yaml").read_text(encoding="utf-8")
    )
    leaf_units = tuple(
        definition["unit"] for definition in nwdaf_definitions(testbed)
        if definition["role"] == "leaf"
    )
    scenario = {
        "workload": {"dataset": "mnist"},
        "partition": {
            "seed": 42,
            "samplesPerLeaf": 20,
            "validationSamples": 20,
            "heldOutSamples": 20,
        },
    }
    train_images, train_labels = source((1, 28, 28), 20)
    test_images, test_labels = source((1, 28, 28), 10)
    with tempfile.TemporaryDirectory(prefix="image-dataset-test-") as temporary:
        first = Path(temporary) / "first"
        second = Path(temporary) / "second"
        first.mkdir()
        second.mkdir()
        first_manifest = build_split(
            scenario, train_images, train_labels, test_images, test_labels,
            first, leaf_units,
        )
        second_manifest = build_split(
            scenario, train_images, train_labels, test_images, test_labels,
            second, leaf_units,
        )
        validate_output(first, scenario, leaf_units)
        validate_output(second, scenario, leaf_units)
        if first_manifest != second_manifest:
            raise AssertionError("the same source and seed did not reproduce the split")

        shared = copy.deepcopy(scenario)
        shared["partition"]["datasetId"] = "shared-split"
        shared_root = Path(temporary) / "shared-split"
        with mock.patch.object(
            dataset_module, "load_source",
            return_value=(train_images, train_labels, test_images, test_labels),
        ) as load_source:
            dataset_module.generate(shared_root, shared, leaf_units)
            dataset_module.generate(shared_root, shared, leaf_units)
            if load_source.call_count != 1:
                raise AssertionError("an existing shared dataset was regenerated")
        changed = copy.deepcopy(shared)
        changed["partition"]["seed"] += 1
        try:
            dataset_module.generate(shared_root, changed, leaf_units)
        except DatasetError:
            pass
        else:
            raise AssertionError("a mismatched shared dataset was reused")

        train_indices = []
        for leaf in leaf_units:
            item = first_manifest["artifacts"][leaf]
            train_indices.extend(item["sourceIndices"])
            if item["classHistogram"] != {label: 2 for label in range(10)}:
                raise AssertionError("leaf shard is not balanced")
        if len(train_indices) != len(set(train_indices)):
            raise AssertionError("leaf source indices overlap")

        invalid = copy.deepcopy(first_manifest)
        invalid["artifacts"]["validation"]["sourceIndices"] = invalid["artifacts"][
            "held-out"
        ]["sourceIndices"]
        (first / "split-manifest.yaml").write_text(
            yaml.safe_dump(invalid, sort_keys=False), encoding="utf-8"
        )
        try:
            validate_output(first, scenario, leaf_units)
        except DatasetError as exc:
            if "overlap" not in str(exc):
                raise
        else:
            raise AssertionError("overlapping validation and held-out indices were accepted")

        source_paths = {
            name: Path(temporary) / name
            for name in (
                "train-images-idx3-ubyte.gz",
                "train-labels-idx1-ubyte.gz",
                "t10k-images-idx3-ubyte.gz",
                "t10k-labels-idx1-ubyte.gz",
            )
        }
        for path in source_paths.values():
            path.write_bytes(b"invalid")
        invalid_labels = train_labels.copy()
        invalid_labels[0] = 10
        with mock.patch.object(dataset_module, "ensure_sources", return_value=source_paths), \
             mock.patch.object(
                 dataset_module,
                 "load_mnist",
                 return_value=(train_images, invalid_labels, test_images, test_labels),
             ):
            try:
                dataset_module.load_source("mnist")
            except DatasetError:
                pass
            else:
                raise AssertionError("invalid source labels were accepted")
        if any(path.exists() for path in source_paths.values()):
            raise AssertionError("semantically invalid cached sources were retained")

        skewed = copy.deepcopy(scenario)
        skewed_leaves = tuple(
            "sample-leaf-{}".format(index) for index in range(len(leaf_units))
        )
        skewed["partition"].update(
            validationSource="official-train",
            heldOutSamples=len(test_labels),
            leafLabels={
                leaf: list(range(5)) if offset < 3 else list(range(5, 10))
                for offset, leaf in enumerate(skewed_leaves)
            },
        )
        selected = yaml.safe_load(
            (ROOT / "experiments/protocol-hierarchical/mnist/smoke.yaml").read_text(
                encoding="utf-8"
            )
        )
        selected["partition"].update(skewed["partition"])
        image_scenario_contract(selected)
        uneven_test_labels = test_labels.copy()
        uneven_test_labels[0] = 1
        skewed_first = Path(temporary) / "skewed-first"
        skewed_second = Path(temporary) / "skewed-second"
        skewed_first.mkdir()
        skewed_second.mkdir()
        skewed_manifest = build_split(
            skewed, train_images, train_labels, test_images, uneven_test_labels,
            skewed_first, leaf_units,
        )
        reordered = copy.deepcopy(skewed)
        reordered["partition"]["leafLabels"] = dict(
            reversed(list(reordered["partition"]["leafLabels"].items()))
        )
        repeated_manifest = build_split(
            reordered, train_images, train_labels, test_images, uneven_test_labels,
            skewed_second, leaf_units,
        )
        validate_output(skewed_first, skewed, leaf_units)
        if skewed_manifest != repeated_manifest:
            raise AssertionError("label-skew split changed with mapping order")
        if skewed_manifest["artifacts"]["validation"]["sourceSplit"] != "official-train":
            raise AssertionError("validation was not selected from official train")
        for leaf in skewed_leaves:
            labels = skewed["partition"]["leafLabels"][leaf]
            expected = {label: 4 if label in labels else 0 for label in range(10)}
            if skewed_manifest["artifacts"][leaf]["classHistogram"] != expected:
                raise AssertionError("Leaf labels do not match the selected partition")
        train_sources = [
            item["sourceIndices"]
            for name, item in skewed_manifest["artifacts"].items()
            if name != "held-out"
        ]
        if len(set().union(*map(set, train_sources))) != sum(map(len, train_sources)):
            raise AssertionError("training and validation source indices overlap")

        quota_leaves = ("sample-leaf-0", "sample-leaf-1")
        quota_scenario = copy.deepcopy(selected)
        quota_scenario["partition"].pop("leafLabels")
        quota_scenario["partition"]["leafClassCounts"] = {
            quota_leaves[0]: {
                label: 3 if label < 5 else 1 for label in range(10)
            },
            quota_leaves[1]: {
                label: 1 if label < 5 else 3 for label in range(10)
            },
        }
        image_scenario_contract(quota_scenario)
        quota_first = Path(temporary) / "quota-first"
        quota_second = Path(temporary) / "quota-second"
        quota_first.mkdir()
        quota_second.mkdir()
        quota_manifest = build_split(
            quota_scenario, train_images, train_labels, test_images, uneven_test_labels,
            quota_first, leaf_units,
        )
        reversed_quotas = copy.deepcopy(quota_scenario)
        reversed_quotas["partition"]["leafClassCounts"] = dict(
            reversed(list(reversed_quotas["partition"]["leafClassCounts"].items()))
        )
        repeated_quota_manifest = build_split(
            reversed_quotas, train_images, train_labels, test_images, uneven_test_labels,
            quota_second, leaf_units,
        )
        validate_output(quota_first, quota_scenario, leaf_units)
        if quota_manifest != repeated_quota_manifest:
            raise AssertionError("class-quota split changed with mapping order")
        for leaf, expected in quota_scenario["partition"]["leafClassCounts"].items():
            if quota_manifest["artifacts"][leaf]["classHistogram"] != expected:
                raise AssertionError("Leaf class quotas do not match the selected partition")

    print("PASS deterministic image partitions and native validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
