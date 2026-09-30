"""Step 5: ranked prospect table + one outreach email per top prospect."""
from __future__ import annotations

import json

import pandas as pd
from pydantic import BaseModel

from . import ai
from .config import OUTPUT, ROOT
from .scoring import CATS

CAT_NAMES = {"shareholder_proposal": "shareholder proposals", "contested_election": "contested elections",
             "mna": "M&A", "pay": "pay"}
LEVELS = ["low", "medium", "high"]


def confidence(row: pd.Series) -> tuple[str, str]:
    """Data volume sets the base; an SAI that tells the same story raises it."""
    base = 1 if row["items"] >= 30 else 0
    seg, adv = row["segment"], row.get("advisor")
    confirms = ""
    if seg in ("Judgment voter", "Partial robo-voter") and (row.get("own_guidelines") is True or row.get("stewardship_team") is True):
        confirms = "SAI describes own guidelines / stewardship team"
    elif seg == "ISS follower" and adv in ("ISS", "both") and row.get("own_guidelines") is not True:
        confirms = "SAI names ISS without own guidelines"
    level = min(base + (1 if confirms else 0), 2)
    return LEVELS[level], confirms


def rank_prospects(sc: pd.DataFrame, sai: pd.DataFrame) -> pd.DataFrame:
    df = sc.merge(sai[["family", "advisor", "roles", "own_guidelines", "stewardship_team", "evidence", "ai_source"]]
                  .rename(columns={"evidence": "sai_evidence", "ai_source": "sai_source"}), on="family", how="left")
    conf = df.apply(confidence, axis=1, result_type="expand")
    df["confidence"], df["confidence_reason"] = conf[0], conf[1]
    tier = {"Judgment voter": 1, "Partial robo-voter": 1, "ISS follower": 2,
            "Management follower": 3, "Insufficient data": 4}
    df["tier"] = df.segment.map(tier)
    df["list"] = df.tier.map({1: "Primary", 2: "Secondary (2027 Glass Lewis pitch)",
                              3: "Deprioritized", 4: "Deprioritized"})
    df = df.sort_values(["tier", "workload_meetings"], ascending=[True, False]).reset_index(drop=True)
    df.insert(0, "rank", range(1, len(df) + 1))
    return df


def pct(x) -> str:
    return "n/a" if pd.isna(x) else f"{x:.0%}"


def fill_template(r: pd.Series) -> dict:
    cats = [(CAT_NAMES[c], r[f"agree_{c}"], r[f"n_{c}"]) for c in CATS if r[f"n_{c}"] >= 3]
    cats.sort(key=lambda x: x[1], reverse=True)
    if len(cats) >= 2:
        hi, lo = cats[0], cats[-1]
        category_line = (f"By category: {pct(hi[1])} on {hi[0]} ({hi[2]} votes) versus {pct(lo[1])} on "
                         f"{lo[0]} ({lo[2]} votes).")
    elif cats:
        category_line = f"On {cats[0][0]} it was {pct(cats[0][1])} across {cats[0][2]} votes."
    else:
        category_line = ""
    adv = r.get("advisor")
    if isinstance(adv, str) and adv not in ("none", "not found"):
        name = "ISS and Glass Lewis" if adv == "both" else adv
        sai_line = f"Your SAI names {name} for proxy work" + (
            ", alongside your own guidelines." if r.get("own_guidelines") is True else ".")
    else:
        sai_line = "Your SAI describes an in-house process without naming a proxy adviser."
    tpl = (ROOT / "templates" / "outreach.md").read_text(encoding="utf-8")
    text = tpl.format(family=r["family"], n_items=int(r["items"]), iss_pct=pct(r["iss_agreement"]),
                      segment=r["segment"], category_line=category_line, meetings=int(r["workload_meetings"]),
                      divergence_pct=pct(r["series_divergence"]), sai_line=sai_line)
    subject, _, body = text.partition("\n")
    return {"subject": subject.replace("Subject: ", "").strip(), "body": body.strip()}


class Email(BaseModel):
    subject: str
    body: str


SYSTEM = """You polish short B2B outreach emails for Pennant, a proxy-voting product, to
mutual fund stewardship teams. Keep every number exactly as given, keep the placeholder in
square brackets, keep it under 170 words, plain and specific, no hype, no exclamation marks."""


def draft_emails(prospects: pd.DataFrame, cfg: dict, log=print) -> pd.DataFrame:
    top = prospects.head(cfg["output"]["top_emails"])
    rows = []
    for _, r in top.iterrows():
        filled = fill_template(r)
        prompt = ("Fund metrics:\n" + r[["family", "segment", "items", "iss_agreement", "workload_meetings",
                                         "series_divergence", "advisor"]].to_string()
                  + f"\n\nTemplate-filled draft:\nSubject: {filled['subject']}\n\n{filled['body']}")
        email, source = ai.ask("outreach", r["family"], SYSTEM, prompt, Email,
                               lambda: Email(**filled), cfg, max_tokens=1200)
        rows.append({"rank": r["rank"], "family": r["family"], "segment": r["segment"],
                     "subject": email.subject, "body": email.body, "ai_source": source})
    out = pd.DataFrame(rows)
    log(f"Outreach: drafted {len(out)} emails; sources {out.ai_source.value_counts().to_dict()}")
    return out


def write_outputs(prospects: pd.DataFrame, emails: pd.DataFrame, key: pd.DataFrame,
                  validation: dict, log=print):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    cols = ["rank", "family", "list", "segment", "confidence", "confidence_reason", "workload_meetings",
            "items", "key_items", "iss_agreement", "iss_agreement_key_only"] + \
           [f"{p}_{c}" for c in CATS for p in ("agree", "n")] + \
           ["series_divergence", "n_series", "with_mgmt_rate_priority", "panel_member",
            "advisor", "roles", "own_guidelines", "stewardship_team", "sai_evidence", "sai_source"]
    prospects[cols].to_csv(OUTPUT / "prospects.csv", index=False)
    emails.to_csv(OUTPUT / "outreach_emails.csv", index=False)
    with open(OUTPUT / "outreach_emails.md", "w", encoding="utf-8") as f:
        for e in emails.itertuples():
            f.write(f"## {e.rank}. {e.family} ({e.segment})\n\n**Subject:** {e.subject}\n\n{e.body}\n\n"
                    f"<sub>draft source: {e.ai_source}</sub>\n\n---\n\n")
    key.to_csv(OUTPUT / "iss_key.csv", index=False)
    (OUTPUT / "validation.json").write_text(json.dumps(validation, indent=2, default=str))
    log(f"Wrote {OUTPUT}/prospects.csv, outreach_emails.md, iss_key.csv, validation.json")
