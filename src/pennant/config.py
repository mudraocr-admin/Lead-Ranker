from __future__ import annotations

from pathlib import Path

import yaml

from .edgar import ROOT, Edgar

PROCESSED = ROOT / "data" / "processed"
OUTPUT = ROOT / "output"


def load_config(path: Path | None = None) -> dict:
    with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def make_edgar(cfg: dict) -> Edgar:
    return Edgar(cfg["edgar"]["user_agent"], cfg["edgar"]["max_rps"])
