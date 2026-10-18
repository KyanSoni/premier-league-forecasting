"""
Upgrade 15.4: expected corners, fouls, cards, shots, shots on target and tackles for each team in
each match, with the referee factor and the spread setting the over/under lines need.

Put this file in the DC upgrade folder (next to player_minutes.py) and run in the VS Code terminal:

  python team_counts.py backtest   chooses the settings on 2024-25 (prints them), checks them on
                                   2025-26 (prints that too), then saves every gameweek's
                                   predictions with what actually happened (about 5-10 minutes)
  python team_counts.py next       the next gameweek, using the saved settings and the referees in
                                   fixture_referees (python player_data.py referees load)

How it works, for every stat, built like your goal model:
  league averages   home and away counts per match, recent matches counting most
  team ratings      for = his count vs the league average, against = what opponents get vs him,
                    pulled towards 1 when a team has few matches (worth K matches of average)
  expected count    home = home average x home 'for' x away 'against' (and the same for away)
  referee factor    fouls and cards only: his matches vs that season's average, pulled towards 1
                    (worth REF_K matches)
  game state        favourites win more corners etc.: x exp(+/- BETA x expected goal difference)
  spread            the negative binomial setting r (counts vary more than Poisson), fitted on
                    seasons before the one being predicted
The baseline to beat: league average with home advantage only.
"""
import os
import sys
import json

import numpy as np
import pandas as pd
from scipy.stats import nbinom, poisson

import player_minutes as pmin
from player_minutes import to_numbers

STATS = ["corners", "fouls", "yellows", "reds", "shots", "sot", "tackles"]
REF_STATS = {"fouls", "yellows"}    # reds: a referee setting made reds slightly worse than the baseline in both seasons (15.7), so none
SETTINGS_FILE = "team_counts_settings.json"
DEFAULT = {"half_life": 365, "k": 8, "beta": 0.0, "ref_k": 1e9}
GRID_HL = [90, 180, 365, 730]
GRID_K = [3, 8, 20, 100, 1000]     # 1000 = ignore team differences (league average)
GRID_BETA = [-0.2, 0.0, 0.2, 0.4]
GRID_REF_K = [10, 30, 60, 100, 1e9]  # 1e9 = no referee factor (60 and 100 added in 15.7: a weaker referee effect)
MAXN = 90


# ------------------------------------------------------------------ data
def load(eng):
    pm, players, matches, tms_small = pmin.load(eng)
    test = os.environ.get("TEST_DIR")
    cols = ["season", "match_date", "round_no", "match_id", "referee", "home_team_id", "away_team_id"]
    for s in ["goals", "shots", "sot", "corners", "fouls", "yellows", "reds"]:
        cols += ["home_" + s, "away_" + s]
    if test:
        tms = pd.read_csv(os.path.join(test, "v_team_match_stats.csv"))
        refs = pd.read_csv(os.path.join(test, "fixture_referees.csv"))
        names = pd.read_csv(os.path.join(test, "v_team_names.csv"))
    else:
        q = lambda s: to_numbers(pd.read_sql(s, eng))
        tms = q("SELECT " + ", ".join(cols) + " FROM v_team_match_stats "
                "WHERE home_team_id IS NOT NULL AND away_team_id IS NOT NULL")
        refs = q("SELECT season, gameweek, home_team, away_team, referee FROM fixture_referees")
        names = q("SELECT name, team_id FROM v_team_names")
    tms = tms[cols].copy()
    tms["match_date"] = pd.to_datetime(tms["match_date"])
    # tackles per team per match, from the player data (2024-25 on)
    tk = pm[pm["match_id"].notna()].groupby(["match_id", "team_id"])["tackles"].sum().reset_index()
    tk["match_id"] = tk["match_id"].astype(int)
    h = tk.rename(columns={"team_id": "home_team_id", "tackles": "home_tackles"})
    a = tk.rename(columns={"team_id": "away_team_id", "tackles": "away_tackles"})
    tms = tms.merge(h, on=["match_id", "home_team_id"], how="left").merge(a, on=["match_id", "away_team_id"], how="left")
    name_id = dict(zip(names["name"], names["team_id"]))
    refs = refs.copy()
    refs["home_team_id"] = refs["home_team"].map(name_id)
    refs["away_team_id"] = refs["away_team"].map(name_id)
    return pm, matches, tms_small, tms, refs


