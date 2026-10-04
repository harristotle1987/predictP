import math
from typing import Dict, Any, List
from backend.engine.historical_store import get_point_in_time_matches
from backend.utils.text_normalize import normalize_team_name

GLICKO2_SCALE = 173.7178
TAU = 0.5
EPSILON = 0.000001

def g_func(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + (3.0 * phi * phi) / (math.pi * math.pi))

def e_func(mu: float, mu_j: float, phi_j: float) -> float:
    return 1.0 / (1.0 + math.exp(-g_func(phi_j) * (mu - mu_j)))

def compute_updated_volatility(sigma: float, phi: float, v: float, delta: float) -> float:
    a = math.log(sigma * sigma)
    phi_sq = phi * phi

    def f(x: float) -> float:
        exp_x = math.exp(x)
        num1 = exp_x * (delta * delta - phi_sq - v - exp_x)
        den1 = 2.0 * math.pow(phi_sq + v + exp_x, 2)
        num2 = x - a
        den2 = TAU * TAU
        return num1 / den1 - num2 / den2

    A = a
    if delta * delta > phi_sq + v:
        B = math.log(delta * delta - phi_sq - v)
    else:
        k = 1
        while f(a - k * TAU) < 0:
            k += 1
        B = a - k * TAU

    f_A = f(A)
    f_B = f(B)

    while abs(B - A) > EPSILON:
        C = A + ((A - B) * f_A) / (f_B - f_A)
        f_C = f(C)
        if f_C * f_B <= 0:
            A = B
            f_A = f_B
        else:
            f_A = f_A / 2.0
        B = C
        f_B = f_C

    return math.exp(A / 2.0)

def compute_historical_glicko2_ratings(sport: str, cutoff_timestamp: str) -> Dict[str, Dict[str, Any]]:
    matches = get_point_in_time_matches(cutoff_timestamp, sport)
    ratings: Dict[str, Dict[str, Any]] = {}

    def get_state(key: str) -> Dict[str, Any]:
        return ratings.get(key, {"r": 1500.0, "rd": 350.0, "sigma": 0.06, "count": 0})

    for m in matches:
        h_key = m.get("homeTeamKey") or normalize_team_name(m["homeTeam"])
        a_key = m.get("awayTeamKey") or normalize_team_name(m["awayTeam"])

        s_home = get_state(h_key)
        s_away = get_state(a_key)

        mu_home = (s_home["r"] - 1500.0) / GLICKO2_SCALE
        phi_home = s_home["rd"] / GLICKO2_SCALE
        mu_away = (s_away["r"] - 1500.0) / GLICKO2_SCALE
        phi_away = s_away["rd"] / GLICKO2_SCALE

        out_home = 0.5
        if m["homeScore"] > m["awayScore"]:
            out_home = 1.0
        elif m["homeScore"] < m["awayScore"]:
            out_home = 0.0
        out_away = 1.0 - out_home

        # Update home
        g_away = g_func(phi_away)
        e_home = e_func(mu_home, mu_away, phi_away)
        v_home = 1.0 / (g_away * g_away * e_home * (1.0 - e_home))
        delta_home = v_home * g_away * (out_home - e_home)
        sig_home_prime = compute_updated_volatility(s_home["sigma"], phi_home, v_home, delta_home)
        phi_home_star = math.sqrt(phi_home * phi_home + sig_home_prime * sig_home_prime)
        phi_home_prime = 1.0 / math.sqrt(1.0 / (phi_home_star * phi_home_star) + 1.0 / v_home)
        mu_home_prime = mu_home + phi_home_prime * phi_home_prime * g_away * (out_home - e_home)

        # Update away
        g_home = g_func(phi_home)
        e_away = e_func(mu_away, mu_home, phi_home)
        v_away = 1.0 / (g_home * g_home * e_away * (1.0 - e_away))
        delta_away = v_away * g_home * (out_away - e_away)
        sig_away_prime = compute_updated_volatility(s_away["sigma"], phi_away, v_away, delta_away)
        phi_away_star = math.sqrt(phi_away * phi_away + sig_away_prime * sig_away_prime)
        phi_away_prime = 1.0 / math.sqrt(1.0 / (phi_away_star * phi_away_star) + 1.0 / v_away)
        mu_away_prime = mu_away + phi_away_prime * phi_away_prime * g_home * (out_away - e_away)

        ratings[h_key] = {
            "r": mu_home_prime * GLICKO2_SCALE + 1500.0,
            "rd": phi_home_prime * GLICKO2_SCALE,
            "sigma": sig_home_prime,
            "count": s_home["count"] + 1,
        }
        ratings[a_key] = {
            "r": mu_away_prime * GLICKO2_SCALE + 1500.0,
            "rd": phi_away_prime * GLICKO2_SCALE,
            "sigma": sig_away_prime,
            "count": s_away["count"] + 1,
        }

    return ratings

