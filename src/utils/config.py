"""Load YAML config files once and cache them."""
from __future__ import annotations

import functools
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


@functools.lru_cache(maxsize=None)
def load_yaml(relative_path: str) -> dict:
    path = REPO_ROOT / relative_path
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def cities_config() -> dict:
    return load_yaml("config/cities.yaml")


def settings() -> dict:
    return load_yaml("config/settings.yaml")


def db_path() -> Path:
    return REPO_ROOT / settings()["database"]["path"]
