import math
from typing import Dict, Any, List
from backend.engine.historical_store import get_point_in_time_matches
from backend.utils.text_normalize import normalize_team_name

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

def dixon_coles_tau(x: int, y: int, lmbda: float, mu: float, rho: float = -0.11) -> float:
    if x == 0 and y == 0:
        return 1.0 - lmbda * mu * rho
    if x == 0 and y == 1:
        return 1.0 + lmbda * rho
    if x == 1 and y == 0:
        return 1.0 + mu * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0

def run_football_poisson_engine(sport: str, home_team: str, away_team: str, cutoff_timestamp: str) -> Dict[str, Any]:
    if sport != "football":
        raise ValueError(f"Mismatched training sport: {sport}. This model only supports football.")

    all_matches = get_point_in_time_matches(cutoff_timestamp, "football")
    home_key = normalize_team_name(home_team)
    away_key = normalize_team_name(away_team)

    home_matches = [m for m in all_matches if m.get("homeTeamKey") == home_key or m.get("awayTeamKey") == home_key]
    away_matches = [m for m in all_matches if m.get("homeTeamKey") == away_key or m.get("awayTeamKey") == away_key]

    has_data = len(home_matches) >= 5 and len(away_matches) >= 5
    training_match_count = len(all_matches)

    metadata = {
        "sport": "football",
        "model_name": "FootballPoisson",
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

    h_scored = sum(m["homeScore"] if m.get("homeTeamKey") == home_key else m["awayScore"] for m in h_rec) / len(h_rec)
    h_conceded = sum(m["awayScore"] if m.get("homeTeamKey") == home_key else m["homeScore"] for m in h_rec) / len(h_rec)

    a_scored = sum(m["homeScore"] if m.get("homeTeamKey") == away_key else m["awayScore"] for m in a_rec) / len(a_rec)
    a_conceded = sum(m["awayScore"] if m.get("homeTeamKey") == away_key else m["homeScore"] for m in a_rec) / len(a_rec)

    h_att = h_scored / avg_league_h
    h_def = h_conceded / avg_league_a
    a_att = a_scored / avg_league_a
    a_def = a_conceded / avg_league_h

    lmbda = max(0.4, h_att * a_def * avg_league_h)
    mu = max(0.3, a_att * h_def * avg_league_a)

    markets: List[Dict[str, Any]] = []

    p_home_win = 0.0
    p_draw = 0.0
    p_away_win = 0.0
    p_over25 = 0.0
    p_over15 = 0.0
    p_btts = 0.0

    for x in range(8):
        for y in range(8):
            p = poisson_prob(lmbda, x) * poisson_prob(mu, y) * dixon_coles_tau(x, y, lmbda, mu)
            if x > y:
                p_home_win += p
            elif x == y:
                p_draw += p
            else:
                p_away_win += p

            if x + y > 2.5:
                p_over25 += p
            if x + y > 1.5:
                p_over15 += p
            if x > 0 and y > 0:
                p_btts += p

    sum_1x2 = p_home_win + p_draw + p_away_win
    if sum_1x2 > 0:
        p_home_win /= sum_1x2
        p_draw /= sum_1x2
        p_away_win /= sum_1x2

    # Full 1X2 distribution
    markets.append({
        "marketName": "Win / Draw / Loss (1X2)",
        "selection": f"{home_team} Win",
        "rawProbability": p_home_win,
        "modelSource": "POISSON",
        "marketCategory": "1X2",
    })
    markets.append({
        "marketName": "Win / Draw / Loss (1X2)",
        "selection": "Draw",
        "rawProbability": p_draw,
        "modelSource": "POISSON",
        "marketCategory": "1X2",
    })
    markets.append({
        "marketName": "Win / Draw / Loss (1X2)",
        "selection": f"{away_team} Win",
        "rawProbability": p_away_win,
        "modelSource": "POISSON",
        "marketCategory": "1X2",
    })

    # Full Over / Under 2.5 Goals distribution
    markets.append({
        "marketName": "Over / Under 2.5 Goals",
        "selection": "Over 2.5 Goals",
        "rawProbability": p_over25,
        "modelSource": "POISSON",
        "marketCategory": "GoalsTotal",
    })
    markets.append({
        "marketName": "Over / Under 2.5 Goals",
        "selection": "Under 2.5 Goals",
        "rawProbability": 1.0 - p_over25,
        "modelSource": "POISSON",
        "marketCategory": "GoalsTotal",
    })

    # Full Both Teams to Score (BTTS) distribution
    markets.append({
        "marketName": "Both Teams To Score (BTTS)",
        "selection": "Yes (BTTS)",
        "rawProbability": p_btts,
        "modelSource": "POISSON",
        "marketCategory": "BTTS",
    })
    markets.append({
        "marketName": "Both Teams To Score (BTTS)",
        "selection": "No (BTTS)",
        "rawProbability": 1.0 - p_btts,
        "modelSource": "POISSON",
        "marketCategory": "BTTS",
    })

    # Full Double Chance distribution
    p1x = min(0.99, p_home_win + p_draw)
    px2 = min(0.99, p_away_win + p_draw)
    p12 = min(0.99, p_home_win + p_away_win)
    markets.append({
        "marketName": "Double Chance",
        "selection": f"{home_team} or Draw (1X)",
        "rawProbability": p1x,
        "modelSource": "POISSON",
        "marketCategory": "DoubleChance",
    })
    markets.append({
        "marketName": "Double Chance",
        "selection": f"Draw or {away_team} (X2)",
        "rawProbability": px2,
        "modelSource": "POISSON",
        "marketCategory": "DoubleChance",
    })
    markets.append({
        "marketName": "Double Chance",
        "selection": f"{home_team} or {away_team} (12)",
        "rawProbability": p12,
        "modelSource": "POISSON",
        "marketCategory": "DoubleChance",
    })

    return {
        "markets": markets,
        "expectedHomeGoals": round(lmbda, 2),
        "expectedAwayGoals": round(mu, 2),
        "hasSufficientData": True,
        "metadata": metadata,
    }
