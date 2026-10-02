"""Step 4: impute ISS and Glass Lewis recommendations from fund votes.

Method: Zytnick (2025), "Imputing Proxy Advisor Recommendations", Journal of
Empirical Legal Studies 22:525, section 4 and appendix B.

Each reference fund filer j has follow rates W[j, situation, rec]: how often it
votes the way the adviser recommends, in a given situation. For each ballot item,
Bayes' theorem turns the filers' votes into the chance the adviser said FOR:

    Pr(For | votes) = Pr(votes | For) g / (Pr(votes | For) g + Pr(votes | Against) (1 - g))
    Pr(votes | rec) = prod_j  W[j, rec] if j voted rec, else 1 - W[j, rec]

where g is the share of items in that board position the adviser supports, and
votes are taken as independent given the recommendation and the situation.

The answer keys (recommendations quoted in soliciting material) are the seed,
in place of the paper's Insightia follow rates. Rounds, as in the paper:

  two_thirds  close followers (>=96% agreement with the seed): impute "against the
              board" if >=1/3 of them voted that way, else "with the board" (sec. 4.1)
  two_cat     Bayes; follow rates split by the board's position and the adviser's rec (4.2)
  three_cat   also split by whether the other adviser agrees (4.2, then 4.3)
  category    also split by ballot category, coarser where a cell is thin (4.3)

Follow rates are capped by how many votes support them, and filers that vote
FOR more often when the adviser says AGAINST are dropped (appendix B). A
posterior between 10% and 90% leaves the item unimputed (4.4). Quoted
recommendations always override imputed ones.

Adaptations: the board's position (from the N-PX for/against-management flags)
stands in for the paper's sponsor split, since "adviser agrees with management"
is what both condition on; voters are fund filers (N-PX registrants), the
closest thing N-PX has to the paper's institutions; the categories are the four
priority categories.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import PROCESSED

ROUNDS = ["two_thirds", "three_cat", "three_cat", "three_cat", "category"]
CATS = ["shareholder_proposal", "contested_election", "mna", "pay"]
ANY_OTHER, ANY_CAT = 2, len(CATS)


def voter_votes(uni: pd.DataFrame) -> pd.DataFrame:
    """One FOR(1)/AGAINST(0) vote per (filer, item_key); a filer whose rows
    split on one item (several descriptions mapped to it) is dropped there."""
    g = uni.groupby(["filer_cik", "item_key"], observed=True).vote_cast.agg(["first", "nunique"])
    g = g[g["nunique"] == 1]
    return pd.DataFrame({"voter": g.index.get_level_values(0).astype(str),
                         "item_key": g.index.get_level_values(1),
                         "v": (g["first"].astype(str) == "FOR").astype(np.int8).values})


def cap(n: np.ndarray) -> np.ndarray:
    """Appendix B: the most extreme follow rate n votes can support
    (0.9 at 18 votes, 0.99 at 60, 0.999 from 550)."""
    with np.errstate(divide="ignore"):
        c = 1 - 1 / np.power(0.1855 * np.maximum(n, 1), 1.90955)
    return np.clip(c, 0.5, 0.999)  # floor 0.5: below ~6 votes the formula goes negative


class Imputer:
    """Votes and items encoded once as integer arrays; each round is a few bincounts."""

    def __init__(self, votes: pd.DataFrame, items: pd.DataFrame, cfg: dict):
        self.cfg = cfg["impute"]
        self.items = items.reset_index(drop=True)
        pos = pd.Series(np.arange(len(self.items)), index=self.items.item_key)
        votes = votes[votes.item_key.isin(pos.index)]
        self.item = pos.reindex(votes.item_key).values
        self.voter_codes, voters = pd.factorize(votes.voter)
        self.n_voters = len(voters)
        self.v = votes.v.values.astype(np.int64)
        self.k = (self.items.board_rec.values == "FOR").astype(np.int64)  # 1 = board supports
        self.cat = pd.Categorical(self.items.category, categories=CATS).codes.astype(np.int64)
        self.kv, self.cv = self.k[self.item], self.cat[self.item]

    # ------------------------------------------------------------- rounds
    def two_thirds(self, label: np.ndarray) -> np.ndarray:
        c = self.cfg
        lv = label[self.item]
        m = ~np.isnan(lv)
        cell = self.voter_codes * 2 + self.kv
        size = self.n_voters * 2
        n = np.bincount(cell[m], minlength=size)
        agree = np.bincount(cell[m], weights=(self.v[m] == lv[m]), minlength=size)
        # The seed is ~100 quoted recs, not the paper's Insightia rates, so a filer
        # could clear 96% on a few board-backed items by voting with management.
        # It must also have followed the adviser where it opposed the board.
        o = m & (lv != self.kv)
        n_opp = np.bincount(cell[o], minlength=size)
        agree_opp = np.bincount(cell[o], weights=(self.v[o] == lv[o]), minlength=size)
        close = ((n >= c["close_min_items"]) & (agree >= c["close_follower"] * np.maximum(n, 1))
                 & (n_opp >= c["close_min_opposed"]) & (agree_opp >= c["close_follower"] * np.maximum(n_opp, 1)))
        f = close[cell]
        against = (self.v != self.kv).astype(float)
        nf = np.bincount(self.item[f], minlength=len(self.k))
        share = np.bincount(self.item[f], weights=against[f], minlength=len(self.k)) / np.maximum(nf, 1)
        rec = np.where(share >= 1 / 3, 1 - self.k, self.k).astype(float)
        return np.where(nf >= 2, rec, np.nan)

    def _key(self, voter, k, rec, oth, cat):
        return (((voter * 2 + k) * 2 + rec) * 3 + oth) * (ANY_CAT + 1) + cat

    def bayes(self, label: np.ndarray, other: np.ndarray | None, mode: str) -> tuple[np.ndarray, np.ndarray]:
        """Posterior Pr(FOR) per item and the number of informative votes behind it."""
        c, size = self.cfg, self.n_voters * 2 * 2 * 3 * (ANY_CAT + 1)
        other = np.full(len(self.k), np.nan) if other is None else other
        lv, ov = label[self.item], other[self.item]

        # Follow-rate cells from labelled items, at every level of detail at once.
        m = ~np.isnan(lv)
        rec = lv[m].astype(np.int64)
        agree = (self.v[m] == rec).astype(float)
        o = np.where(np.isnan(ov[m]), -1, (ov[m] == rec)).astype(np.int64)
        n, a = np.zeros(size), np.zeros(size)
        for oth in ("actual", "any"):
            for cat in ("actual", "any"):
                ok = o >= 0 if oth == "actual" else np.ones(len(o), bool)
                key = self._key(self.voter_codes[m], self.kv[m], rec,
                                o if oth == "actual" else ANY_OTHER,
                                self.cv[m] if cat == "actual" else ANY_CAT)[ok]
                n += np.bincount(key, minlength=size)
                a += np.bincount(key, weights=agree[ok], minlength=size)
        w = np.where(n > 0, a / np.maximum(n, 1), 0.5)
        w = np.clip(w, 1 - cap(np.inf), cap(n))  # a ceiling, as in the paper; the floor only avoids log(0)

        # Appendix B: drop filers whose votes run against the adviser (W_for + W_against < 1).
        base = lambda r: self._key(self.voter_codes, self.kv, r, ANY_OTHER, ANY_CAT)  # noqa: E731
        b1, b0 = base(1), base(0)
        usable = (n[b1] >= c["min_cell_votes"]) & (n[b0] >= c["min_cell_votes"]) & (w[b1] + w[b0] >= 1)

        # Finest level of detail with enough votes under BOTH hypotheses. Mixing
        # levels (a fine rate for one, a coarse one for the other) makes, say, an
        # ISS follower look like strong evidence about Glass Lewis.
        levels = {"two_cat": [(False, False)],
                  "three_cat": [(True, False), (False, False)],
                  "category": [(True, True), (True, False), (False, True), (False, False)]}[mode]
        known = ~np.isnan(ov)
        w1, w0 = np.full(len(self.v), np.nan), np.full(len(self.v), np.nan)
        for use_oth, use_cat in levels:
            cat = self.cv if use_cat else ANY_CAT
            k1 = self._key(self.voter_codes, self.kv, 1, np.where(use_oth, (ov == 1), ANY_OTHER), cat)
            k0 = self._key(self.voter_codes, self.kv, 0, np.where(use_oth, (ov == 0), ANY_OTHER), cat)
            hit = (np.isnan(w1) & (known if use_oth else True)
                   & (n[k1] >= c["min_cell_votes"]) & (n[k0] >= c["min_cell_votes"]))
            w1[hit], w0[hit] = w[k1][hit], w[k0][hit]

        use = usable & ~np.isnan(w1) & ~np.isnan(w0)
        llr = np.where(self.v == 1, np.log(w1) - np.log1p(-w0), np.log1p(-w1) - np.log(w0))
        total = np.bincount(self.item[use], weights=llr[use], minlength=len(self.k))
        n_inf = np.bincount(self.item[use], minlength=len(self.k))

        # Prior g_k: share of labelled items in each board position the adviser supports.
        g = np.array([np.nanmean(np.where(self.k == kk, label, np.nan)) for kk in (0, 1)])
        g = np.clip(np.nan_to_num(g, nan=0.5), 0.01, 0.99)
        logit = np.log(g[self.k] / (1 - g[self.k])) + total
        p = 1 / (1 + np.exp(-np.clip(logit, -50, 50)))
        return np.where(n_inf > 0, p, np.nan), n_inf

    def run(self, seeds: dict[str, np.ndarray]) -> dict[str, pd.DataFrame]:
        """All rounds for every adviser together (the three-category split needs
        the other adviser's current labels). Seeds hold 1/0 on quoted items."""
        c = self.cfg
        labels = {a: s.copy() for a, s in seeds.items()}
        probs = {a: np.full(len(self.k), np.nan) for a in seeds}
        n_inf = {a: np.zeros(len(self.k), int) for a in seeds}
        for mode in ROUNDS:
            new = {}
            for a, seed in seeds.items():
                other = next((labels[b] for b in seeds if b != a), None) if mode in ("three_cat", "category") else None
                if mode == "two_thirds":
                    lab = self.two_thirds(seed)
                else:
                    probs[a], n_inf[a] = self.bayes(labels[a], other, mode)
                    lab = np.where(probs[a] >= c["threshold"], 1.0,
                                   np.where(probs[a] <= 1 - c["threshold"], 0.0, np.nan))
                new[a] = np.where(np.isnan(seed), lab, seed)  # quoted recs override
            labels = new
        return {a: pd.DataFrame({"item_key": self.items.item_key, "p_for": probs[a], "n_voters": n_inf[a],
                                 "imputed": labels[a]}) for a in seeds}


def seed_array(items: pd.DataFrame, key: pd.DataFrame, hide: set = frozenset()) -> np.ndarray:
    k = key[~key.item_key.isin(hide)]
    rec = pd.Series((k.rec == "FOR").astype(float).values, index=k.item_key)
    return rec.reindex(items.item_key).values


def audit_quotes(votes: pd.DataFrame, keys: dict[str, pd.DataFrame], cfg: dict, log=print) -> dict[str, pd.DataFrame]:
    """Drop quoted recs that the adviser's own strict followers contradict.

    A quote is what the company said before the meeting. Advisers revise calls
    after a company amends its plan (Delek and Medifast say so), and a quote can
    be joined to the wrong ballot row (a generic "elect eleven directors" line).
    Strict followers are filers that matched the adviser on >= 80% of >= 15
    *other* board-opposed quotes. If >= 8 of them voted on the item and >= 75%
    voted against the quote, the quote is set aside: not a seed, not scored, not
    part of validation. This check uses no imputed recs. Written to
    data/processed/quote_audit.csv."""
    a_cfg = cfg["impute"]["audit"]
    out, rows = {}, []
    for adv, key in keys.items():
        x = votes.merge(key[["item_key", "rec", "opposed_board"]], on="item_key")
        x["match"] = x.v == (x.rec == "FOR")
        opp = x[x.opposed_board.astype(bool)]
        tot = opp.groupby("voter").match.agg(["sum", "size"])
        # Leave the item itself out of each voter's record.
        x = x.join(tot, on="voter")
        own = x.opposed_board.astype(bool)
        n_other = x["size"].fillna(0) - own
        k_other = x["sum"].fillna(0) - (own & x.match)
        x["strict"] = (n_other >= a_cfg["min_quotes"]) & (k_other >= a_cfg["follower"] * n_other)
        s = x[x.strict].groupby("item_key").match.agg(["size", "mean"]).rename(
            columns={"size": "strict_followers", "mean": "strict_agree"})
        k = key.join(s, on="item_key")
        bad = (k.strict_followers >= a_cfg["min_followers"]) & (k.strict_agree <= 1 - a_cfg["contradict"])
        rows.append(k[bad].assign(adviser=adv))
        out[adv] = k[~bad].drop(columns=["strict_followers", "strict_agree"]).reset_index(drop=True)
        log(f"{adv} quote audit: {int(bad.sum())} of {len(k)} quotes contradicted by the adviser's strict followers "
            f"({int((bad & k.opposed_board.astype(bool)).sum())} of them board-opposed); set aside")
    pd.concat(rows, ignore_index=True)[["adviser", "issuer", "meeting_dt", "item_text", "rec", "opposed_board",
                                        "strict_followers", "strict_agree", "evidence", "item_key"]].to_csv(
        PROCESSED / "quote_audit.csv", index=False)
    return out


def validate(imp: Imputer, keys: dict[str, pd.DataFrame], cfg: dict, log=print) -> dict:
    """k-fold: hide a fold of each adviser's quoted recs, run every round, and
    score the imputations on the hidden items. Board-opposed items are reported
    separately: they are the ones the follower test uses."""
    c = cfg["impute"]
    rng = np.random.default_rng(c["seed"])
    folds = {a: rng.permutation(len(k)) % c["folds"] for a, k in keys.items()}
    rows = []
    for f in range(c["folds"]):
        hide = {a: set(k.item_key[folds[a] == f]) for a, k in keys.items()}
        out = imp.run({a: seed_array(imp.items, k, hide[a]) for a, k in keys.items()})
        for a, k in keys.items():
            h = k[k.item_key.isin(hide[a])][["item_key", "rec", "category", "opposed_board"]]
            rows.append(h.merge(out[a], on="item_key", how="left").assign(adviser=a, fold=f))
    m = pd.concat(rows, ignore_index=True)
    m["covered"] = m.imputed.notna()
    m["correct"] = m.covered & (m.imputed == (m.rec == "FOR"))
    m.to_csv(PROCESSED / "impute_validation.csv", index=False)
    res = {}
    for a, g in m.groupby("adviser"):
        opp = g[g.opposed_board.astype(bool)]
        r = {"held_out": len(g), "covered": int(g.covered.sum()), "coverage": g.covered.mean(),
             "accuracy": g.correct.sum() / max(g.covered.sum(), 1),
             "opposed_held_out": len(opp), "opposed_coverage": opp.covered.mean(),
             "opposed_accuracy": opp.correct.sum() / max(opp.covered.sum(), 1) if opp.covered.any() else None}
        r["passed"] = bool(r["opposed_accuracy"] is not None and r["opposed_accuracy"] >= c["gate_opposed_accuracy"])
        res[a] = {k: (round(float(v), 4) if isinstance(v, (float, np.floating)) else v) for k, v in r.items()}
        log(f"{a} imputation, {c['folds']}-fold hold-out of quoted recs: coverage {r['coverage']:.0%}, "
            f"accuracy {r['accuracy']:.1%}; board-opposed: coverage {r['opposed_coverage']:.0%}, accuracy "
            f"{(r['opposed_accuracy'] or 0):.1%} -> {'PASSED' if r['passed'] else 'FAILED, quoted recs only'}")
    return res


def impute_recs(uni: pd.DataFrame, pri: pd.DataFrame, keys: dict[str, pd.DataFrame], cfg: dict,
                log=print) -> tuple[dict[str, pd.DataFrame], dict]:
    """Recommendations per adviser: quoted where known, imputed elsewhere when the
    hold-out passes. Columns: item_key, rec, source ('key' | 'imputed'), p_for, n_voters."""
    items = (pri.dropna(subset=["board_rec"]).drop_duplicates("item_key")
             [["item_key", "board_rec", "priority_category"]].rename(columns={"priority_category": "category"}))
    votes = voter_votes(uni)
    imp = Imputer(votes, items, cfg)
    log(f"Imputation: {len(imp.v):,} filer votes from {imp.n_voters:,} reference filers on "
        f"{len(np.unique(imp.item)):,} of {len(items):,} target ballot items")
    audited = audit_quotes(votes, keys, cfg, log)
    validation = validate(imp, audited, cfg, log)
    out = imp.run({a: seed_array(items, k) for a, k in audited.items()})
    for a, r in out.items():
        # Raw check against every quote: set-aside quotes were never seeds, so the
        # final run is out of sample for them.
        dropped = keys[a][~keys[a].item_key.isin(audited[a].item_key)]
        d = dropped[["item_key", "rec"]].merge(r, on="item_key")
        ok_raw = int(round(validation[a]["accuracy"] * validation[a]["covered"])) + int(
            (d.imputed == (d.rec == "FOR")).sum())
        cov_raw = validation[a]["covered"] + int(d.imputed.notna().sum())
        validation[a].update({"quotes_set_aside": len(dropped), "accuracy_vs_all_quotes": round(ok_raw / max(cov_raw, 1), 4)})
    keys.update(audited)  # set-aside quotes are not scored either
    recs = {}
    for a, r in out.items():
        r = r.merge(items[["item_key", "board_rec"]], on="item_key")
        r["source"] = np.where(r.item_key.isin(keys[a].item_key), "key", "imputed")
        if not validation[a]["passed"]:
            r = r[r.source == "key"]
        r = r[r.imputed.notna()].assign(rec=lambda d: np.where(d.imputed == 1, "FOR", "AGAINST"))
        opp = r.rec != r.board_rec
        log(f"{a}: {len(r):,} items with a rec ({int((r.source == 'key').sum())} quoted, "
            f"{int((r.source == 'imputed').sum()):,} imputed); {int(opp.sum()):,} oppose the board")
        r.to_parquet(PROCESSED / f"{a.lower()}_recs.parquet", index=False)
        recs[a] = r[["item_key", "rec", "source", "p_for", "n_voters"]].reset_index(drop=True)
    return recs, validation
