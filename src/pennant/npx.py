"""Step 1: N-PX votes -> one tidy table, mapped to the 4 priority categories."""
from __future__ import annotations

import gzip
import io
import re

import pandas as pd
from lxml import etree
from rapidfuzz import fuzz, process

from .config import PROCESSED
from .edgar import RAW, Edgar, load_form_index

NS = "{http://www.sec.gov/edgar/document/npxproxy/informationtable}"
XML_BLOCK = re.compile(rb"<XML>\s*(.*?)\s*</XML>", re.S)

PRIORITY = ["shareholder_proposal", "contested_election", "mna", "pay"]
PAY_CATS = {"SECTION 14A SAY-ON-PAY VOTES", "COMPENSATION"}
FREQUENCY_VOTES = {"ONE YEAR", "TWO YEARS", "THREE YEARS"}
NON_VOTES = {"DO NOT VOTE", "TAKE NO ACTION", "NONE", "", "DID NOT VOTE", "ECHO", "MIRROR"}
VOTE_ALIASES = {"TAKENOACTION": "TAKE NO ACTION", "1 YEAR": "ONE YEAR", "2 YEARS": "TWO YEARS",
                "3 YEARS": "THREE YEARS", "WITHHELD": "WITHHOLD", "DONOTVOTE": "DO NOT VOTE"}


# ---------------------------------------------------------------- download
def select_filings(edgar: Edgar, cfg: dict) -> pd.DataFrame:
    """N-PX filings in the window whose filer name matches a target family."""
    c = cfg["npx"]
    idx = pd.DataFrame(load_form_index(edgar, c["index_quarter"]))
    idx = idx[(idx.form == "N-PX") & idx.date.between(c["filed_from"], c["filed_to"])]
    skip = re.compile(c["skip_name_regex"])
    idx = idx[~idx.company.map(lambda n: bool(skip.search(n)))]
    picks = []
    for fam in cfg["families"]:
        if fam.get("ciks"):
            hit = idx[idx.cik.isin(fam["ciks"])].copy()
        else:
            pat = re.compile(fam["match"], re.I)
            hit = idx[idx.company.map(lambda n: bool(pat.search(n)))].copy()
        if hit.empty:
            print(f"  [families.csv] no N-PX filer matched '{fam['name']}' - check its match/ciks")
        # Amendments and duplicates: keep the latest filing per filer CIK.
        hit = hit.sort_values("date").drop_duplicates("cik", keep="last")
        hit["family"] = fam["name"]
        picks.append(hit.head(c["max_filings_per_family"]))
    return pd.concat(picks, ignore_index=True)


def fetch_filing(edgar: Edgar, path: str) -> bytes:
    """Full submission .txt, stored gzipped under data/raw/npx/."""
    out = RAW / "npx" / (path.replace("/", "_") + ".gz")
    if out.exists():
        return gzip.decompress(out.read_bytes())
    out.parent.mkdir(parents=True, exist_ok=True)
    body = edgar.get(f"https://www.sec.gov/Archives/{path}", cache=False)
    out.write_bytes(gzip.compress(body))
    return body


# ------------------------------------------------------------------- parse
def _text(el, tag):
    x = el.find(NS + tag)
    return x.text.strip() if x is not None and x.text else ""


