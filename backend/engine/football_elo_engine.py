import math
from typing import Dict, Any, List
from backend.engine.historical_store import get_point_in_time_matches
from backend.utils.text_normalize import normalize_team_name

CONFIG = {"k": 32, "homeAdv": 65, "init": 1500}

def compute_historical_elo_ratings(sport: str, cutoff_timestamp: str) -> Dict[str, Dict[str, Any]]:
    if sport != "football":
        raise ValueError(f"Mismatched training sport: {sport}. This model only supports football.")
    
    matches = get_point_in_time_matches(cutoff_timestamp, "football")
    ratings: Dict[str, Dict[str, Any]] = {}

    def get_rating(key: str) -> float:
        return ratings.get(key, {}).get("rating", CONFIG["init"])

    def get_count(key: str) -> int:
        return ratings.get(key, {}).get("count", 0)

    for m in matches:
        if m.get("sport") != "football":
            continue
        h_key = m.get("homeTeamKey") or normalize_team_name(m["homeTeam"])
        a_key = m.get("awayTeamKey") or normalize_team_name(m["awayTeam"])

        r_home = get_rating(h_key)
        r_away = get_rating(a_key)

        exponent = (r_away - (r_home + CONFIG["homeAdv"])) / 400.0
        e_home = 1.0 / (1.0 + math.pow(10, exponent))
        e_away = 1.0 - e_home

        s_home = 0.5
        if m["homeScore"] > m["awayScore"]:
            s_home = 1.0
        elif m["homeScore"] < m["awayScore"]:
            s_home = 0.0
        s_away = 1.0 - s_home

        new_h = r_home + CONFIG["k"] * (s_home - e_home)
        new_a = r_away + CONFIG["k"] * (s_away - e_away)

        ratings[h_key] = {"rating": new_h, "count": get_count(h_key) + 1}
        ratings[a_key] = {"rating": new_a, "count": get_count(a_key) + 1}

    return ratings

def run_football_elo_engine(sport: str, home_team: str, away_team: str, cutoff_timestamp: str) -> Dict[str, Any]:
    if sport != "football":
        raise ValueError(f"Mismatched training sport: {sport}. This model only supports football.")

    matches = get_point_in_time_matches(cutoff_timestamp, "football")
    ratings = compute_historical_elo_ratings("football", cutoff_timestamp)

    home_key = normalize_team_name(home_team)
    away_key = normalize_team_name(away_team)

    h_data = ratings.get(home_key, {"rating": CONFIG["init"], "count": 0})
    a_data = ratings.get(away_key, {"rating": CONFIG["init"], "count": 0})

    has_data = h_data["count"] >= 5 and a_data["count"] >= 5
    training_match_count = sum(ratings[k]["count"] for k in ratings) // 2

    metadata = {
        "sport": "football",
        "model_name": "FootballElo",
        "model_version": "2.0.0",
        "training_window": "all",
        "training_match_count": training_match_count,
        "feature_timestamp": cutoff_timestamp,
        "prediction_timestamp": cutoff_timestamp,
    }

    if not has_data:
        return {
            "markets": [],
            "homeRating": h_data["rating"],
            "awayRating": a_data["rating"],
            "hasSufficientData": False,
            "metadata": metadata,
        }

    r_diff = (h_data["rating"] + CONFIG["homeAdv"]) - a_data["rating"]
    p_home_2way = 1.0 / (1.0 + math.pow(10, -r_diff / 400.0))
    p_away_2way = 1.0 - p_home_2way

    markets: List[Dict[str, Any]] = []

    # Derive baseline draw rate empirically from completed point-in-time matches
    draw_matches = sum(1 for m in matches if m.get("homeScore") == m.get("awayScore"))
    empirical_draw_rate = (draw_matches / len(matches)) if matches else 0.25
    draw_prob = empirical_draw_rate * math.exp(-math.pow(r_diff, 2) / (2 * math.pow(240, 2)))
    non_draw = 1.0 - draw_prob
    p_h = non_draw * p_home_2way
    p_a = non_draw * p_away_2way

    # Full 1X2 outcome distribution
    markets.append({
        "marketName": "Win / Draw / Loss (1X2)",
        "selection": f"{home_team} Win",
        "rawProbability": p_h,
        "modelSource": "ELO",
        "marketCategory": "1X2",
    })
    markets.append({
        "marketName": "Win / Draw / Loss (1X2)",
        "selection": "Draw",
        "rawProbability": draw_prob,
        "modelSource": "ELO",
        "marketCategory": "1X2",
    })
    markets.append({
        "marketName": "Win / Draw / Loss (1X2)",
        "selection": f"{away_team} Win",
        "rawProbability": p_a,
        "modelSource": "ELO",
        "marketCategory": "1X2",
    })

    # Full Double Chance distribution
    p1x = min(0.99, p_h + draw_prob)
    px2 = min(0.99, p_a + draw_prob)
    p12 = min(0.99, p_h + p_a)
    markets.append({
        "marketName": "Double Chance",
        "selection": f"{home_team} or Draw (1X)",
        "rawProbability": p1x,
        "modelSource": "ELO",
        "marketCategory": "DoubleChance",
    })
    markets.append({
        "marketName": "Double Chance",
        "selection": f"Draw or {away_team} (X2)",
        "rawProbability": px2,
        "modelSource": "ELO",
        "marketCategory": "DoubleChance",
    })
    markets.append({
        "marketName": "Double Chance",
        "selection": f"{home_team} or {away_team} (12)",
        "rawProbability": p12,
        "modelSource": "ELO",
        "marketCategory": "DoubleChance",
    })

    return {
        "markets": markets,
        "homeRating": round(h_data["rating"]),
        "awayRating": round(a_data["rating"]),
        "hasSufficientData": True,
        "metadata": metadata,
    }
