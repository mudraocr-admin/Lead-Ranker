"""Step 2a: build an adviser "answer key" (ISS or Glass Lewis) from proxy soliciting material.

Companies and dissidents file supplemental proxy material (DEFA14A, DEFC14A,
DFAN14A) when a proxy adviser disagrees with them or backs them. For each
adviser we pull the sentences that name it next to a recommendation, have the
model turn each company's sentences into (item, adviser direction, board
direction), keep only items where the adviser opposed the board, and join them
to N-PX vote rows.
"""
from __future__ import annotations

import hashlib
import re
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field
from rapidfuzz import fuzz, process

from . import ai
from .config import PROCESSED
from .edgar import Edgar
from .items import director_name, norm_text
from .npx import _norm
from .text import clean, sentences

ADVISERS = {
    "ISS": {"name": "ISS", "pattern": re.compile(r"\bISS\b|Institutional Shareholder Services"),
            "task": "iss_items"},
    "GL": {"name": "Glass Lewis", "pattern": re.compile(r"Glass,? Lewis"), "task": "gl_items"},
}
REC = re.compile(r"recommend", re.I)

ItemType = Literal["contested_director", "uncontested_director", "say_on_pay", "equity_plan",
                   "other_compensation", "shareholder_proposal", "mna", "other"]
Rec = Literal["FOR", "AGAINST", "WITHHOLD", "UNCLEAR"]

TYPE_TO_CATEGORY = {
    "contested_director": "contested_election",
    "say_on_pay": "pay", "equity_plan": "pay", "other_compensation": "pay",
    "shareholder_proposal": "shareholder_proposal",
    "mna": "mna",
}


class AdviserItem(BaseModel):
    item_type: ItemType
    label: str = Field(description="Director full name, or a short description of the proposal")
    keywords: list[str] = Field(description="1-3 lowercase words likely to appear in the ballot "
                                            "item text, e.g. ['incentive plan'] or ['executive compensation']")
    adviser_rec: Rec
    board_rec: Rec
    opposed_board: bool
    evidence: str = Field(description="The sentence the call is based on, verbatim, <=300 chars")


class AdviserItems(BaseModel):
    company: str
    items: list[AdviserItem]


SYSTEM = """You read excerpts from SEC proxy soliciting materials (DEFA14A, DEFC14A, DFAN14A)
that mention the proxy adviser {adviser}. Extract every ballot item at the company's 2026
meeting for which the text states {adviser}'s recommendation. For each item give {adviser}'s
recommendation (adviser_rec) and the company board's recommendation. In a proxy contest,
dissident nominees are items too: the board recommends against/withhold on them.
`contested_director` is any director seat in a proxy contest (board or dissident nominee);
`uncontested_director` is an ordinary election. Mark opposed_board true only when the text
shows {adviser}'s recommendation differs from the board's. Ignore historical recommendations
from prior years, recommendations made only by other advisers, and generic statements.
Return an empty list if nothing qualifies."""


# ---------------------------------------------------------------- collect
def subject_of(hit: dict) -> tuple[str, str]:
    """(cik, name) of the company whose meeting it is. Issuers show a ticker
    in the FTS display name; dissident filers usually do not."""
    names = hit.get("display_names", [])
    ciks = hit.get("ciks", [])
    for cik, name in zip(ciks, names):
        if re.search(r"\([A-Z][A-Z0-9.\-, ]*\)\s+\(CIK", name):
            return cik, name
    return (ciks[0] if ciks else ""), (names[0] if names else "")


def collect_snippets(edgar: Edgar, cfg: dict, adviser: str, log=print) -> pd.DataFrame:
    c, a = cfg["adviser_key"], ADVISERS[adviser]
    hits: dict[str, dict] = {}
    for q in cfg["advisers"][adviser]["queries"]:
        for h in edgar.fts(q, c["forms"], c["search_from"], c["search_to"], max_hits=c["max_docs"]):
            if h["_id"].lower().endswith((".htm", ".html", ".txt")):
                hits[h["_id"]] = h
    log(f"{adviser} key: {len(hits)} soliciting documents from full-text search")
    rows = []
    for h in hits.values():
        cik, name = subject_of(h)
        text = clean(edgar.get_text(edgar.doc_url(h)))
        for s in sentences(text):
            if a["pattern"].search(s) and REC.search(s) and len(s) < 1500:
                rows.append({"doc_id": h["_id"], "form": h["form"], "file_date": h["file_date"],
                             "subject_cik": cik.lstrip("0"), "subject_name": name, "sentence": s})
    df = pd.DataFrame(rows).drop_duplicates(["subject_cik", "sentence"])
    df.to_parquet(PROCESSED / f"{adviser.lower()}_snippets.parquet", index=False)
    log(f"{adviser} key: {len(df)} {adviser}+recommend sentences across {df.subject_cik.nunique()} companies")
    return df


# ---------------------------------------------------------------- classify
PROPOSAL = re.compile(r"(say[- ]on[- ]pay|executive compensation|named executive officer compensation"
                      r"|(equity|incentive|stock|omnibus)[\w\s&-]{0,30}plan|merger|transaction|acquisition"
                      r"|shareholder proposal|stockholder proposal)", re.I)