def parse_filing(body: bytes) -> tuple[dict, list[dict]]:
    """Returns (header, vote rows). Header carries registrantType + series names."""
    header: dict = {"registrant_type": "", "series": {}}
    rows: list[dict] = []
    for block in XML_BLOCK.findall(body):
        if b"proxyVoteTable" in block[:500]:
            for _, pt in etree.iterparse(io.BytesIO(block), tag=NS + "proxyTable", huge_tree=True):
                cats = [c.text.strip() for c in pt.iter(NS + "categoryType") if c.text]
                series = [s.text.strip() for s in pt.iter(NS + "voteSeries") if s.text] or [""]
                base = {
                    "issuer": _text(pt, "issuerName"),
                    "cusip": _text(pt, "cusip").upper(),
                    "isin": _text(pt, "isin"),
                    "meeting_date": _text(pt, "meetingDate"),
                    "description": _text(pt, "voteDescription"),
                    "vote_categories": "|".join(cats),
                    "vote_source": _text(pt, "voteSource"),
                }
                for vr in pt.iter(NS + "voteRecord"):
                    rec = dict(base,
                               vote_cast=VOTE_ALIASES.get(_text(vr, "howVoted").upper(), _text(vr, "howVoted").upper()),
                               shares=_text(vr, "sharesVoted"),
                               mgmt_rec=VOTE_ALIASES.get(_text(vr, "managementRecommendation").upper(), _text(vr, "managementRecommendation").upper()))
                    for s in series:  # one record can cover several series
                        rows.append(dict(rec, series_id=s))
                pt.clear()
        else:
            root = etree.fromstring(block, parser=etree.XMLParser(huge_tree=True, recover=True))
            els = [(etree.QName(e).localname, e.text) for e in root.iter() if isinstance(e.tag, str)]
            header["registrant_type"] = next((t.strip() for n, t in els if n == "registrantType" and t), "")
            ids = [t for n, t in els if n == "idOfSeries"]
            names = [t for n, t in els if n == "nameOfSeries"]
            header["series"] = dict(zip(ids, names))
    return header, rows


def with_management(vote: str, rec: str):
    """True/False for with/against management; None when not comparable."""
    if vote in NON_VOTES or rec in ("NONE", ""):
        return None
    if vote in FREQUENCY_VOTES or rec in FREQUENCY_VOTES:
        return vote == rec
    if vote == "WITHHOLD":
        vote = "AGAINST"
    if rec == "WITHHOLD":
        rec = "AGAINST"
    return vote == rec


# -------------------------------------------------------- priority mapping
def contested_meetings(edgar: Edgar, quarters: list[str]) -> pd.DataFrame:
    """DEFC14A filings (company + CIK + date) from the quarterly form indexes."""
    rows = []
    for q in quarters:
        rows += [r for r in load_form_index(edgar, q) if r["form"] in ("DEFC14A", "DEFC14A/A")]
    return pd.DataFrame(rows).drop_duplicates(["cik", "date"])


