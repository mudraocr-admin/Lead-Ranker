"""One-off: record the SAI proxy-voting tags produced in the build session.

Same arrangement as seed_iss_classifications.py: no API credentials were set,
so Claude tagged each family's SAI excerpt (data/processed/sai_snippets/)
inside the Claude Code session. Written to data/ai/sai_tags.json with
source="claude-session". Delete that file to re-run through the API.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R, E, REC = "research", "execution", "recommendations"


def t(advisor, roles, own, team, ev):
    return {"advisor": advisor, "roles": roles, "own_guidelines": own, "stewardship_team": team, "evidence": ev[:300]}


TAGS = {
    "AMG": t("both", [R, E, REC], False, True, "GW&K utilizes proxy voting guidelines developed by Glass Lewis & Co. ... which provides GW&K recommendations on ballot items (ISS receives holdings daily for proxy voting)"),
    "Alger": t("ISS", [E, REC], False, False, "ISS issues voting recommendations and casts votes ... Alger Management has instructed ISS to base its recommendations on ISS' Socially Responsible Investment Proxy Voting Guidelines."),
    "Allspring": t("ISS", [R, E, REC], True, True, "proxy matters are generally voted in accordance with Allspring's voting policy at ISS designed to implement Allspring's custom enhancements to the ISS Global Benchmark"),
    "Ariel": t("ISS", [R, E], True, True, "The Adviser has retained ISS for the purpose of receiving, cataloging, voting (based upon the Adviser's direction) and reporting proxies as well as to obtain its proxy research."),
    "Artisan": t("both", [R, E], True, False, "ISS and Glass, Lewis & Co., Artisan Partners' proxy voting service providers ... The guidelines are based on Artisan Partners' own research and analyses"),
    "Baird": t("ISS", [REC], False, True, "authorized the Advisor to retain a third party proxy voting service, such as ISS, to provide recommendations on proxy votes ... the Advisor submits the matter to its proxy voting committee."),
    "Brown Advisory": t("ISS", [R, E, REC], True, True, "our general Proxy Voting Guidelines, developed in consultation with ISS ... While we use the recommendations of ISS as a baseline for our voting, especially for routine management proposals, we vote each proposal"),
    "Calamos": t("Glass Lewis", [R, E, REC], True, False, "Glass Lewis facilitates the voting of each proxy by applying Calamos' custom proxy voting rules"),
    "Causeway": t("ISS", [R, E], False, False, "ISS Proxy research and recordkeeping service"),
    "Columbia": t("both", [R, E], True, False, "The Investment Manager has retained both Institutional Shareholder Services Inc. and Glass Lewis & Company, LLC to provide proxy research services."),
    "Delaware / Macquarie": t("Glass Lewis", [R, E, REC], False, False, "The Adviser pre-populates votes with Glass Lewis to help ensure all proxies are voted and such proxies are voted consistent with Glass Lewis' recommendations."),
    "Diamond Hill": t("none", [], True, False, "The Proxy Policy sets forth the Adviser's voting guidelines ... Each vote is ultimately determined on a case-by-case basis"),
    "Dodge & Cox": t("none", [], True, False, "SAI Appendix B carries Dodge & Cox's own Proxy Voting Policies and Procedures; no third-party proxy adviser is named"),
    "Federated Hermes": t("Glass Lewis", [E], False, False, "Proxy Voting Administrator Glass Lewis & Co., LLC"),
    "Franklin Templeton": t("both", [R, E, REC], True, True, "the Investment Managers subscribe to ISS ... also subscribe to Glass, Lewis & Co. ... The Proxy Group will vote such proxies in accordance with the Custom Guidelines"),
    "Harbor": t("both", [R, E, REC], False, False, "Certain of the Subadvisors' proxy voting policies and procedures make reference to ISS and/or Glass Lewis voting guidelines."),
    "Hartford": t("Glass Lewis", [], False, False, "Portfolio holdings are disclosed on a daily basis to ... Glass Lewis & Company, LLC (role not stated in the excerpt)"),
    "Invesco": t("both", [R, E], True, False, "Invesco has retained two independent third-party proxy voting service providers to provide proxy support globally: ISS and Glass Lewis"),
    "Janus Henderson": t("ISS", [R, E], True, True, "The Proxy Voting Committee develops Janus Henderson Investors' positions on all major corporate issues, maintains and updates the Guidelines"),
    "Lord Abbett": t("none", [], True, False, "Appendix C - Proxy Voting Policy; no third-party proxy adviser named in the excerpt"),
    "MFS": t("ISS", [E], True, True, "the proxy administration firm for MFS and its clients, including the MFS Funds, is ISS ... the MFS Proxy Voting Committee"),
    "Neuberger Berman": t("Glass Lewis", [R, E], True, True, "NBIA utilizes Glass Lewis to vote proxies in accordance with NBIA's voting guidelines ... the Global Head of Stewardship and Sustainable Investing"),
    "Parnassus": t("both", [R, E, REC], True, False, "While ISS and Glass Lewis provide research on proxy ballot items, Parnassus conducts its own independent analysis to ensure all proxy votes are cast in accordance with our Policies."),
    "Principal": t("both", [R, E], True, True, "we subscribe to research services from vendors such as ISS and Glass Lewis ... The Investment Stewardship Team"),
    "Putnam": t("ISS", [R, E], True, True, "Putnam has engaged ISS to process proxy votes ... Annually prepares the Proxy Voting Guidelines and distributes them to the Proxy Committee"),
    "Schwab": t("both", [R, E], True, True, "CSIM has retained Glass Lewis and ISS ... voted in accordance with CSIM's Proxy Voting Guidelines ... the Investment Stewardship Team"),
    "Thrivent": t("ISS", [R, E, REC], True, True, "For certain proposal types, Thrivent will provide standing instructions to ISS to vote proxies based on the recommendation of the Benchmark Guidelines"),
    "Victory": t("ISS", [R, E, REC], True, True, "The Adviser's Proxy Committee determines how proxies are voted by following established guidelines ... retained ISS to ... provide voting recommendations, vote shares according to our instructions"),
    "Virtus": t("ISS", [R, E, REC], False, True, "KAR fully reviews and approves the ISS Proxy Voting Guidelines and follows their recommendations on most issues brought to a shareholder vote."),
}

path = ROOT / "data/ai/sai_tags.json"
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({k: {"source": "claude-session", "answer": v} for k, v in TAGS.items()},
                           indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
print(f"{len(TAGS)} families tagged")
