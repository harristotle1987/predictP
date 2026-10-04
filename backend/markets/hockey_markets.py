from typing import Dict, Any, List

HOCKEY_MARKET_CATEGORIES = {
    "Moneyline": "Moneyline",
    "PuckLine": "PuckLine",
    "TotalGoals": "TotalGoals",
    "OverUnderGoals": "OverUnderGoals",
}

import math
from typing import Dict, Any, List

HOCKEY_MARKET_CATEGORIES = {
    "Moneyline": "Moneyline",
    "PuckLine": "PuckLine",
    "TotalGoals": "TotalGoals",
    "OverUnderGoals": "OverUnderGoals",
}

def poisson_pmf(lmbda: float, k: int) -> float:
    if lmbda <= 0 or k < 0:
        return 0.0
    return math.exp(-lmbda) * (lmbda ** k) / math.factorial(k)

def build_hockey_markets(
    home_team: str,
    away_team: str,
    p_home_win: float,
    p_away_win: float,
    expected_home_goals: float,
    expected_away_goals: float,
    total_benchmark: float = 5.5,
    **kwargs,
) -> List[Dict[str, Any]]:
    """
    Constructs mathematically derived, sport-specific hockey markets
    using bivariate Poisson goal distribution (no heuristic constant multipliers):
    - Moneyline (complete 2-way distribution)
    - Puck Line (+/- 1.5 goal spread derived from joint distribution)
    - Over/Under Total Goals (derived from joint distribution)
    """
    total_benchmark = kwargs.get("total_goals_benchmark", total_benchmark)
    spread = kwargs.get("puck_line_spread", 1.5)
    markets: List[Dict[str, Any]] = []

    lmbda = max(0.5, expected_home_goals)
    mu = max(0.5, expected_away_goals)

    # Calculate joint Poisson probability matrix P(x, y)
    p_h_win_reg = 0.0
    p_a_win_reg = 0.0
    p_tie_reg = 0.0
    p_home_cover = 0.0
    p_over = 0.0

    for x in range(15):
        for y in range(15):
            p = poisson_pmf(lmbda, x) * poisson_pmf(mu, y)
            if x > y:
                p_h_win_reg += p
            elif y > x:
                p_a_win_reg += p
            else:
                p_tie_reg += p

            # Puck line: Home covers if x - y > spread (i.e. x - y >= 2 for spread=1.5)
            if (x - y) > spread:
                p_home_cover += p

            if (x + y) > total_benchmark:
                p_over += p

    # In hockey, ties go to overtime/shootout. Split regulation tie proportionally by team rate:
    reg_sum = p_h_win_reg + p_a_win_reg
    ratio = (p_h_win_reg / reg_sum) if reg_sum > 0 else 0.5
    p_h_ml = p_h_win_reg + p_tie_reg * ratio
    p_a_ml = 1.0 - p_h_ml

    # Normalize Moneyline
    p_h_ml = max(0.05, min(0.95, p_h_ml))
    p_a_ml = 1.0 - p_h_ml

    # 1. Full Moneyline distribution
    markets.append({
        "marketName": "Moneyline",
        "selection": f"{home_team} Win",
        "rawProbability": round(p_h_ml, 4),
        "modelSource": "HockeyEnsemble",
        "marketCategory": "Moneyline",
    })
    markets.append({
        "marketName": "Moneyline",
        "selection": f"{away_team} Win",
        "rawProbability": round(p_a_ml, 4),
        "modelSource": "HockeyEnsemble",
        "marketCategory": "Moneyline",
    })

    # 2. Full Puck Line distribution (+/- 1.5 goals)
    p_home_cover = max(0.05, min(0.95, p_home_cover))
    p_away_cover = 1.0 - p_home_cover
    markets.append({
        "marketName": f"Puck Line (-{spread})",
        "selection": f"{home_team} -{spread}",
        "rawProbability": round(p_home_cover, 4),
        "modelSource": "HockeyEnsemble",
        "marketCategory": "PuckLine",
    })
    markets.append({
        "marketName": f"Puck Line (+{spread})",
        "selection": f"{away_team} +{spread}",
        "rawProbability": round(p_away_cover, 4),
        "modelSource": "HockeyEnsemble",
        "marketCategory": "PuckLine",
    })

    # 3. Full Over / Under Goals distribution
    p_over = max(0.05, min(0.95, p_over))
    p_under = 1.0 - p_over
    markets.append({
        "marketName": f"Over / Under Goals {total_benchmark}",
        "selection": f"Over {total_benchmark} Goals",
        "rawProbability": round(p_over, 4),
        "modelSource": "HockeyPoisson",
        "marketCategory": "OverUnderGoals",
    })
    markets.append({
        "marketName": f"Over / Under Goals {total_benchmark}",
        "selection": f"Under {total_benchmark} Goals",
        "rawProbability": round(p_under, 4),
        "modelSource": "HockeyPoisson",
        "marketCategory": "OverUnderGoals",
    })

    return markets

generate_hockey_markets = build_hockey_markets