def _norm(name: str) -> str:
    name = name.upper()
    name = re.sub(r"[^A-Z0-9 ]", " ", name)
    name = re.sub(r"\b(INC|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PLC|HOLDINGS?|GROUP|THE|NV|SA|AG|LLC|LP|CLASS [A-Z])\b", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def flag_contested(meetings: pd.DataFrame, defc: pd.DataFrame, window_days=120, score=92) -> pd.Series:
    """Mark (issuer, meeting_date) as contested when a DEFC14A for a
    fuzzy-matching company was filed 0..window_days before the meeting."""
    names = defc.company.map(_norm).tolist()
    defc = defc.assign(norm=names, date=pd.to_datetime(defc.date))
    flags = []
    for issuer, mdate in zip(meetings.issuer, meetings.meeting_dt):
        m = process.extractOne(_norm(issuer), names, scorer=fuzz.token_sort_ratio, score_cutoff=score)
        if not m or pd.isna(mdate):
            flags.append(False)
            continue
        dates = defc.date[defc.norm == m[0]]
        flags.append(bool(((mdate - dates).dt.days.between(0, window_days)).any()))
    return pd.Series(flags, index=meetings.index)


def map_priority(df: pd.DataFrame) -> pd.Series:
    cats = df.vote_categories
    director = cats.str.contains("DIRECTOR ELECTIONS")
    out = pd.Series(pd.NA, index=df.index, dtype="object")
    pay = cats.apply(lambda c: bool(PAY_CATS & set(c.split("|")))) & ~df.vote_cast.isin(FREQUENCY_VOTES)
    out[pay] = "pay"
    out[df.vote_source.eq("SECURITY HOLDER")] = "shareholder_proposal"
    out[cats.str.contains("EXTRAORDINARY TRANSACTIONS")] = "mna"
    # In a proxy fight dissident nominees are often shareholder-sourced; the
    # contest label wins so they are not mistaken for ordinary proposals.
    out[director & df.contested] = "contested_election"
    return out


# --------------------------------------------------------------------- run
def load_votes(edgar: Edgar, cfg: dict, log=print) -> pd.DataFrame:
    filings = select_filings(edgar, cfg)
    log(f"N-PX: {len(filings)} candidate filings across {filings.family.nunique()} families")
    frames, cap = [], cfg["npx"]["max_mb_per_family"] * 2**20
    used: dict[str, int] = {}
    for f in filings.itertuples():
        if used.get(f.family, 0) > cap:
            log(f"  skip {f.company}: family byte cap reached")
            continue
        body = fetch_filing(edgar, f.path)
        used[f.family] = used.get(f.family, 0) + len(body)
        header, rows = parse_filing(body)
        if header["registrant_type"] != "RMIC" or not rows:
            log(f"  drop {f.company}: type={header['registrant_type'] or '?'} rows={len(rows)}")
            continue
        d = pd.DataFrame(rows)
        d["series_id"] = d.series_id.replace("", f"CIK{f.cik}")
        d["series_name"] = d.series_id.map(header["series"]).fillna(f.company)
        d["family"], d["trust"], d["trust_cik"], d["accession"] = f.family, f.company, f.cik, f.path
        frames.append(d)
        log(f"  {f.family:<20} {f.company[:45]:<45} {len(d):>8,} rows")
    votes = pd.concat(frames, ignore_index=True)

    votes["meeting_dt"] = pd.to_datetime(votes.meeting_date, format="%m/%d/%Y", errors="coerce")
    votes["with_mgmt"] = [with_management(v, r) for v, r in zip(votes.vote_cast, votes.mgmt_rec)]
    votes["shares"] = pd.to_numeric(votes.shares, errors="coerce")

    # Contested elections: N-PX has no contest flag, so match to DEFC14A.
    defc = contested_meetings(edgar, ["2025/QTR2", "2025/QTR3", "2025/QTR4", "2026/QTR1", "2026/QTR2"])
    meetings = votes[votes.vote_categories.str.contains("DIRECTOR ELECTIONS")][["issuer", "meeting_dt"]].drop_duplicates()
    meetings["contested"] = flag_contested(meetings, defc)
    votes = votes.merge(meetings, on=["issuer", "meeting_dt"], how="left")
    votes["contested"] = votes.contested.fillna(False).astype(bool)
    log(f"DEFC14A: {len(defc)} filings; {int(meetings.contested.sum())} contested meetings in N-PX")

    PROCESSED.mkdir(parents=True, exist_ok=True)
    votes.to_parquet(PROCESSED / "npx_all.parquet", index=False)
    log(f"N-PX: {len(votes):,} vote rows")
    return votes


def prioritize(votes: pd.DataFrame, extra_contested: set = frozenset(), log=print) -> pd.DataFrame:
    """Map rows to the 4 priority categories and add canonical item keys.

    `extra_contested` holds (cusip, meeting_dt) pairs known to be proxy fights
    from the ISS soliciting material, on top of the DEFC14A match."""
    from .items import assign_item_keys

    if extra_contested:
        pairs = pd.MultiIndex.from_tuples(list(extra_contested), names=["cusip", "meeting_dt"])
        hit = pd.MultiIndex.from_frame(votes[["cusip", "meeting_dt"]]).isin(pairs)
        votes = votes.assign(contested=votes.contested | hit)
    votes = votes.assign(priority_category=map_priority(votes))
    pri = assign_item_keys(votes[votes.priority_category.notna()].reset_index(drop=True))
    pri.to_parquet(PROCESSED / "npx_priority.parquet", index=False)
    log(f"N-PX: {len(pri):,} rows in priority categories, {pri.item_key.nunique():,} distinct ballot items "
        f"({pri.drop_duplicates('item_key').priority_category.value_counts().to_dict()})")
    return pri
