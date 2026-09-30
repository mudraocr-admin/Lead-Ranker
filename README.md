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

1. **Management rate first, independent of any adviser.** This is the share of the
   family's votes cast with the board across every priority item: shareholder
   proposals, contested elections, M&A, pay. Families at **≥90% with the board**
   (and ≥50 such items) are **Tier 3** and are not analysed further.
   * The board's position comes from who put the item on the ballot: company items
     are FOR, US shareholder proposals and dissident nominees are AGAINST.
   * Non-US shareholder items are left out. Their board position is ambiguous (e.g.
     Italian slates, shareholder nominees that boards back).
   * N-PX's own management-recommendation field is **not** used: on the same
     shareholder proposal about half the filers report the board as FOR.
2. **Answer keys.** For each adviser, collect the ballot items where it publicly
   opposed the board, as stated in company or dissident soliciting material
   (DEFA14A / DEFC14A / DFAN14A, Jan–Jul 2026). Only the 4 priority categories are
   kept.
3. **Which adviser to score against.** Taken from the family's SAI (485BPOS):
   * SAI names ISS → ISS.
   * SAI names Glass Lewis → Glass Lewis.
   * SAI names both, or neither → both are measured, and the higher agreement counts
     (among advisers with ≥10 items).
   * The choice and the reason are in `scored_adviser` / `scored_adviser_reason`.
     Agreement with both advisers is always reported.
4. **Adviser agreement** = votes matching the adviser / that adviser's items the family
   voted on. A family's vote on an item is the majority direction across its funds.
5. **Segment**, checked in this order (thresholds in `config.yaml`):

| check | segment | tier |
|---|---|---|
| < 50 items with a known board position | Insufficient data | last |
| management rate ≥ 90% | Management follower | 3 |
| < 10 items where its adviser opposed the board | Insufficient adviser data | last |
| adviser agreement ≥ 90% | ISS / Glass Lewis follower | 2 |
| ≥ 90% adviser agreement in one category, mixed in another | Partial robo-voter | 1 |
| otherwise | Judgment voter | 1 |

6. **Confidence** (low/medium/high):
   * Management followers: high with ≥200 items.
   * Everyone else: starts at medium with ≥30 adviser items, low otherwise. It goes up
     one level when the SAI supports the segment.
   * It does not affect the order.

### Why so many "Insufficient adviser data"

The adviser benchmark is **≥10 items where the family's adviser publicly opposed the
board**. The keys are small: ISS 40 items, Glass Lewis 21. They also lean toward
mid/small caps that many families don't hold.

Lowering the bar costs accuracy:

| items | a "follower" at 90% means | 95% interval on the true rate |
|---|---|---|
| 5 | 5 of 5 | 57–100% |
| 10 | 9 of 10 | 60–98% |
| 20 | 18 of 20 | 70–97% |

Even 10 is thin. Growing the key is better than lowering the bar:
* Search Jul–Dec 2025 soliciting material. The N-PX year starts Jul 2025, but the
  search starts Jan 2026.
* Drop the 40-item cap.
* Add phrasings like "proxy advisory firm".
* Best: a licensed ISS / Glass Lewis recommendation file.

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

**Keys:** ISS 40 board-opposed items; Glass Lewis 21.

**Segments:**
* **Tier 1, 8 families:** Victory, Columbia, Principal, Thrivent, Harbor, Schwab,
  Allspring, AMG. Management rates are 82–90%, and agreement with their adviser is
  36–73%.
* **Tier 2, none.**
* **Tier 3, 8 families:** Calamos (98% with the board), Ariel, Lord Abbett, Diamond
  Hill, Janus Henderson, Brown Advisory, MFS, Dodge & Cox.
* **Insufficient adviser data, 14 families:** fewer than 10 adviser-opposed items. All
  of them have plenty of data for the management rate (228–14,809 items); what's
  missing is adviser evidence.

**Validation:** not measurable; both follower panels are empty at the headline
thresholds.

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
