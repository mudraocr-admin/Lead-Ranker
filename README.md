# Pennant prospect segmentation (demo)

This demo ranks the mutual fund families you list by **how they actually vote** on
contested proxy items. It measures whether each family follows its proxy adviser
(ISS or Glass Lewis), follows management, or makes its own calls. The inputs are public
SEC filings only. Everything is Python and pandas; an LLM is used only to classify
text snippets (adviser recommendations and SAI proxy-voting passages).

```
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
# edit families.csv (your target list), then:
python run.py        # ~3 min cold (downloads ~1 GB of N-PX), faster warm
```

## Input: `families.csv`

You maintain the target list; the tool never picks firms.

| column | required | meaning |
|---|---|---|
| `family` | yes | display name |
| `match` | no | regex over EDGAR N-PX filer names. Blank means "filer name starts with `family`". |
| `ciks` | no | semicolon-separated filer CIKs. When given, they are used instead of `match`. |

A warning is printed for any family that matches no N-PX filer. The 30 families
currently in the file are **placeholders** from the first demo run; replace them.

## Output: `output/priority_list.csv`

One row per family, ranked:

| tier | who | segments |
|---|---|---|
| **Tier 1** | follows neither an adviser nor management | Judgment voter, Partial robo-voter |
| **Tier 2** | follows its proxy adviser | ISS follower, Glass Lewis follower |
| **Tier 3** | follows management | Management follower |
| last | not enough data to call | Insufficient data |

Within a tier, families are ordered by **workload**: distinct meetings voted in the
N-PX year.

Also written:
* `iss_key.csv` and `gl_key.csv`: each answer key, with the evidence sentence behind
  every call.
* `validation.json`
* `run_summary.json`

### How a family is scored

1. **Answer keys.** For each adviser, collect the ballot items where it publicly
   opposed the board, as stated in company or dissident soliciting material
   (DEFA14A / DEFC14A / DFAN14A, Jan–Jul 2026). Keep items in the 4 priority
   categories: shareholder proposals, contested director elections, M&A, pay.
2. **Which adviser to score against.** Taken from the family's SAI (485BPOS):
   * SAI names ISS → ISS.
   * SAI names Glass Lewis → Glass Lewis.
   * SAI names both, or neither → both are measured, and the higher agreement counts
     (among advisers with ≥10 items).
   * The choice and the reason are in `scored_adviser` / `scored_adviser_reason`.
     Agreement with both advisers is always reported.
3. **Metrics.** A family's vote on an item is the majority direction across its funds.
   * **Adviser agreement** = votes matching the adviser / that adviser's items the
     family voted on.
   * **Management rate** = votes with the board / all items where ISS *or* Glass Lewis
     opposed the board.
   * Both are also broken out by category. Series divergence (how often the family's
     own funds split) is reported but not used in the ranking.
4. **Segment** (thresholds in `config.yaml`), checked in this order:

