import math
from typing import Dict, Any, List
from backend.engine.historical_store import get_point_in_time_matches
from backend.utils.text_normalize import normalize_team_name
from backend.markets.hockey_markets import generate_hockey_markets

def factorial(n: int) -> int:
    if n <= 1:
        return 1
    res = 1
    for i in range(2, n + 1):
        res *= i
    return res

def poisson_prob(lmbda: float, k: int) -> float:
    if lmbda <= 0:
        return 1.0 if k == 0 else 0.0
    return (math.pow(lmbda, k) * math.exp(-lmbda)) / factorial(k)

def run_hockey_poisson_engine(sport: str, home_team: str, away_team: str, cutoff_timestamp: str) -> Dict[str, Any]:
    if sport not in {"hockey", "ice_hockey"}:
        raise ValueError(f"Mismatched training sport: {sport}. This model only supports hockey.")

    all_matches = get_point_in_time_matches(cutoff_timestamp, "hockey")
    home_key = normalize_team_name(home_team)
    away_key = normalize_team_name(away_team)

    home_matches = [m for m in all_matches if m.get("homeTeamKey") == home_key or m.get("awayTeamKey") == home_key]
    away_matches = [m for m in all_matches if m.get("homeTeamKey") == away_key or m.get("awayTeamKey") == away_key]

    has_data = len(home_matches) >= 5 and len(away_matches) >= 5
    training_match_count = len(all_matches)

    metadata = {
        "sport": "hockey",
        "model_name": "HockeyPoisson",
        "model_version": "2.0.0",
        "training_window": "all",
        "training_match_count": training_match_count,
        "feature_timestamp": cutoff_timestamp,
        "prediction_timestamp": cutoff_timestamp,
    }

    if not has_data or not all_matches:
        return {
            "markets": [],
            "expectedHomeGoals": 0.0,
            "expectedAwayGoals": 0.0,
            "hasSufficientData": False,
            "metadata": metadata,
        }

    total_h = sum(m["homeScore"] for m in all_matches)
    total_a = sum(m["awayScore"] for m in all_matches)
    n_all = len(all_matches)
    avg_league_h = total_h / n_all
    avg_league_a = total_a / n_all

    if avg_league_h <= 0.0 or avg_league_a <= 0.0:
        return {
            "markets": [],
            "expectedHomeGoals": 0.0,
            "expectedAwayGoals": 0.0,
            "hasSufficientData": False,
            "metadata": metadata,
        }

    h_rec = home_matches[-5:]
    a_rec = away_matches[-5:]

    # Dixon-Coles / penaltyblog exponential time-decay weighting (recency weighting)
    h_weights = [math.exp(-0.06 * (len(h_rec) - 1 - i)) for i in range(len(h_rec))]
    h_sum_w = sum(h_weights) if sum(h_weights) > 0 else 1.0
    a_weights = [math.exp(-0.06 * (len(a_rec) - 1 - i)) for i in range(len(a_rec))]
    a_sum_w = sum(a_weights) if sum(a_weights) > 0 else 1.0

    h_scored = sum(
        (m["homeScore"] if m.get("homeTeamKey") == home_key else m["awayScore"]) * w
        for m, w in zip(h_rec, h_weights)
    ) / h_sum_w
    h_conceded = sum(
        (m["awayScore"] if m.get("homeTeamKey") == home_key else m["homeScore"]) * w
        for m, w in zip(h_rec, h_weights)
    ) / h_sum_w

    a_scored = sum(
        (m["homeScore"] if m.get("homeTeamKey") == away_key else m["awayScore"]) * w
        for m, w in zip(a_rec, a_weights)
    ) / a_sum_w
    a_conceded = sum(
        (m["awayScore"] if m.get("homeTeamKey") == away_key else m["homeScore"]) * w
        for m, w in zip(a_rec, a_weights)
    ) / a_sum_w

    h_att = h_scored / avg_league_h
    h_def = h_conceded / avg_league_a
    a_att = a_scored / avg_league_a
    a_def = a_conceded / avg_league_h

    lmbda = max(0.4, h_att * a_def * avg_league_h)
    mu = max(0.3, a_att * h_def * avg_league_a)

    p_home_win = 0.0
    p_away_win = 0.0
    for x in range(12):
        for y in range(12):
            p = poisson_prob(lmbda, x) * poisson_prob(mu, y)
            if x > y:
                p_home_win += p
            elif y > x:
                p_away_win += p

    tot_win = p_home_win + p_away_win
    p_home_win_adj = p_home_win / tot_win if tot_win > 0 else 0.5
    p_away_win_adj = 1.0 - p_home_win_adj

    raw_markets = generate_hockey_markets(
        home_team=home_team,
        away_team=away_team,
        p_home_win=p_home_win_adj,
        p_away_win=p_away_win_adj,
        expected_home_goals=lmbda,
        expected_away_goals=mu,
        total_goals_benchmark=round(avg_league_h + avg_league_a, 1) or 5.5,
        puck_line_spread=1.5,
    )

    for m in raw_markets:
        m["modelSource"] = "POISSON"

    return {
        "markets": raw_markets,
        "expectedHomeGoals": round(lmbda, 2),
        "expectedAwayGoals": round(mu, 2),
        "hasSufficientData": True,
        "metadata": metadata,
    }
