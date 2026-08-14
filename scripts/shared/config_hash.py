#!/usr/bin/env python3
"""Compute the canonical identity of a native config directory."""

import argparse
import hashlib
from pathlib import Path


def sha256_tree(directory):
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError("config directory does not exist: {}".format(directory))
    digest = hashlib.sha256()
    for path in sorted(path for path in directory.rglob("*") if path.is_file()):
        relative = path.relative_to(directory).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    args = parser.parse_args()
    try:
        print(sha256_tree(args.directory))
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
