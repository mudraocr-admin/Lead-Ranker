"""Step 5: family scoring.

Unit of analysis is the fund family (the prospect). A family's vote on an
item is the majority direction across its series; series that split are
tracked separately as "series divergence".

Management following is measured first, independent of any adviser. Adviser
following is measured against each adviser's recommendations (quoted, plus
imputed in impute.py) on the items where the adviser opposed the board. Each
family is scored against whichever adviser it agrees with more there.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import PROCESSED

CATS = ["shareholder_proposal", "contested_election", "mna", "pay"]
ADVISER_NAMES = {"ISS": "ISS", "GL": "Glass Lewis"}


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
    # Share of the family's series whose vote the filer flagged "for management".
    wm = (d[d.with_mgmt.notna()].drop_duplicates(["family", "series_id", "item_key"])
          .assign(with_mgmt=lambda x: x.with_mgmt.astype(float)))
    fv["with_mgmt"] = wm.groupby(["family", "item_key"]).with_mgmt.mean()
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


def score_adviser(fv: pd.DataFrame, recs: pd.DataFrame, adviser: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-family agreement with one adviser, overall, where it opposed or backed
    the board, and by category."""
    m = agreement(fv, recs).merge(recs[["item_key", "source"]], on="item_key")
    m["opposed_board"] = m.rec != m.board_rec
    rows = []
    for fam, mf in m.groupby("family"):
        row = {"family": fam, f"items_{adviser}": len(mf), f"key_items_{adviser}": int((mf.source == "key").sum()),
               f"agree_{adviser}": mf.match.mean()}
        for flag, name in ((True, "opposed"), (False, "aligned")):
            mo = mf[mf.opposed_board == flag]
            row[f"n_{adviser}_{name}"] = len(mo)
            row[f"agree_{adviser}_{name}"] = mo.match.mean() if len(mo) else np.nan
        for c in CATS:
            mc = mf[mf.category == c]
            row[f"n_{adviser}_{c}"] = len(mc)
            row[f"agree_{adviser}_{c}"] = mc.match.mean() if len(mc) else np.nan
            mo = mc[mc.opposed_board]
            row[f"n_{adviser}_{c}_opposed"] = len(mo)
            row[f"agree_{adviser}_{c}_opposed"] = mo.match.mean() if len(mo) else np.nan
        rows.append(row)
    table = pd.DataFrame(rows).set_index("family").reindex(sorted(fv.family.unique()))
    for col in table.columns:
        if col.startswith(("n_", "items_", "key_items_")):
            table[col] = table[col].fillna(0).astype(int)
    return table, m.assign(adviser=adviser)


def management_rates(fv: pd.DataFrame) -> pd.DataFrame:
    """Step 1 of scoring, independent of any proxy adviser: how often the family
    votes with management across every priority item, read directly from the
    N-PX for/against-management flag. A family whose series split on an item
    counts the with-management share of its series."""
    d = fv[fv.with_mgmt.notna()].assign(with_board=lambda x: x.with_mgmt)
    g = d.groupby("family")
    out = pd.DataFrame({"mgmt_items": g.size(), "mgmt_rate": g.with_board.mean()})
    for c in CATS:
        dc = d[d.category == c].groupby("family")
        out[f"mgmt_n_{c}"] = dc.size()
        out[f"mgmt_rate_{c}"] = dc.with_board.mean()
    return out


def pick_adviser(row: pd.Series, advisers: list[str], min_items: int) -> tuple[str, str]:
    """Adviser to score the family against, and why: the one it agrees with
    more where that adviser opposed the board (see classify_family)."""
    ok = [a for a in advisers if row[f"n_{a}_opposed"] >= min_items]
    if ok:
        best = max(ok, key=lambda a: row[f"agree_{a}_opposed"])
        return best, "higher agreement where it opposed the board"
    best = max(advisers, key=lambda a: row[f"n_{a}_opposed"])
    return best, "more board-opposed items"


