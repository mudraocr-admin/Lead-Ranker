"""Build the demo website's data file from the pipeline outputs.

    .venv/Scripts/python.exe scripts/build_site_data.py [out_dir]   # default: output/

Reads output/priority_list.csv, output/run_summary.json, output/filers.csv and
families.csv; writes <out_dir>/pennant-data.json, which the page in site/ fetches.
Every displayed value comes from those files; missing values become "n/a".
See HANDOFF.md, "Demo website".
"""
import json
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "output"

p = pd.read_csv(ROOT / "output/priority_list.csv")
f = pd.read_csv(ROOT / "families.csv").set_index("family")
fl = pd.read_csv(ROOT / "output/filers.csv")
rs = json.loads((ROOT / "output/run_summary.json").read_text())

NA = "n/a"
ADV = {"ISS": "ISS", "GL": "Glass Lewis"}
CATS = [("shareholder_proposal", "Shareholder proposals"), ("contested_election", "Contested director elections"),
        ("mna", "M&A"), ("pay", "Executive pay")]


def ok(v):
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def pct(v):
    return f"{v * 100:.0f}%" if ok(v) else NA


def num(v):
    return f"{int(v):,}" if ok(v) else NA


def tag(rationale):
    """Values badge, read from the user's fit_rationale column in families.csv."""
    t = str(rationale)
    return "Faith-based" if "Faith" in t else ("ESG" if "ESG" in t else "")


def filer_links(ciks, names):
    """One link per trust: its N-PX filing from filers.csv, else its EDGAR N-PX list."""
    out = []
    for c, n in zip(ciks, names):
        u = fl[(fl.cik == int(c)) & (fl.status == "used")].sort_values("filed")
        if len(u):
            x = u.iloc[-1]
            out.append(dict(name=n, cik=c, url="https://www.sec.gov/Archives/" + x.accession, label=f"N-PX filed {x.filed}"))
        else:
            out.append(dict(name=n, cik=c, url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={c}&type=N-PX",
                            label="EDGAR N-PX filings"))
    return out


rows = []
for _, r in p.iterrows():
    tier = int(r.priority.split()[1])
    a = r.scored_adviser
    fm = f.loc[r.family]
    ciks = [c.strip() for c in str(r.ciks).split(";")]
    names = [n.strip() for n in str(r.filers).split(";")]
    assert len(ciks) == len(names), r.family
    cats = [dict(name=nm, ag=pct(r[f"agree_{a}_{c}_opposed"]), agn=num(r[f"n_{a}_{c}_opposed"]),
                 mg=pct(r[f"mgmt_rate_{c}"]), mgn=num(r[f"mgmt_n_{c}"])) for c, nm in CATS]
    ag, wl = r.adviser_agreement, r.workload_meetings
    rows.append(dict(
        rank=int(r["rank"]), family=r.family, tier=tier, tierLabel=f"Tier {tier}", segment=r.segment, conf=r.confidence,
        why=r.confidence_reason, workload=num(wl), mgmt=pct(r.mgmt_rate), mgmtN=num(r.mgmt_items), adviser=ADV[a],
        adviserWhy=r.scored_adviser_reason, agree=pct(ag), agreeN=num(r[f"n_{a}_opposed"]),
        aligned=pct(r[f"agree_{a}_aligned"]), alignedN=num(r[f"n_{a}_aligned"]), cats=cats,
        divergence=pct(r.series_divergence), nSeries=num(r.n_series), nFilers=len(ciks), filers=filer_links(ciks, names),
        fit=fm.pennant_fit if ok(fm.pennant_fit) else NA, structure=fm.structure, rationale=fm.fit_rationale,
        risk=fm.fit_risk if ok(fm.fit_risk) else NA, tag=tag(fm.fit_rationale),
        # scatter position: x = log10 meetings on a 100..10,000 axis (0-100%), y = adviser agreement %
        x=round((math.log10(wl) - 2) / 2 * 100, 2), y=round(ag * 100, 2) if ok(ag) else None))

missing = [dict(family=k, tag=tag(f.loc[k].fit_rationale), structure=f.loc[k].structure,
                ciks="; ".join(str(f.loc[k].ciks).split(";"))) for k in f.index if k not in set(p.family)]

v = rs["validation"]
summary = dict(
    familiesListed=len(f), familiesScored=len(p), votes=num(rs["vote_rows"]), priorityVotes=num(rs["priority_rows"]),
    issQuoted=num(rs["key_items"]["ISS"]), glQuoted=num(rs["key_items"]["GL"]),
    issImputed=num(rs["rec_items"]["ISS"].get("imputed")),
    issGate=f"{v['ISS']['opposed_accuracy'] * 100:.1f}%", issGateN=v["ISS"]["opposed_held_out"],
    glGate=f"{v['GL']['opposed_accuracy'] * 100:.1f}%", glPassed=v["GL"]["passed"],
    issSetAside=v["ISS"]["quotes_set_aside"], runtime=rs["runtime_s"],
    tiers={t: int(p.priority.str.startswith(f"Tier {t}").sum()) for t in (1, 2, 3)},
    segments=rs["segments"], high=int((p.confidence == "high").sum()))

out_dir.mkdir(parents=True, exist_ok=True)
out = out_dir / "pennant-data.json"
out.write_text(json.dumps(dict(summary=summary, rows=rows, missing=missing), indent=0))
print(f"wrote {out}: {len(rows)} scored families, {len(missing)} not scored")
