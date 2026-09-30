"""Step 3: what each family's SAI (filed in 485BPOS) says about proxy advisers."""
from __future__ import annotations

import re
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from . import ai
from .config import PROCESSED
from .edgar import Edgar
from .text import clean

ADVISER = re.compile(r"Institutional Shareholder Services|\bISS\b|Glass,? Lewis")
PROXY_CTX = re.compile(r"prox|vot", re.I)
SECTION = re.compile(r"proxy voting (policies|policy|procedures|guidelines)", re.I)


class SaiTag(BaseModel):
    advisor: Literal["ISS", "Glass Lewis", "both", "none"]
    roles: list[Literal["research", "execution", "recommendations"]] = Field(
        description="What the adviser does for the fund: research/analysis, vote execution "
                    "and recordkeeping, and/or voting recommendations")
    own_guidelines: bool = Field(description="Votes under the adviser's own proxy voting guidelines, "
                                             "not the proxy adviser's benchmark policy")
    stewardship_team: bool = Field(description="Mentions an in-house stewardship, governance, or proxy "
                                               "voting team/committee that reviews votes")
    evidence: str = Field(description="Most telling sentence, verbatim, <=300 chars")


SYSTEM = """You read excerpts from a mutual fund's Statement of Additional Information about
proxy voting. Tag which third-party proxy adviser the fund family uses (ISS, Glass Lewis,
both, or none), what role it plays, whether votes follow the family's own guidelines,
and whether an in-house stewardship/governance team or proxy committee is described.
Mentions of ISS only as a portfolio-holdings recipient or for class-action services do not
count as a proxy voting role."""


def snippets_for(text: str, max_windows: int = 4) -> str:
    out = []
    for m in ADVISER.finditer(text):
        w = text[max(0, m.start() - 500): m.end() + 700]
        if PROXY_CTX.search(w) and not any(w[:200] in o for o in out):
            out.append(w)
        if len(out) >= max_windows:
            break
    sec = SECTION.search(text)
    if sec:
        out.append(text[sec.start(): sec.start() + 2500])
    return "\n...\n".join(out)


def rules_tag(snip: str) -> SaiTag:
    iss = bool(re.search(r"Institutional Shareholder Services|\bISS\b", snip))
    gl = bool(re.search(r"Glass,? Lewis", snip))
    advisor = "both" if iss and gl else "ISS" if iss else "Glass Lewis" if gl else "none"
    roles = []
    if re.search(r"research|analys", snip, re.I):
        roles.append("research")
    if re.search(r"execut|record ?keeping|administ|platform|submit", snip, re.I):
        roles.append("execution")
    if re.search(r"recommendation", snip, re.I):
        roles.append("recommendations")
    own = bool(re.search(r"(own|adviser's|advisor's|its) (proxy voting )?(guidelines|policies|policy)|custom (policy|guidelines)", snip, re.I))
    team = bool(re.search(r"stewardship|governance team|proxy (voting )?committee|proxy team", snip, re.I))
    ev = next((s for s in re.split(r"(?<=\.)\s", snip) if ADVISER.search(s)), "")[:300]
    return SaiTag(advisor=advisor, roles=roles, own_guidelines=own, stewardship_team=team, evidence=ev)


def run(edgar: Edgar, cfg: dict, votes: pd.DataFrame, log=print) -> pd.DataFrame:
    c = cfg["sai"]
    trusts = votes[["family", "trust_cik"]].drop_duplicates()
    rows = []
    for fam, g in trusts.groupby("family"):
        hits = {}
        for q in c["queries"]:
            for h in edgar.fts(q, c["forms"], c["search_from"], c["search_to"],
                               max_hits=20, ciks=g.trust_cik.tolist()):
                if h["_id"].lower().endswith((".htm", ".html", ".txt")):
                    hits.setdefault(h["_id"], h)
        if not hits:  # SAI never names ISS/Glass Lewis: read its proxy voting section anyway
            for h in edgar.fts('"proxy voting"', c["forms"], c["search_from"], c["search_to"],
                               max_hits=10, ciks=g.trust_cik.tolist()):
                if h["_id"].lower().endswith((".htm", ".html", ".txt")) and "485bpos" in h["_id"].lower():
                    hits.setdefault(h["_id"], h)
        docs = sorted(hits.values(), key=lambda h: h["file_date"], reverse=True)[: c["max_docs_per_family"]]
        snips = []
        for h in docs:
            s = snippets_for(clean(edgar.get_text(edgar.doc_url(h))))
            if s:
                snips.append(s)
        text = "\n\n=====\n\n".join(snips)[:12000]
        if not text:
            rows.append({"family": fam, "sai_docs": 0, "advisor": "not found", "ai_source": "none"})
            continue
        tag, source = ai.ask("sai_tags", fam, SYSTEM, f"Fund family: {fam}\n\n{text}", SaiTag,
                             lambda: rules_tag(text), cfg, max_tokens=1000)
        rows.append({"family": fam, "sai_docs": len(docs), "sai_accessions": ";".join(h["_id"] for h in docs),
                     "ai_source": source, **tag.model_dump(), "roles": ",".join(tag.roles)})
        # Keep the raw snippet next to the tag for review.
        (PROCESSED / "sai_snippets").mkdir(parents=True, exist_ok=True)
        (PROCESSED / "sai_snippets" / f"{re.sub(r'[^A-Za-z0-9]+', '_', fam)}.txt").write_text(text, encoding="utf-8")
    df = pd.DataFrame(rows)
    df.to_csv(PROCESSED / "sai_tags.csv", index=False)
    log(f"SAI: tagged {len(df)} families; advisors {df.advisor.value_counts().to_dict()}")
    return df
