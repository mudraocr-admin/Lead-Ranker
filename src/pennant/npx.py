"""Step 1: N-PX votes -> one tidy table, mapped to the 4 priority categories."""
from __future__ import annotations

import gzip
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
CONTEST_QUARTERS = ["2025/QTR2", "2025/QTR3", "2025/QTR4", "2026/QTR1", "2026/QTR2"]


def npx_index(edgar: Edgar, cfg: dict, skip_names: bool = True) -> pd.DataFrame:
    """Every N-PX and N-PX/A filing in the window, oldest first, minus non-equity
    trusts by name.

    A filer can make several filings in one window, and they are not all
    amendments: GuideStone Funds filed a fund *voting* report and a separate
    fund *notice* report (funds with nothing to vote on) the same day. Keeping
    one filing per filer silently dropped ~1,000 filings, so every filing is
    kept and combine_filings() reconciles them."""
    c = cfg["npx"]
    idx = pd.DataFrame(load_form_index(edgar, c["index_quarter"]))
    idx = idx[idx.form.isin(["N-PX", "N-PX/A"]) & idx.date.between(c["filed_from"], c["filed_to"])]
    if skip_names:
        skip = re.compile(c["skip_name_regex"])
        idx = idx[~idx.company.map(lambda n: bool(skip.search(n)))]
    return idx.sort_values(["date", "path"]).reset_index(drop=True)


def select_filings(edgar: Edgar, cfg: dict) -> pd.DataFrame:
    """N-PX filings in the window whose filer matches a target family."""
    idx = npx_index(edgar, cfg)
    picks = []
    for fam in cfg["families"]:
        if fam.get("ciks"):
            hit = idx[idx.cik.isin(fam["ciks"])].copy()
        else:
            pat = re.compile(fam["match"], re.I)
            hit = idx[idx.company.map(lambda n: bool(pat.search(n)))].copy()
        if hit.empty:
            print(f"  [families.csv] no N-PX filer matched '{fam['name']}' - check its match/ciks")
        hit["family"] = fam["name"]
        # Cap by trust, not by filing, so a trust split across filings stays whole.
        trusts = hit.cik.drop_duplicates().head(cfg["npx"]["max_trusts_per_family"])
        picks.append(hit[hit.cik.isin(trusts)])
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


def _prev_quarter(q: str) -> str:
    year, n = int(q[:4]), int(q[-1])
    return f"{year - 1}/QTR4" if n == 1 else f"{year}/QTR{n - 1}"


def fund_filers(edgar: Edgar, quarter: str) -> set[str]:
    """CIKs that filed NPORT-P in the quarter or the one before: registered funds.
    Institutional managers file N-PX too (about 3 in 4 filers) but never NPORT-P,
    so this drops them before download instead of after."""
    rows = load_form_index(edgar, quarter) + load_form_index(edgar, _prev_quarter(quarter))
    return {r["cik"] for r in rows if r["form"].startswith("NPORT-P")}


def contest_issuers(edgar: Edgar) -> set[str]:
    """Normalized names of companies with proxy-fight filings (DEFC14A, DFAN14A).
    Director elections are kept in the universe cache only at these companies."""
    forms = {"DEFC14A", "DEFC14A/A", "DFAN14A"}
    return {_norm(r["company"]) for q in CONTEST_QUARTERS for r in load_form_index(edgar, q) if r["form"] in forms}


def universe_keep(contest: set[str]):
    """Row filter for the universe cache: the ballot items scoring can use
    (pay, shareholder proposals, M&A), plus director elections at companies with
    a proxy fight. Uncontested director elections, more than half of all rows,
    are never scored."""
    names = sorted(contest)
    seen: dict[str, bool] = {}

    def in_contest(issuer: str) -> bool:
        if issuer not in seen:
            seen[issuer] = process.extractOne(_norm(issuer), names, scorer=fuzz.token_sort_ratio,
                                              score_cutoff=92) is not None
        return seen[issuer]

    def keep(base: dict, cats: list[str]) -> bool:
        if base["vote_source"] == "SECURITY HOLDER" or "EXTRAORDINARY TRANSACTIONS" in cats or PAY_CATS & set(cats):
            return True
        return "DIRECTOR ELECTIONS" in cats and in_contest(base["issuer"])
    return keep


