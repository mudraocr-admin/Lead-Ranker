"""Pennant fund prospect segmentation — end-to-end pipeline.

    python run.py      # target families come from families.csv; downloads cached under data/raw/
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from pennant import adviser_key, impute, npx, prospects, scoring  # noqa: E402
from pennant.items import match_universe  # noqa: E402
from pennant.config import OUTPUT, load_config, make_edgar  # noqa: E402


def main():
    t0 = time.time()
    log = lambda *a: print(f"[{time.time() - t0:6.0f}s]", *a, flush=True)  # noqa: E731
    cfg = load_config()
    edgar = make_edgar(cfg)
    log(f"{len(cfg['families'])} target families from families.csv")

    # Step 1: N-PX votes of the target families
    votes = npx.load_votes(edgar, cfg, log)

    # Step 2: answer keys (ISS, Glass Lewis) quoted in soliciting material
    quoted, contested = {}, set()
    for adv in cfg["advisers"]:
        snips = adviser_key.collect_snippets(edgar, cfg, adv, log)
        items = adviser_key.classify(snips, cfg, adv, log)
        quoted[adv] = adviser_key.keyed_items(items, votes)
        contested |= adviser_key.contested_from_key(quoted[adv], votes)
    pri = npx.prioritize(votes, cfg, contested, log)
    keys = {adv: adviser_key.rank_and_trim(adviser_key.join_to_items(q, pri, cfg, log), pri, cfg, log)
            for adv, q in quoted.items()}

    # Step 3: every other fund filer's votes (cached once per N-PX year)
    uni = match_universe(npx.load_universe(edgar, cfg, log), pri)
    log(f"Universe: {len(uni):,} filer votes matched to target ballot items ({uni.filer_cik.nunique():,} filers)")

    # Step 4: impute each adviser's recommendations from those votes (Zytnick 2025)
    recs, validation = impute.impute_recs(uni, pri, keys, cfg, log)

    # Step 5: family scoring
    sc = scoring.score(votes, pri, recs, cfg, log)

    # Step 6: priority list
    ranked = prospects.rank_prospects(sc, votes)
    prospects.write_outputs(ranked, keys, validation, log)
    (OUTPUT / "run_summary.json").write_text(json.dumps({
        "families": int(votes.family.nunique()), "vote_rows": int(len(votes)),
        "priority_rows": int(len(pri)), "key_items": {a: int(len(k)) for a, k in keys.items()},
        "rec_items": {a: r.source.value_counts().to_dict() for a, r in recs.items()},
        "validation": validation, "segments": ranked.segment.value_counts().to_dict(),
        "runtime_s": round(time.time() - t0)}, indent=2, default=str))
    log("Done.")
    print(ranked[["rank", "family", "priority", "segment", "confidence", "workload_meetings", "scored_adviser",
                  "adviser_agreement", "mgmt_rate", "mgmt_items"]].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
