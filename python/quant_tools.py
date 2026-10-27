"""
Shared helpers for Upgrades 19-24 (calibration, market blend, significance, scoreline and prop checks).

Put this file in the DC upgrade (6) folder next to dc_model.py. The other new scripts import it.
The database connection comes from the PREM_DB_URL environment variable (Upgrade 16), exactly
like every other script.
"""
import os
import unicodedata

import numpy as np
import pandas as pd
from scipy.optimize import brentq, minimize_scalar

FD_CACHE = "player_data_cache"          # player_data.py keeps the football-data files here
SEASONS = ["2023-24", "2024-25", "2025-26", "2026-27"]
TEST_SEASONS = ["2024-25", "2025-26"]   # the two seasons every upgrade is judged on


# ------------------------------------------------------------------ database
def engine():
    from sqlalchemy import create_engine
    import dc_model
    return create_engine(os.environ.get("PREM_DB_URL", dc_model.DB_URL))


def q(sql, params=None, eng=None):
    """Run a SELECT and return a DataFrame."""
    from sqlalchemy import text
    eng = eng or engine()
    with eng.connect() as con:
        return pd.read_sql(text(sql), con, params=params or {})


def execute_many(sql, rows, eng=None):
    from sqlalchemy import text
    eng = eng or engine()
    with eng.begin() as con:
        con.execute(text(sql), rows)


# ------------------------------------------------------------------ scoring
def rps(p, y):
    """Ranked Probability Score for 1X2. p: (n,3) home/draw/away; y: (n,) 0/1/2. Lower is better."""
    p = np.asarray(p, float)
    o = np.zeros_like(p)
    o[np.arange(len(y)), np.asarray(y, int)] = 1
    cp, co = np.cumsum(p, 1)[:, :2], np.cumsum(o, 1)[:, :2]
    return 0.5 * ((cp - co) ** 2).sum(1)


def logloss(p, y):
    """Negative log of the chance given to what happened (per row). Lower is better."""
    p = np.clip(np.asarray(p, float), 1e-12, 1)
    return -np.log(p[np.arange(len(y)), np.asarray(y, int)])


def result_index(home_goals, away_goals):
    hg, ag = np.asarray(home_goals), np.asarray(away_goals)
    return np.where(hg > ag, 0, np.where(hg == ag, 1, 2))


# ------------------------------------------------------------------ bookmaker margin removal
def _row_fair(odds, method):
    o = np.asarray(odds, float)
    if np.any(~np.isfinite(o)) or np.any(o <= 1):
        return np.full(len(o), np.nan)
    pi = 1 / o
    if method == "proportional":
        return pi / pi.sum()
    if method == "power":                       # p_i = pi_i ^ k with k chosen so they sum to 1
        k = brentq(lambda k: (pi ** k).sum() - 1, 0.5, 3.0)
        return pi ** k
    if method == "shin":                        # Shin (1993): allows for insider trading on longshots
        s = pi.sum()

        def probs(z):
            return (np.sqrt(z ** 2 + 4 * (1 - z) * pi ** 2 / s) - z) / (2 * (1 - z))
        if abs(s - 1) < 1e-9:
            return pi
        z = brentq(lambda z: probs(z).sum() - 1, 0.0, 0.4)
        return probs(z)
    raise ValueError(method)


def fair_probs(odds, method="power"):
    """Margin-free chances from decimal odds. odds: (n,k) array (k = 3 for 1X2, 2 for over/under)."""
    odds = np.atleast_2d(np.asarray(odds, float))
    return np.vstack([_row_fair(r, method) for r in odds])


# ------------------------------------------------------------------ calibration
def power_scale(p, a):
    """Sharpen (a > 1) or flatten (a < 1) chances: q_i proportional to p_i ^ a."""
    p = np.clip(np.asarray(p, float), 1e-9, 1)
    q = p ** a
    return q / q.sum(1, keepdims=True)


def fit_power(p, y):
    """The a that minimises log loss on (p, y)."""
    res = minimize_scalar(lambda a: logloss(power_scale(p, a), y).mean(), bounds=(0.5, 2.5), method="bounded")
    return float(res.x)


