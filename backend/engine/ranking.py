from typing import List, Dict, Any, Optional

SPORTS = ["football", "basketball", "baseball", "hockey", "formula_1"]

def rank_and_select_best_of_day(
    fixtures: List[Dict[str, Any]],
    max_limit: int = 20,
    max_results: Optional[int] = None,
    **kwargs,
) -> List[Dict[str, Any]]:
    limit = max_results if max_results is not None else max_limit

    for fix in fixtures:
        markets = fix.get("markets", [])
        if markets:
            markets.sort(key=lambda m: (m.get("calibratedPercentage", 0), m.get("confidenceScore", 0)), reverse=True)
            top_m = markets[0]
            fix["highestPercentagePrediction"] = {
                "marketName": top_m["marketName"],
                "selection": top_m["selection"],
                "percentage": top_m["calibratedPercentage"],
            }

    validated = [f for f in fixtures if f.get("validationStatus") == "validated" and len(f.get("markets", [])) > 0]
    if not validated:
        validated = [
            f for f in fixtures
            if len(f.get("markets", [])) > 0
            or f.get("calibratedPercentage") is not None
            or f.get("highestPercentagePrediction") is not None
            or f.get("validatedMarkets")
        ]

    by_sport = {sport: [] for sport in SPORTS}

    for candidate in validated:
        sport = (candidate.get("sport") or "").lower().strip()
        if sport in ("ice_hockey", "nhl"):
            sport = "hockey"
        elif sport in ("f1", "formula1"):
            sport = "formula_1"
        if sport in by_sport:
            by_sport[sport].append(candidate)

    # Rank independently within each sport
    for sport in by_sport:
        by_sport[sport].sort(
            key=lambda x: (
                x.get("highestPercentagePrediction", {}).get("percentage")
                or x.get("calibratedPercentage")
                or x.get("percentage")
                or 0
            ),
            reverse=True
        )

    # First give each sport with REAL validated candidates representation.
    selected = []
    active_sports = [s for s in SPORTS if by_sport[s]]

    for sport in active_sports:
        selected.append(by_sport[sport][0])

    # Fill remaining slots using the strongest remaining candidates.
    remaining = [
        candidate
        for sport in active_sports
        for candidate in by_sport[sport][1:]
    ]

    remaining.sort(
        key=lambda x: (
            x.get("highestPercentagePrediction", {}).get("percentage")
            or x.get("calibratedPercentage")
            or x.get("percentage")
            or 0
        ),
        reverse=True
    )

    selected.extend(remaining)
    result = selected[:limit]

    if result:
        best_candidate = max(
            result,
            key=lambda x: (
                x.get("highestPercentagePrediction", {}).get("percentage")
                or x.get("calibratedPercentage")
                or x.get("percentage")
                or 0
            )
        )
        for f in result:
            f["isBestOfDay"] = False
        best_candidate["isBestOfDay"] = True

    return result
