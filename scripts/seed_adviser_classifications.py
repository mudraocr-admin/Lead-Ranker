"""One-off: record the adviser-snippet classifications produced in the build session.

No Anthropic API credentials were configured when the demo was built, so the
"small AI step" for the ISS and Glass Lewis answer keys was done by Claude
inside the Claude Code session that wrote this repo, reading
data/processed/{iss,gl}_snippets.parquet company by company. This script writes
those answers into the same caches the pipeline's API path writes to
(data/ai/iss_items.json, data/ai/gl_items.json), tagged source="claude-session".
Delete a cache file to re-run that step against the API instead.
"""
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

SOP = ["executive compensation", "named executive", "say on pay", "say-on-pay", "compensation of"]
EQ = ["incentive plan", "equity plan", "stock plan", "omnibus", "award plan", "equity incentive"]
A, F, W = "AGAINST", "FOR", "WITHHOLD"


def it(t, label, kw, iss, board, opp, ev):
    return dict(item_type=t, label=label, keywords=kw, adviser_rec=iss, board_rec=board,
                opposed_board=opp, evidence=ev[:300])


ITEMS = {
    # --- proxy contests -------------------------------------------------
    "1000209": [  # Medallion Financial vs BIMIZCI
        it("contested_director", "John Kiernan", [], F, A, True, "Both Recommend Stockholders Vote on the BLUE Proxy Card FOR Election of Two BIMIZCI Nominees ... John Kiernan and Eric Kelly"),
        it("contested_director", "Eric Kelly", [], F, A, True, "Both Recommend Stockholders Vote on the BLUE Proxy Card FOR Election of Two BIMIZCI Nominees ... John Kiernan and Eric Kelly"),
        it("contested_director", "Alvin Murstein", [], W, F, True, "ISS and Glass Lewis Both Recommend Stockholders WITHHOLD Support from Executive Chair Alvin Murstein and ... Cynthia Hallenbeck"),
        it("contested_director", "Cynthia Hallenbeck", [], W, F, True, "ISS and Glass Lewis Both Recommend Stockholders WITHHOLD Support from Executive Chair Alvin Murstein and ... Cynthia Hallenbeck"),
    ],
    "1309108": [  # WEX vs Impactive
        it("contested_director", "Kurt Adams", [], F, A, True, "ISS has recommended that shareholders vote on the WHITE proxy card FOR the election of Impactive director nominees Kurt Adams and Lauren Taylor Wolfe"),
        it("contested_director", "Lauren Taylor Wolfe", [], F, A, True, "ISS has recommended that shareholders vote on the WHITE proxy card FOR the election of Impactive director nominees Kurt Adams and Lauren Taylor Wolfe"),
        it("contested_director", "Nancy Altobello", [], W, F, True, "... and WITHHOLD on director Nancy Altobello and Compensation Committee Chair Stephen Smith."),
        it("contested_director", "Stephen Smith", [], W, F, True, "... and WITHHOLD on director Nancy Altobello and Compensation Committee Chair Stephen Smith."),
    ],
    "1726445": [  # Seer vs Radoff-JEC
        it("contested_director", "Howard H. Berman", [], F, A, True, "ISS Recommends Stockholders Vote FOR Radoff-JEC Group Nominees Howard H. Berman and Luis E. Rinaldini"),
        it("contested_director", "Luis E. Rinaldini", [], F, A, True, "ISS Recommends Stockholders Vote FOR Radoff-JEC Group Nominees Howard H. Berman and Luis E. Rinaldini"),
        it("contested_director", "Terrance McGuire", [], W, F, True, "WITHHOLD Support on Seer Directors Terrance McGuire, Dipchand Nishar and Nicolas Roelofs"),
        it("contested_director", "Dipchand Nishar", [], W, F, True, "WITHHOLD Support on Seer Directors Terrance McGuire, Dipchand Nishar and Nicolas Roelofs"),
        it("contested_director", "Nicolas Roelofs", [], W, F, True, "WITHHOLD Support on Seer Directors Terrance McGuire, Dipchand Nishar and Nicolas Roelofs"),
        it("other", "Ratification of NOL pill", ["rights", "nol"], A, F, True, "and vote AGAINST the ratification of the NOL pill"),
    ],
    "50493": [  # Ingles Markets vs Summer Road
        it("contested_director", "Rory A. Held", [], F, A, True, "All Three Independent Proxy Advisors Recommend Ingles Markets Shareholders Vote for Rory A. Held"),
    ],
    "1326200": [  # Genco vs Diana: ISS backed the board slate, opposed the pill and the equity plan
        it("equity_plan", "Proposal 3 equity incentive plan", EQ, A, F, True, "Institutional Shareholder Services (ISS) has recommended shareholders vote AGAINST Genco's equity incentive plan"),
        it("other", "Ratification of shareholder rights plan", ["rights"], A, F, True, "ISS recommendation that Genco shareholders vote AGAINST the ratification of Genco's poison pill"),
        it("contested_director", "Genco board nominees", [], F, F, False, "all three independent proxy advisory firms - ISS, Glass Lewis and Egan-Jones - recommend shareholders vote FOR ALL of Genco's director nominees"),
    ],
    "1864843": [
        it("contested_director", "Two of nine incumbent trustees (unnamed)", [], A, F, True, "ISS recommended that shareholders vote in favor of seven of nine incumbent Board nominees"),
    ],
    "1396814": [it("contested_director", "Pacira board nominees", [], F, F, False, "ISS has recommended that Pacira stockholders vote FOR each of Pacira's three director nominees ... and AGAINST all three of DOMA Perpetual's nominees")],
    "18498": [it("contested_director", "Genesco board nominees", [], F, F, False, "ISS recommends that shareholders vote FOR all nine of the Company's directors on the WHITE proxy card")],
    "1856437": [it("contested_director", "VS&Co board nominees", [], F, F, False, "ISS ... recommended that VS&Co shareholders vote FOR all nine of the Company's director nominees")],
    "807882": [it("contested_director", "Jack in the Box board nominees", [], F, F, False, "ISS has recommended that Jack in the Box shareholders vote FOR all 10 of the Company's director nominees")],

    # --- M&A / strategic -------------------------------------------------
    "1465740": [it("mna", "Proposed CCM merger", ["merger", "cross country", "crosscountry"], A, F, True, "ISS and Glass Lewis have recommended that stockholders of Two Harbors VOTE AGAINST the Proposed CCM Merger")],
    "1603923": [it("mna", "Redomestication proposals", ["redomestic", "scheme", "merger", "texas"], A, F, True, "ISS and Glass Lewis have each recommended that shareholders vote against these items ... ISS's recommendation against Weatherford's proposal")],

    # --- shareholder proposals --------------------------------------------
    "19617": [it("shareholder_proposal", "Proposal 5 independent chair", ["independent", "chair"], F, A, True, "PROPOSAL 5: SHAREHOLDER PROPOSAL REGARDING INDEPENDENT CHAIR The ISS Report makes assertions that ... claim support for this proposal is warranted.")],

    # --- say-on-pay ---------------------------------------------------------
    "1063761": [it("say_on_pay", "Say-on-Pay Proposal", SOP, A, F, True, "ISS has recommended that shareholders vote against our Say-on-Pay Proposal")],
    "1070423": [it("say_on_pay", "Proposal 3 advisory vote on 2025 NEO compensation", SOP, A, F, True, "ISS has recommended that PAGP shareholders and PAA unitholders vote against Proposal 3 ... Advisory Vote to Approve Our 2025 Named Executive Officer Compensation")],
    "1581990": [it("say_on_pay", "Proposal 3 advisory vote on 2025 NEO compensation", SOP, A, F, True, "ISS has recommended that PAGP shareholders and PAA unitholders vote against Proposal 3 ... Advisory Vote to Approve Our 2025 Named Executive Officer Compensation")],
    "1093691": [it("say_on_pay", "Say on pay", SOP, A, F, True, "We respectfully disagree with ISS's recommendation to vote against our say on pay proposal.")],
    "1130713": [it("say_on_pay", "Say on Pay", SOP, A, F, True, "We recognize that ISS has recommended a vote AGAINST our Say on Pay proposal")],
    "1173420": [it("say_on_pay", "Say-on-Pay", SOP, A, F, True, "ISS recommending a vote Against NOVAGOLD's Say-on-Pay.")],
    "1173489": [it("say_on_pay", "Proposal 2 say-on-pay", SOP, A, F, True, "ISS has recommended against Proposal 2, the advisory say-on-pay proposal")],
    "1497770": [it("say_on_pay", "Say-on-Pay Proposal", SOP, A, F, True, "ISS has recommended stockholders vote against our say-on-pay proposal due to concerns regarding a one-time, multi-year award")],
    "1580808": [it("say_on_pay", "Proposal 2 say on pay", SOP, A, F, True, "ISS recommends a vote against Proposal 2 ... advisory resolution to ratify named executive officer compensation")],
    "1640147": [it("say_on_pay", "Say-on-Pay proposal", SOP, A, F, True, "ISS and Glass Lewis issued their respective proxy voting reports recommending that stockholders vote against the Say-on-Pay proposal")],
    "1768267": [it("say_on_pay", "Say-on-pay", SOP, A, F, True, "Institutional Shareholder Services (ISS), however, is recommending against the say-on-pay proposal.")],
    "46195": [it("say_on_pay", "Say-on-Pay Proposal", SOP, A, F, True, "ISS and Glass Lewis ... recommended voting against the Say-on-Pay Proposal")],
    "74208": [it("say_on_pay", "Say-on-Pay Proposal", SOP, A, F, True, "ISS has recommended that shareholders vote AGAINST the Say-on-Pay Proposal. The Company's Board strongly disagrees")],

    # --- equity plans (N-PX category COMPENSATION -> pay) ---------------------
    "1042776": [
        it("equity_plan", "Third Amended and Restated Omnibus Incentive Plan", EQ, A, F, True, "the Company was informed by ISS that the proposal to approve the Third Amended and Restated Omnibus Incentive Plan ... we firmly disagree with its recommendation"),
        it("say_on_pay", "2025 NEO say-on-pay", SOP, F, F, False, "our approach resulted in ISS recommending a vote FOR our 2025 named executive officer compensation say-on-pay proposal"),
    ],
    "1062231": [it("equity_plan", "Plan Amendment", EQ, A, F, True, "In the ISS Report, ISS recommended that stockholders vote against the proposal to approve the Plan Amendment.")],
    "1108524": [it("equity_plan", "Proposal 2 equity plan", EQ, A, F, True, "this year ISS is recommending that stockholders vote against Proposal 2 because of a formulaic application of its Equity [Plan Scorecard]")],
    "1288469": [it("equity_plan", "Equity Plan Proposal", EQ, A, F, True, "ISS issued a report recommending that our stockholders vote against the Equity Plan Proposal.")],
    "1304492": [it("equity_plan", "Proposal 3 share increase to 2023 Plan", EQ + ["2023 plan"], A, F, True, "In the ISS Report, ISS recommended that stockholders vote against Proposal 3 ... the size of the share increase to 2023 Plan")],
    "1352010": [
        it("equity_plan", "Amendment to the 2025 Plan", EQ + ["2025 plan"], A, F, True, "ISS has recommended a vote AGAINST the amendment to the 2025 Plan"),
        it("say_on_pay", "Say-on-pay", SOP, F, F, False, "ISS' recommendation to vote FOR our 2025 named executive officer compensation say-on-pay proposal"),
    ],
    "1360901": [it("equity_plan", "Proposal No. 4 equity plan", EQ, A, F, True, "ISS recommends shareholders vote against Proposal No. 4 ... our response to ISS's recommendation against our equity plan proposal")],
    "1395937": [it("other_compensation", "Proposal No. 4", EQ + ["employee stock purchase"], A, F, True, "Institutional Shareholder Services (ISS) has recommended a vote against Proposal No. 4. We disagree with ISS's recommendation.")],
    "1515673": [it("equity_plan", "Equity plan", EQ, A, F, True, "ISS has recommended AGAINST this proposal. We respectfully disagree ... Equity awards are critical")],
    "1597033": [it("equity_plan", "Proposal 3 equity plan", EQ, A, F, True, "ISS ultimately recommended stockholders vote against Proposal 3, applying quantitative tests ... equity compensation needs")],
    "1694426": [it("equity_plan", "Proposal 3 2026 Long-Term Incentive Plan", EQ + ["long-term incentive"], A, F, True, "ISS recommended voting against Proposal 3 ... that would approve a 2026 Long-Term Incentive Plan")],
    "1872789": [it("equity_plan", "2022 Plan Amendment", EQ + ["2022"], A, F, True, "In the ISS Report, ISS recommended that stockholders vote against the proposal to approve the 2022 Plan Amendment.")],
    "34782": [
        it("equity_plan", "Proposal 3 amended 1982 Executive Incentive Plan", EQ + ["executive incentive"], A, F, True, "ISS supports the other proposals but recommends voting AGAINST Proposal 3 (approval of the amended 1982 Executive Incentive Plan)"),
        it("uncontested_director", "Timothy Ozark", [], A, F, True, "ISS recommends voting AGAINST Timothy Ozark and Todd Schurz"),
        it("uncontested_director", "Todd Schurz", [], A, F, True, "ISS recommends voting AGAINST Timothy Ozark and Todd Schurz"),
    ],
    "720672": [it("equity_plan", "Item 4 equity plan", EQ, A, F, True, "Institutional Shareholder Services (ISS) has recommended a vote against Item 4 ... our equity compensation approach")],
    "874501": [it("equity_plan", "Proposal 4 equity plan", EQ, A, F, True, "ISS Recommendation: AGAINST (Proposal 4 only) - citing plan cost, burn rate, share recycling")],
    "898437": [it("equity_plan", "Proposal 4 Seventh Amended Plan", EQ + ["seventh amended"], A, F, True, "ISS issued an unfavorable recommendation regarding Proposal 4, in part because ISS estimated that the Seventh Amended Plan is excessively dilutive")],
    "910329": [it("equity_plan", "Amended and restated Plan", EQ, A, F, True, "the proposal to amend and restate the Plan received an unfavorable recommendation as determined by ISS")],
    "910638": [it("equity_plan", "Proposal 5 Equity Plan", EQ, A, F, True, "ISS recommended that stockholders vote AGAINST Proposal 5, the Equity Plan Proposal")],

    # --- outside the 4 priority categories (kept for completeness, dropped later) ---
    "34088": [it("other", "Texas redomiciliation proposal", ["texas", "reincorporat", "redomic"], A, F, True, "ISS recommended Against all identified Texas redomestication proposals at XOM")],
    "1895262": [it("other", "UK share allotment authorities", ["allot", "pre-emption"], A, F, True, "the recommendation to vote Against these proposals by Institutional Shareholder Services (ISS)")],
    "1284812": [it("uncontested_director", "Independent Director Nominees", [], A, F, True, "ISS issued a recommendation with respect to the Independent Director Nominees that is inconsistent with our Board's recommendation.")],
    "1287750": [it("uncontested_director", "Steven B. McKeever", [], A, F, True, "ISS has issued a voting recommendation that is inconsistent with the recommendation of the board ... on the election of Steven B.")],
    "1307954": [it("uncontested_director", "Jose Antonio Munoz Barcelo", [], A, F, True, "ISS and Glass Lewis have each recommended a vote against the re-election of Jose Antonio Munoz Barcelo")],
    "1318220": [it("uncontested_director", "Susan Lee", [], A, F, True, "ISS has recommended a vote against the re-election of Susan Lee as an independent director")],
    "1320461": [it("uncontested_director", "All director nominees", [], A, F, True, "ISS has recommended that its clients vote against the Company's director nominees")],
    "1529377": [it("uncontested_director", "William S. Benjamin", [], A, F, True, "ISS has issued voting recommendations that are inconsistent with the recommendations of the board ... on the election of William S.")],
    "1801169": [it("uncontested_director", "CEO director", [], A, F, True, "Proxy advisors at ISS and Glass Lewis have recommended shareholders to vote against me")],
    "1820721": [it("uncontested_director", "Board chair (Forth)", [], W, F, True, "ISS issued a withhold recommendation against Mr. Forth, the Chair of our Board")],
    "33002": [it("uncontested_director", "Michael D. Magill", [], A, F, True, "ISS has recommended that shareholders vote AGAINST the election of Michael D. Magill")],
    "730272": [it("uncontested_director", "Audit Committee members", [], A, F, True, "ISS has recommended that shareholders vote against the re-election of Audit Committee members")],
    "92380": [it("uncontested_director", "Christopher P. (director)", [], A, F, True, "ISS has recommended a vote against Christopher P.")],
    "926282": [it("uncontested_director", "Audit Committee members", [], A, F, True, "ISS has recommended that stockholders vote against the re-election of the members of the Audit Committee")],
    "1044777": [it("uncontested_director", "Michael McConnell", [], A, F, True, "ISS recommended a vote FOR the election of all of OneSpan's director nominees except for Michael McConnell")],
    "1035443": [it("uncontested_director", "James Cain", [], A, F, True, "ISS recommended that the Company's shareholders vote against the election of ... James Cain")],
    "105319": [
        it("uncontested_director", "Carney Hawks", [], A, F, True, "we respectfully but firmly disagree with ISS's recommendation that shareholders vote against Mr. Davis and Mr. Hawks"),
        it("say_on_pay", "Say-on-Pay Proposal", SOP, A, F, True, "we respectfully but firmly disagree ... with both firms' recommendations with respect to the Say-on-Pay Proposal"),
    ],
}

