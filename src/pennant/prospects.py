"""Step 5: the ranked priority list.

Tiers:
  1  Follows neither an adviser nor management  (Judgment voter, Partial robo-voter)
  2  Adviser follower                           (ISS follower, Glass Lewis follower)
  3  Management follower
Every family gets a tier. Families classified on thin data carry low
confidence and say why. Within a tier, families are ordered by workload
(meetings voted per year).
"""
from __future__ import annotations

import json

import pandas as pd

from .config import OUTPUT
from .scoring import CATS, wilson

THRESHOLD = 0.90  # scoring.mgmt_follower and scoring.adviser_follower

TIER_LABELS = {1: "Tier 1 - independent voter", 2: "Tier 2 - adviser follower",
               3: "Tier 3 - management follower"}


def tier_of(segment: str) -> int:
    if segment in ("Judgment voter", "Partial robo-voter"):
        return 1
    if segment == "Management follower":
        return 3
    return 2  # ISS / Glass Lewis follower


def confidence(row: pd.Series) -> tuple[str, str]:
    """High when the 95% interval on the deciding rate sits entirely on one side
    of its 90% line, medium when it straddles it. Thin-data labels are low."""
    if row.get("segment_basis"):
        return "low", row["segment_basis"]
    if row["segment"] == "Management follower":
        rate, n, what = row["mgmt_rate"], row["mgmt_items"], "votes with the board"
    else:
        a = row["scored_adviser"]
        rate, n, what = row["adviser_agreement"], row[f"n_{a}_opposed"], f"agreement with {a} where it opposed the board"
    lo, hi = wilson(round(rate * n), int(n))
    clear = lo >= THRESHOLD or hi < THRESHOLD
    return ("high" if clear else "medium"), f"{what}: {rate:.0%} of {int(n)} (95% interval {lo:.0%}-{hi:.0%})"


def filer_ciks(votes: pd.DataFrame) -> pd.DataFrame:
    """The N-PX filer CIKs (trusts) whose votes make up each family, largest first."""
    t = votes.groupby(["family", "trust_cik", "trust"]).size().rename("rows").reset_index()
    t = t.sort_values(["family", "rows"], ascending=[True, False])
    return t.groupby("family").agg(ciks=("trust_cik", "; ".join), filers=("trust", "; ".join)).reset_index()


def rank_prospects(sc: pd.DataFrame, votes: pd.DataFrame) -> pd.DataFrame:
    df = sc.merge(filer_ciks(votes), on="family", how="left")
    conf = df.apply(confidence, axis=1, result_type="expand")
    df["confidence"], df["confidence_reason"] = conf[0], conf[1]
    df["tier"] = df.segment.map(tier_of)
    df["priority"] = df.tier.map(TIER_LABELS)
    df = df.sort_values(["tier", "workload_meetings"], ascending=[True, False]).reset_index(drop=True)
    df.insert(0, "rank", range(1, len(df) + 1))
    return df


def write_outputs(prospects: pd.DataFrame, keys: dict[str, pd.DataFrame], validation: dict, log=print):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    cols = (["rank", "family", "ciks", "priority", "segment", "confidence", "confidence_reason", "workload_meetings",
             "mgmt_rate", "mgmt_items", "scored_adviser", "scored_adviser_reason", "adviser_agreement"]
            + [f"{p}_{a}" for a in keys for p in ("agree", "items", "key_items")]
            + [f"{p}_{a}_{k}" for a in keys for k in ("opposed", "aligned") for p in ("agree", "n")]
            + [f"{p}_{a}_{c}{o}" for a in keys for c in CATS for o in ("_opposed", "") for p in ("agree", "n")]
            + [f"mgmt_{p}_{c}" for c in CATS for p in ("rate", "n")]
            + ["series_divergence", "n_series", "filers"])
    prospects[cols].to_csv(OUTPUT / "priority_list.csv", index=False)
    for adv, key in keys.items():
        key.to_csv(OUTPUT / f"{adv.lower()}_key.csv", index=False)
    (OUTPUT / "validation.json").write_text(json.dumps(validation, indent=2, default=str))
    log(f"Wrote {OUTPUT}/priority_list.csv, " + ", ".join(f"{a.lower()}_key.csv" for a in keys) + ", validation.json")
