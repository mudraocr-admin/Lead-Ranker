"""Pennant fund prospect segmentation — end-to-end pipeline.

    python run.py            # everything (downloads are cached under data/raw/)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from pennant import iss_key, npx, outreach, sai, scoring  # noqa: E402
from pennant.config import OUTPUT, load_config, make_edgar  # noqa: E402


def main():
    t0 = time.time()
    log = lambda *a: print(f"[{time.time() - t0:6.0f}s]", *a, flush=True)  # noqa: E731
    cfg = load_config()
    edgar = make_edgar(cfg)

    # Step 1: N-PX votes
    votes = npx.load_votes(edgar, cfg, log)

    # Step 2a: ISS answer key from soliciting material
    snips = iss_key.collect_snippets(edgar, cfg, log)
    items = iss_key.classify(snips, cfg, log)
    opp = iss_key.opposed_items(items, votes)
    pri = npx.prioritize(votes, iss_key.contested_from_key(opp, votes), log)
    key = iss_key.join_to_items(opp, pri, cfg, log)
    key = iss_key.rank_and_trim(key, pri, cfg, log)

    # Step 3: SAI advisor identity
    tags = sai.run(edgar, cfg, votes, log)

    # Steps 2b + 4: panel, inference, validation, scoring
    sc, validation, _ = scoring.score(votes, pri, key, cfg, log)

    # Step 5: output
    prospects = outreach.rank_prospects(sc, tags)
    emails = outreach.draft_emails(prospects, cfg, log)
    outreach.write_outputs(prospects, emails, key, validation, log)
    (OUTPUT / "run_summary.json").write_text(json.dumps({
        "families": int(votes.family.nunique()), "vote_rows": int(len(votes)),
        "priority_rows": int(len(pri)), "key_items": int(len(key)), "validation": validation,
        "segments": prospects.segment.value_counts().to_dict()}, indent=2, default=str))
    log("Done.")
    print(prospects[["rank", "family", "segment", "confidence", "workload_meetings", "items",
                     "iss_agreement", "series_divergence", "advisor"]].to_string(index=False))


if __name__ == "__main__":
    main()
