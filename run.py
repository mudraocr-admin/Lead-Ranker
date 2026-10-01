"""Pennant fund prospect segmentation — end-to-end pipeline.

    python run.py      # target families come from families.csv; downloads cached under data/raw/
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from pennant import adviser_key, npx, prospects, sai, scoring  # noqa: E402
from pennant.items import match_universe  # noqa: E402
from pennant.config import OUTPUT, load_config, make_edgar  # noqa: E402


def main():
    t0 = time.time()
    log = lambda *a: print(f"[{time.time() - t0:6.0f}s]", *a, flush=True)  # noqa: E731
    cfg = load_config()
    edgar = make_edgar(cfg)
    log(f"{len(cfg['families'])} target families from families.csv")

    # Step 1: N-PX votes
    votes = npx.load_votes(edgar, cfg, log)

    # Step 2a: adviser answer keys (ISS, Glass Lewis) from soliciting material
    opposed, contested = {}, set()
    for adv in cfg["advisers"]:
        snips = adviser_key.collect_snippets(edgar, cfg, adv, log)
        items = adviser_key.classify(snips, cfg, adv, log)
        opposed[adv] = adviser_key.keyed_items(items, votes)
        contested |= adviser_key.contested_from_key(opposed[adv], votes)
    pri = npx.prioritize(votes, cfg, contested, log)
    keys = {}
    for adv, opp in opposed.items():
        key = adviser_key.join_to_items(opp, pri, cfg, log)
        keys[adv] = adviser_key.rank_and_trim(key, pri, cfg, log)

    # Step 2b: reference universe - every other N-PX fund filer, never scored
    uni = None
    if cfg["universe"].get("enabled", True):
        uni = match_universe(npx.load_universe(edgar, cfg, pri, log), pri)
        log(f"Universe: {len(uni):,} reference votes matched to target ballot items "
            f"({uni.series_id.nunique():,} funds)")

    # Step 3: SAI adviser identity
    tags = sai.run(edgar, cfg, votes, log)

    # Steps 2c + 4: panels, inference, validation, scoring
    sc, validation, _ = scoring.score(votes, pri, keys, tags, cfg, log, uni=uni)

    # Step 5: priority list
    ranked = prospects.rank_prospects(sc, tags, votes)
    prospects.write_outputs(ranked, keys, validation, log)
    (OUTPUT / "run_summary.json").write_text(json.dumps({
        "families": int(votes.family.nunique()), "vote_rows": int(len(votes)),
        "priority_rows": int(len(pri)), "key_items": {a: int(len(k)) for a, k in keys.items()},
        "validation": validation, "segments": ranked.segment.value_counts().to_dict()}, indent=2, default=str))
    log("Done.")
    print(ranked[["rank", "family", "priority", "segment", "confidence", "workload_meetings", "scored_adviser",
                  "adviser_agreement", "mgmt_rate", "mgmt_items", "sai_advisor"]].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
