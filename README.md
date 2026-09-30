# Pennant prospect segmentation (demo)

This demo sorts 30 US mutual fund families by **how they actually vote** on contested
proxy items. The inputs are public SEC filings only. Everything is Python and pandas.
An LLM is used for exactly three small jobs: classifying ISS snippets, tagging SAI
proxy-voting text, and polishing outreach emails. Every one of those answers is cached
and labelled with its source.

```
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
python run.py                 # ~5 min cold (downloads ~1 GB of N-PX), seconds warm
```

Outputs land in `output/`:

| file | what |
|---|---|
| `prospects.csv` | ranked prospect table: segment, confidence, workload, ISS agreement overall and by category, series divergence, SAI advisor tags |
| `outreach_emails.md` / `.csv` | one email per top-10 prospect, filled from `templates/outreach.md` |
| `iss_key.csv` | the ISS "answer key": board-opposed items joined to N-PX, with the sentence each call rests on |
| `validation.json` | hold-out test of the panel inference (headline + sensitivity) |
| `run_summary.json` | row counts, segment counts |

## Pipeline

| step | module | source | what it does |
|---|---|---|---|
| 1 | `npx.py` | EDGAR `full-index/2026/QTR3/form.idx`, N-PX filed Aug 2026 (votes Jul 2025–Jun 2026) | Picks each target family's trusts by filer-name regex (`config.yaml`). Keeps registrantType `RMIC` only, so adviser-level 13F N-PX filings drop out. Streams the XML into one row per vote record: family, trust, series, issuer, CUSIP, meeting date, N-PX vote categories, vote source, vote cast, management rec, with/against management. |
| 1 | `npx.py` | form.idx DEFC14A 2025Q2–2026Q2 | N-PX has **no contest flag** (checked: the schema only has category, source and vote). So contested meetings are found by fuzzy-matching the issuer to a DEFC14A filed 0–120 days before the meeting. Meetings that the ISS soliciting material shows were proxy fights are added on top. |
| 1 | `npx.py` | — | Priority mapping: vote source `SECURITY HOLDER` → shareholder proposals. `EXTRAORDINARY TRANSACTIONS` → M&A. `SECTION 14A SAY-ON-PAY VOTES` or `COMPENSATION` → pay (frequency votes excluded). Director elections at a contested meeting → contested elections. Everything else is dropped. |
| 1 | `items.py` | — | Each family words the same ballot item its own way, so descriptions are clustered within (CUSIP, meeting, category) into one canonical `item_key`. Director rows are matched on the extracted nominee name. |
| 2 | `iss_key.py` | EDGAR full-text search: DEFA14A, DEFC14A, DFAN14A, Jan–Jul 2026 | Regex pulls sentences mentioning ISS plus "recommend". **AI step:** each company's sentences are turned into items (type, label, ISS rec, board rec, opposed?) with the verbatim evidence sentence. Only items where ISS opposed the board are kept. CIK is bridged to CUSIP by issuer name, and the meeting is matched by date window. Proposals are matched by category and keywords. Directors are matched by rapidfuzz name match. Items are ranked by how many target families voted on them; the top 40 are kept. |
| 2 | `scoring.py` | — | **Seed and expand.** Families matching ISS on ≥90% of ≥10 key items form the follower panel. For other priority items, ISS's rec is inferred when ≥5 panel families vote together at ≥90%. Only board-opposed inferences are kept, to match the key. The scored family is excluded from its own panel. **Validation:** 8 key items are held out, the panel is rebuilt without them, and the held-out items are inferred back. |
| 3 | `sai.py` | EDGAR full-text search: 485BPOS filed by each family's trusts, queries "Institutional Shareholder Services" and "Glass Lewis" | Regex pulls the proxy-voting passages. **AI step:** tags advisor (ISS / Glass Lewis / both / none), role (research / execution / recommendations), own guidelines, and stewardship team. |
| 4 | `scoring.py` | — | Pure algorithm (thresholds in `config.yaml`). |
| 5 | `outreach.py` | — | Ranked table. The template is filled with each fund's metrics; **AI step** polishes it. |

### Scoring

A family's vote on an item is the majority direction across its series.

* **ISS agreement** = votes matching ISS / disagreement items voted (key + inferred items)
* **Category agreement** = the same, per category (only counted with ≥3 items)
* **Series divergence** = share of priority items, voted by ≥2 series, where the family's series split
* **Workload** = distinct meetings voted in the N-PX year (all categories)

| segment | rule |
|---|---|
| Insufficient data | < 10 items |
| ISS follower | ≥ 90% agreement |
| Management follower | ≤ 10% |
| Partial robo-voter | ≥ 90% in one category, mixed in another |
| Judgment voter | mixed overall |

