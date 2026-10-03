# Handoff: Pennant GTM prospect segmentation

Updated 2026-10-03 (after the post-fix rerun). Last commit `e4b8a88` on `main` (not pushed; `23e3796` is the last push to
private `mudraocr-admin/pennant-prospects`, a temporary home the user will move). `README.md`
has the full method; this page is the state, the demo website and the open problems.

## State (2026-10-03): rerun done, website refreshed

The post-fix 50-family rerun is done and the demo website shows it. The outputs in
`output/` are current. A cold run took 1,623 s, because the universe cache rebuilt. The
warm rerun after it took 428 s and gave an identical `priority_list.csv`.

**Next rerun**, if inputs change: run `.venv/Scripts/python.exe -u run.py` in the background.
Diff the new `output/priority_list.csv` against the committed one and report the diff to the
user. Then refresh the site (see "Demo website").

## What it does

Ranks the fund families in `families.csv` by **how they actually vote**, using only
public SEC filings. Output: `output/priority_list.csv`. Tier 1 = independent voters
(the best prospects), Tier 2 = proxy-adviser followers, Tier 3 = management followers.
Tiers 1 and 2 are ordered from least to most adviser agreement (where the adviser opposed
the board); Tier 3 from least to most agreement with management. Every family
that has votes gets a tier.

`families.csv` holds the **user's real 50-family list** (2026-10-01). The fit columns are
theirs and the pipeline ignores them. I mapped the `ciks`. For families with more than
10 trusts (John Hancock, PGIM, Voya, BNY, Natixis, Cohen & Steers, Gabelli) I picked the
main equity trusts, and Natixis includes Harris/Oakmark and Loomis Sayles.

## Pipeline (`run.py`; 428 s warm, 1,623 s cold for 50 families)

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

## Demo website

A one-page web app for the user's job-application demo video (Pennant, YC S26). It only
**displays** the pipeline's results; all analysis stays in Python.

- **Where:** a claude.ai Design canvas, https://claude.ai/artifact/JgBTUGqHJ6kDDEdHM6Hnk9
  (private; the user shares it from the page's Share menu). Its files are
  `project/canvas.json` (one artboard, `Main.dc.html`, a fill-width interactive page that
  launches focused) and `project/Main.dc.html` (the whole page). Copies of both are in
  `site/project/`; before editing, read the live `project/Main.dc.html` from the artifact
  (Artifact tool, `action: "read"`, `path`) in case the user edited it in the page.
- **Data:** the page `fetch`es `/_blob/af9074528c707b7d84a82c5505a2e031`, an uploaded
  asset holding `pennant-data.json` from `scripts/build_site_data.py`. Nothing is
  hand-copied; the per-firm averages are computed in the page from the summary.
- **How to update after a rerun:** run the script; upload the JSON with the Artifact tool
  (`action: "publish"`, `url` = the canvas, `asset: true`, `file_path` = the JSON); put
  the returned `/_blob/<id>` in the page's `componentDidMount` `fetch(...)`; republish with
  `url` = the canvas, `root` = a folder holding `project/Main.dc.html`, `file_path` = that
  file. Send `canvas.json` only if the layout changes. Never publish `index.html`,
  `SKILL.md` or anything under `artifact-type/`. The page is a `.dc.html` Design
  Component: template markup with `{{hole}}` lookups, `<sc-for>`/`<sc-if>`, and a
  `class Component extends DCLogic` whose `renderVals()` formats everything.
- **Loading:** the page shows a dark screen with a spinner for 5–10 s before it renders.
  The user once read that as "blank"; it was only loading.

### What the page shows (top to bottom)

1. **Header:** eyebrow "Pennant · Prospect ranking"; title "Fund families ranked by how
   they actually vote"; subtitle "SEC Form N-PX proxy votes, Jul 2025 – Jun 2026.
   Independent voters rank first. Tiers 1 and 2 run from least to most agreement with
   their proxy adviser; Tier 3 from least to most agreement with management."; line
   "Pipeline runtime: {runtime} s · source: SEC EDGAR" (runtime from `run_summary.json`,
   now the 428 s warm run). The user had the run date removed on 2026-10-03, and the amber
   "Preliminary · rerun pending" badge went after the rerun.
2. **Four summary boxes:** Families analyzed (50 = rows in `families.csv`); N-PX votes read
   per firm (`vote_rows` / scored families = 79,309; note shows the 3,648,221 total);
   Judgment votes per firm (`priority_rows` / scored families = 12,819; total 589,678);
   Families scored (46 / 50; note "40 at high confidence · 6 medium").
3. **Scatter "Adviser agreement vs. workload":** x = meetings voted per year on a log axis
   fixed at 100–10,000; y = agreement with the scored adviser where it opposed the board,
   0–100%, with a dashed 90% follower line. Tier 1 filled steel blue, Tier 2 filled grey,
   Tier 3 hollow grey ring. All points the same size and no selection ring; a name label
   (family · agreement · meetings) appears only on hover or keyboard focus. Clicking a
   point opens that family in the evidence panel. A tier filter dims the other tiers.
4. **Three tier cards:** count, rule and segment split (Tier 1: 30, "22 judgment voters · 8
   partial robo-voters"; Tier 2: 11, "10 follow ISS · 1 follows Glass Lewis"; Tier 3: 5,
   "Low priority"), plus a dashed "Not scored: no fund-level votes read · 4" card.
5. **Ranked prospects table** with filter buttons All / Tier 1 / Tier 2 / Tier 3. Columns:
   # · Fund family (structure from `families.csv`; ESG / Faith-based badge from its
   `fit_rationale`) · Tier · segment (with confidence) · **With adviser** (sub-label
   "where it opposed the board"; adviser name and item count) · **With management** (item
   count) · **Meetings / yr**. Scrolls inside a 760 px box with a sticky header. Row order
   = `priority_list.csv` rank.