UNIVERSE_COLS = ["cusip", "meeting_date", "description", "vote_categories", "vote_source", "isin"]
UNIVERSE_VERSION = 2  # 2: all of a filer's N-PX filings, not just its last one


def combine_filings(parts: list[tuple[dict, pd.DataFrame]]) -> pd.DataFrame:
    """One filer's filings, oldest first, as (header, vote rows with series_id).
    Original reports and NEW HOLDINGS amendments add votes; a RESTATEMENT
    amendment replaces the earlier votes of every series it reports."""
    out = pd.DataFrame()
    for header, d in parts:
        if header["amendment_type"] == "RESTATEMENT" and len(out):
            out = out[~out.series_id.isin(set(d.series_id))]
        out = pd.concat([out, d], ignore_index=True)
    return out


def collapse_filer(d: pd.DataFrame) -> pd.DataFrame:
    """One FOR/AGAINST vote per ballot item for the whole filer. Items its funds
    split on are dropped, as Zytnick (2025) does when collapsing funds to institutions."""
    d = d.copy()
    d["vote_cast"] = d.vote_cast.replace({"WITHHOLD": "AGAINST"})
    d = d[d.vote_cast.isin(["FOR", "AGAINST"])]
    g = d.groupby(UNIVERSE_COLS).vote_cast.agg(["first", "nunique"])
    return g[g["nunique"] == 1]["first"].rename("vote_cast").reset_index()


def load_universe(edgar: Edgar, cfg: dict, log=print) -> pd.DataFrame:
    """Votes of every registered fund that filed N-PX in the window, one vote per
    filer per ballot item. These filers are the voters the recommendation
    imputation learns from (see impute.py); target families are dropped at use.

    Built once per N-PX window and independent of families.csv, so a new target
    list reuses it. Raw filings are cached under data/raw/npx/."""
    c, u = cfg["npx"], cfg["universe"]
    out = PROCESSED / f"npx_universe_{c['filed_from']}_{c['filed_to']}_v{UNIVERSE_VERSION}.parquet"
    if out.exists():
        uni = pd.read_parquet(out)
        log(f"Universe: {len(uni):,} cached filer votes from {uni.filer_cik.nunique():,} fund filers")
        return uni

    idx = npx_index(edgar, cfg)
    funds = fund_filers(edgar, c["index_quarter"])
    log(f"Universe: {idx.cik.nunique():,} N-PX filers, {idx[idx.cik.isin(funds)].cik.nunique():,} of them registered "
        f"funds (NPORT-P), {int(idx.cik.isin(funds).sum()):,} filings")
    idx = idx[idx.cik.isin(funds)]

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

    keep = universe_keep(contest_issuers(edgar))
    frames = []
    for i, (cik, g) in enumerate(idx.groupby("cik", sort=False), 1):
        if i % 250 == 0:
            log(f"  universe: {i:,}/{idx.cik.nunique():,} filers read")
        parts = []
        for f in g.itertuples():
            try:
                body = fetch_filing(edgar, f.path)
            except Exception as e:
                log(f"  skip {f.company} {f.path}: {type(e).__name__}")
                continue
            if body.find(b"<registrantType>RMIC</registrantType>", 0, 5_000_000) < 0:
                continue
            header, rows = parse_filing(body, keep)
            if rows:
                parts.append((header, pd.DataFrame(rows)))
        if parts:
            frames.append(collapse_filer(combine_filings(parts)).assign(filer_cik=cik, filer=g.company.iat[-1]))
    uni = pd.concat(frames, ignore_index=True)
    uni["meeting_dt"] = pd.to_datetime(uni.meeting_date, format="%m/%d/%Y", errors="coerce")
    for col in ("vote_categories", "vote_source", "vote_cast", "filer", "filer_cik"):
        uni[col] = uni[col].astype("category")
    uni.to_parquet(out, index=False)
    log(f"Universe: {len(uni):,} filer votes from {uni.filer_cik.nunique():,} fund filers")
    return uni


# ------------------------------------------------------------------- parse
def _text(el, tag):
    x = el.find(NS + tag)
    return x.text.strip() if x is not None and x.text else ""