def logistic_slope(p_event, hit):
    """Calibration intercept and slope: fit P(hit) = expit(a + b * logit(p)).
    b = 1 is perfect; b > 1 means the model is too cautious (too flat), b < 1 too confident."""
    p = np.clip(np.asarray(p_event, float), 1e-6, 1 - 1e-6)
    x = np.log(p / (1 - p))
    yv = np.asarray(hit, float)
    X = np.column_stack([np.ones_like(x), x])
    beta = np.array([0.0, 1.0])
    for _ in range(50):                         # Newton / IRLS
        mu = 1 / (1 + np.exp(-X @ beta))
        w = mu * (1 - mu)
        grad = X.T @ (yv - mu)
        hess = X.T @ (X * w[:, None])
        step = np.linalg.solve(hess, grad)
        beta += step
        if np.abs(step).max() < 1e-9:
            break
    se = np.sqrt(np.diag(np.linalg.inv(hess)))
    return beta[0], beta[1], se[1]


# ------------------------------------------------------------------ football-data files
def read_fd(season, folder=FD_CACHE):
    """A season's football-data.co.uk file from player_data.py's cache."""
    path = os.path.join(folder, f"fd_{season}_E0.csv")
    if not os.path.exists(path):
        return None
    for enc in ("utf-8-sig", "latin-1"):
        try:
            d = pd.read_csv(path, encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    d.columns = [c.replace("﻿", "").replace("ï»¿", "").strip() for c in d.columns]
    d = d.dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG"]).copy()
    d["season"] = season
    return d


def attach_match_ids(fd, eng=None):
    """Add match_id to football-data rows using your team_aliases and matches tables."""
    alias = q("SELECT alias, team_id FROM team_aliases", eng=eng)
    amap = dict(zip(alias["alias"].str.strip(), alias["team_id"]))
    m = q("""SELECT m.match_id, s.label AS season, m.home_team_id, m.away_team_id
             FROM matches m JOIN seasons s USING (season_id)""", eng=eng)
    fd = fd.copy()
    fd["home_team_id"] = fd["HomeTeam"].str.strip().map(amap)
    fd["away_team_id"] = fd["AwayTeam"].str.strip().map(amap)
    unknown = sorted(set(fd.loc[fd["home_team_id"].isna(), "HomeTeam"]) |
                     set(fd.loc[fd["away_team_id"].isna(), "AwayTeam"]))
    if unknown:
        print("  team names not in team_aliases (rows skipped):", ", ".join(unknown))
    fd = fd.dropna(subset=["home_team_id", "away_team_id"])
    fd[["home_team_id", "away_team_id"]] = fd[["home_team_id", "away_team_id"]].astype(int)
    return fd.merge(m, on=["season", "home_team_id", "away_team_id"], how="inner")


# ------------------------------------------------------------------ predictions
def closest_predictions(model, seasons=TEST_SEASONS, eng=None):
    """Each match once: the out-of-sample prediction from the latest run before it was played
    (the realistic betting horizon). Comes from v_scored_predictions."""
    d = q("""SELECT model, season, run_label, run_gameweek, match_id, round_no,
                    p_home, p_draw, p_away, result, rps
             FROM v_scored_predictions
             WHERE model = :m AND season = ANY(:s) AND run_label LIKE '%after GW%'""",
          {"m": model, "s": list(seasons)}, eng=eng)
    if d.empty:
        return d
    d = d.sort_values(["match_id", "run_gameweek"]).drop_duplicates("match_id", keep="last")
    d["y"] = d["result"].map({"H": 0, "D": 1, "A": 2})
    return d


def first_existing(models, eng=None):
    have = set(q("SELECT DISTINCT model FROM v_scores", eng=eng)["model"])
    for m in models:
        if m in have:
            return m
    return None


# ------------------------------------------------------------------ names
def plain(s):
    """Lower case, no accents or punctuation: 'Ødegaard' -> 'odegaard'."""
    s = str(s).replace("-", " ").replace("ø", "o").replace("Ø", "O").replace("ß", "ss").replace("đ", "d").replace("ł", "l")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return "".join(ch for ch in s.lower() if ch.isalnum() or ch == " ").strip()
