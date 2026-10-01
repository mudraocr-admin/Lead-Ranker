"""Step 1: N-PX votes -> one tidy table, mapped to the 4 priority categories."""
from __future__ import annotations

import gzip
import hashlib
import io
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from lxml import etree
from rapidfuzz import fuzz, process

from .config import OUTPUT, PROCESSED
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
        try:
            return gzip.decompress(out.read_bytes())
        except (OSError, EOFError):
            pass  # truncated by an interrupted run: download again
    out.parent.mkdir(parents=True, exist_ok=True)
    body = edgar.get(f"https://www.sec.gov/Archives/{path}", cache=False)
    tmp = out.with_name(out.name + f".{threading.get_ident()}.tmp")
    tmp.write_bytes(gzip.compress(body))
    tmp.replace(out)  # atomic: a killed run never leaves a half-written file
    return body


def load_universe(edgar: Edgar, cfg: dict, pri: pd.DataFrame, log=print) -> pd.DataFrame:
    """Votes from every other fund filer of N-PX, on the meetings our target
    families voted in. These funds are never scored; they are reference voters
    used to find adviser followers and infer the adviser's call on more items.

    Raw filings are cached under data/raw/npx/, and the filtered result under
    data/processed/ keyed by the meeting set, so reruns are cheap."""
    c, u = cfg["npx"], cfg["universe"]
    meetings = set(zip(pri.cusip, pri.meeting_dt.dt.strftime("%m/%d/%Y")))
    tag = hashlib.sha1("\n".join(sorted(f"{a}|{b}" for a, b in meetings)).encode()).hexdigest()[:10]
    out = PROCESSED / f"npx_universe_{tag}.parquet"
    if out.exists():
        uni = pd.read_parquet(out)
        log(f"Universe: {len(uni):,} cached vote rows from {uni.filer_cik.nunique():,} reference filers")
        return uni

    idx = pd.DataFrame(load_form_index(edgar, c["index_quarter"]))
    idx = idx[(idx.form == "N-PX") & idx.date.between(c["filed_from"], c["filed_to"])]
    idx = idx[~idx.company.map(lambda n: bool(re.search(c["skip_name_regex"], n)))]
    targets = set(pri.trust_cik.unique())
    idx = idx[~idx.cik.isin(targets)].sort_values("date").drop_duplicates("cik", keep="last")
    # Fund-like names first (most filers are institutional managers, which are
    # skipped), so a download cap or interruption still leaves the funds.
    fundish = idx.company.str.contains(r"(?i)\b(trust|funds?|portfolios?|series|etf|variable|investors?)\b")
    idx = idx.assign(_f=fundish).sort_values("_f", ascending=False, kind="stable")
    log(f"Universe: {len(idx):,} candidate N-PX filers outside the target families "
        f"({int(fundish.sum()):,} with fund-like names first)")

    # Download in parallel; Edgar's lock keeps the whole pool within max_rps.
    def prefetch(path):
        try:
            fetch_filing(edgar, path)
        except Exception as e:  # one bad filing must not stop the pull
            return path, type(e).__name__
        return path, None
    with ThreadPoolExecutor(u.get("workers", 6)) as pool:
        for i, (path, err) in enumerate(pool.map(prefetch, idx.path), 1):
            if err:
                log(f"  download failed {path}: {err}")
            if i % 500 == 0:
                log(f"  universe download: {i:,}/{len(idx):,}")

    keep = lambda cusip, mdate: (cusip, mdate) in meetings  # noqa: E731
    frames, total, cap = [], 0, u["max_gb"] * 2**30
    n_fund = 0
    for i, f in enumerate(idx.itertuples(), 1):
        if i % 250 == 0:
            log(f"  universe: {i:,}/{len(idx):,} filers read, {n_fund:,} fund filers, {total / 2**30:.1f} GB")
        if total > cap:
            log(f"  universe download cap ({u['max_gb']} GB) reached after {i - 1:,} filers")
            break
        try:
            body = fetch_filing(edgar, f.path)
        except Exception as e:  # one bad filing must not stop a multi-hour download
            log(f"  skip {f.company}: {type(e).__name__}")
            continue
        total += len(body)
        if body.find(b"<registrantType>RMIC</registrantType>", 0, 5_000_000) < 0:
            continue  # institutional managers, not funds
        header, rows = parse_filing(body, keep)
        n_fund += 1
        if rows:
            d = pd.DataFrame(rows)[["cusip", "meeting_date", "description", "vote_categories", "vote_source",
                                    "isin", "vote_cast", "vs_mgmt", "series_id"]]
            d["series_id"] = d.series_id.replace("", f"CIK{f.cik}")
            d["filer_cik"], d["filer"] = f.cik, f.company
            frames.append(d)
    uni = pd.concat(frames, ignore_index=True)
    uni["meeting_dt"] = pd.to_datetime(uni.meeting_date, format="%m/%d/%Y", errors="coerce")
    uni.to_parquet(out, index=False)
    log(f"Universe: {len(uni):,} vote rows on target meetings from {uni.filer_cik.nunique():,} fund filers "
        f"({uni.series_id.nunique():,} funds)")
    return uni