def parse_filing(body: bytes, keep=None) -> tuple[dict, list[dict]]:
    """Returns (header, vote rows). Header carries registrantType + series names.
    `keep(item_fields, categories)` optionally filters ballot items before their
    vote records are expanded, which keeps very large filings cheap to parse."""
    header: dict = {"registrant_type": "", "report_type": "", "amendment_type": "", "series": {}}
    rows: list[dict] = []
    for block in XML_BLOCK.findall(body):
        if b"proxyVoteTable" in block[:500]:
            for _, pt in etree.iterparse(io.BytesIO(block), tag=NS + "proxyTable", huge_tree=True):
                cats = [c.text.strip() for c in pt.iter(NS + "categoryType") if c.text]
                base = {
                    "issuer": _text(pt, "issuerName"),
                    "cusip": _text(pt, "cusip").upper(),
                    "isin": _text(pt, "isin"),
                    "meeting_date": _text(pt, "meetingDate"),
                    "description": _text(pt, "voteDescription"),
                    "vote_categories": "|".join(cats),
                    "vote_source": _text(pt, "voteSource"),
                }
                if keep is not None and not keep(base, cats):
                    pt.clear()
                    continue
                series = [s.text.strip() for s in pt.iter(NS + "voteSeries") if s.text] or [""]
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
            first = lambda tag: next((t.strip() for n, t in els if n == tag and t), "")  # noqa: E731
            header["registrant_type"] = first("registrantType")
            header["report_type"] = first("reportType")        # e.g. FUND VOTING REPORT / FUND NOTICE REPORT
            header["amendment_type"] = first("amendmentType")  # RESTATEMENT / NEW HOLDINGS on an N-PX/A
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
    for (family, cik), g in filings.groupby(["family", "cik"], sort=False):
        parts = []
        for f in g.itertuples():
            a = {"family": family, "cik": cik, "filer": f.company, "form": f.form, "filed": f.date,
                 "accession": f.path}
            if used.get(family, 0) > cap:
                log(f"  skip {f.company}: family byte cap reached")
                audit.append({**a, "status": "skipped: family download cap"})
                continue
            body = fetch_filing(edgar, f.path)
            used[family] = used.get(family, 0) + len(body)
            header, rows = parse_filing(body)
            a.update(report_type=header["report_type"], amendment_type=header["amendment_type"],
                     series="; ".join(header["series"].values()))
            if header["registrant_type"] != "RMIC":
                why = f"dropped: not a fund filing (registrant type {header['registrant_type'] or '?'})"
            elif "NOTICE" in header["report_type"]:
                why = "no votes: notice report (nothing to vote on)"
            elif not rows:
                why = "WARNING: voting report but no votes parsed"
            else:
                why = ""
            if why:
                log(f"  {why}: {f.company} {f.path}")
                audit.append({**a, "status": why})
                continue
            d = pd.DataFrame(rows)
            d["series_id"] = d.series_id.replace("", f"CIK{cik}")
            d["series_name"] = d.series_id.map(header["series"]).fillna(f.company)
            d["family"], d["trust"], d["trust_cik"], d["accession"] = family, f.company, cik, f.path
            parts.append((header, d))
            audit.append({**a, "status": "used", "vote_rows": len(d)})
        if parts:
            d = combine_filings(parts)
            frames.append(d)
            log(f"  {family:<20} {g.company.iat[-1][:45]:<45} {len(d):>8,} rows from {len(parts)} filing(s)")
    votes = pd.concat(frames, ignore_index=True)
    # A restatement can replace an earlier filing's votes entirely.
    kept = set(votes.accession)
    for a in audit:
        if a["status"] == "used" and a["accession"] not in kept:
            a["status"] = "replaced by a later restatement"
    OUTPUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(audit).to_csv(OUTPUT / "filers.csv", index=False)
    log(f"N-PX: filer audit written to output/filers.csv")

    votes["meeting_dt"] = pd.to_datetime(votes.meeting_date, format="%m/%d/%Y", errors="coerce")
    votes["with_mgmt"] = [with_management(v, m) for v, m in zip(votes.vote_cast, votes.vs_mgmt)]
    votes["shares"] = pd.to_numeric(votes.shares, errors="coerce")

    # Contested elections: N-PX has no contest flag, so match to DEFC14A.
    defc = contested_meetings(edgar, CONTEST_QUARTERS)
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
