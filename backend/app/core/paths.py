"""Locate the repository ``data/`` directory across local, container and CI runs.

Resolution order: the ``AGENTOPS_DATA_DIR`` environment variable, then a walk up from
this file looking for a ``data/policies`` directory, then common container mount points.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


@lru_cache
def get_data_dir() -> Path:
    override = os.environ.get("AGENTOPS_DATA_DIR")
    if override:
        return Path(override)

    candidates: list[Path] = [
        parent / "data" for parent in Path(__file__).resolve().parents
    ]
    candidates += [Path("/app/data"), Path("/data")]
    for candidate in candidates:
        if (candidate / "policies").is_dir():
            return candidate

    raise RuntimeError(
        "Could not locate the data/ directory; set AGENTOPS_DATA_DIR to its path."
    )


def get_policies_dir() -> Path:
    return get_data_dir() / "policies"


def get_synthetic_dir() -> Path:
    return get_data_dir() / "synthetic"
