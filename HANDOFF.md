# Handoff: Pennant GTM prospect segmentation

Updated 2026-10-01 · HEAD `a4dd09e` · private repo `mudraocr-admin/pennant-prospects`
(temporary home; the user will move it). `README.md` has the full method; this page is
the state and the open problems.

## What it does

Ranks the fund families in `families.csv` by **how they actually vote**, using only
public SEC filings. Output: `output/priority_list.csv`. Tier 1 = independent voters
(the best prospects), Tier 2 = proxy-adviser followers, Tier 3 = management followers.
Within a tier, families are ordered by workload (meetings voted per year). Every family
gets a tier; there is no "insufficient data" outcome any more.

## Pipeline (`python run.py`; ~10 min with warm caches, ~35 min cold)

1. **N-PX votes** (`npx.py`, `items.py`): each family's trusts (CIKs listed in the
   `ciks` column, audited in `output/filers.csv`), parsed from raw EDGAR XML. Kept:
   shareholder proposals, contested elections, M&A, pay. Ballot items are fuzzy-clustered
   across filers.
2. **Management rate first**: the N-PX `managementRecommendation` field means "this vote
   was for/against management". It is read as-is. A rate of ≥90% (on ≥50 items) puts a
   family in Tier 3. The board's position per item is the consensus that field implies;
   who sponsored the item is used only as a fallback.
3. **Answer keys** (`adviser_key.py`): ISS and Glass Lewis recommendations quoted in
   DEFA14A/DEFC14A/DFAN14A, Jul 2025–Jul 2026, turned into items by the AI step and
   joined to ballot items. ISS has 133 items (64 board-opposed); Glass Lewis has 99 (45).
4. **Reference universe**: votes from 969 other fund filers (4,763 funds), cached in
   `data/processed/npx_universe_*.parquet`. Funds that follow an adviser form a panel
   whose consensus infers more recommendations, but only if a 5×25% hold-out scores ≥90%
   on board-opposed items.
5. **Segment** (`scoring.py`): a follower is a family that agrees with its adviser on
   ≥90% of items where the adviser **opposed the board**. The adviser comes from the
   SAI. With fewer than 10 such items, a 95% Wilson bound decides, then the SAI. Those
   calls are always low confidence, with the rule named in `confidence_reason`.

## Decisions the user made (don't relitigate)

- No outreach email drafting. The user supplies the target firms (`families.csv`;
  the current 30 are placeholders).
- Management followers are removed first, independent of advisers.
- Read the N-PX management flag as-is (verified by cross-tab on 2026-09-30).
- Following is judged on board-opposed items only (2026-10-01). Board-backed items
  inflated 11 families into Tier 2.
- The `ciks` column sits right after `family`.

## Active problems, in priority order

1. **ISS inference is unused.** The ISS panel scored 85% on board-opposed hold-outs
   against a 90% gate, so ISS families are judged on public quotes only.
2. **The Glass Lewis gate passed at exactly 90%** (about 18 of 20). It could flip on a
   rerun or with a tweak, and it drives much of the Glass Lewis scoring.
3. **Gaps in `families.csv` coverage.** MFS has only 6 funds (its big equity trusts are
   not matched); Delaware and Neuberger are thin. Sibling trusts are missed (Lord Abbett
   Global, standalone Putnam funds, Invesco/MFS ETF trusts). Thrivent ETF Trust is cut
   by the 150 MB per-family cap.
4. **Weak Tier 2 labels.** Alger and Baird are Tier 2 only via the SAI rule (0–5
   board-opposed items). Causeway is Tier 1 via its SAI; it votes 91% at non-US
   companies, which US soliciting material can't cover.
5. **No API key.** All AI answers are in-session classifications cached in
   `data/ai/*.json` (seed scripts are in `scripts/`). New families' SAIs fall back to
   regex rules. `config.yaml` sets `ai.model: "claude-opus-5"`; verify that ID before
   the first API run.
6. **`universe.max_gb` is not enforced on download**, only on parsing; the parallel
   prefetch pulls every filing.
7. **Dead-weight code.** The old target-family panel plus `panel_sensitivity` still
   run and write a meaningless `headline` block to `validation.json`.
8. **No tests.** Verify by rerunning and diffing `priority_list.csv`.
9. **Unmatched key items.** 30 ISS and 27 Glass Lewis items are unmatched (mostly
   meetings after 30 June 2026); see `data/processed/*_key_misses.csv`.

## Housekeeping

Commits use the gmail identity, as all history does. After the repo moves, run
`git remote set-url origin <new>`. GitHub account quirks are in the user's Claude
memory (`git-github-accounts`).
