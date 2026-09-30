from __future__ import annotations

import csv
import re
from pathlib import Path

import yaml

from .edgar import ROOT, Edgar

PROCESSED = ROOT / "data" / "processed"
OUTPUT = ROOT / "output"


def load_families(path: Path | None = None) -> list[dict]:
    """The target list is an input you maintain in families.csv.

    Columns:
      family  display name (required)
      match   optional regex over EDGAR N-PX filer names; blank = filer name starts with `family`
      ciks    optional semicolon-separated filer CIKs; when given, these are used instead of `match`
    """
    out = []
    with open(path or ROOT / "families.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("family") or "").strip()
            if not name or name.startswith("#"):
                continue
            match = (row.get("match") or "").strip() or "^" + re.escape(name)
            ciks = [c.strip().lstrip("0") for c in (row.get("ciks") or "").split(";") if c.strip()]
            out.append({"name": name, "match": match, "ciks": ciks})
    return out


def load_config(path: Path | None = None) -> dict:
    with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["families"] = load_families()
    return cfg


def make_edgar(cfg: dict) -> Edgar:
    return Edgar(cfg["edgar"]["user_agent"], cfg["edgar"]["max_rps"])
