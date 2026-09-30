"""Steps 2b + 4: follower panels, adviser-rec inference, validation, and family scoring.

Unit of analysis is the fund family (the prospect). A family's vote on an
item is the majority direction across its series; series that split are
tracked separately as "series divergence".

Everything here is run once per adviser (ISS, Glass Lewis). Each family is
then scored against the adviser its SAI names; families whose SAI names both
or neither are scored against whichever adviser they agree with more.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import PROCESSED

CATS = ["shareholder_proposal", "contested_election", "mna", "pay"]
DIRS = ["FOR", "AGAINST", "ABSTAIN"]
ADVISER_NAMES = {"ISS": "ISS", "GL": "Glass Lewis"}
SAI_TO_ADVISER = {"ISS": "ISS", "Glass Lewis": "GL"}


def _direction(v: str):
    v = (v or "").upper()
    if v == "FOR":
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
                 board_rec=("board_rec", lambda s: s.dropna().iat[0] if s.notna().any() else None)))
    return fv.reset_index().merge(meta, on="item_key", how="left")


def agreement(fv: pd.DataFrame, recs: pd.DataFrame) -> pd.DataFrame:
    """Family votes joined to reference recs; `match` = voted the way the adviser recommended."""
    m = fv.merge(recs[["item_key", "rec"]], on="item_key")
    m["match"] = m.direction == m.rec
    return m


def follower_panel(fv: pd.DataFrame, key: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    p = cfg["panel"]
    a = agreement(fv, key).groupby("family").agg(items=("match", "size"), rate=("match", "mean"))
    a["panel"] = (a["items"] >= p["follower_min_items"]) & (a["rate"] >= p["follower_agreement"])
    return a


def infer_recs(fv: pd.DataFrame, panel: list[str], cfg: dict, exclude: str | None = None,
               items: pd.Index | None = None, disagreement_only: bool = True) -> pd.DataFrame:
    """Infer the adviser's rec on items from panel families' votes.

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
    if pv.empty:
        return pd.DataFrame(columns=["item_key", "rec", "panel_n", "panel_share"])
    counts = pv.pivot_table(index="item_key", columns="direction", values="family",
                            aggfunc="count", fill_value=0).reindex(columns=DIRS, fill_value=0)
    n = counts.sum(axis=1)
    top = counts.idxmax(axis=1)
    share = counts.max(axis=1) / n.replace(0, np.nan)
    ok = (n >= p["infer_min_panel_votes"]) & (share >= p["infer_min_agreement"]) & top.isin(["FOR", "AGAINST"])
    out = pd.DataFrame({"item_key": counts.index[ok], "rec": top[ok].values,
                        "panel_n": n[ok].values, "panel_share": share[ok].values})
    if disagreement_only:
        mg = fv.drop_duplicates("item_key").set_index("item_key").board_rec
        out = out[out.rec.values != mg.reindex(out.item_key).values]
    return out.reset_index(drop=True)


def validate(fv: pd.DataFrame, key: pd.DataFrame, cfg: dict, adviser: str, log=print) -> dict:
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
    covered = m.rec_inferred.notna()
    correct = (m.rec == m.rec_inferred) & covered
    res = {"holdout_items": int(k), "panel_size": len(members), "inferred": int(covered.sum()),
           "correct": int(correct.sum()),
           "accuracy": float(correct.sum() / covered.sum()) if covered.any() else None,
           "coverage": float(covered.mean()) if k else 0.0}
    m.assign(correct=correct).to_csv(PROCESSED / f"validation_holdout_{adviser.lower()}.csv", index=False)
    log(f"{adviser} validation: held out {k} key items, panel of {len(members)}, inferred {res['inferred']}, "
        f"correct {res['correct']} (accuracy {res['accuracy']}, coverage {res['coverage']:.0%})")
    return res


def score_adviser(fv: pd.DataFrame, key: pd.DataFrame, cfg: dict, adviser: str, log=print):
    """Per-family agreement with one adviser (key + panel-inferred items)."""
    panel = follower_panel(fv, key, cfg)
    members = panel.index[panel.panel].tolist()
    log(f"{adviser} panel: {len(members)} follower families on the seed key: {members}")
    validation = {"headline": validate(fv, key, cfg, adviser, log)}
    if not members:
        # Headline thresholds found no followers: show what seed-and-expand would
        # do under a relaxed panel. Reported separately, never used to score.
        relaxed = {**cfg, "panel": {**cfg["panel"], **cfg["panel_sensitivity"]}}
        rp = follower_panel(fv, key, relaxed)
        rmembers = rp.index[rp.panel].tolist()
        rinf = infer_recs(fv, rmembers, relaxed)
        rinf = rinf[~rinf.item_key.isin(key.item_key)]
        log(f"{adviser} sensitivity (panel >= {relaxed['panel']['follower_agreement']:.0%} on >= "
            f"{relaxed['panel']['follower_min_items']} items): panel {rmembers}, {len(rinf)} extra items inferred")
        validation.update({"sensitivity_panel": rmembers, "sensitivity_inferred_items": int(len(rinf)),
                           "sensitivity_validation": validate(fv, key, relaxed, adviser, log)})
        rinf.merge(fv.drop_duplicates("item_key")[["item_key", "issuer", "meeting_dt", "category", "item_text"]],
                   on="item_key").to_csv(PROCESSED / f"sensitivity_inferred_{adviser.lower()}.csv", index=False)

    key_recs = key[["item_key", "rec"]].assign(source="key")
    rows, matches = [], []
    for fam in sorted(fv.family.unique()):
        inferred = infer_recs(fv, members, cfg, exclude=fam)
        inferred = inferred[~inferred.item_key.isin(key.item_key)].assign(source="inferred")
        recs = pd.concat([key_recs, inferred[["item_key", "rec", "source"]]], ignore_index=True)
        m = agreement(fv[fv.family == fam], recs).merge(recs[["item_key", "source"]], on="item_key")
        matches.append(m.assign(adviser=adviser))
        row = {"family": fam, f"items_{adviser}": len(m), f"key_items_{adviser}": int((m.source == "key").sum()),
               f"agree_{adviser}": m.match.mean() if len(m) else np.nan}
        for c in CATS:
            mc = m[m.category == c]
            row[f"n_{adviser}_{c}"] = len(mc)
            row[f"agree_{adviser}_{c}"] = mc.match.mean() if len(mc) else np.nan
        rows.append(row)
    table = pd.DataFrame(rows).set_index("family")
    table[f"panel_{adviser}"] = table.index.isin(members)
    return table, validation, pd.concat(matches, ignore_index=True)


