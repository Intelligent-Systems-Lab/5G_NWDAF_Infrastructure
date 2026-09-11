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
from image_dataset import DatasetError, LEAF_UNITS, build_split, validate_output  # noqa: E402


def source(shape: tuple[int, ...], per_class: int) -> tuple[np.ndarray, np.ndarray]:
    labels = np.repeat(np.arange(10, dtype=np.uint8), per_class)
    images = np.arange(labels.size * int(np.prod(shape)), dtype=np.uint8).reshape(
        labels.size, *shape
    )
    return images, labels


def main() -> int:
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
            scenario, train_images, train_labels, test_images, test_labels, first
        )
        second_manifest = build_split(
            scenario, train_images, train_labels, test_images, test_labels, second
        )
        validate_output(first, scenario)
        validate_output(second, scenario)
        if first_manifest != second_manifest:
            raise AssertionError("the same source and seed did not reproduce the split")

        train_indices = []
        for leaf in LEAF_UNITS:
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
            validate_output(first, scenario)
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

    print("PASS deterministic image dataset partition and native validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
