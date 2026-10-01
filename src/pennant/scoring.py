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


def follower_panel(fv: pd.DataFrame, key: pd.DataFrame, cfg: dict, exclude: set = frozenset()) -> pd.DataFrame:
    """Families that track the adviser closely. Management followers are never
    panel members: on board-aligned items they would look like adviser followers."""
    p = cfg["panel"]
    a = agreement(fv, key).groupby("family").agg(items=("match", "size"), rate=("match", "mean"))
    a["panel"] = ((a["items"] >= p["follower_min_items"]) & (a["rate"] >= p["follower_agreement"])
                  & ~a.index.isin(list(exclude)))
    return a


def infer_recs(fv: pd.DataFrame, panel: list[str], cfg: dict, exclude: str | None = None,
               items: pd.Index | None = None, disagreement_only: bool = False) -> pd.DataFrame:
    """Infer the adviser's rec on items from panel families' votes.

    Rule: >= infer_min_panel_votes panel families voted and >= infer_min_agreement
    of them voted the same way. `exclude` drops the scored family from its own
    panel. With `disagreement_only`, keep only items where the inferred rec
    opposes the board."""
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


def validate(fv: pd.DataFrame, key: pd.DataFrame, cfg: dict, adviser: str, log=print,
             exclude: set = frozenset()) -> dict:
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
    panel = follower_panel(fv, train, cfg, exclude)
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


# ------------------------------------------------- reference-universe panel
def fund_votes(uni: pd.DataFrame) -> pd.DataFrame:
    """Reference funds' votes, one row per (fund, item_key), in the same shape as
    family_votes so infer_recs can use them (the `family` column is the fund)."""
    d = uni.assign(direction=uni.vote_cast.map(_direction)).dropna(subset=["direction"])
    d = d[d.direction.isin(["FOR", "AGAINST"])]
    g = d.groupby(["series_id", "item_key"]).direction
    return (g.agg(lambda s: s.value_counts().idxmax()).rename("direction").reset_index()
            .rename(columns={"series_id": "family"}))


def fund_panel(ufv: pd.DataFrame, key: pd.DataFrame, cfg: dict) -> list[str]:
    """Reference funds that track the adviser, including where it opposed the
    board, so a fund that simply votes with management cannot qualify."""
    p = cfg["universe"]["panel"]
    m = ufv.merge(key[["item_key", "rec", "opposed_board"]], on="item_key")
    m["match"] = m.direction == m.rec
    m["opp_match"] = m.match.where(m.opposed_board)
    a = m.groupby("family").agg(n=("match", "size"), rate=("match", "mean"),
                                n_opp=("opposed_board", "sum"), opp_rate=("opp_match", "mean"))
    ok = ((a.n >= p["min_items"]) & (a.rate >= p["agreement"])
          & (a.n_opp >= p["min_opposed"]) & (a.opp_rate >= p["opposed_agreement"]))
    return a.index[ok].tolist()


def validate_universe(ufv: pd.DataFrame, fv: pd.DataFrame, key: pd.DataFrame, cfg: dict, adviser: str,
                      log=print) -> dict:
    """Repeated hold-outs: drop a share of key items, rebuild the fund panel
    without them, infer them back. Scored on board-opposed items separately,
    since those are the calls that separate followers from everyone else."""
    u = cfg["universe"]
    board = fv.drop_duplicates("item_key").set_index("item_key").board_rec
    rows = []
    for r in range(u["holdout_rounds"]):
        hold = key.sample(frac=u["holdout_share"], random_state=r)
        members = fund_panel(ufv, key[~key.item_key.isin(hold.item_key)], cfg)
        inf = infer_recs(ufv, members, cfg, items=pd.Index(hold.item_key))
        m = hold.merge(inf[["item_key", "rec"]], on="item_key", how="left", suffixes=("", "_inf"))
        m["round"], m["panel"] = r, len(members)
        rows.append(m)
    m = pd.concat(rows, ignore_index=True)
    cov, ok = m.rec_inf.notna(), m.rec == m.rec_inf
    opp = m.opposed_board.astype(bool)
    res = {"rounds": u["holdout_rounds"], "held_out": int(len(m)), "panel_size_avg": float(m.panel.mean()),
           "coverage": float(cov.mean()), "accuracy": float(ok[cov].mean()) if cov.any() else None,
           "opposed_held_out": int(opp.sum()), "opposed_coverage": float(cov[opp].mean()) if opp.any() else 0.0,
           "opposed_accuracy": float(ok[cov & opp].mean()) if (cov & opp).any() else None}
    g = u["gate"]
    res["passed"] = bool(res["opposed_accuracy"] is not None and res["opposed_accuracy"] >= g["opposed_accuracy"]
                         and res["opposed_coverage"] >= g["opposed_coverage"])
    m.to_csv(PROCESSED / f"validation_universe_{adviser.lower()}.csv", index=False)
    log(f"{adviser} universe panel hold-out: coverage {res['coverage']:.0%}, accuracy {res['accuracy']}, "
        f"board-opposed: coverage {res['opposed_coverage']:.0%}, accuracy {res['opposed_accuracy']} "
        f"-> {'PASSED, inferred recs used' if res['passed'] else 'FAILED, inferred recs not used'}")
    return res


