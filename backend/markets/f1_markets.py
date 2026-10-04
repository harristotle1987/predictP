from typing import List, Dict, Any

def build_f1_markets(drivers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Constructs sport-specific F1 markets strictly from validated driver probabilistic models:
    - Race Winner: P(finish_position == 1)
    - Podium: P(finish_position <= 3)
    - Top 10: P(finish_position <= 10)
    - Fastest Lap: P(fastest_lap_rank == 1) (only published if backed by validated model)
    - Head-to-Head: Only published if backed by a validated pairwise model (abstains otherwise).

    Strict rule: Never uses heuristic probability formulas or arbitrary scalar multipliers.
    If a market is unsupported or lacks a validated model, it abstains (omitted).
    """
    markets: List[Dict[str, Any]] = []
    if not drivers:
        return markets

    # Sort drivers by win probability descending
    sorted_drivers = sorted(
        drivers,
        key=lambda x: float(x.get("winProbability") or 0.0),
        reverse=True,
    )

    # 1. Race Winner market (top candidate from validated win model)
    top_driver = sorted_drivers[0]
    p_win = top_driver.get("winProbability")
    if p_win is not None and 0.0 < float(p_win) <= 1.0:
        markets.append({
            "marketName": "Race Winner",
            "selection": f"{top_driver['driverName']} to Win",
            "rawProbability": round(float(p_win), 4),
            "modelSource": "F1ProbabilityEngine",
            "marketCategory": "RaceWinner",
        })

    # 2. Podium market (top 3 drivers get their validated podium probabilities)
    for d in sorted_drivers[:3]:
        p_pod = d.get("podiumProbability")
        if p_pod is not None and 0.0 < float(p_pod) <= 1.0 and d.get("hasValidatedPodium", True):
            markets.append({
                "marketName": "Podium",
                "selection": f"{d['driverName']} to Finish on Podium",
                "rawProbability": round(float(p_pod), 4),
                "modelSource": "F1ProbabilityEngine",
                "marketCategory": "Podium",
            })

    # 3. Top 10 market (drivers ranked 4-8)
    for d in sorted_drivers[3:8]:
        p_t10 = d.get("top10Probability")
        if p_t10 is not None and 0.0 < float(p_t10) <= 1.0 and d.get("hasValidatedTop10", True):
            markets.append({
                "marketName": "Top 10",
                "selection": f"{d['driverName']} to Finish in Top 10",
                "rawProbability": round(float(p_t10), 4),
                "modelSource": "F1ProbabilityEngine",
                "marketCategory": "Top10",
            })

    # 4. Fastest Lap: only publish if backed by a validated model, never a heuristic formula
    drivers_with_fl = [d for d in sorted_drivers if d.get("hasValidatedFastestLap") and d.get("fastestLapProbability") is not None]
    if drivers_with_fl:
        fl_top = max(drivers_with_fl, key=lambda x: float(x.get("fastestLapProbability") or 0.0))
        p_fl = fl_top.get("fastestLapProbability")
        if p_fl is not None and 0.0 < float(p_fl) <= 1.0:
            markets.append({
                "marketName": "Fastest Lap",
                "selection": f"{fl_top['driverName']} Fastest Lap",
                "rawProbability": round(float(p_fl), 4),
                "modelSource": "F1ProbabilityEngine",
                "marketCategory": "FastestLap",
            })

    # 5. Head-to-Head (H2H): Only publish if explicit pairwise validated model exists
    # If pairwise model does not exist, ABSTAIN (do not publish fake heuristic H2H)
    for d in sorted_drivers:
        if d.get("hasValidatedH2H") and d.get("h2hProbability") is not None:
            opponent_name = d.get("h2hOpponentName", "Opponent")
            p_h2h = float(d["h2hProbability"])
            if 0.0 < p_h2h <= 1.0:
                markets.append({
                    "marketName": f"H2H - {d['driverName']} vs {opponent_name}",
                    "selection": f"{d['driverName']} defeats {opponent_name}",
                    "rawProbability": round(p_h2h, 4),
                    "modelSource": "F1ProbabilityEngine",
                    "marketCategory": "H2H",
                })

    return markets