def management_rates(fv: pd.DataFrame) -> pd.DataFrame:
    """Step 1 of scoring, independent of any proxy adviser: how often the family
    votes with the board across every priority item whose board position is
    known (company proposals, US shareholder proposals, dissident nominees)."""
    d = fv[fv.board_rec.isin(["FOR", "AGAINST"])].assign(with_board=lambda x: x.direction == x.board_rec)
    g = d.groupby("family")
    out = pd.DataFrame({"mgmt_items": g.size(), "mgmt_rate": g.with_board.mean()})
    for c in CATS:
        dc = d[d.category == c].groupby("family")
        out[f"mgmt_n_{c}"] = dc.size()
        out[f"mgmt_rate_{c}"] = dc.with_board.mean()
    return out


def pick_adviser(row: pd.Series, sai_adviser, advisers: list[str], min_items: int) -> tuple[str, str]:
    """Adviser to score the family against, and why."""
    named = SAI_TO_ADVISER.get(sai_adviser)
    if named in advisers:
        return named, f"SAI names {ADVISER_NAMES[named]}"
    why = "SAI names both" if sai_adviser == "both" else "SAI names neither"
    ok = [a for a in advisers if row[f"items_{a}"] >= min_items]
    if ok:
        best = max(ok, key=lambda a: row[f"agree_{a}"])
        return best, f"{why}; higher agreement"
    best = max(advisers, key=lambda a: row[f"items_{a}"])
    return best, f"{why}; more items"


def classify_family(row: pd.Series, s: dict) -> str:
    # 1. Management first, independent of advisers.
    if pd.isna(row["mgmt_items"]) or row["mgmt_items"] < s["mgmt_min_items"]:
        return "Insufficient data"
    if row["mgmt_rate"] >= s["mgmt_follower"]:
        return "Management follower"
    # 2. Adviser following, on items where the adviser opposed the board.
    a = row["scored_adviser"]
    if row[f"items_{a}"] < s["min_items"]:
        return "Insufficient adviser data"
    if row["adviser_agreement"] >= s["adviser_follower"]:
        return f"{ADVISER_NAMES[a]} follower"
    rates = [row[f"agree_{a}_{c}"] for c in CATS
             if pd.notna(row[f"agree_{a}_{c}"]) and row[f"n_{a}_{c}"] >= s["min_category_items"]]
    if any(r >= s["partial_category"] for r in rates) and any(r < s["partial_category"] for r in rates):
        return "Partial robo-voter"
    return "Judgment voter"


def score(votes: pd.DataFrame, pri: pd.DataFrame, keys: dict[str, pd.DataFrame], sai: pd.DataFrame,
          cfg: dict, log=print):
    s = cfg["scoring"]
    fv = family_votes(pri)
    mgmt = management_rates(fv)
    log(f"Management rate: {int((mgmt.mgmt_rate >= s['mgmt_follower']).sum())} of {len(mgmt)} families "
        f">= {s['mgmt_follower']:.0%} with the board")
    advisers = list(keys)
    tables, validation, matches = [], {}, []
    for adv, key in keys.items():
        t, v, m = score_adviser(fv, key, cfg, adv, log)
        tables.append(t)
        validation[adv] = v
        matches.append(m)
    sc = pd.concat([mgmt] + tables, axis=1)
    allm = pd.concat(matches, ignore_index=True)

    sai_adv = sai.set_index("family").advisor.reindex(sc.index)
    picked = [pick_adviser(sc.loc[f], sai_adv.get(f), advisers, s["min_items"]) for f in sc.index]
    sc["scored_adviser"] = [p[0] for p in picked]
    sc["scored_adviser_reason"] = [p[1] for p in picked]
    sc["adviser_agreement"] = [sc.loc[f, f"agree_{a}"] for f, a in zip(sc.index, sc.scored_adviser)]

    # Series divergence over all priority items where >=2 series voted.
    multi = fv[fv.n_series >= 2]
    sc["series_divergence"] = multi.groupby("family").split.mean()
    sc["n_series"] = pri.groupby("family").series_id.nunique()
    # Workload: distinct meetings voted in the Jul 2025 - Jun 2026 year, all categories.
    sc["workload_meetings"] = votes.drop_duplicates(["family", "cusip", "meeting_dt"]).groupby("family").size()
    sc["segment"] = sc.apply(classify_family, axis=1, s=s)
    allm.to_parquet(PROCESSED / "family_item_matches.parquet", index=False)
    return sc.reset_index(), validation, fv
