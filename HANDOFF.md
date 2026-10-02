# Handoff: Pennant GTM prospect segmentation

Updated 2026-10-02 · commit `7b22ada` on `main`, pushed to private
`mudraocr-admin/pennant-prospects` (temporary home; the user will move it). `README.md`
has the full method; this page is the state and the open problems.

## First thing to do

**The outputs are stale.** The 50-family rerun after the GuideStone fix was cut off
mid-run (the session ended), and it had deleted the reference-universe cache.
`filers.csv` and `*_key_misses.csv` come from that partial rerun; `priority_list.csv`,
`validation.json` and `run_summary.json` come from the run *before* the fix. Rerun:

    .venv/Scripts/python.exe -u run.py      # `python` is not on PATH; use the venv

That takes ~20–25 min, because the universe cache rebuilds once. Then check the problems
below against the fresh output and report the diff to the user.

## What it does

Ranks the fund families in `families.csv` by **how they actually vote**, using only
public SEC filings. Output: `output/priority_list.csv`. Tier 1 = independent voters
(the best prospects), Tier 2 = proxy-adviser followers, Tier 3 = management followers.
Within a tier, families are ordered by workload (meetings voted per year). Every family
that has votes gets a tier.

`families.csv` holds the **user's real 50-family list** (2026-10-01). The fit columns are
theirs and the pipeline ignores them. I mapped the `ciks`. For families with more than
10 trusts (John Hancock, PGIM, Voya, BNY, Natixis, Cohen & Steers, Gabelli) I picked the
main equity trusts, and Natixis includes Harris/Oakmark and Loomis Sayles.

## Pipeline (`run.py`; 347 s warm for 50 families)

1. **N-PX votes** (`npx.py`, `items.py`) for each family's trusts, clustered into ballot
   items. Since 2026-10-01 *every* N-PX filing a trust made in the window is read: a
   trust can file a voting report and a separate notice report. Only the latest was read
   before, which is how GuideStone lost its votes. `scripts/npx_votes.py <cik>` dumps a
   filer's parsed votes and reconciles them against counts taken directly from the raw XML.
2. **Management rate first**, read from the N-PX for/against-management flag as-is;
   ≥90% on ≥50 items → Tier 3. Board position per item = ≥75% consensus of those
   flags, with the sponsor rule as the fallback.
3. **Answer keys** (`adviser_key.py`): ISS / Glass Lewis recommendations quoted in
   DEFA14A/DEFC14A/DFAN14A, classified by the AI step (cached in `data/ai/`, independent
   of the family list). A **quote audit** sets aside quotes that the adviser's strict
   followers contradict.
4. **Reference universe**: every registered fund (NPORT-P filer) that filed N-PX,
   excluding the target families, one vote per filer per item. Cached as a parquet file.
5. **Imputation** (`impute.py`): Zytnick (2025, JELS) section 4 / appendix B Bayes. Gate:
   5-fold hold-out of quotes, ≥90% on board-opposed items, or that adviser's imputations
   are not used.
6. **Segment** (`scoring.py`): follower = ≥90% agreement where the adviser opposed the
   board, against whichever adviser the family follows more. Confidence comes from
   whether the 95% Wilson interval clears 90%.

## Decisions the user made (don't relitigate)

- Never draft outreach emails. The user supplies the target firms.
- Management followers are removed first. Read the N-PX management flag as-is.
- Following is judged on board-opposed items only. `ciks` is the 2nd output column.
- Use Zytnick's imputation and cut what it makes redundant; keep runtime and complexity down.
- On the 50-family run the user asked for "run only, report problems". They then asked
  to fix GuideStone and to make pulling actual N-PX records easy (`scripts/npx_votes.py`).

## My calls the user has not confirmed

- SAI step removed. Two-category Bayes round skipped (on simulated votes it copied ISS's
  calls onto Glass Lewis).
- Quote audit cutoff (80%) was picked **after** looking at the flagged quotes. Without the
  audit the ISS gate fails.
- The trust picks for the >10-trust families (above).

## Pre-fix 50-family result

45 of 50 families scored: 31 in Tier 1, 9 in Tier 2 (8 ISS followers and Russell for
Glass Lewis), 5 in Tier 3. ISS gate passed at 92.2% (7 quotes set aside); Glass Lewis
**failed** at 86.0%.

## Active problems, in priority order (recheck after the rerun)

1. **5 families missing.** Mutual of America, Boston Partners, Glenmede and Westwood have
   no fund N-PX filer in the window, only their advisers' own filings, which are
   dropped. Their funds may file under other trusts (e.g. Westwood in an Ultimus trust),
   and finding them is the user's call. GuideStone should now score; that's unverified.
2. **Zero-vote filings.** 13 trusts read zero votes. Most are genuine "FUND NOTICE
   REPORT"s with no votes (JH Strategic Series, SEI Asset Allocation, PGIM Portfolios 4,
   Voya Funds Trust, Voya Separate Portfolios). Verify that MassMutual's trusts now
   score (before the fix it was 1 of 5).
3. **The Glass Lewis gate depends on the family list.** It passed (92.7%) on the 30
   families and failed (86.0%) on these 50, because target families are left out of
   the universe.
4. **Adviser picking is lopsided when one gate fails.** Imputed ISS is compared against
   quotes-only Glass Lewis. Russell is a "Glass Lewis follower" on 17/17 quotes, and SEI
   (33) and PGIM (17) were scored against Glass Lewis. All three calls are unreliable.
5. **The 150 MB per-family cap** cut John Hancock Funds II (its largest), Funds III and
   its ETF trust, and Voya Variable Products Trust.
6. **Imputation coverage fell** to 77% of items (89% on the 30 families); hold-out
   coverage is 74–75%.
7. **Board consensus vs sponsor rule disagree on ~830 items** (93 on the 30 families).
   Non-US-heavy holders are suspected; unchecked.
8. **Calvert is an ISS follower** (91% of 1,275), but an ESG voter agrees with ISS on
   shareholder proposals anyway. The test can't separate the two.
9. **No API key.** AI answers are in-session classifications cached in `data/ai/*.json`.
   `config.yaml` has `ai.model: "claude-opus-5"`, which is not a real ID; fix it before
   any API run.
10. **Few tests.** `scripts/check_impute_synthetic.py` (simulated votes) and
    `scripts/npx_votes.py` (parser reconciliation) are the checks; otherwise rerun and
    diff `priority_list.csv`.
11. **Unmatched key items:** 28 ISS / 22 Glass Lewis, mostly meetings after
    30 Jun 2026 (`data/processed/*_key_misses.csv`).

## Housekeeping

Commits use the gmail identity, as all history does. After the repo moves, run
`git remote set-url origin <new>`. `data/raw/`, parquet caches and `npx_votes_*.csv`
dumps are gitignored and regenerable.