# --------------------------------------------------------------------------
# Glass Lewis, from data/processed/gl_snippets.parquet
GL_ITEMS = {
    "1000209": [  # Medallion vs BIMIZCI
        it("contested_director", "John Kiernan", [], F, A, True, "Both Leading Independent Proxy Advisory Firms Have Now Recommended FOR BIMIZCI Nominees John Kiernan and Eric Kelly"),
        it("contested_director", "Eric Kelly", [], F, A, True, "Both Leading Independent Proxy Advisory Firms Have Now Recommended FOR BIMIZCI Nominees John Kiernan and Eric Kelly"),
        it("contested_director", "Alvin Murstein", [], W, F, True, "ISS and Glass Lewis Both Recommend Stockholders WITHHOLD Support from Executive Chair Alvin Murstein and Nominating & Governance Committee Chair Cynthia Hallenbeck"),
        it("contested_director", "Cynthia Hallenbeck", [], W, F, True, "Glass Lewis recommends that shareholders replace Cynthia Hallenbeck with a dissident candidate"),
    ],
    "1309108": [  # WEX vs Impactive
        it("contested_director", "Kurt Adams", [], F, A, True, "Glass Lewis Recommends Shareholders Vote FOR Impactive Nominees Kurt Adams and Lauren Taylor Wolfe"),
        it("contested_director", "Lauren Taylor Wolfe", [], F, A, True, "Glass Lewis Recommends Shareholders Vote FOR Impactive Nominees Kurt Adams and Lauren Taylor Wolfe"),
        it("contested_director", "Melissa Smith", [], W, F, True, "WITHHOLD Support on WEX Board Chair and CEO Melissa Smith and Compensation Committee Chair Stephen Smith"),
        it("contested_director", "Stephen Smith", [], W, F, True, "WITHHOLD Support on WEX Board Chair and CEO Melissa Smith and Compensation Committee Chair Stephen Smith"),
    ],
    "1726445": [  # Seer vs Radoff-JEC
        it("contested_director", "Howard H. Berman", [], F, A, True, "Glass Lewis Recommends Stockholders Vote FOR Radoff-JEC Group Nominees Howard H. Berman and Luis E. Rinaldini"),
        it("contested_director", "Luis E. Rinaldini", [], F, A, True, "Glass Lewis Recommends Stockholders Vote FOR Radoff-JEC Group Nominees Howard H. Berman and Luis E. Rinaldini"),
        it("contested_director", "Terrance McGuire", [], W, F, True, "WITHHOLD Support on Seer Directors Terrance McGuire and Dipchand Nishar"),
        it("contested_director", "Dipchand Nishar", [], W, F, True, "WITHHOLD Support on Seer Directors Terrance McGuire and Dipchand Nishar"),
        it("other", "Ratification of NOL pill", ["rights", "nol"], A, F, True, "Glass Lewis also recommends stockholders vote AGAINST the ratification of the NOL pill."),
    ],
    "50493": [it("contested_director", "Rory A. Held", [], F, A, True, "ISS, Glass Lewis and Egan-Jones All Endorse the Election of Rory A. Held")],
    "807882": [it("contested_director", "David Goebel", [], A, F, True, "Glass Lewis ... has recommended that shareholders vote AGAINST Chairman David Goebel")],
    "1326200": [it("contested_director", "Genco board nominees", [], F, F, False, "Glass Lewis and Egan-Jones also recommend that Genco shareholders vote WITHHOLD on all nominees of Diana")],
    "1864843": [it("contested_director", "ECAT board nominees", [], F, F, False, "Glass Lewis ... recommended that shareholders vote on the WHITE proxy card FOR all nine of the Board nominees")],
    "1260729": [it("contested_director", "GDV board nominees", [], F, F, False, "Glass Lewis has recommended that the shareholders vote FOR ALL of the Board's nominees on the WHITE proxy card")],
    "1396814": [it("contested_director", "Pacira board nominees", [], F, F, False, "Glass Lewis Recommends Stockholders Vote FOR All of Pacira's Director Nominees")],

    "1465740": [it("mna", "Proposed CCM merger", ["merger", "cross country", "crosscountry"], A, F, True, "Glass Lewis ... has recommended that TWO stockholders VOTE AGAINST the CrossCountry Mortgage (CCM) merger proposal")],
    "1603923": [it("mna", "Redomestication proposals", ["redomestic", "scheme", "merger", "texas"], A, F, True, "ISS and Glass Lewis have each recommended that shareholders vote against these items.")],

    "19617": [it("shareholder_proposal", "Proposal 5 independent chair", ["independent", "chair"], F, A, True, "Proposal 5: Support For the Shareholder Proposal Requesting an Independent Chair - The Glass Lewis Benchmark Policy asserts that an independent chair is nearly always preferable")],

    "102752": [it("say_on_pay", "Proposal 3 say-on-pay", SOP, A, F, True, "Glass Lewis recommends that stockholders vote against Proposal 3 (approval, on a non-binding advisory basis, of the compensation of the Company's named executive officers).")],
    "105319": [it("say_on_pay", "Say-on-Pay Proposal", SOP, A, F, True, "we respectfully but firmly disagree ... with both firms' recommendations with respect to the Say-on-Pay Proposal")],
    "1173420": [it("say_on_pay", "Say-on-Pay", SOP, A, F, True, "NOVAGOLD takes note of Glass Lewis' recommendation to vote AGAINST NOVAGOLD's Proposal 6.00 Advisory Vote on Executive Compensation.")],
    "1640147": [it("say_on_pay", "Say-on-Pay proposal", SOP, A, F, True, "ISS and Glass Lewis issued their respective proxy voting reports recommending that stockholders vote against the Say-on-Pay proposal")],
    "203596": [it("say_on_pay", "Say-on-Pay proposal", SOP, A, F, True, "Glass Lewis has issued its Proxy Paper with voting recommendations ... including the Say-on-Pay proposal, where it has recommended [against]; Our Board continues to recommend that you vote FOR")],
    "46195": [it("say_on_pay", "Say-on-Pay Proposal", SOP, A, F, True, "ISS and Glass Lewis ... recommended voting against the Say-on-Pay Proposal")],
    "921638": [it("say_on_pay", "Say on Pay", SOP, A, F, True, "The Glass Lewis recommendation centers on SSR Mining's pay practices ... recommendations Glass Lewis has made against our proposal")],

    # outside the priority categories
    "1580808": [it("uncontested_director", "Eric B. Singer", [], W, F, True, "the Glass Lewis Proxy Paper ... recommends shareholders WITHHOLD support for the re-election of director Eric B. Singer")],
    "34088": [it("other", "Texas redomicile proposal", ["texas", "reincorporat", "redomic"], A, F, True, "Glass Lewis developed an ill-informed recommendation against our proposal to redomicile")],
    "2071778": [it("other", "Consent to call special meeting", ["special meeting"], F, A, True, "Glass Lewis and Egan-Jones issued reports recommending that shareholders CONSENT to the calling of the special meeting")],
    "34782": [it("uncontested_director", "Timothy Ozark", [], A, F, True, "Under that screen, it recommends voting AGAINST Mr. Ozark. We disagree with Glass Lewis.")],
    "730272": [it("uncontested_director", "Martin D. Madaus", [], A, F, True, "Glass Lewis has recommended that shareholders vote against the re-election of Dr. Martin D. Madaus")],
    "893949": [it("uncontested_director", "John M. Starcher, Jr.", [], A, F, True, "have recommended a vote against the re-election of John M. Starcher, Jr.")],
    "1307954": [it("uncontested_director", "Jose Antonio Munoz Barcelo", [], A, F, True, "ISS and Glass Lewis have each recommended a vote against the re-election of Jose Antonio Munoz Barcelo")],
    "92380": [it("uncontested_director", "Nominating committee members", [], A, F, True, "Glass Lewis has ... recommended votes against Nominating and Corporate Governance Committee members")],
    "1801169": [it("uncontested_director", "CEO director", [], A, F, True, "Proxy advisors at ISS and Glass Lewis have recommended shareholders to vote against me")],
}


def write(task: str, snippets: str, items: dict):
    s = pd.read_parquet(ROOT / "data/processed" / snippets)
    names = s.groupby("subject_cik").subject_name.first().to_dict()
    out = {cik: {"source": "claude-session", "answer": {"company": name, "items": items.get(cik, [])}}
           for cik, name in names.items()}
    path = ROOT / "data/ai" / f"{task}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    print(f"{task}: {len(out)} companies, {sum(len(v['answer']['items']) for v in out.values())} items; "
          f"classified but missing from snippets: {sorted(set(items) - set(names))}")


write("iss_items", "iss_snippets.parquet", ITEMS)
write("gl_items", "gl_snippets.parquet", GL_ITEMS)