AGAINST = re.compile(r"\b(against|withhold|unfavorable|negative)\b", re.I)
DISAGREE = re.compile(r"disagree|inconsistent with|flawed|does not reflect", re.I)


def rules_classify(company: str, sents: list[str]) -> AdviserItems:
    """Conservative fallback: only the unambiguous 'adviser recommends against
    <compensation/merger item>, and we disagree' pattern becomes an item."""
    items: dict[str, AdviserItem] = {}
    board_disagrees = bool(DISAGREE.search(" ".join(sents)))
    for s in sents:
        m = PROPOSAL.search(s)
        if not m or not AGAINST.search(s):
            continue
        what = m.group(0).lower()
        if "plan" in what:
            t, kw = "equity_plan", ["plan"]
        elif "merger" in what or "transaction" in what or "acquisition" in what:
            t, kw = "mna", ["merger"]
        elif "proposal" in what:
            continue  # direction of shareholder proposals is too ambiguous for regex
        else:
            t, kw = "say_on_pay", ["executive compensation", "named executive"]
        if t not in items and board_disagrees:
            items[t] = AdviserItem(item_type=t, label=what, keywords=kw, adviser_rec="AGAINST",
                                   board_rec="FOR", opposed_board=True, evidence=s[:300])
    return AdviserItems(company=company, items=list(items.values()))


def classify(snips: pd.DataFrame, cfg: dict, adviser: str, log=print) -> pd.DataFrame:
    a = ADVISERS[adviser]
    system = SYSTEM.format(adviser=a["name"])
    rows = []
    for cik, g in snips.groupby("subject_cik"):
        company = g.subject_name.iloc[0]
        sents = list(dict.fromkeys(g.sentence))
        digest = hashlib.sha1("\n".join(sorted(sents)).encode()).hexdigest()[:10]
        prompt = f"Company: {company}\n\nExcerpts:\n" + "\n".join(f"- {s}" for s in sents)
        ans, source = ai.ask(a["task"], f"{cik}", system, prompt, AdviserItems,
                             lambda: rules_classify(company, sents), cfg)
        for it in ans.items:
            rows.append({"subject_cik": cik, "company": company, "first_filed": g.file_date.min(),
                         "last_filed": g.file_date.max(), "ai_source": source, "snippet_hash": digest,
                         **it.model_dump()})
    items = pd.DataFrame(rows, columns=["subject_cik", "company", "first_filed", "last_filed", "ai_source",
                                        "snippet_hash", *AdviserItem.model_fields])
    items["adviser"] = adviser
    items["keywords"] = items.keywords.map(lambda k: "|".join(k))
    items["category"] = items.item_type.map(TYPE_TO_CATEGORY)
    items.to_parquet(PROCESSED / f"{adviser.lower()}_items_all.parquet", index=False)
    log(f"{adviser} key: {len(items)} classified items; sources {items.ai_source.value_counts().to_dict()}")
    return items


# -------------------------------------------------------------- join to N-PX
def _dir(v: str) -> str:
    v = (v or "").upper()
    return "AGAINST" if v in ("AGAINST", "WITHHOLD") else v


def resolve_cusips(items: pd.DataFrame, npx_all: pd.DataFrame, score=88) -> pd.DataFrame:
    """Soliciting material carries a CIK, N-PX carries a CUSIP: bridge them by issuer name."""
    issuers = npx_all.groupby("cusip").issuer.agg(lambda s: s.mode().iat[0]).reset_index()
    norm = issuers.issuer.map(_norm).tolist()
    out = {}
    for company in items.company.unique():
        clean_name = re.sub(r"\s*\(.*$", "", company)
        m = process.extractOne(_norm(clean_name), norm, scorer=fuzz.token_sort_ratio, score_cutoff=score)
        out[company] = issuers.cusip.iat[m[2]] if m else None
    return items.assign(cusip=items.company.map(out))


def meeting_in_window(b: pd.DataFrame, first_filed: str, last_filed: str) -> pd.DataFrame:
    """Soliciting material runs from ~30 days before to the meeting itself."""
    lo = pd.Timestamp(first_filed) - pd.Timedelta(days=30)
    hi = pd.Timestamp(last_filed) + pd.Timedelta(days=120)
    return b[b.meeting_dt.between(lo, hi)]


def keyed_items(items: pd.DataFrame, npx_all: pd.DataFrame) -> pd.DataFrame:
    """Every priority-category item with a stated adviser rec, whether the
    adviser backed or opposed the board. Management followers are removed
    before adviser scoring, so board-aligned items no longer blur the signal."""
    keep = items[items.category.notna() & items.adviser_rec.isin(["FOR", "AGAINST", "WITHHOLD"])].copy()
    # Named items before slates, so a named call wins when both hit one ballot item.
    keep["_slate"] = keep.label.str.startswith("__")
    return resolve_cusips(keep.sort_values("_slate"), npx_all)


