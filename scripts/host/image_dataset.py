#!/usr/bin/env python3
"""Acquire, partition, and validate image-classification datasets."""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import struct
import sys
import tarfile
import tempfile
import urllib.request
from collections import Counter
from pathlib import Path

import numpy as np
import yaml

from configlib import (
    ROOT, image_scenario_contract, nwdaf_definitions, resolve_config_dir,
    resolve_config_scenario,
)
from py_mtlf.core.image_classification import ImageDatasetLoader


CACHE_ROOT = ROOT / ".cache" / "image-datasets"
OUTPUT_ROOT = ROOT / ".generated" / "image-datasets"
SOURCES = {
    "mnist": {
        "train-images-idx3-ubyte.gz": (
            "https://storage.googleapis.com/cvdf-datasets/mnist/train-images-idx3-ubyte.gz",
            12_000_000,
        ),
        "train-labels-idx1-ubyte.gz": (
            "https://storage.googleapis.com/cvdf-datasets/mnist/train-labels-idx1-ubyte.gz",
            100_000,
        ),
        "t10k-images-idx3-ubyte.gz": (
            "https://storage.googleapis.com/cvdf-datasets/mnist/t10k-images-idx3-ubyte.gz",
            2_000_000,
        ),
        "t10k-labels-idx1-ubyte.gz": (
            "https://storage.googleapis.com/cvdf-datasets/mnist/t10k-labels-idx1-ubyte.gz",
            100_000,
        ),
    },
    "cifar10": {
        "cifar-10-binary.tar.gz": (
            "https://www.cs.toronto.edu/~kriz/cifar-10-binary.tar.gz",
            200_000_000,
        ),
    },
}


class DatasetError(RuntimeError):
    pass


def _download(url: str, destination: Path, size_limit: int) -> None:
    if not url.startswith("https://"):
        raise DatasetError("dataset source must use HTTPS")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp-{}".format(os.getpid()))
    temporary.unlink(missing_ok=True)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "5g-nwdaf-testbed/1"})
        with urllib.request.urlopen(request, timeout=30) as response, temporary.open("xb") as stream:
            if response.geturl() != url and not response.geturl().startswith("https://"):
                raise DatasetError("dataset redirect left HTTPS")
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > size_limit:
                    raise DatasetError("dataset archive exceeds its size ceiling")
                stream.write(chunk)
            if total == 0:
                raise DatasetError("dataset source returned an empty archive")
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def ensure_sources(dataset: str) -> dict[str, Path]:
    sources = {}
    for filename, (url, size_limit) in SOURCES[dataset].items():
        path = CACHE_ROOT / dataset / filename
        if not path.is_file():
            print("DOWNLOAD dataset={} source={}".format(dataset, url))
            _download(url, path, size_limit)
        if path.stat().st_size <= 0 or path.stat().st_size > size_limit:
            raise DatasetError("cached dataset archive has an invalid size: {}".format(path))
        sources[filename] = path
    return sources


def _read_idx(path: Path, expected_magic: int, expected_count: int) -> np.ndarray:
    try:
        with gzip.open(path, "rb") as stream:
            header = stream.read(16 if expected_magic == 2051 else 8)
            if len(header) < 8:
                raise DatasetError("MNIST IDX header is truncated")
            magic, count = struct.unpack(">II", header[:8])
            if magic != expected_magic or count != expected_count:
                raise DatasetError("MNIST IDX header does not match the official split")
            if expected_magic == 2051:
                rows, columns = struct.unpack(">II", header[8:16])
                if (rows, columns) != (28, 28):
                    raise DatasetError("MNIST image shape is not 28x28")
                expected_bytes = count * rows * columns
                values = np.frombuffer(stream.read(), dtype=np.uint8)
                if values.size != expected_bytes:
                    raise DatasetError("MNIST image payload length is invalid")
                return values.reshape(count, 1, rows, columns).copy()
            values = np.frombuffer(stream.read(), dtype=np.uint8)
            if values.size != count:
                raise DatasetError("MNIST label payload length is invalid")
            return values.copy()
    except (gzip.BadGzipFile, OSError) as exc:
        raise DatasetError("MNIST archive cannot be read: {}".format(path)) from exc