def score_adviser(fv: pd.DataFrame, key: pd.DataFrame, cfg: dict, adviser: str, log=print,
                  exclude: set = frozenset(), ufv: pd.DataFrame | None = None):
    """Per-family agreement with one adviser (key + panel-inferred items).

    The panel comes from reference funds outside the target list when that panel
    passes its hold-out gate; otherwise from the target families themselves."""
    validation = {}
    if ufv is not None and len(ufv):
        umembers = fund_panel(ufv, key, cfg)
        log(f"{adviser} universe panel: {len(umembers)} reference funds follow {ADVISER_NAMES[adviser]}")
        validation["universe"] = {**validate_universe(ufv, fv, key, cfg, adviser, log), "panel_size": len(umembers)}
        if validation["universe"]["passed"]:
            return _score_with(fv, key, adviser, lambda fam: infer_recs(ufv, umembers, cfg), [], validation,
                               per_family=False)
    panel = follower_panel(fv, key, cfg, exclude)
    members = panel.index[panel.panel].tolist()
    log(f"{adviser} panel: {len(members)} follower families on the seed key: {members}")
    validation["headline"] = validate(fv, key, cfg, adviser, log, exclude)
    if not members:
        # Headline thresholds found no followers: show what seed-and-expand would
        # do under a relaxed panel. Reported separately, never used to score.
        relaxed = {**cfg, "panel": {**cfg["panel"], **cfg["panel_sensitivity"]}}
        rp = follower_panel(fv, key, relaxed, exclude)
        rmembers = rp.index[rp.panel].tolist()
        rinf = infer_recs(fv, rmembers, relaxed)
        rinf = rinf[~rinf.item_key.isin(key.item_key)]
        log(f"{adviser} sensitivity (panel >= {relaxed['panel']['follower_agreement']:.0%} on >= "
            f"{relaxed['panel']['follower_min_items']} items): panel {rmembers}, {len(rinf)} extra items inferred")
        validation.update({"sensitivity_panel": rmembers, "sensitivity_inferred_items": int(len(rinf)),
                           "sensitivity_validation": validate(fv, key, relaxed, adviser, log, exclude)})
        rinf.merge(fv.drop_duplicates("item_key")[["item_key", "issuer", "meeting_dt", "category", "item_text"]],
                   on="item_key").to_csv(PROCESSED / f"sensitivity_inferred_{adviser.lower()}.csv", index=False)

    return _score_with(fv, key, adviser, lambda fam: infer_recs(fv, members, cfg, exclude=fam), members, validation)


def _score_with(fv: pd.DataFrame, key: pd.DataFrame, adviser: str, infer, members: list[str], validation: dict,
                per_family: bool = True):
    """`infer(fam)` gives the inferred recs used for one family. A target-family
    panel must leave the family out (per_family); a reference-universe panel
    never contains it, so its recs are computed once."""
    key_recs = key[["item_key", "rec", "opposed_board"]].assign(source="key")
    rows, matches = [], []
    shared = None if per_family else infer(None)
    for fam in sorted(fv.family.unique()):
        inferred = infer(fam) if per_family else shared
        inferred = inferred[~inferred.item_key.isin(key.item_key)].assign(source="inferred")
        inferred["opposed_board"] = inferred.rec.values != fv.drop_duplicates("item_key").set_index(
            "item_key").board_rec.reindex(inferred.item_key).values
        recs = pd.concat([key_recs, inferred[["item_key", "rec", "opposed_board", "source"]]], ignore_index=True)
        m = agreement(fv[fv.family == fam], recs).merge(recs[["item_key", "source", "opposed_board"]],
                                                        on="item_key")
        matches.append(m.assign(adviser=adviser))
        row = {"family": fam, f"items_{adviser}": len(m), f"key_items_{adviser}": int((m.source == "key").sum()),
               f"agree_{adviser}": m.match.mean() if len(m) else np.nan}
        for flag, name in ((True, "opposed"), (False, "aligned")):
            mo = m[m.opposed_board == flag]
            row[f"n_{adviser}_{name}"] = len(mo)
            row[f"agree_{adviser}_{name}"] = mo.match.mean() if len(mo) else np.nan
        for c in CATS:
            mc = m[m.category == c]
            row[f"n_{adviser}_{c}"] = len(mc)
            row[f"agree_{adviser}_{c}"] = mc.match.mean() if len(mc) else np.nan
            mo = mc[mc.opposed_board == True]  # noqa: E712
            row[f"n_{adviser}_{c}_opposed"] = len(mo)
            row[f"agree_{adviser}_{c}_opposed"] = mo.match.mean() if len(mo) else np.nan
        rows.append(row)
    table = pd.DataFrame(rows).set_index("family")
    table[f"panel_{adviser}"] = table.index.isin(members)
    return table, validation, pd.concat(matches, ignore_index=True)


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