The **primary list** is judgment and partial voters, ranked by workload. The
**secondary list** is ISS followers (pitch: Glass Lewis's 2027 change). Confidence starts
from data volume. It goes up one level when the SAI tells the same story: own guidelines
or a stewardship team for judgment voters, or ISS named without own guidelines for
followers.

## About the AI steps in this build

No Anthropic API credentials were configured on the build machine. The three AI steps
were therefore done by Claude inside the Claude Code session that built the repo, and
recorded with `source: "claude-session"`:

* `scripts/seed_iss_classifications.py` → `data/ai/iss_items.json`
* `scripts/seed_sai_tags.py` → `data/ai/sai_tags.json`
* `data/ai/outreach.json` (email polish)

`src/pennant/ai.py` resolves every answer in this order:
1. cache,
2. Claude API (`claude-opus-5`, structured output, server-side fallback),
3. deterministic regex rules.

Rules output is never cached. To re-run the AI steps against the API, set
`ANTHROPIC_API_KEY` and delete the JSON files in `data/ai/`. Every output row carries
`ai_source`.

## Deviations from the brief (and why)

* **ISS search widened.** The exact phrase "Institutional Shareholder Services" in
  DEFA14A/DEFC14A for Jan–Jul 2026 returns only 83 documents. Most of them are companies
  touting ISS *support*. The search therefore also uses `"ISS"` and dissident DFAN14A
  filings. Those are where ISS-backs-the-dissident items show up.
* **Contested elections past June 30 are out of scope.** Seer (July 2026) and a few other
  fights fall outside the N-PX year and are listed in
  `data/processed/iss_key_misses.csv`.
* **T. Rowe Price dropped.** Its fund trusts don't appear as N-PX filers in the Q3 2026
  index under a matchable name.
* **Families with no vote table.** Several filer CIKs (some MFS, Virtus, Neuberger
  trusts) filed N-PX notices with no vote table. Those trusts contribute nothing; the
  family is scored on the trusts that did report.
* **Per-family download cap.** Downloads are capped at 10 filings / 150 MB per family to
  keep the demo fast. Large families are therefore scored on their main equity trusts.

## Demo caveats

* Thresholds are assumptions, not calibrated values.
* N-PX covers registered funds only. Pensions, SMAs and other non-fund accounts are
  excluded.
* The key only covers items where ISS **publicly** opposed the board, as revealed by
  company or dissident soliciting material. That is a biased sample: it leans toward
  equity plans and say-on-pay at mid/small caps plus a handful of proxy fights.
* A family's "vote" is a majority across series. Sub-advised series can vote very
  differently from each other, and series divergence is reported for that reason.
* Validation accuracy rests on 8 held-out items. Treat it as a sanity check, not a
  measurement.

## Results (run of 2026-09-30)

**Data:**
* 30 families; 1,739,728 N-PX vote rows, of which 282,902 are in priority categories
  (34,240 canonical ballot items).
* 41 contested meetings found via DEFC14A.
* 164 soliciting documents produced 80 companies with ISS statements and 50
  board-opposed priority items. 40 of those were kept as the key: 29 pay,
  10 contested-election, 1 shareholder proposal.

| # | family | segment | confidence | meetings | key items | ISS agreement |
|---|---|---|---|---|---|---|
| 1 | Victory | Judgment voter | high | 4,849 | 36 | 72% |
| 2 | Columbia | Judgment voter | medium | 4,451 | 27 | 33% |
| 3 | Principal | Judgment voter | high | 3,866 | 37 | 68% |
| 4 | Thrivent | Judgment voter | medium | 3,826 | 22 | 73% |
| 5 | Harbor | Judgment voter | low | 3,006 | 21 | 33% |
| 6 | Schwab | Judgment voter | medium | 2,397 | 22 | 27% |
| 7 | Allspring | Judgment voter | medium | 1,971 | 14 | 71% |
| 8 | Federated Hermes | Judgment voter | low | 1,382 | 17 | 35% |
| 9 | AMG | Judgment voter | medium | 786 | 15 | 53% |
| 10 | Hartford | Management follower | low | 1,311 | 10 | 10% |

The other 20 families have fewer than 10 key items and are marked "Insufficient data".

### What the demo actually shows

1. **No family is an ISS follower on board-opposed items.** The follower panel
   (≥90% on ≥10 items) is empty. The highest agreement is 73% (Thrivent). This matches
   the SAIs, most of which describe custom or house guidelines. As a result, the
   secondary "follower" list is empty on this key.
2. **Seed-and-expand did not run at the headline thresholds** (empty panel). A labelled
   what-if with a panel of ≥70% on ≥8 items (Allspring, Franklin Templeton,
   Thrivent, Victory, Virtus) infers 163 more board-opposed items, almost all
   shareholder proposals. That output is in
   `data/processed/sensitivity_inferred_items.csv` and is **not** used for scoring.
3. **Validation could not be measured.** Holding out 8 of the 40 key items leaves too few
   items to qualify any panel family. Across relaxed settings and seeds (tested
   ad hoc), the panel inferred only 0–2 of 5–6 held-out items. That is too few to state
   an accuracy. A useful validation needs a bigger key: more search terms, earlier
   months, or a licensed ISS rec file.
4. **Coverage is the binding constraint.** Only 9 of 30 families reach 10 key items. The
   key leans toward mid/small-cap equity plans, which the smaller families don't hold.