def long_form(tms):
    """Two rows per match: one for each team, with its count 'for' and 'against'."""
    out = []
    for side, opp in (("home", "away"), ("away", "home")):
        d = pd.DataFrame({"season": tms["season"], "match_date": tms["match_date"], "match_id": tms["match_id"],
                          "referee": tms["referee"], "team": tms[side + "_team_id"], "opp": tms[opp + "_team_id"],
                          "is_home": side == "home"})
        for s in ["goals"] + STATS:
            d[s + "_for"] = tms[side + "_" + s].values
            d[s + "_against"] = tms[opp + "_" + s].values
        out.append(d)
    return pd.concat(out, ignore_index=True)


# ------------------------------------------------------------------ the model
def ratings(hist, stat, cutoff, half_life, k):
    h = hist[hist[stat + "_for"].notna() & hist[stat + "_against"].notna()]
    if len(h) == 0:
        return None
    age = (cutoff - h["match_date"]).dt.days.clip(lower=0)
    w = 0.5 ** (age / half_life)
    home = h["is_home"].values
    H = np.average(h.loc[home, stat + "_for"], weights=w[home])
    A = np.average(h.loc[~home, stat + "_for"], weights=w[~home])
    base_for = np.where(home, H, A)
    base_against = np.where(home, A, H)
    m = (H + A) / 2
    g = pd.DataFrame({"team": h["team"].values, "w": w.values,
                      "xf": w.values * h[stat + "_for"].values, "bf": w.values * base_for,
                      "xa": w.values * h[stat + "_against"].values, "ba": w.values * base_against}).groupby("team").sum()
    att = (g["xf"] + k * m) / (g["bf"] + k * m)
    dfn = (g["xa"] + k * m) / (g["ba"] + k * m)
    return {"H": H, "A": A, "att": att.to_dict(), "def": dfn.to_dict()}


def ref_factors(hist_matches, stat, ref_k):
    """Each referee's matches vs that season's average total, pulled towards 1."""
    if ref_k >= 1e8:
        return {}
    d = hist_matches[hist_matches["referee"].notna() & hist_matches["home_" + stat].notna()].copy()
    if len(d) == 0:
        return {}
    d["tot"] = d["home_" + stat] + d["away_" + stat]
    d["ratio"] = d["tot"] / d.groupby("season")["tot"].transform("mean")
    g = d.groupby("referee")["ratio"].agg(["sum", "count"])
    return ((g["sum"] + ref_k) / (g["count"] + ref_k)).to_dict()


def fit_dispersion(long_hist, stat):
    """Negative binomial r from in-season team averages (method of moments)."""
    d = long_hist[long_hist[stat + "_for"].notna()]
    if len(d) < 200:
        return 30.0
    mu = d.groupby(["season", "team"])[stat + "_for"].transform("mean")
    x = d[stat + "_for"]
    excess = ((x - mu) ** 2).mean() - mu.mean()
    if excess <= 0:
        return 1000.0
    return float(np.clip((mu ** 2).mean() / excess, 2, 1000))


def pmf(mu, r):
    k = np.arange(MAXN)
    if r >= 999:
        return poisson.pmf(k, mu)
    return nbinom.pmf(k, r, r / (r + mu))


def main_line(base_expected):
    return np.floor(base_expected) + 0.5


def score_rows(rows):
    """Log score and P(over main line) for team and match totals; rows hold mu, base_mu, r, actual."""
    out = []
    for r_ in rows:
        ph, pa = pmf(r_["mu_h"], r_["r"]), pmf(r_["mu_a"], r_["r"])
        bh, ba = pmf(r_["base_h"], r_["r"]), pmf(r_["base_a"], r_["r"])
        pm_, bm = np.convolve(ph, pa)[:MAXN], np.convolve(bh, ba)[:MAXN]
        for side, p, b, mu, bmu, act in (("home", ph, bh, r_["mu_h"], r_["base_h"], r_["act_h"]),
                                         ("away", pa, ba, r_["mu_a"], r_["base_a"], r_["act_a"]),
                                         ("match", pm_, bm, r_["mu_h"] + r_["mu_a"], r_["base_h"] + r_["base_a"],
                                          (r_["act_h"] + r_["act_a"]) if pd.notna(r_["act_h"]) and pd.notna(r_["act_a"]) else np.nan)):
            line = main_line(bmu)
            o = dict(r_["key"], side=side, expected=mu, base_expected=bmu, r=r_["r"], line=line,
                     p_over=float(p[int(line) + 1:].sum()), base_p_over=float(b[int(line) + 1:].sum()),
                     actual=act)
            if pd.notna(act):
                a = int(min(act, MAXN - 1))
                o["logscore"] = -np.log(max(p[a], 1e-12))
                o["base_logscore"] = -np.log(max(b[a], 1e-12))
            out.append(o)
    return out


