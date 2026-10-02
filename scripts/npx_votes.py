"""Pull a filer's N-PX voting records for the configured window, for spot checks.

    python scripts/npx_votes.py 1131013            # one or more filer CIKs
    python scripts/npx_votes.py --family "GuideStone Funds"   # CIKs from families.csv

Lists every N-PX / N-PX/A filing the filer made in the window (config.yaml npx
section) with its report type, then writes the parsed votes, combined exactly as
the pipeline combines them, to data/processed/npx_votes_<cik>.csv. For each
filing it also recounts the raw vote table independently of the parser: ballot
entries, entries with no vote cast (e.g. shares on loan, sharesVoted 0), and the
vote rows expected (vote records x series). "MISMATCH" means the parser lost votes.

The vote table EDGAR renders at .../xslNPX-INFO-TABLE_X01/ProxyVotingTable.xml
is one document inside the submission .txt this reads.
"""
import argparse
import io
import sys
from pathlib import Path

import pandas as pd
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pennant.config import PROCESSED, load_config, make_edgar  # noqa: E402
from pennant.npx import NS, XML_BLOCK, combine_filings, fetch_filing, npx_index, parse_filing  # noqa: E402


def raw_counts(body: bytes) -> tuple[int, int, int]:
    """(ballot entries, entries with no vote record, expected vote rows), read straight from the XML."""
    entries = no_vote = expected = 0
    for block in XML_BLOCK.findall(body):
        if b"proxyVoteTable" not in block[:500]:
            continue
        for _, pt in etree.iterparse(io.BytesIO(block), tag=NS + "proxyTable", huge_tree=True):
            records = sum(1 for _ in pt.iter(NS + "voteRecord"))
            series = sum(1 for s in pt.iter(NS + "voteSeries") if s.text) or 1
            entries += 1
            no_vote += records == 0
            expected += records * series
            pt.clear()
    return entries, no_vote, expected


ap = argparse.ArgumentParser()
ap.add_argument("ciks", nargs="*")
ap.add_argument("--family")
args = ap.parse_args()
cfg = load_config()
ciks = [c.lstrip("0") for c in args.ciks]
if args.family:
    ciks += next(f["ciks"] for f in cfg["families"] if f["name"] == args.family)
if not ciks:
    ap.error("give filer CIKs or --family")

edgar = make_edgar(cfg)
idx = npx_index(edgar, cfg, skip_names=False)  # no name filter: show everything filed
for cik in ciks:
    g = idx[idx.cik == cik]
    if g.empty:
        print(f"CIK {cik}: no N-PX filing between {cfg['npx']['filed_from']} and {cfg['npx']['filed_to']}")
        continue
    parts = []
    print(f"CIK {cik}  {g.company.iat[-1]}")
    for f in g.itertuples():
        body = fetch_filing(edgar, f.path)
        header, rows = parse_filing(body)
        entries, no_vote, expected = raw_counts(body)
        d = pd.DataFrame(rows)
        flag = "ok" if len(d) == expected else "MISMATCH"
        print(f"  {f.form:<7} {f.date}  {f.path.split('/')[-1]}  {header['report_type'] or '?':<20} "
              f"{header['amendment_type'] or '':<12} series {len(header['series']):>3}  entries {entries:>6} "
              f"(not voted {no_vote:>5})  vote rows parsed {len(d):>7,} / expected {expected:>7,}  {flag}")
        print(f"          https://www.sec.gov/Archives/{f.path}")
        if len(d):
            d["series_id"] = d.series_id.replace("", f"CIK{cik}")
            d["series_name"] = d.series_id.map(header["series"])
            d["accession"] = f.path
            parts.append((header, d))
    if parts:
        out = PROCESSED / f"npx_votes_{cik}.csv"
        combine_filings(parts).to_csv(out, index=False)
        print(f"  -> {out}")
