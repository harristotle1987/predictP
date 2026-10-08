import math
from typing import Dict, Any, Optional, List

def devig_odds(
    home_odds: float,
    away_odds: float,
    draw_odds: Optional[float] = None
) -> Dict[str, float]:
    """
    De-vigs 2-way or 3-way bookmaker decimal odds using proportional normalization
    (as popularized by penaltyblog and Pinnacle benchmark models).
    Returns fair un-vigged implied probabilities summing to 1.0.
    """
    raw_odds = [home_odds, away_odds]
    if draw_odds is not None and draw_odds > 1.0:
        raw_odds.append(draw_odds)

    implied = []
    for o in raw_odds:
        if o and o > 1.0:
            implied.append(1.0 / o)
        else:
            implied.append(0.0)

    total_overround = sum(implied)
    if total_overround <= 0:
        return {
            "home_fair_prob": 0.5,
            "away_fair_prob": 0.5,
            "draw_fair_prob": 0.0,
            "overround_pct": 0.0,
        }

    return {
        "home_fair_prob": implied[0] / total_overround,
        "away_fair_prob": implied[1] / total_overround,
        "draw_fair_prob": (implied[2] / total_overround) if len(implied) > 2 else 0.0,
        "overround_pct": round((total_overround - 1.0) * 100, 2),
    }


def calculate_quant_metrics(
    calibrated_prob: float,
    reference_odds: Optional[float] = None,
    closing_odds: Optional[float] = None,
    kelly_fraction: float = 0.25
) -> Dict[str, Any]:
    """
    Computes mathematical Expected Value (+EV), Market Edge, Fractional Kelly stake sizing,
    and Closing Line Value (CLV) benchmark metrics.
    Derived from NBA-Machine-Learning-Sports-Betting and penaltyblog quantitative standards.
    """
    prob = max(0.001, min(0.999, float(calibrated_prob)))

    # If no external odds were provided, derive fair consensus market odds with standard 4.5% vig
    if not reference_odds or reference_odds <= 1.01:
        fair_odds = 1.0 / prob
        reference_odds = round(fair_odds * 0.955, 2)
        if reference_odds <= 1.01:
            reference_odds = 1.05

    # 1. Expected Value (EV) = (Probability * Decimal Odds) - 1.0
    ev = (prob * reference_odds) - 1.0
    ev_pct = round(ev * 100, 2)

    # 2. Fractional Kelly Criterion: f* = (b*p - q) / b
    # where b = decimal_odds - 1, q = 1 - p
    b = reference_odds - 1.0
    q = 1.0 - prob
    full_kelly = (b * prob - q) / b if b > 0 else 0.0
    # Conservative fraction (default quarter-Kelly = 0.25), capped at 5% of bankroll
    recommended_stake_pct = round(max(0.0, min(full_kelly * kelly_fraction, 0.05)) * 100, 2)

    # 3. Model Edge against bookmaker implied probability
    implied_prob = (1.0 / reference_odds) if reference_odds > 1.0 else 0.5
    edge_pct = round((prob - implied_prob) * 100, 2)

    # 4. Closing Line Value (CLV) Alpha: (Odds_open / Odds_closing) - 1.0
    if closing_odds and closing_odds > 1.0:
        clv_alpha_pct = round(((reference_odds / closing_odds) - 1.0) * 100, 2)
    else:
        # Estimated CLV alpha derived from model edge against consensus
        clv_alpha_pct = round(max(0.0, edge_pct * 0.42), 2)

    return {
        "evPercentage": ev_pct,
        "isPositiveEV": ev_pct >= 1.5,  # +1.5% edge threshold for positive expectation
        "recommendedKellyPct": recommended_stake_pct,
        "marketOdds": reference_odds,
        "impliedMarketProb": round(implied_prob * 100, 1),
        "modelEdgePct": edge_pct,
        "clvAlpha": clv_alpha_pct,
    }


def compute_exponential_time_weight(days_ago: float, xi: float = 0.005) -> float:
    """
    Computes time-decay weight for historical match results (Dixon-Coles & penaltyblog pattern).
    w = exp(-xi * days_ago)
    """
    return math.exp(-xi * max(0.0, float(days_ago)))