def pick_adviser(row: pd.Series, sai_adviser, advisers: list[str], min_items: int) -> tuple[str, str]:
    """Adviser to score the family against, and why. Comparisons use items
    where the adviser opposed the board (see classify_family)."""
    named = SAI_TO_ADVISER.get(sai_adviser)
    if named in advisers:
        return named, f"SAI names {ADVISER_NAMES[named]}"
    why = "SAI names both" if sai_adviser == "both" else "SAI names neither"
    ok = [a for a in advisers if row[f"n_{a}_opposed"] >= min_items]
    if ok:
        best = max(ok, key=lambda a: row[f"agree_{a}_opposed"])
        return best, f"{why}; higher agreement where it opposed the board"
    best = max(advisers, key=lambda a: row[f"n_{a}_opposed"])
    return best, f"{why}; more board-opposed items"


def wilson_upper(k: float, n: int, z: float = 1.96) -> float:
    """Upper end of the 95% Wilson interval for k agreements out of n."""
    p = k / n
    return (p + z * z / (2 * n) + z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


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
    following statistically; otherwise go by what the SAI says the adviser does."""
    name = ADVISER_NAMES[a]
    if n >= s["thin_min_items"] and pd.notna(row["adviser_agreement"]):
        k = round(row["adviser_agreement"] * n) if n else 0
        up = wilson_upper(k, n)
        if up < s["adviser_follower"]:
            return "Judgment voter", (f"thin adviser data: {k}/{n} with {name} where it opposed the board; "
                                      f"95% upper bound {up:.0%} rules out following")
    sai = row.get("sai_advisor")
    names_it = sai in (name, "both")
    if names_it and "recommendations" in str(row.get("sai_roles") or "") and row.get("sai_own_guidelines") is not True:
        return f"{name} follower", (f"thin adviser data ({n} board-opposed items); "
                                    f"SAI: uses {name} recommendations, no own guidelines")
    why = ("own guidelines" if row.get("sai_own_guidelines") is True
           else f"{name} not used for recommendations" if names_it else "no adviser named")
    return "Judgment voter", f"thin adviser data ({n} board-opposed items); SAI: {why}"


def score(votes: pd.DataFrame, pri: pd.DataFrame, keys: dict[str, pd.DataFrame], sai: pd.DataFrame,
          cfg: dict, log=print, uni: pd.DataFrame | None = None):
    s = cfg["scoring"]
    fv = family_votes(pri)
    ufv = fund_votes(uni) if uni is not None else None
    mgmt = management_rates(fv)
    log(f"Management rate: {int((mgmt.mgmt_rate >= s['mgmt_follower']).sum())} of {len(mgmt)} families "
        f">= {s['mgmt_follower']:.0%} with the board")
    advisers = list(keys)
    tables, validation, matches = [], {}, []
    mgmt_followers = set(mgmt.index[mgmt.mgmt_rate >= s["mgmt_follower"]])
    for adv, key in keys.items():
        t, v, m = score_adviser(fv, key, cfg, adv, log, exclude=mgmt_followers, ufv=ufv)
        tables.append(t)
        validation[adv] = v
        matches.append(m)
    sc = pd.concat([mgmt] + tables, axis=1)
    allm = pd.concat(matches, ignore_index=True)

    si = sai.set_index("family").reindex(sc.index)
    sai_adv = si.advisor
    sc["sai_advisor"], sc["sai_roles"], sc["sai_own_guidelines"] = si.advisor, si.roles, si.own_guidelines
    picked = [pick_adviser(sc.loc[f], sai_adv.get(f), advisers, s["min_items"]) for f in sc.index]
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
    sc = sc.drop(columns=["sai_advisor", "sai_roles", "sai_own_guidelines"])
    allm.to_parquet(PROCESSED / "family_item_matches.parquet", index=False)
    return sc.reset_index(), validation, fv
