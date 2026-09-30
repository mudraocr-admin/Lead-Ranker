"""Step 5: the ranked priority list.

Tiers:
  1  Follows neither an adviser nor management  (Judgment voter, Partial robo-voter)
  2  Adviser follower                           (ISS follower, Glass Lewis follower)
  3  Management follower
  4  Insufficient data
Within a tier, families are ordered by workload (meetings voted per year).
"""
from __future__ import annotations

import json

import pandas as pd

from .config import OUTPUT
from .scoring import CATS

LEVELS = ["low", "medium", "high"]
TIER_LABELS = {1: "Tier 1 - independent voter", 2: "Tier 2 - adviser follower",
               3: "Tier 3 - management follower", 4: "Unclassified - insufficient data"}


def tier_of(segment: str) -> int:
    if segment in ("Judgment voter", "Partial robo-voter"):
        return 1
    if segment.endswith("follower") and segment != "Management follower":
        return 2
    if segment == "Management follower":
        return 3
    return 4  # Insufficient data / Insufficient adviser data


def confidence(row: pd.Series) -> tuple[str, str]:
    """Data volume sets the base; an SAI that tells the same story raises it."""
    if row["segment"] == "Management follower":
        return ("high" if row["mgmt_items"] >= 200 else "medium"), "rate over all priority items"
    a = row.get("scored_adviser")
    base = 1 if a and row.get(f"items_{a}", 0) >= 30 else 0
    seg, sai_adv = row["segment"], row.get("advisor")
    followed = {"ISS follower": "ISS", "Glass Lewis follower": "Glass Lewis"}.get(seg)
    confirms = ""
    if seg in ("Judgment voter", "Partial robo-voter") and (row.get("own_guidelines") is True
                                                             or row.get("stewardship_team") is True):
        confirms = "SAI describes own guidelines / stewardship team"
    elif followed and sai_adv in (followed, "both") and row.get("own_guidelines") is not True:
        confirms = f"SAI names {followed} without own guidelines"
    return LEVELS[min(base + (1 if confirms else 0), 2)], confirms


def rank_prospects(sc: pd.DataFrame, sai: pd.DataFrame) -> pd.DataFrame:
    df = sc.merge(sai[["family", "advisor", "roles", "own_guidelines", "stewardship_team", "evidence", "ai_source"]]
                  .rename(columns={"advisor": "sai_advisor", "evidence": "sai_evidence", "ai_source": "sai_source"})
                  .assign(advisor=lambda d: d.sai_advisor), on="family", how="left")
    conf = df.apply(confidence, axis=1, result_type="expand")
    df["confidence"], df["confidence_reason"] = conf[0], conf[1]
    df["tier"] = df.segment.map(tier_of)
    df["priority"] = df.tier.map(TIER_LABELS)
    df = df.sort_values(["tier", "workload_meetings"], ascending=[True, False]).reset_index(drop=True)
    df.insert(0, "rank", range(1, len(df) + 1))
    return df


def write_outputs(prospects: pd.DataFrame, keys: dict[str, pd.DataFrame], validation: dict, log=print):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    cols = (["rank", "family", "priority", "segment", "confidence", "confidence_reason", "workload_meetings",
             "mgmt_rate", "mgmt_items", "scored_adviser", "scored_adviser_reason", "adviser_agreement"]
            + [f"{p}_{a}" for a in keys for p in ("agree", "items", "key_items")]
            + [f"{p}_{a}_{c}" for a in keys for c in CATS for p in ("agree", "n")]
            + [f"mgmt_{p}_{c}" for c in CATS for p in ("rate", "n")]
            + ["series_divergence", "n_series", "sai_advisor", "roles", "own_guidelines", "stewardship_team",
               "sai_evidence", "sai_source"])
    prospects[cols].to_csv(OUTPUT / "priority_list.csv", index=False)
    for adv, key in keys.items():
        key.to_csv(OUTPUT / f"{adv.lower()}_key.csv", index=False)
    (OUTPUT / "validation.json").write_text(json.dumps(validation, indent=2, default=str))
    log(f"Wrote {OUTPUT}/priority_list.csv, " + ", ".join(f"{a.lower()}_key.csv" for a in keys) + ", validation.json")
