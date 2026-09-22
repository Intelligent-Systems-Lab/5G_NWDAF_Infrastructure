#!/usr/bin/env python3
"""Prepare a per-seed image model using the selected PyMTLF source and bundle format."""

import argparse
import importlib.util
import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
import torch
from py_mtlf.config import ArtifactSettings
from py_mtlf.core.artifacts import ArtifactRepository
from py_mtlf.core.seed_import import build_seed_bundle


ROOT = Path(__file__).resolve().parents[2]


def prepare(dataset, seed, model_id, interoperability):
    source = ROOT / "ML" / "PyMTLF" / "seed_models" / "image_classification" / dataset
    destination = ROOT / ".generated" / "seed-models" / "image_classification" / dataset / "seed-{}".format(seed)
    expected_config = json.loads((source / "config.json").read_text(encoding="utf-8"))
    expected_config["initialization_seed"] = seed
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="seed-source-", dir=destination.parent) as temporary:
            temporary = Path(temporary)
            shutil.copy2(source / "model.py", temporary / "model.py")
            (temporary / "config.json").write_text(json.dumps(expected_config, indent=2) + "\n", encoding="utf-8")
            spec = importlib.util.spec_from_file_location("selected_seed_model", temporary / "model.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            torch.manual_seed(seed)
            model = module.Model(**expected_config["model"])
            weights = np.empty(len(model.state_dict()), dtype=object)
            weights[:] = [value.detach().cpu().numpy() for value in model.state_dict().values()]
            np.save(temporary / "model.npy", weights, allow_pickle=True)
            temporary.rename(destination)
    if (json.loads((destination / "config.json").read_text(encoding="utf-8")) != expected_config
            or (destination / "model.py").read_bytes() != (source / "model.py").read_bytes()):
        raise ValueError("selected seed source differs from the current PyMTLF model; inspect it before reuse")
    # TemporaryDirectory creates a private directory; the Root container reads this bind as UID 10001.
    destination.chmod(0o755)
    with tempfile.TemporaryDirectory(prefix="selected-seed-") as temporary:
        temporary = Path(temporary)
        bundle = temporary / "seed.tar.gz"
        build_seed_bundle(destination, bundle, model_id=model_id,
                          event="X_IMAGE_CLASSIFICATION", model_interoperability=interoperability)
        repository = ArtifactRepository(temporary / "artifacts", ArtifactSettings())
        repository.open()
        return repository.publish(bundle).key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=("mnist", "cifar10"), required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--model-id", type=int, required=True)
    parser.add_argument("--interoperability", required=True)
    args = parser.parse_args()
    if args.seed < 0:
        parser.error("seed must be non-negative")
    try:
        print(prepare(args.dataset, args.seed, args.model_id, args.interoperability))
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