# ------------------------------------------------------------------- parse
def _text(el, tag):
    x = el.find(NS + tag)
    return x.text.strip() if x is not None and x.text else ""


def parse_filing(body: bytes, keep=None) -> tuple[dict, list[dict]]:
    """Returns (header, vote rows). Header carries registrantType + series names.
    `keep(cusip, meeting_date)` optionally filters ballot items before their vote
    records are expanded, which keeps very large filings cheap to parse."""
    header: dict = {"registrant_type": "", "series": {}}
    rows: list[dict] = []
    for block in XML_BLOCK.findall(body):
        if b"proxyVoteTable" in block[:500]:
            for _, pt in etree.iterparse(io.BytesIO(block), tag=NS + "proxyTable", huge_tree=True):
                if keep is not None and not keep(_text(pt, "cusip").upper(), _text(pt, "meetingDate")):
                    pt.clear()
                    continue
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
                               # N-PX "managementRecommendation": whether this vote was FOR or
                               # AGAINST management's recommendation, not the board's position.
                               vs_mgmt=_text(vr, "managementRecommendation").upper())
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


def with_management(vote: str, vs_mgmt: str):
    """The filer's own for/against-management flag, read as-is; None when there is none."""
    if vote in NON_VOTES or vs_mgmt not in ("FOR", "AGAINST"):
        return None
    return vs_mgmt == "FOR"


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


def sponsor_rule(df: pd.DataFrame) -> pd.Series:
    """Fallback board position from who put the item on the ballot: company
    items -> FOR; shareholder items at US issuers (incl. dissident nominees) ->
    AGAINST; non-US shareholder items -> unknown. Used only where the filers'
    for/against-management flags give no clear consensus (see board_position)."""
    us = df["isin"].str[:2].eq("US") | (df["isin"].eq("") & df["cusip"].str.len().eq(9))
    out = pd.Series(pd.NA, index=df.index, dtype="object")
    out[df.vote_source.eq("ISSUER")] = "FOR"
    out[df.vote_source.eq("SECURITY HOLDER") & us] = "AGAINST"
    # Proxy fights: some filers list dissident nominees as issuer items, so the
    # ballot text ("COMPANY NOMINEE: ...", "DOMA Nominees Opposed by the
    # Company: ...", "Diana Nominee: ...") decides where it says anything.
    fight = df.contested & df.vote_categories.str.contains("DIRECTOR ELECTIONS")
    text_side = df.loc[fight, "description"].map(_contest_side)
    known = text_side.notna()
    out.loc[text_side.index[known]] = text_side[known]
    return out


_NOT_DISSIDENT = {"company", "management", "board", "director", "the", "all", "incumbent", "class", "our"}


def _contest_side(desc: str):
    t = desc.lower()
    if re.search(r"opposed by the (company|board)|dissident", t):
        return "AGAINST"
    if re.search(r"(company|management|board|incumbent) nominees?", t):
        return "FOR"
    m = re.search(r"\b([a-z][a-z.&'-]+) nominees?\b", t)
    if m and m.group(1) not in _NOT_DISSIDENT:
        return "AGAINST"  # "Impactive nominee", "Diana Nominee", "DOMA Nominees"
    return None