def contested_from_key(opp: pd.DataFrame, npx_all: pd.DataFrame) -> set[tuple[str, pd.Timestamp]]:
    """Meetings the adviser text shows were proxy fights. Used alongside the
    DEFC14A match, which misses fights where only the dissident filed."""
    out = set()
    meetings = npx_all[["cusip", "meeting_dt"]].drop_duplicates()
    for it in opp[opp.category.eq("contested_election") & opp.cusip.notna()].itertuples():
        m = meeting_in_window(meetings[meetings.cusip == it.cusip], it.first_filed, it.last_filed)
        out |= {(it.cusip, d) for d in m.meeting_dt}
    return out


def join_to_items(opp: pd.DataFrame, pri: pd.DataFrame, cfg: dict, log=print) -> pd.DataFrame:
    """Map each adviser item onto canonical N-PX item_keys."""
    thr = cfg["adviser_key"]["director_fuzzy_threshold"]
    ballots = (pri.groupby("item_key")
               .agg(cusip=("cusip", "first"), issuer=("issuer", "first"), meeting_dt=("meeting_dt", "first"),
                    item_text=("item_text", "first"), priority_category=("priority_category", "first"),
                    vote_categories=("vote_categories", "first"), vote_source=("vote_source", "first"),
                    board_rec=("board_rec", lambda s: s.dropna().mode().iat[0] if s.notna().any() else None))
               .reset_index())
    key_rows, misses = [], []
    for it in opp.itertuples():
        if not it.cusip:
            misses.append((it.company, it.label, "issuer not held by target families"))
            continue
        b = meeting_in_window(ballots[ballots.cusip == it.cusip], it.first_filed, it.last_filed)
        b = b[b.priority_category == it.category]
        if b.empty:
            misses.append((it.company, it.label, "no N-PX meeting in window (or after Jun 30 2026)"))
            continue
        if it.label == "__board_slate__":
            cand = b[b.board_rec == "FOR"]
        elif it.label == "__dissident_slate__":
            cand = b[b.board_rec == "AGAINST"]
        elif it.label == "__shareholder_proposals__":
            cand = b[b.vote_source == "SECURITY HOLDER"]
        elif it.category == "contested_election":
            score = b.item_text.map(lambda d: fuzz.token_set_ratio(it.label.lower(), director_name(d)))
            cand = b[score >= thr]
        else:
            kws = [k for k in it.keywords.split("|") if k]
            pat = "|".join(re.escape(k) for k in kws) if kws else "."
            cand = b[b.item_text.str.contains(pat, case=False, regex=True)
                     & ~b.item_text.str.contains("frequency", case=False)]
            if len(cand) > 1:  # several plans/pay items at one meeting: keep the best-described one(s)
                score = cand.item_text.map(lambda d: fuzz.token_set_ratio(norm_text(it.label), norm_text(d)))
                cand = cand[score >= score.max() - 5]
        if cand.empty:
            misses.append((it.company, it.label, "no matching ballot item"))
            continue
        for c in cand.itertuples():
            key_rows.append({"item_key": c.item_key, "adviser": it.adviser, "cusip": it.cusip,
                             "issuer": c.issuer, "meeting_dt": c.meeting_dt, "item_text": c.item_text,
                             "category": it.category, "rec": _dir(it.adviser_rec),
                             "board_rec": _dir(it.board_rec), "opposed_board": bool(it.opposed_board),
                             "company": it.company, "label": it.label,
                             "ai_source": it.ai_source, "evidence": it.evidence})
    key = pd.DataFrame(key_rows).drop_duplicates("item_key")
    adv = opp.adviser.iat[0] if len(opp) else "?"
    pd.DataFrame(misses, columns=["company", "label", "reason"]).to_csv(
        PROCESSED / f"{adv.lower()}_key_misses.csv", index=False)
    log(f"{adv} key: {len(opp)} priority items with a stated rec -> {len(key)} N-PX ballot items "
        f"({len(misses)} unmatched, see data/processed/{adv.lower()}_key_misses.csv)")
    return key


def rank_and_trim(key: pd.DataFrame, pri: pd.DataFrame, cfg: dict, log=print) -> pd.DataFrame:
    """Rank key items by how many target families voted on them; keep the top N."""
    c = cfg["adviser_key"]
    adv = key.adviser.iat[0]
    fam = pri[pri.item_key.isin(key.item_key)].groupby("item_key").family.nunique().rename("n_families")
    key = key.merge(fam, on="item_key", how="left").fillna({"n_families": 0})
    key = key.sort_values(["n_families", "meeting_dt"], ascending=[False, True])
    kept = key[key.n_families > 0]
    if c.get("top_items_max"):
        kept = kept.head(c["top_items_max"])
    kept = kept.reset_index(drop=True)
    kept.to_parquet(PROCESSED / f"{adv.lower()}_key.parquet", index=False)
    log(f"{adv} key: kept {len(kept)} items ({int(kept.opposed_board.sum())} opposing the board); "
        f"categories {kept.category.value_counts().to_dict()}")
    return kept
