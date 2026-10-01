"""One-off: classifications for companies added when the answer-key search was
widened to Jul-Dec 2025 (adviser_key.search_from: 2025-07-01).

Same method as seed_adviser_classifications.py: done by Claude in the Claude Code
session, reading data/processed/{iss,gl}_snippets.parquet company by company,
and recorded as source="claude-session". Only priority-category items (contested
directors, pay, M&A, shareholder proposals) are recorded; companies whose
snippets state no such call get an empty list. This script only ADDS companies
missing from the cache; it never overwrites an existing answer.
"""
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EQ = ["incentive plan", "equity plan", "stock plan", "omnibus", "award plan", "equity incentive"]
MNA = ["merger", "transaction", "arrangement", "acquisition", "issuance", "sale"]
A, F, W = "AGAINST", "FOR", "WITHHOLD"


def it(t, label, kw, rec, board, opp, ev):
    return dict(item_type=t, label=label, keywords=kw, adviser_rec=rec, board_rec=board,
                opposed_board=opp, evidence=ev[:300])


def merger(label, rec, board, ev, kw=MNA):
    return it("mna", label, kw, rec, board, rec != board, ev)


ISS = {
    "16058": [it("equity_plan", "2025 Incentive Compensation Plan", EQ, A, F, True, "Proposal 3 relating to our proposed 2025 Incentive Compensation Plan (the \"2025 Plan\"), which ISS recommended a vote against.")],
    "85535": [merger("Share issuance for Sandstorm transaction", F, F, "Institutional Shareholder Services Inc. (\"ISS\"), have recommended that Royal Gold stockholders vote \"FOR\" the proposals related to the Sandstorm Transaction.", ["sandstorm", "issuance", "share"])],
    "717954": [it("contested_director", "__dissident_slate__", [], F, A, True, "ISS Recommends UniFirst Shareholders Vote \"FOR\" Engine Capital's New Director Candidates at 2026 Annual Meeting")],
    "718937": [merger("Merger with Alcon", F, F, "ISS is now recommending that all STAAR stockholders vote \"FOR\" STAAR's amended merger agreement with Alcon")],
    "814586": [it("contested_director", "__board_slate__", [], F, F, False, "Institutional Shareholder Services (\"ISS\") has recommended that shareholders vote FOR all of the Company's director nominees")],
    "866273": [it("equity_plan", "Amended 2020 Plan (Proposal 5)", EQ + ["2020"], A, F, True, "ISS has recommended a vote against Proposal 5, primarily due its view that the cost of the Amended 2020 Plan is excessive.")],
    "1020859": [it("equity_plan", "Fifth Amended and Restated Plan", EQ + ["plan"], A, F, True, "Institutional Shareholder Services (\"ISS\") recommended against the approval of our Fifth Amended and Restated Plan")],
    "1040161": [merger("Proposal 1 (sale transaction)", F, F, "the Board of Directors and both leading independent proxy voting advisory groups, Institutional Shareholder Services and Glass Lewis, recommended that shareholders vote FOR Proposal 1.")],
    "1067294": [
        it("contested_director", "Gilbert Davila", [], A, F, True, "ISS has recommended voting against re-electing Mr. Davila"),
        it("contested_director", "Julie Masino", [], F, F, False, "Both ISS and Glass Lewis recommend shareholders vote for Ms. Masino."),
    ],
    "1156388": [merger("Merger", F, F, "Independent proxy advisory firms ISS and Glass Lewis both recently issued recommendations in favor of the Merger proposal.")],
    "1178697": [it("contested_director", "__board_slate__", [], F, F, False, "The Sonim Board is delighted that ISS has recommended stockholders to vote for our nominees at next week's Annual Meeting.")],
    "1191070": [it("contested_director", "__board_slate__", [], F, F, False, "ISS ... has recommended that shareholders vote FOR BOTH of Neuphoria's highly-qualified director nominees on the WHITE proxy card.")],
    "1318605": [it("other_compensation", "CEO performance award (Proposal 4)", ["performance award", "performance-based", "award"], A, F, True, "This year, both firms once again recommend against our CEO performance award (Proposal 4)")],
    "1327318": [merger("Transaction with Fair Holdings", F, F, "Independent Proxy Advisory Firms ISS and Glass Lewis Recommend TrueCar Stockholders Vote \"FOR\" Pending Transaction with Fair Holdings")],
    "1393726": [merger("Merger", F, F, "In their recommendation FOR the Merger Proposal, ISS noted")],
    "1413898": [merger("Merger with Hearst", F, F, "Institutional Shareholder Services Inc., a leading independent proxy advisory firm, recommended that the Company's shareholders vote FOR the Merger.")],
    "1425287": [merger("Transaction with Motiv", F, F, "ISS and Glass Lewis both recommend that shareholders vote FOR the transaction with Motiv.", MNA + ["motiv"])],
    "1538716": [
        it("contested_director", "Warren Wilcox", [], F, A, True, "ISS Recommends Oportun Stockholders Vote FOR Findell Nominee Warren Wilcox"),
        it("contested_director", "Raul Vazquez", [], W, F, True, "ISS also recommended stockholders vote WITHHOLD on Chief Executive Officer (\"CEO\") Raul Vazquez"),
    ],
    "1628369": [merger("Redomiciliation", F, F, "ISS has recommended that the Company's shareholders vote \"FOR\" all ... Proposals Related to Cushman & Wakefield's Redomiciliation", ["redomicil", "scheme", "merger"])],
    "1704720": [it("contested_director", n, [], F, A, True, "follow the recommendations of ISS and Glass Lewis and vote \"FOR\" Mona Aboelnaga, Benjamin Duster, Dennis Prieto and Cherie Schaible")
                for n in ("Mona Aboelnaga", "Benjamin Duster", "Dennis Prieto", "Cherie Schaible")]
              + [it("contested_director", n, [], W, F, True, "follow the recommendations of ISS and Glass Lewis and vote ... \"WITHHOLD\" on Erika Meinhardt, Barry B. Moullet, James B. Stallings, Jr., and Frank P. Willey")
                 for n in ("Erika Meinhardt", "Barry B. Moullet", "James B. Stallings", "Frank P. Willey")],
    "1708341": [it("contested_director", "Roy Choi", [], F, A, True, "In fact, ISS recommended that stockholders vote for just one of Knighted's candidates, Roy Choi.")],
    "1725057": [merger("Merger", F, F, "ISS and Glass Lewis have both affirmed our Board of Directors' recommendation that stockholders vote \"FOR\" the Merger")],
    "1754301": [it("shareholder_proposal", "Simple majority vote", ["simple majority", "supermajority", "majority vote"], F, A, True, "ISS's misguided recommendation to support a stockholder proposal to implement a simple majority vote")],
    "1822250": [merger("Reorganization Proposal", A, F, "Institutional Shareholder Services (\"ISS\") issued a recommendation to ContextLogic's stockholders that they should vote against the Reorganization Proposal", ["reorganization", "merger"])],
    "1839341": [merger("Merger with CoreWeave", A, F, "Leading Proxy Advisory Firm Institutional Shareholder Services Recommends Core Scientific Shareholders Vote \"AGAINST\" Sale to CoreWeave")],
}