def load_mnist(sources: dict[str, Path]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    train_images = _read_idx(sources["train-images-idx3-ubyte.gz"], 2051, 60_000)
    train_labels = _read_idx(sources["train-labels-idx1-ubyte.gz"], 2049, 60_000)
    test_images = _read_idx(sources["t10k-images-idx3-ubyte.gz"], 2051, 10_000)
    test_labels = _read_idx(sources["t10k-labels-idx1-ubyte.gz"], 2049, 10_000)
    return train_images, train_labels, test_images, test_labels


def _cifar_members() -> list[str]:
    prefix = "cifar-10-batches-bin/"
    return [prefix + "data_batch_{}.bin".format(index) for index in range(1, 6)] + [
        prefix + "test_batch.bin"
    ]


def _read_cifar_member(archive: tarfile.TarFile, name: str, count: int) -> tuple[np.ndarray, np.ndarray]:
    member = archive.getmember(name)
    if not member.isfile() or member.size != count * 3073:
        raise DatasetError("CIFAR-10 member has an invalid type or size: {}".format(name))
    stream = archive.extractfile(member)
    if stream is None:
        raise DatasetError("CIFAR-10 member cannot be read: {}".format(name))
    payload = stream.read()
    if len(payload) != member.size:
        raise DatasetError("CIFAR-10 member is truncated: {}".format(name))
    records = np.frombuffer(payload, dtype=np.uint8).reshape(count, 3073)
    return records[:, 1:].reshape(count, 3, 32, 32).copy(), records[:, 0].copy()


def load_cifar10(sources: dict[str, Path]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    path = sources["cifar-10-binary.tar.gz"]
    prefix = "cifar-10-batches-bin/"
    expected_payloads = set(_cifar_members())
    allowed_members = expected_payloads | {
        prefix.rstrip("/"),
        prefix + "batches.meta.txt",
        prefix + "readme.html",
    }
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if any(member.name not in allowed_members for member in members):
                raise DatasetError("CIFAR-10 archive contains an unexpected member")
            if any(not (member.isfile() or member.isdir()) for member in members):
                raise DatasetError("CIFAR-10 archive contains a non-file member")
            present = {member.name for member in members if member.isfile()}
            if not expected_payloads.issubset(present):
                raise DatasetError("CIFAR-10 archive is missing an expected binary member")
            train = [
                _read_cifar_member(archive, name, 10_000)
                for name in _cifar_members()[:5]
            ]
            test_images, test_labels = _read_cifar_member(
                archive, _cifar_members()[-1], 10_000
            )
    except (KeyError, OSError, tarfile.TarError) as exc:
        raise DatasetError("CIFAR-10 archive cannot be read") from exc
    return (
        np.concatenate([part[0] for part in train]),
        np.concatenate([part[1] for part in train]),
        test_images,
        test_labels,
    )


def load_source(dataset: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    sources = ensure_sources(dataset)
    try:
        values = load_mnist(sources) if dataset == "mnist" else load_cifar10(sources)
        train_images, train_labels, test_images, test_labels = values
        if train_images.dtype != np.uint8 or test_images.dtype != np.uint8:
            raise DatasetError("source images are not uint8")
        if train_labels.dtype != np.uint8 or test_labels.dtype != np.uint8:
            raise DatasetError("source labels are not uint8")
        if np.any(train_labels > 9) or np.any(test_labels > 9):
            raise DatasetError("source labels are outside the supported class range")
        if set(train_labels.tolist()) != set(range(10)) or set(test_labels.tolist()) != set(range(10)):
            raise DatasetError("source split does not contain all expected classes")
        return values
    except DatasetError:
        for path in sources.values():
            path.unlink(missing_ok=True)
        raise


def balanced_indices(labels: np.ndarray, per_class: int, seed: int) -> list[int]:
    selected = []
    generator = np.random.default_rng(seed)
    for label in range(10):
        candidates = np.flatnonzero(labels == label)
        if candidates.size < per_class:
            raise DatasetError("source split has insufficient samples for class {}".format(label))
        selected.extend(generator.permutation(candidates)[:per_class].tolist())
    return selected


def _histogram(labels: np.ndarray) -> dict[int, int]:
    values = Counter(int(value) for value in labels.tolist())
    return {label: values.get(label, 0) for label in range(10)}


def _write_npz(path: Path, images: np.ndarray, labels: np.ndarray) -> None:
    np.savez(path, images=images, labels=labels)


def build_split(
    scenario: dict,
    train_images: np.ndarray,
    train_labels: np.ndarray,
    test_images: np.ndarray,
    test_labels: np.ndarray,
    destination: Path,
    legacy_leaves: tuple[str, ...],
) -> dict:
    (destination / "leaves").mkdir(parents=True, exist_ok=True)
    partition = scenario["partition"]
    per_leaf = partition["samplesPerLeaf"]
    leaf_labels = partition.get("leafLabels")
    leaf_units = tuple(sorted(leaf_labels)) if leaf_labels is not None else legacy_leaves
    labels_by_leaf = leaf_labels if leaf_labels is not None else {
        leaf: list(range(10)) for leaf in leaf_units
    }
    quotas = {
        leaf: per_leaf // len(labels_by_leaf[leaf]) for leaf in leaf_units
    }
    validation_source = partition.get("validationSource", "official-test")
    validation_count = partition["validationSamples"]
    held_out_count = partition["heldOutSamples"]
    validation_indices = []
    train_by_class = {}
    if validation_source == "official-train":
        if held_out_count != len(test_labels):
            raise DatasetError("heldOutSamples must cover the complete official test split")
        generator = np.random.default_rng(partition["seed"])
        validation_per_class = validation_count // 10
        for label in range(10):
            candidates = generator.permutation(np.flatnonzero(train_labels == label))
            needed = sum(quotas[leaf] for leaf in leaf_units if label in labels_by_leaf[leaf])
            if len(candidates) < validation_per_class + needed:
                raise DatasetError("official train split has insufficient samples for class {}".format(label))
            validation_indices.extend(candidates[:validation_per_class].tolist())
            train_by_class[label] = candidates[
                validation_per_class : validation_per_class + needed
            ].tolist()
    else:
        if leaf_labels is not None:
            raise DatasetError("leafLabels requires official-train validation")
        per_leaf_class = per_leaf // 10
        train_selected = balanced_indices(
            train_labels, per_leaf_class * len(leaf_units), partition["seed"]
        )
        train_by_class = {
            label: [index for index in train_selected if int(train_labels[index]) == label]
            for label in range(10)
        }
    split_manifest = {
        "schemaVersion": 1,
        "dataset": scenario["workload"]["dataset"],
        "source": {"trainSplit": "official-train", "testSplit": "official-test"},
        "seed": (
            {"train": partition["seed"]}
            if validation_source == "official-train"
            else {"train": partition["seed"], "test": partition["seed"] + 1}
        ),
        "imageShape": list(train_images.shape[1:]),
        "classCount": 10,
        "artifacts": {},
    }
    class_offsets = {label: 0 for label in range(10)}
    for leaf_offset, leaf in enumerate(leaf_units):
        indices = []
        for label in labels_by_leaf[leaf]:
            start = (
                class_offsets[label]
                if validation_source == "official-train"
                else leaf_offset * per_leaf_class
            )
            quota = quotas[leaf]
            indices.extend(train_by_class[label][start : start + quota])
            if validation_source == "official-train":
                class_offsets[label] += quota
        indices.sort()
        images = train_images[indices]
        labels = train_labels[indices]
        filename = "leaves/{}.npz".format(leaf)
        _write_npz(destination / filename, images, labels)
        split_manifest["artifacts"][leaf] = {
            "file": filename,
            "sourceSplit": "official-train",
            "sourceIndices": indices,
            "count": len(indices),
            "classHistogram": _histogram(labels),
        }

    if validation_source == "official-train":
        held_out_indices = list(range(len(test_labels)))
    else:
        if validation_count % 10 or held_out_count % 10:
            raise DatasetError("validation and held-out counts must be divisible by 10")
        per_test_class = (validation_count + held_out_count) // 10
        test_selected = balanced_indices(test_labels, per_test_class, partition["seed"] + 1)
        test_by_class = {
            label: [index for index in test_selected if int(test_labels[index]) == label]
            for label in range(10)
        }
        held_out_indices = []
        validation_per_class = validation_count // 10
        for label in range(10):
            validation_indices.extend(test_by_class[label][:validation_per_class])
            held_out_indices.extend(test_by_class[label][validation_per_class:])
    for key, indices in (
        ("validation", sorted(validation_indices)),
        ("held-out", sorted(held_out_indices)),
    ):
        source_images, source_labels = (
            (train_images, train_labels)
            if key == "validation" and validation_source == "official-train"
            else (test_images, test_labels)
        )
        images = source_images[indices]
        labels = source_labels[indices]
        filename = "{}.npz".format(key)
        _write_npz(destination / filename, images, labels)
        split_manifest["artifacts"][key] = {
            "file": filename,
            "sourceSplit": (
                "official-train" if key == "validation" and validation_source == "official-train"
                else "official-test"
            ),
            "sourceIndices": indices,
            "count": len(indices),
            "classHistogram": _histogram(labels),
        }
    (destination / "split-manifest.yaml").write_text(
        yaml.safe_dump(split_manifest, sort_keys=False), encoding="utf-8"
    )
    return split_manifest


def _expected_counts(scenario: dict, leaf_units: tuple[str, ...]) -> dict[str, int]:
    return {
        **{leaf: scenario["partition"]["samplesPerLeaf"] for leaf in leaf_units},
        "validation": scenario["partition"]["validationSamples"],
        "held-out": scenario["partition"]["heldOutSamples"],
    }


def validate_output(root: Path, scenario: dict, legacy_leaves: tuple[str, ...]) -> dict:
    manifest_path = root / "split-manifest.yaml"
    if not manifest_path.is_file():
        raise DatasetError("split manifest is missing")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    dataset = scenario["workload"]["dataset"]
    if manifest.get("dataset") != dataset:
        raise DatasetError("split manifest dataset does not match the scenario")
    partition = scenario["partition"]
    validation_source = partition.get("validationSource", "official-test")
    expected_seed = {"train": partition["seed"]}
    if validation_source == "official-test":
        expected_seed["test"] = partition["seed"] + 1
    if manifest.get("seed") != expected_seed:
        raise DatasetError("split manifest seeds do not match the scenario")
    leaf_labels = partition.get("leafLabels")
    leaf_units = tuple(sorted(leaf_labels)) if leaf_labels is not None else legacy_leaves
    expected = _expected_counts(scenario, leaf_units)
    artifacts = manifest.get("artifacts", {})
    if set(artifacts) != set(expected):
        raise DatasetError("split manifest artifact inventory is invalid")
    source_indices: dict[str, set[int]] = {"official-train": set(), "official-test": set()}
    loader = ImageDatasetLoader()
    for name, count in expected.items():
        item = artifacts[name]
        expected_file = (
            "leaves/{}.npz".format(name)
            if name in leaf_units
            else "{}.npz".format(name)
        )
        if item.get("file") != expected_file or item.get("count") != count:
            raise DatasetError("split manifest metadata is invalid for {}".format(name))
        source_split = item.get("sourceSplit")
        indices = item.get("sourceIndices")
        expected_source = (
            "official-train"
            if name in leaf_units or (name == "validation" and validation_source == "official-train")
            else "official-test"
        )
        if source_split != expected_source or not isinstance(indices, list):
            raise DatasetError("split source mapping is invalid for {}".format(name))
        if len(indices) != count or len(set(indices)) != count:
            raise DatasetError("split source indices are invalid for {}".format(name))
        overlap = source_indices[source_split].intersection(indices)
        if overlap:
            raise DatasetError("split source indices overlap for {}".format(name))
        source_indices[source_split].update(indices)
        loaded = loader.load(root / item["file"], dataset)
        if loaded.sample_count != count:
            raise DatasetError("native dataset count is invalid for {}".format(name))
        if item.get("classHistogram") != _histogram(loaded.targets.numpy()):
            raise DatasetError("split class histogram is invalid for {}".format(name))
        if name == "held-out" and validation_source == "official-train":
            continue
        if name in leaf_units and leaf_labels is not None:
            labels = leaf_labels[name]
            expected_histogram = {
                label: count // len(labels) if label in labels else 0
                for label in range(10)
            }
        else:
            expected_histogram = {label: count // 10 for label in range(10)}
        if item["classHistogram"] != expected_histogram:
            raise DatasetError("split class distribution is invalid for {}".format(name))
    return manifest


def generate(root: Path, scenario: dict, legacy_leaves: tuple[str, ...]) -> None:
    dataset = scenario["workload"]["dataset"]
    train_images, train_labels, test_images, test_labels = load_source(dataset)
    root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".image-dataset-", dir=root.parent))
    backup = root.with_name(root.name + ".previous-{}".format(os.getpid()))
    try:
        build_split(
            scenario, train_images, train_labels, test_images, test_labels,
            temporary, legacy_leaves,
        )
        validate_output(temporary, scenario, legacy_leaves)
        if root.exists():
            os.replace(root, backup)
        os.replace(temporary, root)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if backup.exists() and not root.exists():
            os.replace(backup, root)
        raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    print("GENERATED dataset={} root={}".format(dataset, root))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--testbed", required=True)
    parser.add_argument("--config-dir")
    parser.add_argument("action", choices=("generate", "check", "show"))
    args = parser.parse_args()
    try:
        testbed = yaml.safe_load((ROOT / args.testbed).read_text(encoding="utf-8"))
        config_dir = resolve_config_dir(testbed, args.config_dir)
        legacy_leaves = tuple(
            definition["unit"] for definition in nwdaf_definitions(testbed)
            if definition["role"] == "leaf"
        )
        _scenario_path, scenario = resolve_config_scenario(config_dir)
        image_scenario_contract(scenario)
        root = OUTPUT_ROOT / scenario["name"]
        if args.action == "generate":
            generate(root, scenario, legacy_leaves)
        else:
            manifest = validate_output(root, scenario, legacy_leaves)
            if args.action == "show":
                leaf_labels = scenario["partition"].get("leafLabels")
                leaf_units = tuple(sorted(leaf_labels)) if leaf_labels is not None else legacy_leaves
                print(
                    "DATASET name={} dataset={} root={} artifacts={} train_samples={} validation_samples={} held_out_samples={}".format(
                        scenario["name"],
                        scenario["workload"]["dataset"],
                        root,
                        len(manifest["artifacts"]),
                        sum(manifest["artifacts"][leaf]["count"] for leaf in leaf_units),
                        manifest["artifacts"]["validation"]["count"],
                        manifest["artifacts"]["held-out"]["count"],
                    )
                )
            else:
                print("OK dataset={} root={}".format(scenario["workload"]["dataset"], root))
    except (DatasetError, KeyError, OSError, ValueError, yaml.YAMLError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