6. **Evidence panel** (right of the table; rank 1 is selected on load): rank, tier,
   segment, confidence and values badges; "Basis for the call" = `confidence_reason`;
   meetings, with management, with adviser when it opposed / backed the board, fund
   series, series-split rate; "Scored against X: reason"; a by-category table (shareholder
   proposals, contested director elections, M&A, executive pay: with adviser vs board and
   with management, with counts); source filings, one row per trust with its CIK, linked
   to the trust's N-PX filing on EDGAR (accession from `filers.csv`, else the trust's
   EDGAR N-PX list); "Fit notes · from families.csv, not used in ranking".
7. **Not scored (4):** Mutual of America, Boston Partners, Glenmede and Westwood, each
   with a reason and its CIKs.
8. **Method** (5 steps) and **Limits** (mutual funds only; one proxy year; Glass Lewis
   inference failed so the Russell, SEI and PGIM calls are unconfirmed; ISS quotes set
   aside by the audit; the 150 MB cap skipped six filings, named).

### Hard-coded text to recheck after a rerun

All in `project/Main.dc.html`; everything else comes from the data file.
- The last Limits bullet naming the six filings the 150 MB cap skipped (from `filers.csv`,
  status "skipped: family download cap").
- The `reasons` map in `renderVals()` (why each unscored family is missing). A family
  missing from the map shows "n/a".
- Tier card wording: "follows Glass Lewis" (singular) and the Tier 1 split.
- Method step 4: "1,015 reference funds" (from the run log line "Imputation: … from N
  reference filers"; not in `run_summary.json`) and "it scored {issGate}" (assumes the ISS
  gate passed).
- Limits: "Glass Lewis inference failed its check" and the names Russell, SEI and PGIM
  (assumes the GL gate failed); "without it the ISS check does not pass" (true on the
  3 Oct run: with the 8 set-aside quotes back, board-opposed accuracy is 50/62 = 80.6%).
- The scatter axis is fixed at 100–10,000 meetings (fine while workloads stay in range).

### The user's choices for the site (settled)

- Use only real data. Never invent funds, numbers, quotes or links; missing values show
  "n/a". The user rejected a guessed "~2 min per firm" figure and kept the measured
  runtime (347 s then, 428 s warm now). On 2026-10-03 they dropped the run date: the
  header reads "Pipeline runtime: 428 s · source: SEC EDGAR".
- Style: clean institutional finance; light theme, white ground, one muted accent (steel
  blue `#2C5D7F`); IBM Plex Sans with IBM Plex Serif for the title; tabular right-aligned
  numbers; 16 px+ text; badges for tier and values tag; no gradients, emoji or animation
  beyond hover. Must look sharp in a 1080p screen recording. No login, settings, extra
  pages, landing page or marketing copy.
- Sorting: Tiers 1 and 2 by adviser agreement ascending (most independent first), Tier 3
  by management agreement ascending; ties by meetings. Done in `prospects.order()`, so the
  CSV and the page agree.
- Adviser agreement stays measured **only where the adviser opposed the board**. The user
  proposed overall agreement instead; I showed that ISS sides with the board on ~82% of a
  typical family's items, so overall agreement would put 15 of 31 Tier 1 families at ≥90%
  "with ISS" (median 89.9%), raise Impax from 10% to 85% and drop Calvert from 91% to
  74%. The user went on with the board-opposed measure.