GL = {
    "717954": [it("contested_director", "__dissident_slate__", [], F, A, True, "All Three Leading Independent Proxy Advisors Recommend UniFirst Shareholders Vote \"FOR\" Engine Capital's New Director Candidates")],
    "718937": [merger("Merger with Alcon", A, F, "Glass, Lewis & Co. issued its recommendation that shareholders vote against the proposed sale of STAAR to Alcon.")],
    "866273": [it("equity_plan", "Amended 2020 Plan (Proposal 5)", EQ + ["2020"], F, F, False, "Glass, Lewis & Co., LLC has recommended a vote FOR Proposal 5.")],
    "1020859": [it("equity_plan", "Fifth Amended and Restated Plan", EQ + ["plan"], F, F, False, "Glass Lewis ... recommended a vote for the Fifth Amended and Restated Plan.")],
    "1040161": [merger("Proposal 1 (sale transaction)", F, F, "both leading independent proxy voting advisory groups, Institutional Shareholder Services and Glass Lewis, recommended that shareholders vote FOR Proposal 1.")],
    "1067294": [
        it("contested_director", "Gilbert Davila", [], A, F, True, "Glass Lewis has recommended voting against Mr. Davila and another independent director, Jody Bilney"),
        it("contested_director", "Jody Bilney", [], A, F, True, "Glass Lewis has recommended voting against Mr. Davila and another independent director, Jody Bilney"),
        it("contested_director", "Julie Masino", [], F, F, False, "Both ISS and Glass Lewis recommend shareholders vote for Ms. Masino."),
    ],
    "1156388": [merger("Merger", F, F, "Independent proxy advisory firms ISS and Glass Lewis both recently issued recommendations in favor of the Merger proposal.")],
    "1178697": [it("contested_director", n, [], F, A, True, "Glass Lewis & Co. has recommended ... voting \"FOR\" the election of three director candidates (Joseph M. Glynn, Surendra Singh and Michael Wallace) proposed by AJP and Orbic")
                for n in ("Joseph M. Glynn", "Surendra Singh", "Michael Wallace")],
    "1191070": [it("contested_director", "Stephen Doberstein", [], F, A, True, "Glass Lewis Recommends Voting FOR Lynx1's Independent Nominee, Stephen Doberstein, Ph.D.")],
    "1318605": [it("other_compensation", "CEO performance award (Proposal 4)", ["performance award", "performance-based", "award"], A, F, True, "This year, both firms once again recommend against our CEO performance award (Proposal 4)")],
    "1327318": [merger("Transaction with Fair Holdings", F, F, "Independent Proxy Advisory Firms ISS and Glass Lewis Recommend TrueCar Stockholders Vote \"FOR\" Pending Transaction with Fair Holdings")],
    "1332349": [it("contested_director", "__dissident_slate__", [], F, A, True, "Glass, Lewis & Co. (\"Glass Lewis\"), has recommended that stockholders vote FOR the election of Ortelius nominees")],
    "1413898": [merger("Merger with Hearst", F, F, "On August 29, 2025, Glass, Lewis & Co. ... recommended that the Company's shareholders vote FOR the Merger.")],
    "1425287": [merger("Transaction with Motiv", F, F, "ISS and Glass Lewis both recommend that shareholders vote FOR the transaction with Motiv.", MNA + ["motiv"])],
    "1628369": [merger("Redomiciliation", F, F, "Glass Lewis Joins ISS in Recommending Shareholders Vote \"FOR\" All Proposals Related to Cushman & Wakefield's Redomiciliation", ["redomicil", "scheme", "merger"])],
    "1704720": [it("contested_director", n, [], F, A, True, "Glass Lewis Recommends Cannae Shareholders Vote \"FOR\" All Four of Carronade's Director Nominees")
                for n in ("Mona Aboelnaga", "Benjamin Duster", "Dennis Prieto", "Cherie Schaible")]
              + [it("contested_director", n, [], W, F, True, "follow the recommendations of ISS and Glass Lewis and vote ... \"WITHHOLD\" on Erika Meinhardt, Barry B. Moullet, James B. Stallings, Jr., and Frank P. Willey")
                 for n in ("Erika Meinhardt", "Barry B. Moullet", "James B. Stallings", "Frank P. Willey")],
    "1725057": [merger("Merger", F, F, "ISS and Glass Lewis have both affirmed our Board of Directors' recommendation that stockholders vote \"FOR\" the Merger")],
    "1839341": [merger("Merger with CoreWeave", A, F, "Glass Lewis Joins ISS In Recommending Core Scientific Shareholders Vote \"AGAINST\" Proposed Sale to CoreWeave")],
}


def add(task: str, snippets: str, items: dict):
    s = pd.read_parquet(ROOT / "data/processed" / snippets)
    names = s.groupby("subject_cik").subject_name.first().to_dict()
    path = ROOT / "data/ai" / f"{task}.json"
    cache = json.loads(path.read_text(encoding="utf-8"))
    new = {cik: name for cik, name in names.items() if cik not in cache}
    for cik, name in new.items():
        cache[cik] = {"source": "claude-session", "answer": {"company": name, "items": items.get(cik, [])}}
    path.write_text(json.dumps(cache, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"{task}: added {len(new)} companies, {sum(len(items.get(c, [])) for c in new)} items; "
          f"classified but not in snippets: {sorted(set(items) - set(names))}")


add("iss_items", "iss_snippets.parquet", ISS)
add("gl_items", "gl_snippets.parquet", GL)
