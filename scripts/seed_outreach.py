"""One-off: record the outreach-email polish done in the build session.

The template fill (templates/outreach.md) is the deterministic draft; Claude
polished each top-10 draft inside the Claude Code session because no API
credentials were set. Every number below is copied from output/prospects.csv
of the 2026-09-30 run. Written to data/ai/outreach.json, source="claude-session".
If you change the data or thresholds, delete that file so the emails are
redrafted from the new metrics.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VP = "[one-sentence Pennant value proposition]"
SIGN = "\n\nWould a 20-minute walkthrough of your own vote record be useful?\n\nBest,\n[Name]"

EMAILS = {
    "Victory": ("Victory's votes on 36 items where ISS opposed the board",
        "Hi Victory stewardship team,\n\nWe matched your 2025-26 N-PX against 36 ballot items where ISS publicly "
        "opposed the board. Your funds sided with ISS 72% of the time: 86% on contested elections (7 votes) but "
        "68% on pay (28 votes). That is a team making calls, not following a feed.\n\nIt is also a lot of calls: "
        "4,849 meetings in the year, with your series agreeing with each other on 98% of priority items. Your SAI "
        f"describes ISS for execution alongside your own Proxy Committee guidelines.\n\n{VP}" + SIGN),
    "Columbia": ("Columbia's votes on 27 items where ISS opposed the board",
        "Hi Columbia stewardship team,\n\nAcross 27 ballot items this season where ISS publicly opposed the "
        "board, your funds sided with ISS only 33% of the time, and 26% on pay (23 votes). On the 3 contested "
        "elections it was 67%.\n\nThat independence runs across 4,451 meetings a year, and your series voted as "
        "one on virtually every priority item. Your SAI names both ISS and Glass Lewis for research, with your "
        f"own process deciding the vote.\n\n{VP}" + SIGN),
    "Principal": ("Principal's votes on 37 items where ISS opposed the board",
        "Hi Principal stewardship team,\n\nOn 37 ballot items where ISS publicly opposed the board, your funds "
        "sided with ISS 68% of the time: 86% on contested elections (7 votes), 66% on pay (29 votes).\n\nThat "
        "pattern holds across 3,866 meetings in the year, and your series split on 3% of priority items. Your "
        "SAI describes an Investment Stewardship Team using ISS and Glass Lewis research under your own "
        f"guidelines.\n\n{VP}" + SIGN),
    "Thrivent": ("Thrivent's votes on 22 items where ISS opposed the board",
        "Hi Thrivent stewardship team,\n\nWe looked at 22 ballot items from the 2025-26 season where ISS publicly "
        "opposed the board. Your funds sided with ISS 73% of the time, 78% on pay (18 votes) and 67% on the 3 "
        "contested elections.\n\nYour SAI says Thrivent gives ISS standing instructions on some proposal types "
        "and decides others itself. Across 3,826 meetings a year, that split is where most of the work sits.\n\n"
        f"{VP}" + SIGN),
    "Harbor": ("Harbor's votes on 21 items where ISS opposed the board",
        "Hi Harbor team,\n\nOn 21 ballot items where ISS publicly opposed the board, Harbor funds sided with ISS "
        "33% of the time: 31% on pay (13 votes) and 29% on contested elections (7 votes).\n\nThe interesting "
        "number is 11%: the share of priority items where Harbor series voted differently from each other, "
        "across 3,006 meetings. With subadvisers on ISS and Glass Lewis guidelines, that is expected, and it "
        f"is hard to oversee.\n\n{VP}" + SIGN),
    "Schwab": ("Schwab's votes on 22 items where ISS opposed the board",
        "Hi Schwab Investment Stewardship team,\n\nAcross 22 ballot items where ISS publicly opposed the board, "
        "Schwab funds sided with ISS 27% of the time, 22% on pay (18 votes). On the 3 contested elections it was "
        "67%.\n\nYour series split on 13% of priority items across 2,397 meetings, higher than most families we "
        "looked at. Your SAI describes Glass Lewis for research and execution, ISS for research, and CSIM's own "
        f"guidelines.\n\n{VP}" + SIGN),
    "Allspring": ("Allspring's votes on 14 items where ISS opposed the board",
        "Hi Allspring Stewardship team,\n\nOn 14 ballot items where ISS publicly opposed the board, Allspring "
        "funds sided with ISS 71% of the time, 69% on pay (13 votes). That fits your SAI: ISS executes "
        "Allspring's custom enhancements to its benchmark, with portfolio managers able to override.\n\nAcross "
        f"1,971 meetings a year your series voted as one on virtually every priority item.\n\n{VP}" + SIGN),
    "Federated Hermes": ("Federated Hermes' votes on 17 items where ISS opposed the board",
        "Hi Federated Hermes team,\n\nWe matched your 2025-26 N-PX to 17 ballot items where ISS publicly opposed "
        "the board. Your funds sided with ISS 35% of the time, 23% on pay (13 votes) and 67% on the 3 contested "
        "elections.\n\nThat is across 1,382 meetings a year, with Glass Lewis named in your SAI as proxy voting "
        f"administrator.\n\n{VP}" + SIGN),
    "AMG": ("AMG's votes on 15 items where ISS opposed the board",
        "Hi AMG team,\n\nOn 15 ballot items where ISS publicly opposed the board, AMG funds sided with ISS 53% of "
        "the time: 83% on contested elections (6 votes), 33% on pay (9 votes).\n\nThe gap between those two "
        "categories, and a 7% series-split rate across 786 meetings, reflects affiliates voting on different "
        f"playbooks. Your SAI shows GW&K on Glass Lewis guidelines while ISS handles processing.\n\n{VP}" + SIGN),
    "Hartford": ("Hartford's votes on 10 items where ISS opposed the board",
        "Hi Hartford Funds team,\n\nOn 10 ballot items where ISS publicly opposed the board, Hartford funds sided "
        "with management 9 times out of 10, 11% with ISS on pay.\n\nWhat stood out is internal: on 18% of "
        "priority items across 1,311 meetings, Hartford series voted differently from each other, one of "
        f"the highest divergence rates among the 30 families we reviewed.\n\n{VP}" + SIGN),
}

path = ROOT / "data/ai/outreach.json"
path.write_text(json.dumps({k: {"source": "claude-session", "answer": {"subject": s, "body": b}}
                            for k, (s, b) in EMAILS.items()}, indent=2, ensure_ascii=False, sort_keys=True),
                encoding="utf-8")
print(f"{len(EMAILS)} emails")
