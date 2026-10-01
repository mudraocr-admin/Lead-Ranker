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

One row per family, ranked. The second column, `ciks`, lists the N-PX filer CIKs
(the family's trusts) whose votes were used, largest first; `filers` at the end
gives their names in the same order.

| tier | who | segments |
|---|---|---|
| **Tier 1** | follows neither an adviser nor management | Judgment voter, Partial robo-voter |
| **Tier 2** | follows its proxy adviser | ISS follower, Glass Lewis follower |
| **Tier 3** | follows management | Management follower |

Within a tier, families are ordered by **workload**: distinct meetings voted in the
N-PX year.

Also written:
* `iss_key.csv` and `gl_key.csv`: each answer key, with the evidence sentence behind
  every call.
* `filers.csv`: every N-PX filing matched for each family and what happened to it
  (used, dropped as an adviser's own filing, dropped as a fund filing with no votes,
  or skipped by the download cap). Use it to check the right trusts were picked up.
* `validation.json`
* `run_summary.json`

### How a family is scored

1. **Management rate first, independent of any adviser.** This is the share of the
   family's votes cast with the board across every priority item: shareholder
   proposals, contested elections, M&A, pay. Families at **≥90% with the board**
   (and ≥50 such items) are **Tier 3** and are not analysed further.
   * "With the board" is read straight from each N-PX vote's for/against-management
     field. That field says whether *the vote* matched management's recommendation;
     it is not the board's position. (Cross-tab on JPMorgan's 2026 independent-chair
     proposal: all 104 AGAINST votes are flagged "for management", all 92 FOR votes
     "against management".) Where a family's funds split, the item counts the share
     of its funds that voted with management.
   * The board's position on each item, used by the adviser keys, is recovered from
     the same field: each vote implies one (the vote itself if "for management", the
     opposite if "against"), and the item takes the consensus across all filers
     (≥75% agreement; in practice 99%+ on almost every item).
   * Where there is no consensus, the sponsor rule fills in: company items FOR, US
     shareholder proposals and dissident nominees AGAINST. The two disagree on 93
     items, all cases the sponsor rule gets wrong (boards backing a declassification
     proposal, Australian spill resolutions, a company's own nominees in a fight
     labelled "Genco Nominee"); see
     `data/processed/board_position_vs_sponsor_rule.csv`.
2. **Answer keys.** For each adviser, collect every priority-category ballot item where
   its recommendation is stated in company or dissident soliciting material (DEFA14A /
   DEFC14A / DFAN14A, Jul 2025–Jul 2026, the full N-PX year). Both kinds are kept:
   items where the adviser **opposed** the board, and items where it **backed** it.
   Only the opposed ones decide following (step 5); both are reported
   (`agree_<adviser>_opposed` / `_aligned`).
   * The key is extended by the reference-fund panel when that panel passes its
     hold-out gate (see Pipeline).
   * In proxy fights, a whole-slate call ("ISS backs all company nominees") applies to
     each nominee. The board's side is read from the ballot text ("COMPANY
     NOMINEE", "…Nominee Opposed by the Company").
3. **Which adviser to score against.** Taken from the family's SAI (485BPOS):
   * SAI names ISS → ISS.
   * SAI names Glass Lewis → Glass Lewis.
   * SAI names both, or neither → both are measured, and the higher agreement on
     board-opposed items counts (among advisers with ≥10 such items).
   * The choice and the reason are in `scored_adviser` / `scored_adviser_reason`.
     Agreement with both advisers is always reported.
4. **Adviser agreement** (`adviser_agreement`) = the family's agreement with its
   adviser **on items where the adviser opposed the board**. A family's vote on an item
   is the majority direction across its funds. Overall agreement (`agree_<adviser>`)
   is still reported but does not decide anything. See the caveat below for why.
5. **Segment**, checked in this order (thresholds in `config.yaml`):

