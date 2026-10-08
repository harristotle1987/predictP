import math
from typing import Dict, Any, List
from backend.engine.historical_store import get_point_in_time_matches
from backend.utils.text_normalize import normalize_team_name

def run_basketball_poisson_engine(sport: str, home_team: str, away_team: str, cutoff_timestamp: str) -> Dict[str, Any]:
    if sport not in {"basketball"}:
        raise ValueError(f"Mismatched training sport: {sport}. This model only supports basketball.")

    all_matches = get_point_in_time_matches(cutoff_timestamp, "basketball")
    home_key = normalize_team_name(home_team)
    away_key = normalize_team_name(away_team)

    home_matches = [m for m in all_matches if m.get("homeTeamKey") == home_key or m.get("awayTeamKey") == home_key]
    away_matches = [m for m in all_matches if m.get("homeTeamKey") == away_key or m.get("awayTeamKey") == away_key]

    has_data = len(home_matches) >= 5 and len(away_matches) >= 5
    training_match_count = len(all_matches)

    metadata = {
        "sport": "basketball",
        "model_name": "BasketballPoisson",
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

    lmbda = h_att * a_def * avg_league_h
    mu = a_att * h_def * avg_league_a

    markets: List[Dict[str, Any]] = []

    # Moneyline distribution derived from Poisson attack/defense parameters using normal approximation of Skellam difference
    variance = max(1.0, lmbda + mu)
    std_dev = math.sqrt(variance)
    diff = lmbda - mu
    # Normal CDF via math.erf
    p_home = 0.5 * (1.0 + math.erf(diff / (std_dev * math.sqrt(2.0))))
    p_home = max(0.05, min(0.95, p_home))
    p_away = 1.0 - p_home

    markets.append({
        "marketName": "Moneyline",
        "selection": f"{home_team} Win",
        "rawProbability": round(p_home, 4),
        "modelSource": "POISSON",
        "marketCategory": "Moneyline",
    })
    markets.append({
        "marketName": "Moneyline",
        "selection": f"{away_team} Win",
        "rawProbability": round(p_away, 4),
        "modelSource": "POISSON",
        "marketCategory": "Moneyline",
    })

    total_exp = lmbda + mu
    benchmark = (avg_league_h + avg_league_a)
    tot_diff = total_exp - benchmark
    tot_std = math.sqrt(max(1.0, total_exp))
    p_over = 0.5 * (1.0 + math.erf(tot_diff / (tot_std * math.sqrt(2.0))))
    p_over = max(0.05, min(0.95, p_over))

    markets.append({
        "marketName": f"Over / Under {round(benchmark, 1)} Points",
        "selection": f"Over {round(benchmark, 1)} Points",
        "rawProbability": round(p_over, 4),
        "modelSource": "POISSON",
        "marketCategory": "PointsTotal",
    })
    markets.append({
        "marketName": f"Over / Under {round(benchmark, 1)} Points",
        "selection": f"Under {round(benchmark, 1)} Points",
        "rawProbability": round(1.0 - p_over, 4),
        "modelSource": "POISSON",
        "marketCategory": "PointsTotal",
    })

    return {
        "markets": markets,
        "expectedHomeGoals": round(lmbda, 2),
        "expectedAwayGoals": round(mu, 2),
        "hasSufficientData": True,
        "metadata": metadata,
    }