def run_glicko2_engine(sport: str, home_team: str, away_team: str, cutoff_timestamp: str) -> Dict[str, Any]:
    if sport not in {"football", "basketball", "baseball", "hockey", "ice_hockey"}:
        raise ValueError(f"Mismatched training sport: {sport}. This model does not support {sport}.")

    ratings = compute_historical_glicko2_ratings(sport, cutoff_timestamp)
    home_key = normalize_team_name(home_team)
    away_key = normalize_team_name(away_team)

    h_state = ratings.get(home_key, {"r": 1500.0, "rd": 350.0, "sigma": 0.06, "count": 0})
    a_state = ratings.get(away_key, {"r": 1500.0, "rd": 350.0, "sigma": 0.06, "count": 0})

    has_data = h_state["count"] >= 5 and a_state["count"] >= 5
    training_match_count = sum(ratings[k]["count"] for k in ratings) // 2

    metadata = {
        "sport": sport,
        "model_name": "Glicko2",
        "model_version": "2.0.0",
        "training_window": "all",
        "training_match_count": training_match_count,
        "feature_timestamp": cutoff_timestamp,
        "prediction_timestamp": cutoff_timestamp,
    }

    if not has_data:
        return {
            "markets": [],
            "homeRating": h_state["r"],
            "awayRating": a_state["r"],
            "hasSufficientData": False,
            "metadata": metadata,
        }

    mu_home = (h_state["r"] - 1500.0) / GLICKO2_SCALE
    phi_home = h_state["rd"] / GLICKO2_SCALE
    mu_away = (a_state["r"] - 1500.0) / GLICKO2_SCALE
    phi_away = a_state["rd"] / GLICKO2_SCALE

    p_home_win_2way = e_func(mu_home, mu_away, phi_away)
    p_away_win_2way = 1.0 - p_home_win_2way

    markets: List[Dict[str, Any]] = []

    if sport == "football":
        comb_rd = math.sqrt(h_state["rd"] ** 2 + a_state["rd"] ** 2)
        draw_base = 0.25 + min(0.06, (comb_rd / 700.0) * 0.06)
        r_diff = h_state["r"] - a_state["r"]
        draw_prob = draw_base * math.exp(-math.pow(r_diff, 2) / (2 * math.pow(260, 2)))

        non_draw = 1.0 - draw_prob
        p_h = non_draw * p_home_win_2way
        p_a = non_draw * p_away_win_2way

        if p_h >= p_a:
            markets.append({
                "marketName": "Win / Draw / Loss (1X2)",
                "selection": f"{home_team} Win",
                "rawProbability": p_h,
                "modelSource": "GLICKO2",
                "marketCategory": "1X2",
            })
        else:
            markets.append({
                "marketName": "Win / Draw / Loss (1X2)",
                "selection": f"{away_team} Win",
                "rawProbability": p_a,
                "modelSource": "GLICKO2",
                "marketCategory": "1X2",
            })

        p1x = p_h + draw_prob
        px2 = p_a + draw_prob
        markets.append({
            "marketName": "Double Chance",
            "selection": f"{home_team} or Draw (1X)" if p1x >= px2 else f"Draw or {away_team} (X2)",
            "rawProbability": max(p1x, px2),
            "modelSource": "GLICKO2",
            "marketCategory": "DoubleChance",
        })
    else:
        fav_h = p_home_win_2way >= 0.5
        top_prob = max(p_home_win_2way, p_away_win_2way)
        fav_team = home_team if fav_h else away_team
        markets.append({
            "marketName": "Moneyline",
            "selection": f"{fav_team} Win",
            "rawProbability": top_prob,
            "modelSource": "GLICKO2",
            "marketCategory": "Moneyline",
        })

    return {
        "markets": markets,
        "homeRating": round(h_state["r"]),
        "awayRating": round(a_state["r"]),
        "hasSufficientData": True,
        "metadata": metadata,
    }
