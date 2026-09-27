# Image Dataset Reference

The retained workload supports official MNIST and CIFAR-10 inputs. Dataset
preparation is driven by the selected scenario snapshot in the generated
manifest.

## Storage layout

| Path | Contents |
| --- | --- |
| `.cache/image-datasets/<dataset>/` | downloaded official archives |
| `.generated/image-datasets/<dataset-id>/` | generated Leaf, validation, and held-out artifacts |
| `.generated/seed-models/image_classification/<dataset>/seed-<n>/` | generated formal seed models |

These paths are local, ignored artifacts. A clean checkout downloads and
regenerates them when the relevant commands are run.

Each generated dataset directory contains:

```text
split-manifest.yaml
validation.npz
held-out.npz
leaves/<leaf-name>.npz
```

The split manifest records source splits and indices, counts, image shape, seed,
class count, and class histograms.

## Generate and inspect

```sh
make dataset-generate CONFIG_DIR=config/local/<name>
make dataset-validate CONFIG_DIR=config/local/<name>
make dataset-show CONFIG_DIR=config/local/<name>
```

The dataset facade deliberately uses `ML/PyMTLF/.venv/bin/python` so generation
and validation load the same native image dataset contract as PyMTLF. Prepare
that environment with `uv sync --project ML/PyMTLF`.

Downloads use HTTPS and are cached. Generation writes a temporary complete
split, validates it, and then replaces the destination. When a scenario has a
`datasetId` and that directory already exists, generation validates and reuses
it instead of silently producing a second copy.

## Split behavior

Leaf data always comes from the official training split.

- Without explicit quotas, each Leaf receives a balanced ten-class sample.
- `leafLabels` gives each Leaf an equal allocation over only the listed
  classes.
- `leafClassCounts` gives each Leaf exact class quotas.

For explicit non-IID quotas, validation comes from the official training split
and is disjoint from every Leaf shard. The complete official test split is the
held-out set. For the default smoke behavior, balanced validation and held-out
subsets are selected from the official test split.

`dataset-validate` checks the scenario-selected artifact inventory, counts,
source mapping, disjoint source indices, loadability, and class distribution.
It does not require Leaf names to match a separately hard-coded list; the
selected scenario and testbed provide the identities.

## Formal seed sharing

For an E0–E2b scenario, `config-create SEED=<n>` creates or selects:

- dataset ID `<dataset>-formal-s<n>`;
- partition seed `n`;
- the PyMTLF seed model under the matching dataset/seed path;
- the component-native seed artifact identity written into the generated config.

All four conditions for the same workload and seed therefore reuse one verified
dataset directory and one initial model. Different seeds use distinct generated
inputs. The series runner performs config creation and dataset preparation for
each selected slot before invoking the single-run pipeline.
