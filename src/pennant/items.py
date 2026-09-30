"""Canonical ballot items.

Each fund family types its own N-PX description for the same ballot item
("Advisory vote on executive compensation" vs "Say on Pay - An advisory vote
..."), and director rows may read "DIRECTOR: Kurt Adams" or "To elect eleven
directors ...: Kurt Adams". To compare families item by item we cluster the
descriptions within each (CUSIP, meeting date, priority category) into one
`item_key`.
"""
from __future__ import annotations

import re

import pandas as pd
from rapidfuzz import fuzz

STOP = re.compile(r"\b(to|the|a|an|of|and|for|our|its|on|in|approve|approval|proposal|vote|advisory|"
                  r"non binding|nonbinding|basis|resolution|company|s|inc|corporation)\b")


def director_name(desc: str) -> str:
    """Best-effort nominee name out of an election row."""
    d = desc.split(":")[-1] if ":" in desc else desc
    d = re.sub(r"(?i)^\s*(elect(ion)?( of)?( director)?s?|director|re-?elect(ion)?( of)?)\b", "", d)
    d = re.sub(r"(?i)\b(as a director|to the board|for a term.*|nominee|dissident|management)\b", "", d)
    d = re.sub(r"\(.*?\)", "", d)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", d.lower())).strip() or desc.lower()


def norm_text(desc: str) -> str:
    t = re.sub(r"[^a-z0-9 ]", " ", desc.lower())
    t = STOP.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def _cluster(texts: list[str], threshold: int) -> list[int]:
    """Greedy single-pass clustering; texts are short and groups are small."""
    reps: list[str] = []
    labels = []
    for t in texts:
        for i, r in enumerate(reps):
            if fuzz.token_sort_ratio(t, r) >= threshold:
                labels.append(i)
                break
        else:
            reps.append(t)
            labels.append(len(reps) - 1)
    return labels


def assign_item_keys(pri: pd.DataFrame) -> pd.DataFrame:
    """Adds item_key and item_text to priority vote rows."""
    uniq = pri[["cusip", "meeting_dt", "priority_category", "description", "vote_categories"]].drop_duplicates(
        ["cusip", "meeting_dt", "priority_category", "description"])
    is_dir = uniq.vote_categories.str.contains("DIRECTOR ELECTIONS") & uniq.priority_category.eq("contested_election")
    uniq["cmp"] = [director_name(d) if dr else norm_text(d) for d, dr in zip(uniq.description, is_dir)]
    uniq = uniq.sort_values(["cusip", "meeting_dt", "priority_category", "cmp"])
    keys = []
    for (cusip, mdt, cat), g in uniq.groupby(["cusip", "meeting_dt", "priority_category"], sort=False):
        thr = 90 if cat == "contested_election" else 88
        labels = _cluster(g.cmp.tolist(), thr)
        date = mdt.strftime("%Y-%m-%d") if pd.notna(mdt) else "NA"
        keys += [f"{cusip}|{date}|{cat}|{i}" for i in labels]
    uniq["item_key"] = keys
    text = (pri.merge(uniq[["cusip", "meeting_dt", "priority_category", "description", "item_key"]],
                      on=["cusip", "meeting_dt", "priority_category", "description"])
            .groupby("item_key").description.agg(lambda s: s.mode().iat[0]).rename("item_text"))
    out = pri.merge(uniq[["cusip", "meeting_dt", "priority_category", "description", "item_key"]],
                    on=["cusip", "meeting_dt", "priority_category", "description"], how="left")
    return out.merge(text, on="item_key", how="left")