- The "Families scored" box keeps that label, not "confidently scored": 6 of 46 calls are
  medium confidence.
- Per-firm averages divide by the 46 families with votes, not 50 (the note says so).

## Decisions the user made (don't relitigate)

- Never draft outreach emails. The user supplies the target firms.
- Management followers are removed first. Read the N-PX management flag as-is.
- Following is judged on board-opposed items only. `ciks` is the 2nd output column.
- Tiers 1–2 sort by adviser agreement, Tier 3 by management agreement, lowest first
  (2026-10-03; was workload).
- Use Zytnick's imputation and cut what it makes redundant; keep runtime and complexity down.
- On the 50-family run the user asked for "run only, report problems". They then asked
  to fix GuideStone and to make pulling actual N-PX records easy (`scripts/npx_votes.py`).

## My calls the user has not confirmed

- SAI step removed. Two-category Bayes round skipped (on simulated votes it copied ISS's
  calls onto Glass Lewis).
- Quote audit cutoff (80%) was picked **after** looking at the flagged quotes. Without the
  audit the ISS gate fails.
- The trust picks for the >10-trust families (above).

## Post-fix 50-family result (3 Oct 2026; what the website shows)

46 of 50 families scored: 30 in Tier 1, 11 in Tier 2 (10 ISS followers and Russell for
Glass Lewis), 5 in Tier 3; 40 high confidence, 6 medium. ISS gate passed at 92.6% (8
quotes set aside); Glass Lewis **failed** at 81.6%. 3,648,221 N-PX vote rows, 589,678 in
the judgment categories.

Against the pre-fix run (1 Oct: 45 scored, 31 / 9 / 5):
- **GuideStone now scores:** Tier 2, ISS follower, 97% of 731 board-opposed items, rank 36.
- **MassMutual:** 4 of 5 trusts now read votes (Advantage Funds files a notice report
  only); 200 → 1,486 meetings; still Tier 1 (rank 14 → 8).
- **John Hancock** moved Tier 1 → Tier 2 (86% → 91% with ISS; Funds III and the ETF
  trust are now read).
- **Jackson National** rank 4 → 25 (41% → 79% with ISS; Judgment voter → Partial
  robo-voter): its big JNL Series Trust voting report is now read (211k rows).
- Equitable and Lincoln became Judgment voters (were Partial robo-voters).
- No other tier changes. The same three families are scored against Glass Lewis.

## Active problems, in priority order

1. **4 families missing.** Mutual of America, Boston Partners, Glenmede and Westwood have
   no fund N-PX filer in the window, only their advisers' own filings, which are
   dropped. Their funds may file under other trusts (e.g. Westwood in an Ultimus trust),
   and finding them is the user's call.
2. **The 150 MB per-family cap now bites harder.** Reading every filing per trust means
   more bytes per family. Skipped: John Hancock Funds II (its largest) and Strategic
   Series, Voya Variable Products Trust, 1290 Funds, and one of two reports each from
   JNL Series Trust and EQ Advisors Trust. Jackson's and Equitable's calls rest on part
   of their votes. Raising `npx.max_mb_per_family` in `config.yaml` is the fix to weigh.
3. **The Glass Lewis gate depends on the family list.** It passed (92.7%) on the 30
   families and failed on these 50 (86.0% pre-fix, 81.6% now), because target families
   are left out of the universe.
4. **Adviser picking is lopsided when one gate fails.** Imputed ISS is compared against
   quotes-only Glass Lewis. Russell is a "Glass Lewis follower" on 17/17 quotes, and SEI
   (33) and PGIM (17) were scored against Glass Lewis. All three calls are unreliable.
5. **Zero-vote filings.** 6 trusts read zero votes, all genuine "FUND NOTICE REPORT"s
   (SEI Asset Allocation, PGIM Portfolios 4, Voya Funds Trust, Voya Variable Insurance
   Trust, Voya Separate Portfolios, MassMutual Advantage Funds).
6. **Imputation coverage** is 82% of items (37,210 of 45,228; 89% on the 30 families);
   hold-out coverage is 71%. 1,015 reference filers.
7. **Board consensus vs sponsor rule disagree on 832 items** (93 on the 30 families).
   Non-US-heavy holders are suspected; unchecked.
8. **Calvert is an ISS follower** (91% of 1,268), but an ESG voter agrees with ISS on
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