def board_position(pri: pd.DataFrame, min_share: float, log=print) -> pd.DataFrame:
    """The board's position on each item, recovered from the votes themselves.

    Each vote row implies one: the fund's vote if it was flagged "for
    management", the opposite if "against". The item takes the consensus across
    all filers when at least `min_share` of rows agree; otherwise the sponsor
    rule. Disagreements between the two are written out as a sanity check."""
    vote = pri.vote_cast.replace({"WITHHOLD": "AGAINST"})
    ok = vote.isin(["FOR", "AGAINST"]) & pri.with_mgmt.notna()
    flip = vote.map({"FOR": "AGAINST", "AGAINST": "FOR"})
    implied = vote.where(pri.with_mgmt.eq(True), flip)[ok]
    counts = pd.crosstab(pri.item_key[ok], implied)
    share = counts.max(axis=1) / counts.sum(axis=1)
    consensus = counts.idxmax(axis=1)[share >= min_share]

    items = pri.drop_duplicates("item_key").set_index("item_key")
    rule = sponsor_rule(items)
    board = consensus.reindex(items.index)
    source = pd.Series("votes", index=items.index).where(board.notna(), "sponsor rule")
    board = board.fillna(rule)
    source = source.where(board.notna(), "unknown")

    check = pd.DataFrame({"issuer": items.issuer, "item_text": items.item_text, "vote_source": items.vote_source,
                          "from_votes": consensus.reindex(items.index), "share": share.reindex(items.index),
                          "n_rows": counts.sum(axis=1).reindex(items.index), "sponsor_rule": rule})
    dis = check[check.from_votes.notna() & check.sponsor_rule.notna() & (check.from_votes != check.sponsor_rule)]
    dis.sort_values("n_rows", ascending=False).to_csv(PROCESSED / "board_position_vs_sponsor_rule.csv")
    log(f"Board position: {int(source.eq('votes').sum()):,} items from filer consensus (>= {min_share:.0%}), "
        f"{int(source.eq('sponsor rule').sum()):,} from the sponsor rule, {int(source.eq('unknown').sum()):,} unknown; "
        f"sponsor rule disagrees on {len(dis)} (data/processed/board_position_vs_sponsor_rule.csv)")
    return pd.DataFrame({"board_rec": board, "board_source": source}).reset_index()


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
    audit = []
    for f in filings.itertuples():
        a = {"family": f.family, "cik": f.cik, "filer": f.company, "filed": f.date, "accession": f.path}
        if used.get(f.family, 0) > cap:
            log(f"  skip {f.company}: family byte cap reached")
            audit.append({**a, "status": "skipped: family download cap"})
            continue
        body = fetch_filing(edgar, f.path)
        used[f.family] = used.get(f.family, 0) + len(body)
        header, rows = parse_filing(body)
        if header["registrant_type"] != "RMIC" or not rows:
            log(f"  drop {f.company}: type={header['registrant_type'] or '?'} rows={len(rows)}")
            why = ("dropped: not a fund filing (registrant type " + (header["registrant_type"] or "?") + ")"
                   if header["registrant_type"] != "RMIC" else "dropped: fund filing with no votes")
            audit.append({**a, "status": why, "series": "; ".join(header["series"].values())})
            continue
        audit.append({**a, "status": "used", "vote_rows": len(rows), "series": "; ".join(header["series"].values())})
        d = pd.DataFrame(rows)
        d["series_id"] = d.series_id.replace("", f"CIK{f.cik}")
        d["series_name"] = d.series_id.map(header["series"]).fillna(f.company)
        d["family"], d["trust"], d["trust_cik"], d["accession"] = f.family, f.company, f.cik, f.path
        frames.append(d)
        log(f"  {f.family:<20} {f.company[:45]:<45} {len(d):>8,} rows")
    votes = pd.concat(frames, ignore_index=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(audit).to_csv(OUTPUT / "filers.csv", index=False)
    log(f"N-PX: filer audit written to output/filers.csv")

    votes["meeting_dt"] = pd.to_datetime(votes.meeting_date, format="%m/%d/%Y", errors="coerce")
    votes["with_mgmt"] = [with_management(v, m) for v, m in zip(votes.vote_cast, votes.vs_mgmt)]
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


def prioritize(votes: pd.DataFrame, cfg: dict, extra_contested: set = frozenset(), log=print) -> pd.DataFrame:
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
    pri = pri.merge(board_position(pri, cfg["npx"]["board_consensus"], log), on="item_key", how="left")
    pri.to_parquet(PROCESSED / "npx_priority.parquet", index=False)
    log(f"N-PX: {len(pri):,} rows in priority categories, {pri.item_key.nunique():,} distinct ballot items "
        f"({pri.drop_duplicates('item_key').priority_category.value_counts().to_dict()})")
    return pri
