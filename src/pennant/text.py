"""HTML/text helpers shared by the DEFA14A and 485BPOS steps."""
from __future__ import annotations

import html
import re

TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")
SENT = re.compile(r"(?<!\s[A-Z]\.)(?<!Mr\.)(?<!Ms\.)(?<!Dr\.)(?<!No\.)(?<!Inc\.)(?<!Co\.)(?<!Jr\.)(?<!Mrs\.)(?<=[.!?])\s+(?=[A-Z\"“(])")


def clean(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    txt = html.unescape(TAG.sub(" ", raw))
    txt = txt.replace("“", '"').replace("”", '"').replace("’", "'").replace("�", '"')
    return WS.sub(" ", txt).strip()


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT.split(text) if s.strip()]
