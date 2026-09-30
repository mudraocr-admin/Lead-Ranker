"""The only place the pipeline calls an LLM.

Two small jobs: classify ISS / Glass Lewis snippets and tag SAI proxy-voting
snippets. Every answer is cached in data/ai/<task>.json keyed by
a stable id, so reruns are free and deterministic. Resolution order:

  1. cache hit            -> source "cache:<original source>"
  2. Claude API           -> source "claude-api"   (needs ANTHROPIC_API_KEY or `ant auth login`)
  3. deterministic rules  -> source "rules"

Every output row carries its source so reviewers can see what the model did.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, TypeVar

from pydantic import BaseModel

from .edgar import ROOT

AI_DIR = ROOT / "data" / "ai"
T = TypeVar("T", bound=BaseModel)

_client = None
_client_failed = False


def _get_client():
    global _client, _client_failed
    if _client is not None or _client_failed:
        return _client
    if not api_available():
        _client_failed = True
        return None
    try:
        import anthropic
        _client = anthropic.Anthropic()
        return _client
    except Exception:
        _client_failed = True
        return None


class Cache:
    def __init__(self, task: str):
        self.path = AI_DIR / f"{task}.json"
        self.data: dict = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def get(self, key: str):
        return self.data.get(key)

    def put(self, key: str, value: dict):
        self.data[key] = value
        AI_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def ask(task: str, key: str, system: str, prompt: str, schema: type[T],
        fallback: Callable[[], T], cfg: dict, max_tokens: int = 2000) -> tuple[T, str]:
    """Return (answer, source). Never raises for model problems: it falls back."""
    cache = Cache(task)
    hit = cache.get(key)
    if hit is not None:
        return schema.model_validate(hit["answer"]), "cache:" + hit.get("source", "?")

    answer, source = None, "rules"
    client = _get_client() if cfg["ai"].get("enabled", True) else None
    if client is not None:
        try:
            import anthropic
            resp = client.beta.messages.parse(
                model=cfg["ai"]["model"],
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
                output_config={"effort": "low"},
                # Server-side fallback: if the primary model declines, the API
                # retries on a substitute model instead of returning a refusal.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if resp.stop_reason not in ("refusal", "max_tokens") and resp.parsed_output is not None:
                answer, source = resp.parsed_output, "claude-api"
        except anthropic.AuthenticationError:
            # No usable credentials: stop trying for the rest of the run.
            global _client_failed, _client
            _client_failed, _client = True, None
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            print(f"  [ai] {task}/{key}: API error {type(e).__name__}; using rules")

    if answer is None:
        answer = fallback()  # rules output is not cached: a later run with credentials retries
    else:
        cache.put(key, {"source": source, "answer": answer.model_dump()})
    return answer, source


def api_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or Path.home().joinpath(".config", "anthropic").exists())