def wilson(k: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval for k agreements out of n."""
    p = k / n
    mid, half = p + z * z / (2 * n), z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (mid - half) / (1 + z * z / n), (mid + half) / (1 + z * z / n)


def classify_family(row: pd.Series, s: dict) -> tuple[str, str]:
    """(segment, basis). `basis` is empty for a full-data call; otherwise it says
    what thin-data rule produced the label, and the label is low confidence.
    Every family gets a segment: there is no "insufficient data" outcome."""
    # 1. Management first, independent of advisers.
    basis = ""
    if row["mgmt_items"] < s["mgmt_min_items"]:
        basis = f"thin management data ({int(row['mgmt_items'])} items)"
    if row["mgmt_rate"] >= s["mgmt_follower"]:
        return "Management follower", basis
    # 2. Adviser following, judged only where the adviser OPPOSED the board.
    #    Where adviser and board agree, any management-leaning family also
    #    "agrees with the adviser", and those items outnumber the opposed ones
    #    ~7 to 1 once panel-inferred recs are in, so they would swamp the test.
    a = row["scored_adviser"]
    n = int(row[f"n_{a}_opposed"])
    if n < s["min_items"]:
        return thin_adviser_call(row, a, n, s)
    if row["adviser_agreement"] >= s["adviser_follower"]:
        return f"{ADVISER_NAMES[a]} follower", basis
    rates = [row[f"agree_{a}_{c}_opposed"] for c in CATS
             if pd.notna(row[f"agree_{a}_{c}_opposed"]) and row[f"n_{a}_{c}_opposed"] >= s["min_category_items"]]
    if any(r >= s["partial_category"] for r in rates) and any(r < s["partial_category"] for r in rates):
        return "Partial robo-voter", basis
    return "Judgment voter", basis


def thin_adviser_call(row: pd.Series, a: str, n: int, s: dict) -> tuple[str, str]:
    """Fewer adviser items than min_items. With enough items to test, rule out
    following statistically; otherwise there is no evidence of following."""
    name = ADVISER_NAMES[a]
    if n >= s["thin_min_items"] and pd.notna(row["adviser_agreement"]):
        k = round(row["adviser_agreement"] * n)
        up = wilson(k, n)[1]
        if up < s["adviser_follower"]:
            return "Judgment voter", (f"thin adviser data: {k}/{n} with {name} where it opposed the board; "
                                      f"95% upper bound {up:.0%} rules out following")
    return "Judgment voter", f"thin adviser data ({n} board-opposed items): following not shown"


def score(votes: pd.DataFrame, pri: pd.DataFrame, recs: dict[str, pd.DataFrame], cfg: dict, log=print):
    s = cfg["scoring"]
    fv = family_votes(pri)
    mgmt = management_rates(fv)
    log(f"Management rate: {int((mgmt.mgmt_rate >= s['mgmt_follower']).sum())} of {len(mgmt)} families "
        f">= {s['mgmt_follower']:.0%} with the board")
    advisers = list(recs)
    tables, matches = [], []
    for adv, r in recs.items():
        t, m = score_adviser(fv, r, adv)
        tables.append(t)
        matches.append(m)
    sc = pd.concat([mgmt] + tables, axis=1)
    allm = pd.concat(matches, ignore_index=True)

    picked = [pick_adviser(sc.loc[f], advisers, s["min_items"]) for f in sc.index]
    sc["scored_adviser"] = [p[0] for p in picked]
    sc["scored_adviser_reason"] = [p[1] for p in picked]
    # Agreement where the scored adviser opposed the board: the follower test.
    sc["adviser_agreement"] = [sc.loc[f, f"agree_{a}_opposed"] for f, a in zip(sc.index, sc.scored_adviser)]

    # Series divergence over all priority items where >=2 series voted.
    multi = fv[fv.n_series >= 2]
    sc["series_divergence"] = multi.groupby("family").split.mean()
    sc["n_series"] = pri.groupby("family").series_id.nunique()
    # Workload: distinct meetings voted in the Jul 2025 - Jun 2026 year, all categories.
    sc["workload_meetings"] = votes.drop_duplicates(["family", "cusip", "meeting_dt"]).groupby("family").size()
    seg = sc.apply(classify_family, axis=1, s=s, result_type="expand")
    sc["segment"], sc["segment_basis"] = seg[0], seg[1]
    allm.to_parquet(PROCESSED / "family_item_matches.parquet", index=False)
    return sc.reset_index()