| check | segment |
|---|---|
| fewer than 10 items overall | Insufficient data |
| ≥10 items with its adviser and ≥90% agreement | ISS / Glass Lewis follower |
| management rate ≥90% | Management follower |
| fewer than 10 items with its adviser | Insufficient data (can't rule out following it) |
| ≥90% adviser agreement in one category, mixed in another | Partial robo-voter |
| otherwise | Judgment voter |

5. **Confidence** (low/medium/high) starts at medium with ≥30 items, low otherwise. It
   goes up one level when the SAI supports the segment: own guidelines or a
   stewardship team for tier 1, or the followed adviser named without own guidelines
   for tier 2. It does not affect the order.

## Pipeline

| step | module | source |
|---|---|---|
| 1 N-PX votes | `npx.py`, `items.py` | EDGAR `full-index/2026/QTR3/form.idx`: N-PX filed Aug 2026 (votes Jul 2025–Jun 2026). Only fund filers (registrantType RMIC) are kept. Same-item descriptions from different families are clustered into one ballot item. |
| 1 contested elections | `npx.py` | N-PX has no contest flag, so issuers are matched to DEFC14A filings made 0–120 days before the meeting. Meetings the adviser text shows were proxy fights are added. |
| 2 answer keys | `adviser_key.py` | EDGAR full-text search. ISS: `"Institutional Shareholder Services"`, `"ISS"`. Glass Lewis: `"Glass Lewis"`, `"Glass, Lewis"`. Sentences naming the adviser + "recommend" → **AI** turns them into items (adviser rec, board rec, evidence) → items opposing the board are joined to N-PX. Up to 40 per adviser, ranked by families voting. |
| 2 panel / inference | `scoring.py` | Per adviser: families at ≥90% on ≥10 key items form a panel. Items where ≥5 panel families vote ≥90% alike get an inferred rec. The scored family is excluded from its own panel. A hold-out validation is run. |
| 3 SAI | `sai.py` | 485BPOS full-text search for ISS / Glass Lewis → **AI** tags advisor, role (research / execution / recommendations), own guidelines, stewardship team. |
| 4–5 scoring, list | `scoring.py`, `prospects.py` | as above |

## About the AI steps in this build

No Anthropic API credentials were configured on the build machine. The snippet
classifications were therefore done by Claude inside the Claude Code session that
built the repo, and recorded with `source: "claude-session"`:

* `scripts/seed_adviser_classifications.py` → `data/ai/iss_items.json`,
  `data/ai/gl_items.json`
* `scripts/seed_sai_tags.py` → `data/ai/sai_tags.json`

`src/pennant/ai.py` resolves in this order: cache → Claude API → regex rules. Rules
output is never cached. **When you change `families.csv`:**
* The adviser keys don't depend on the family list, so they stay valid.
* New families' SAIs are tagged by the API, or by the regex rules if no key is set.
* Set `ANTHROPIC_API_KEY` to have the model tag them.

## Results (run of 2026-09-30, placeholder list of 30 families)

**Keys:**
* ISS: 40 board-opposed items (30 pay, 9 contested-election, 1 shareholder proposal).
* Glass Lewis: 21 items (13 contested-election, 7 pay, 1 shareholder proposal). That is
  fewer than the 25 target, because companies publish rebuttals to Glass Lewis less
  often.

**Segments:**
* **Tier 1, 8 families:** Victory, Columbia, Principal, Thrivent, Harbor, Schwab,
  Allspring, AMG.
* **Tier 2, none:** no family reaches 90% agreement with its adviser. The highest are
  Thrivent (73% with ISS) and AMG (78% with Glass Lewis, on 9 items only).
* **Tier 3, Hartford:** 90% with management, on 10 items.
* **Insufficient data, 21 families:** too few key items. This includes American
  Century and Franklin Templeton despite heavy voting, and Virtus, which agreed with
  ISS on 9 of 9 items (one short of the minimum).

**Validation:** not measurable for either adviser. Both panels are empty at the
headline thresholds. A relaxed what-if (≥70% on ≥8 items) is in
`data/processed/sensitivity_inferred_*.csv`; it is not used for scoring.

## Deviations from the brief (and why)

* **Wider soliciting-material search.** `"ISS"` and dissident DFAN14A filings were added
  to the ISS query, because the exact phrase alone returned 83 documents, mostly
  companies touting ISS *support*.
* **Glass Lewis added** as a second answer key, so families using Glass Lewis are
  measured against Glass Lewis.
* **Fights after June 30 are out.** Proxy fights after June 30, 2026 (e.g. Seer) fall
  outside the N-PX year; see `data/processed/*_key_misses.csv`.
* **Per-family download cap.** Capped at 10 N-PX filings / 150 MB per family, so large
  families are scored on their main equity trusts.

## Caveats

* Thresholds are assumptions.
* N-PX covers registered funds only, so pensions and SMAs are excluded.
* The keys only include items where an adviser **publicly** opposed the board. That
  leans toward equity plans, say-on-pay and a handful of proxy fights.
* With 10–42 items per family, agreement rates carry roughly ±15-point uncertainty.
* A family's vote is a majority across its funds. Sub-advised funds can differ, which
  is reported as series divergence.