def predict(run, tms, lng, refs_next, stat, st, disp, mode, sup):
    label, season, gw, cutoff, fx = run
    ids = set(fx["match_id"])                  # never use the predicted matches themselves
    hist = lng[(lng["match_date"] <= cutoff) & ~lng["match_id"].isin(ids)]
    hm = tms[(tms["match_date"] <= cutoff) & ~tms["match_id"].isin(ids)]
    rt = ratings(hist, stat, cutoff, st["half_life"], st["k"])
    if rt is None:
        return []
    rf = ref_factors(hm, stat, st["ref_k"]) if stat in REF_STATS else {}
    rows = []
    for f in fx.itertuples(index=False):
        h, a = f.home_team_id, f.away_team_id
        base_h, base_a = rt["H"], rt["A"]
        mu_h = rt["H"] * rt["att"].get(h, 1.0) * rt["def"].get(a, 1.0)
        mu_a = rt["A"] * rt["att"].get(a, 1.0) * rt["def"].get(h, 1.0)
        s = sup.get(f.match_id, 0.0)
        mu_h *= np.exp(st["beta"] * s)
        mu_a *= np.exp(-st["beta"] * s)
        if mode == "backtest":
            m = tms[tms["match_id"] == f.match_id]
            ref = m["referee"].iloc[0] if len(m) else None
            act_h = m["home_" + stat].iloc[0] if len(m) else np.nan
            act_a = m["away_" + stat].iloc[0] if len(m) else np.nan
        else:
            rr = refs_next[(refs_next["season"] == season) & (refs_next["home_team_id"] == h) & (refs_next["away_team_id"] == a)]
            ref = rr["referee"].iloc[0] if len(rr) else None
            act_h = act_a = np.nan
        fac = rf.get(ref, 1.0) if ref else 1.0
        rows.append({"key": {"run_label": label, "season": season, "gameweek": gw, "match_id": int(f.match_id),
                             "home_team_id": int(h), "away_team_id": int(a), "stat": stat,
                             "referee": ref, "ref_factor": fac},
                     "mu_h": mu_h * fac, "mu_a": mu_a * fac, "base_h": base_h, "base_a": base_a,
                     "r": disp, "act_h": act_h, "act_a": act_a})
    return rows


def supremacy(runs, lng):
    """Expected goal difference (log of home/away expected goals) from the same machinery."""
    sup = {}
    for run in runs:
        label, season, gw, cutoff, fx = run
        rt = ratings(lng[(lng["match_date"] <= cutoff) & ~lng["match_id"].isin(set(fx["match_id"]))], "goals", cutoff, 365, 8)
        for f in fx.itertuples(index=False):
            gh = rt["H"] * rt["att"].get(f.home_team_id, 1) * rt["def"].get(f.away_team_id, 1)
            ga = rt["A"] * rt["att"].get(f.away_team_id, 1) * rt["def"].get(f.home_team_id, 1)
            sup[(label, f.match_id)] = float(np.log(gh / ga))
    return sup


def dispersion_for(lng, season, stat):
    prior = lng[lng["season"] < season]
    if prior[stat + "_for"].notna().sum() < 400:
        prior = lng[lng["season"] <= season]
    return fit_dispersion(prior, stat)


def evaluate(runs, tms, lng, stat, st, sup_all, disp_by_season):
    rows = []
    for run in runs:
        sup = {mid: v for (lab, mid), v in sup_all.items() if lab == run[0]}
        rows += predict(run, tms, lng, None, stat, st, disp_by_season[run[1]], "backtest", sup)
    sc = pd.DataFrame(score_rows(rows))
    return sc


def tune(runs, tms, lng, sup_all, disp):
    """Choose settings per stat on 2024-25 (log score of team counts), report 2025-26."""
    chosen = {}
    r24 = [r for r in runs if r[1] == "2024-25"]
    r25 = [r for r in runs if r[1] == "2025-26"]
    print("Choosing settings on 2024-25 (lower log score = better):")
    for stat in STATS:
        best, best_score = None, np.inf
        ref_grid = GRID_REF_K if stat in REF_STATS else [1e9]
        for hl in GRID_HL:
            for k in GRID_K:
                for beta in GRID_BETA:
                    for rk in ref_grid:
                        st = {"half_life": hl, "k": k, "beta": beta, "ref_k": rk}
                        sc = evaluate(r24, tms, lng, stat, st, sup_all, disp[stat])
                        sc = sc[(sc["side"] != "match") & sc["logscore"].notna()]
                        if len(sc) == 0:
                            continue
                        v = sc["logscore"].mean()
                        if v < best_score:
                            best, best_score = st, v
        chosen[stat] = best or dict(DEFAULT)
        print(f"  {stat:8s} half-life {chosen[stat]['half_life']} days, K {chosen[stat]['k']}, "
              f"game-state {chosen[stat]['beta']:+.1f}, referee "
              f"{'none' if chosen[stat]['ref_k'] >= 1e8 else 'K ' + str(int(chosen[stat]['ref_k']))}", flush=True)
    return chosen


