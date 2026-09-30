"""Steps 2b + 4: follower panel, ISS inference, validation, and fund scoring.

Unit of analysis is the fund family (the prospect). A family's vote on an
item is the majority direction across its series; series that split are
tracked separately as "series divergence".
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import PROCESSED

CATS = ["shareholder_proposal", "contested_election", "mna", "pay"]
DIRS = ["FOR", "AGAINST", "ABSTAIN"]


def _direction(v: str):
    v = (v or "").upper()
    if v in ("FOR",):
        return "FOR"
    if v in ("AGAINST", "WITHHOLD"):
        return "AGAINST"
    if v == "ABSTAIN":
        return "ABSTAIN"
    return None  # non-votes, frequency choices, echo voting


def family_votes(pri: pd.DataFrame) -> pd.DataFrame:
    """One row per (family, item_key): majority direction + whether series split."""
    d = pri.assign(direction=pri.vote_cast.map(_direction)).dropna(subset=["direction"])
    per_series = d.drop_duplicates(["family", "series_id", "item_key", "direction"])
    g = per_series.groupby(["family", "item_key"])
    fv = g.direction.agg(lambda s: s.value_counts().idxmax()).rename("direction").to_frame()
    fv["n_series"] = g.series_id.nunique()
    fv["split"] = g.direction.nunique() > 1
    meta = (d.groupby("item_key")
            .agg(category=("priority_category", "first"), cusip=("cusip", "first"),
                 issuer=("issuer", "first"), meeting_dt=("meeting_dt", "first"),
                 item_text=("item_text", "first"),
                 mgmt_rec=("mgmt_rec", lambda s: _direction(s.mode().iat[0]) if len(s.mode()) else None)))
    return fv.reset_index().merge(meta, on="item_key", how="left")


def agreement(fv: pd.DataFrame, recs: pd.DataFrame) -> pd.DataFrame:
    """Per family: items voted and share matching the reference rec."""
    m = fv.merge(recs[["item_key", "iss_rec"]], on="item_key")
    m["match"] = m.direction == m.iss_rec
    return m


def follower_panel(fv: pd.DataFrame, key: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    p = cfg["panel"]
    a = agreement(fv, key).groupby("family").agg(items=("match", "size"), rate=("match", "mean"))
    a["panel"] = (a["items"] >= p["follower_min_items"]) & (a["rate"] >= p["follower_agreement"])
    return a


def infer_recs(fv: pd.DataFrame, panel: list[str], cfg: dict, exclude: str | None = None,
               items: pd.Index | None = None, disagreement_only: bool = True) -> pd.DataFrame:
    """Infer ISS's rec on items from panel families' votes.

    Rule: >= infer_min_panel_votes panel families voted and >= infer_min_agreement
    of them voted the same way. `exclude` drops the scored family from its own
    panel. With `disagreement_only`, keep items where the inferred rec opposes
    management (the key only covers board-opposed items, so the expansion must
    match it)."""
    p = cfg["panel"]
    members = [f for f in panel if f != exclude]
    pv = fv[fv.family.isin(members)]
    if items is not None:
        pv = pv[pv.item_key.isin(items)]
    counts = pv.pivot_table(index="item_key", columns="direction", values="family",
                            aggfunc="count", fill_value=0).reindex(columns=DIRS, fill_value=0)
    n = counts.sum(axis=1)
    top = counts.idxmax(axis=1)
    share = counts.max(axis=1) / n.replace(0, np.nan)
    ok = (n >= p["infer_min_panel_votes"]) & (share >= p["infer_min_agreement"]) & top.isin(["FOR", "AGAINST"])
    out = pd.DataFrame({"item_key": counts.index[ok], "iss_rec": top[ok].values,
                        "panel_n": n[ok].values, "panel_share": share[ok].values})
    if disagreement_only:
        mg = fv.drop_duplicates("item_key").set_index("item_key").mgmt_rec
        out = out[out.iss_rec.values != mg.reindex(out.item_key).values]
    return out.reset_index(drop=True)


def validate(fv: pd.DataFrame, key: pd.DataFrame, cfg: dict, log=print) -> dict:
    """Hold out key items, rebuild the panel without them, infer them back."""
    p = cfg["panel"]
    rng = np.random.default_rng(p["seed"])
    # Only items enough families voted on can be inferred at all; holding out
    # a 3-family item would test nothing.
    eligible = key[key.n_families >= max(p["infer_min_panel_votes"] * 2, 10)]
    if len(eligible) < p["holdout_items"]:
        eligible = key
    k = min(p["holdout_items"], max(len(key) // 4, 1), len(eligible))
    hold = eligible.iloc[rng.choice(len(eligible), size=k, replace=False)]
    train = key[~key.item_key.isin(hold.item_key)]
    panel = follower_panel(fv, train, cfg)
    members = panel.index[panel.panel].tolist()
    inf = infer_recs(fv, members, cfg, items=pd.Index(hold.item_key), disagreement_only=False)
    m = hold.merge(inf, on="item_key", how="left", suffixes=("", "_inferred"))
    covered = m.iss_rec_inferred.notna()
    correct = (m.iss_rec == m.iss_rec_inferred) & covered
    res = {"holdout_items": int(k), "panel_size": len(members), "inferred": int(covered.sum()),
           "correct": int(correct.sum()),
           "accuracy": float(correct.sum() / covered.sum()) if covered.any() else None,
           "coverage": float(covered.mean())}
    m.assign(correct=correct).to_csv(PROCESSED / "validation_holdout.csv", index=False)
    log(f"Validation: held out {k} key items, panel of {len(members)}, inferred {res['inferred']}, "
        f"correct {res['correct']} (accuracy {res['accuracy']}, coverage {res['coverage']:.0%})")
    return res


def classify_family(row: pd.Series, s: dict) -> str:
    if row["items"] < s["min_items"]:
        return "Insufficient data"
    if row["iss_agreement"] >= s["iss_follower"]:
        return "ISS follower"
    if row["iss_agreement"] <= s["mgmt_follower"]:
        return "Management follower"
    rates = [row[f"agree_{c}"] for c in CATS
             if pd.notna(row[f"agree_{c}"]) and row[f"n_{c}"] >= s["min_category_items"]]
    if any(r >= s["partial_category"] for r in rates) and any(s["mgmt_follower"] < r < s["partial_category"] for r in rates):
        return "Partial robo-voter"
    return "Judgment voter"


def score(votes: pd.DataFrame, pri: pd.DataFrame, key: pd.DataFrame, cfg: dict, log=print):
    s = cfg["scoring"]
    fv = family_votes(pri)
    panel = follower_panel(fv, key, cfg)
    members = panel.index[panel.panel].tolist()
    log(f"Panel: {len(members)} ISS-follower families on the seed key: {members}")
    validation = validate(fv, key, cfg, log)
    if not members:
        # The headline thresholds found no followers. Show what seed-and-expand
        # would do under a relaxed panel, reported separately, never used to score.
        relaxed = {**cfg, "panel": {**cfg["panel"], **cfg["panel_sensitivity"]}}
        rp = follower_panel(fv, key, relaxed)
        rmembers = rp.index[rp.panel].tolist()
        rinf = infer_recs(fv, rmembers, relaxed)
        rinf = rinf[~rinf.item_key.isin(key.item_key)]
        log(f"Sensitivity (panel >= {relaxed['panel']['follower_agreement']:.0%} on >= "
            f"{relaxed['panel']['follower_min_items']} items): panel {rmembers}, "
            f"{len(rinf)} extra items inferred")
        validation = {"headline": validation, "sensitivity_panel": rmembers,
                      "sensitivity_inferred_items": int(len(rinf)),
                      "sensitivity_validation": validate(fv, key, relaxed, log)}
        rinf.merge(fv.drop_duplicates("item_key")[["item_key", "issuer", "meeting_dt", "category", "item_text"]],
                   on="item_key").to_csv(PROCESSED / "sensitivity_inferred_items.csv", index=False)

    key_recs = key[["item_key", "iss_rec"]].assign(source="key")
    rows, all_matches = [], []
    for fam in sorted(fv.family.unique()):
        inferred = infer_recs(fv, members, cfg, exclude=fam)
        inferred = inferred[~inferred.item_key.isin(key.item_key)].assign(source="inferred")
        recs = pd.concat([key_recs, inferred[["item_key", "iss_rec", "source"]]], ignore_index=True)
        m = agreement(fv[fv.family == fam], recs).merge(recs[["item_key", "source"]], on="item_key")
        all_matches.append(m)
        row = {"family": fam, "items": len(m), "key_items": int((m.source == "key").sum()),
               "iss_agreement": m.match.mean() if len(m) else np.nan,
               "iss_agreement_key_only": m[m.source == "key"].match.mean() if (m.source == "key").any() else np.nan}
        for c in CATS:
            mc = m[m.category == c]
            row[f"n_{c}"] = len(mc)
            row[f"agree_{c}"] = mc.match.mean() if len(mc) else np.nan
        rows.append(row)
    sc = pd.DataFrame(rows).set_index("family")

    # Series divergence over all priority items where >=2 series voted.
    multi = fv[fv.n_series >= 2]
    sc["series_divergence"] = multi.groupby("family").split.mean()
    sc["n_series"] = pri.groupby("family").series_id.nunique()
    # Workload: distinct meetings voted in the Jul 2025 - Jun 2026 year, all categories.
    sc["workload_meetings"] = votes.drop_duplicates(["family", "cusip", "meeting_dt"]).groupby("family").size()
    sc["with_mgmt_rate_priority"] = pri.dropna(subset=["with_mgmt"]).groupby("family").with_mgmt.mean()
    sc["panel_member"] = sc.index.isin(members)
    sc["segment"] = sc.apply(classify_family, axis=1, s=s)
    matches = pd.concat(all_matches, ignore_index=True)
    matches.to_parquet(PROCESSED / "family_item_matches.parquet", index=False)
    return sc.reset_index(), validation, fv