| check | segment | tier |
|---|---|---|
| management rate ≥ 90% | Management follower | 3 |
| < 10 board-opposed items with its adviser's rec | thin-data rules, below | 1 or 2 |
| agreement on board-opposed items ≥ 90% | ISS / Glass Lewis follower | 2 |
| ≥ 90% on board-opposed items in one category, mixed in another | Partial robo-voter | 1 |
| otherwise | Judgment voter | 1 |

   **Every family gets a segment.** When a family has fewer than 10 board-opposed
   items with its adviser's recommendation:
   * **3 or more items:** if the 95% Wilson upper bound on its agreement is below 90%,
     following is ruled out statistically → Judgment voter. (5 of 9 gives an upper
     bound of 81%; 4 of 5 gives 96%, which can't rule it out.)
   * **Otherwise the SAI decides:** it names the adviser for voting recommendations
     and describes no own guidelines → that adviser's follower; anything else →
     Judgment voter.
   * These labels are always **low** confidence, and `confidence_reason` says which
     rule produced them. A family with under 50 management items is still classified
     on its rate, also at low confidence.

6. **Confidence** (low/medium/high):
   * Management followers: high with ≥200 items.
   * Everyone else: starts at medium with ≥20 board-opposed adviser items, low
     otherwise. It goes up one level when the SAI supports the segment.
   * Thin-data labels are always low.
   * It does not affect the order.

### Why only board-opposed items decide following

When the adviser and the board agree, any family that leans toward management also
"agrees with the adviser". Once the reference-fund panel added inferred Glass Lewis
recommendations, board-backed items outnumbered board-opposed ones about 7 to 1, and
overall agreement put 11 of 30 families in Tier 2. Most of them rarely side with the
adviser against the board:

| family | overall agreement with Glass Lewis | where Glass Lewis **opposed** the board |
|---|---|---|
| Schwab | 93% | 48% of 143 |
| Hartford | 93% | 54% of 41 |
| Delaware / Macquarie | 91% | 52% of 31 |
| Federated Hermes | 93% | 65% of 69 |

Following an adviser means siding with it against management, so only those items
decide (decision of 2026-10-01).

## Pipeline

| step | module | source |
|---|---|---|
| 1 N-PX votes | `npx.py`, `items.py` | EDGAR `full-index/2026/QTR3/form.idx`: N-PX filed Aug 2026 (votes Jul 2025–Jun 2026). Only fund filers (registrantType RMIC) are kept. Same-item descriptions from different families are clustered into one ballot item. |
| 1 contested elections | `npx.py` | N-PX has no contest flag, so issuers are matched to DEFC14A filings made 0–120 days before the meeting. Meetings the adviser text shows were proxy fights are added. |
| 2 answer keys | `adviser_key.py` | EDGAR full-text search. ISS: `"Institutional Shareholder Services"`, `"ISS"`. Glass Lewis: `"Glass Lewis"`, `"Glass, Lewis"`. Sentences naming the adviser + "recommend" → **AI** turns them into items (adviser rec, board rec, evidence) → every priority item with a stated rec (backed or opposed the board) is joined to N-PX ballot items. No cap. |
| 2 reference universe | `npx.py`, `items.py` | Every other fund filer of N-PX in Aug 2026 (≈8,000 filers outside the target list). Only their votes at meetings the target families voted in are kept, matched to the same ballot items. They are never scored. One-time download, capped at `universe.max_gb`, cached afterwards. |
| 2 panel / inference | `scoring.py` | Per adviser: **reference funds** at ≥90% agreement on ≥10 key items, including ≥3 where the adviser opposed the board (≥75% there), form a panel. Items where ≥5 panel funds vote ≥90% alike get an inferred rec. **Gate:** 5 hold-out rounds, each hiding 25% of key items; inferred recs are used only if accuracy on hidden board-opposed items is ≥90% with ≥30% of them inferred (`validation.json`, `data/processed/validation_universe_*.csv`). If the gate fails, the older target-family panel is tried. |
| 3 SAI | `sai.py` | 485BPOS full-text search for ISS / Glass Lewis → **AI** tags advisor, role (research / execution / recommendations), own guidelines, stewardship team. |
| 4–5 scoring, list | `scoring.py`, `prospects.py` | as above |

## About the AI steps in this build

No Anthropic API credentials were configured on the build machine. The snippet
classifications were therefore done by Claude inside the Claude Code session that
built the repo, and recorded with `source: "claude-session"`:

* `scripts/seed_adviser_classifications.py` → `data/ai/iss_items.json`,
  `data/ai/gl_items.json`
* `scripts/seed_adviser_classifications_h2_2025.py` adds the 39 ISS / 25 Glass Lewis
  companies that appeared when the search was widened to Jul 2025. It only adds
  companies and never overwrites earlier answers.
* `scripts/seed_sai_tags.py` → `data/ai/sai_tags.json`

`src/pennant/ai.py` resolves in this order: cache → Claude API → regex rules. Rules
output is never cached. **When you change `families.csv`:**
* The adviser keys don't depend on the family list, so they stay valid.
* New families' SAIs are tagged by the API, or by the regex rules if no key is set.
* Set `ANTHROPIC_API_KEY` to have the model tag them.

## Results (run of 2026-10-01, placeholder list of 30 families)

**Keys** (Jul 2025–Jul 2026 soliciting material):
* ISS: 133 items, of which 64 oppose the board.
* Glass Lewis: 99 items, of which 45 oppose the board.

**Reference universe:** 969 fund filers outside the target list (4,763 funds); 1.5M of
their votes match the target families' ballot items.

**Panel gate** (`validation.json`):
* ISS: 58 follower funds. Hold-out accuracy 93% overall, but **85%** on board-opposed
  items, so the gate failed and inferred ISS recs are **not** used.
* Glass Lewis: 17 follower funds. Hold-out accuracy 97% overall and **90%** on
  board-opposed items (36% covered), so it passed, but only just. Inferred Glass Lewis
  recs are used.

**Segments:** no family is left unclassified.
* **Tier 1, 19 families:** American Century, Franklin Templeton, Victory, Columbia,
  Principal, Thrivent, Harbor, Schwab, Allspring, Putnam, Federated Hermes, Invesco,
  Hartford, AMG, Neuberger Berman, Delaware / Macquarie, Causeway, Artisan, Parnassus.
* **Tier 2, 3 families:** Virtus (ISS, 11 of 12 against the board), Alger and Baird
  (ISS, both low confidence: too few board-opposed items, so the SAI decides).
* **Tier 3, 8 families:** Janus Henderson, Calamos, Lord Abbett, Brown Advisory,
  Dodge & Cox, MFS, Diamond Hill, Ariel.
* Low confidence, with the rule named in `confidence_reason`: Putnam, Causeway,
  Artisan, Parnassus, Alger, Baird.

## Deviations from the brief (and why)

* **All adviser items kept, not just board-opposed ones.** The brief's 25–40 item cap
  was dropped too.
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
* The keys only include recommendations that appear in **public** soliciting
  material. That leans toward equity plans, say-on-pay, M&A and proxy fights, and
  toward US companies. Families that mostly hold non-US stocks (Causeway: 91% of its
  items) get few key items.
* Board-opposed items per family range from 0 to ~200; under ~30, agreement carries
  roughly ±15-point uncertainty.
* A family's vote is a majority across its funds. Sub-advised funds can differ, which
  is reported as series divergence.