def main(mode):
    use_db = not os.environ.get("TEST_DIR")
    eng = pmin.engine() if use_db else None
    pm, matches, tms_small, tms, refs = load(eng)
    lng = long_form(tms)
    runs = pmin.runs(matches, tms_small, mode)
    if not runs:
        print("No gameweeks to predict. Are the results in your matches table?")
        return
    sup_all = supremacy(runs, lng)
    seasons = sorted({r[1] for r in runs})
    disp = {s: {se: dispersion_for(lng, se, s) for se in seasons} for s in STATS}

    if mode == "backtest":
        settings = tune(runs, tms, lng, sup_all, disp)
        json.dump(settings, open(SETTINGS_FILE, "w"), indent=1)
    else:
        settings = json.load(open(SETTINGS_FILE)) if os.path.exists(SETTINGS_FILE) else {s: dict(DEFAULT) for s in STATS}
        missing = refs[refs["home_team_id"].isna() | refs["away_team_id"].isna()]
        if len(missing):
            print("Unknown team names in fixture_referees:", ", ".join(sorted(set(missing["home_team"]) | set(missing["away_team"]))))

    parts = []
    for stat in STATS:
        rows = []
        for run in runs:
            sup = {mid: v for (lab, mid), v in sup_all.items() if lab == run[0]}
            rows += predict(run, tms, lng, refs, stat, settings[stat], disp[stat][run[1]], mode, sup)
        parts.append(pd.DataFrame(score_rows(rows)))
    df = pd.concat(parts, ignore_index=True)
    df.insert(3, "mode", mode)
    for c in ["expected", "base_expected", "r", "p_over", "base_p_over", "ref_factor"]:
        df[c] = df[c].astype(float).round(5)

    if mode == "backtest":
        print("Checking on 2025-26 (and 2024-25) - the model should beat the baseline in both:")
        t = df[(df["side"] != "match") & df["logscore"].notna()]
        summ = t.groupby(["stat", "season"])[["logscore", "base_logscore"]].mean().round(4)
        print(summ.to_string())

    save(eng, df)
    print(f"Saved {len(df)} rows ({df['run_label'].nunique()} gameweeks x matches x 7 stats x home/away/match) to team_counts.")
    if mode == "next":
        nr = df[df["referee"].isna() & df["stat"].isin(list(REF_STATS))]["match_id"].nunique()
        if nr:
            print(f"  {nr} fixtures have no referee yet (factor 1). Load them with: python player_data.py referees load")


def save(eng, df):
    os.makedirs("player_data", exist_ok=True)
    df.to_csv(os.path.join("player_data", "team_counts.csv"), index=False, encoding="utf-8-sig")
    if eng is None:
        return
    from sqlalchemy import text
    text_cols = {"run_label", "season", "mode", "stat", "side", "referee"}
    for c in df.columns:
        if c not in text_cols:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    with eng.begin() as con:
        con.execute(text("""CREATE TABLE IF NOT EXISTS team_counts (
            run_label TEXT, season TEXT, gameweek DOUBLE PRECISION, mode TEXT, match_id DOUBLE PRECISION,
            home_team_id DOUBLE PRECISION, away_team_id DOUBLE PRECISION, stat TEXT, referee TEXT,
            ref_factor DOUBLE PRECISION, side TEXT, expected DOUBLE PRECISION, base_expected DOUBLE PRECISION,
            r DOUBLE PRECISION, line DOUBLE PRECISION, p_over DOUBLE PRECISION, base_p_over DOUBLE PRECISION,
            actual DOUBLE PRECISION, logscore DOUBLE PRECISION, base_logscore DOUBLE PRECISION)"""))
        con.execute(text("DELETE FROM team_counts WHERE run_label = ANY(:l)"), {"l": list(df["run_label"].unique())})
        cols = ["run_label", "season", "gameweek", "mode", "match_id", "home_team_id", "away_team_id", "stat",
                "referee", "ref_factor", "side", "expected", "base_expected", "r", "line", "p_over",
                "base_p_over", "actual", "logscore", "base_logscore"]
        for c in cols:
            if c not in df.columns:
                df[c] = np.nan
        df[cols].to_sql("team_counts", con, if_exists="append", index=False, method="multi", chunksize=2000)


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("backtest", "next"):
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
