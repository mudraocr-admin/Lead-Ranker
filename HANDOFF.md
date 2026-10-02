# Handoff: Pennant GTM prospect segmentation

Updated 2026-10-01 (evening) · last commit `03e2c02` plus **uncommitted** work: Zytnick
imputation, SAI step removed · private repo `mudraocr-admin/pennant-prospects`
(temporary home; the user will move it). `README.md` has the full method; this page
is the state and the open problems.

## What it does

Ranks the fund families in `families.csv` by **how they actually vote**, using only
public SEC filings. Output: `output/priority_list.csv`. Tier 1 = independent voters
(the best prospects), Tier 2 = proxy-adviser followers, Tier 3 = management followers.
Within a tier, families are ordered by workload (meetings voted per year). Every family
gets a tier.

## Pipeline (`python run.py`; 214 s warm for 30 families, ~6 min for 50)

1. **N-PX votes** of the target families (`npx.py`, `items.py`), clustered into ballot items.
2. **Management rate first**, read from the N-PX for/against-management flag as-is;
   ≥90% on ≥50 items → Tier 3. Board position per item = consensus of those flags.
3. **Answer keys** (`adviser_key.py`): ISS / Glass Lewis recommendations quoted in
   DEFA14A/DEFC14A/DFAN14A, turned into items by the AI step (cached; independent of
   the family list). A **quote audit** sets aside quotes the adviser's strict followers
   contradict (9 ISS: stale pre-meeting calls and one mis-joined WEX row).
4. **Reference universe** (`npx.py`): every registered fund (NPORT-P filer) that filed
   N-PX, one vote per filer per item, cached once per N-PX year (39 MB, family-independent).
5. **Imputation** (`impute.py`): Zytnick (2025) section 4 Bayes with follow rates by
   board position × rec × other adviser × category. Gate: 5-fold hold-out of quotes,
   ≥90% on board-opposed. Now ISS 96.1%, Glass Lewis 92.7% → both used; ~25k items each.
6. **Segment** (`scoring.py`): follower = ≥90% agreement where the adviser opposed the
   board, against whichever adviser the family follows more. Confidence = whether the
   95% Wilson interval clears the 90% line.

## Decisions the user made (don't relitigate)

- No outreach email drafting. The user supplies the target firms (`families.csv`;
  the current 30 are placeholders).
- Management followers are removed first, independent of advisers.
- Read the N-PX management flag as-is (verified by cross-tab on 2026-09-30).
- Following is judged on board-opposed items only (2026-10-01).
- The `ciks` column sits right after `family`.
- 2026-10-01: use Zytnick's Bayesian imputation and cut whatever it makes redundant;
  reduce runtime and complexity before running a new family list.

## Calls I made on 2026-10-01 that the user has not yet confirmed

- **SAI step removed** (per-family 485BPOS search + AI call). It no longer decided any
  tier except Delaware / Macquarie, now scored against ISS (its votes) instead of
  Glass Lewis (its SAI).
- **Quote audit threshold 80%** was chosen after inspecting the 9 flagged quotes.
  Without the audit the ISS gate fails (81%).
- **Two-category round skipped** (simulation showed it locks Glass Lewis onto ISS's calls).
- Six families changed tier (see README Results); Virtus and Baird left Tier 2.

## Active problems, in priority order

1. **Glass Lewis margin.** 92.7% on 41 hidden board-opposed quotes, so the gate has a
   thin margin; errors concentrate where ISS and Glass Lewis disagree.
2. **Quotes are the only ground truth** and are contested-heavy (78 of 133 ISS are
   proxy-fight seats). More quotes (e.g. 8-K vote results as a check) would firm up the gate.
3. **Gaps in `families.csv` coverage.** MFS has only 6 funds; Delaware and Neuberger
   thin; sibling trusts missed; Thrivent ETF Trust cut by the 150 MB per-family cap.
4. **Voter independence.** Each filer CIK is a voter; one manager's several trusts
   count several times.
5. **No API key.** AI answers are in-session classifications in `data/ai/*.json`.
   `config.yaml` sets `ai.model: "claude-opus-5"`; verify that ID before the first API run.
6. **Few tests.** `scripts/check_impute_synthetic.py` checks the imputation on simulated
   votes; otherwise verify by rerunning and diffing `priority_list.csv`.
7. **Unmatched key items.** 30 ISS and 27 Glass Lewis items (mostly meetings after
   30 June 2026); see `data/processed/*_key_misses.csv`.

## Housekeeping

Commits use the gmail identity, as all history does. After the repo moves, run
`git remote set-url origin <new>`. GitHub account quirks are in the user's Claude
memory (`git-github-accounts`).
