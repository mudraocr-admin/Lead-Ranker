"""Sanity check for impute.py on simulated votes with known recommendations.

    python scripts/check_impute_synthetic.py

4,000 items, 300 voters: ISS followers (97%), Glass Lewis followers (96%),
management followers (95%) and mixed voters who half follow ISS. Glass Lewis
agrees with ISS on half the items. 130 random items are "quoted" as the seed.
Prints coverage and accuracy per adviser; on 2026-10-01 this gave ISS 100% / 100%
and Glass Lewis 99% coverage, ~97.6% accuracy.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pennant.impute import Imputer, seed_array  # noqa: E402

rng = np.random.default_rng(0)
I, J = 4000, 300
board = np.where(rng.random(I) < 0.85, "FOR", "AGAINST")
cat = rng.choice(["pay", "mna", "shareholder_proposal", "contested_election"], I)
with_board, against_board = (board == "FOR").astype(int), (board != "FOR").astype(int)
iss = np.where(rng.random(I) < 0.15, against_board, with_board)
gl = np.where(rng.random(I) < 0.5, iss, np.where(rng.random(I) < 0.2, against_board, with_board))
kind = rng.choice(["iss", "gl", "mgmt", "mix"], J, p=[.15, .10, .40, .35])

rows = []
for j in range(J):
    for i in rng.choice(I, size=rng.integers(50, 1500), replace=False):
        if kind[j] == "iss":
            v = iss[i] if rng.random() < 0.97 else 1 - iss[i]
        elif kind[j] == "gl":
            v = gl[i] if rng.random() < 0.96 else 1 - gl[i]
        elif kind[j] == "mgmt":
            v = with_board[i] if rng.random() < 0.95 else against_board[i]
        else:
            v = (iss[i] if rng.random() < 0.5 else with_board[i]) if rng.random() < 0.9 else rng.integers(0, 2)
        rows.append((f"v{j}", f"i{i}", v))
votes = pd.DataFrame(rows, columns=["voter", "item_key", "v"])
items = pd.DataFrame({"item_key": [f"i{i}" for i in range(I)], "board_rec": board, "category": cat})
cfg = {"impute": {"close_follower": 0.96, "close_min_items": 5, "close_min_opposed": 3,
                  "min_cell_votes": 10, "threshold": 0.9}}

seed = rng.choice(I, 130, replace=False)
quotes = lambda t: pd.DataFrame({"item_key": [f"i{i}" for i in seed],  # noqa: E731
                                 "rec": np.where(t[seed] == 1, "FOR", "AGAINST")})
out = Imputer(votes, items, cfg).run({"ISS": seed_array(items, quotes(iss)), "GL": seed_array(items, quotes(gl))})
for a, truth in (("ISS", iss), ("GL", gl)):
    lab = out[a].imputed.values
    cov = ~np.isnan(lab)
    print(f"{a}: coverage {cov.mean():.1%}, accuracy {(lab[cov] == truth[cov]).mean():.2%}")
