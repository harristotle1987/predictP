from typing import List, Dict, Any
from backend.features.f1_feature_builder import build_f1_features

def compute_f1_ratings(sport: str, cutoff_timestamp: str, circuit_id: str = "all") -> List[Dict[str, Any]]:
    """
    F1 Rating Engine.
    Computes distinct driver ratings, constructor ratings, recent-form adjustments, and circuit adjustments.
    Strictly validates sport parameter. Fail closed on mismatch.
    """
    if sport != "formula_1":
        raise ValueError(f"Mismatched training sport: {sport}. F1RatingEngine only supports formula_1.")

    features = build_f1_features(cutoff_timestamp, circuit_id)
    if not features.get("hasSufficientData", False):
        return []

    rated_competitors = []
    # Loop over all drivers retrieved from point-in-time features
    for d in features["drivers"]:
        d_id = d["driverId"]
        c_id = d["constructorId"]

        # Base driver rating: derived from average historical finish
        # Lower average finish (e.g. 2.5) -> Higher Rating (e.g. 1800)
        # Higher average finish (e.g. 18.0) -> Lower Rating (e.g. 1000)
        base_driver_rating = 2000.0 - (d["avgFinish"] * 50.0)

        # Constructor performance rating
        c_feats = next((c for c in features["constructors"] if c["constructorId"] == c_id), None)
        c_rating = 1500.0
        if c_feats:
            c_rating = 2000.0 - (c_feats["racePerformanceAvg"] * 50.0)

        # Circuit adjustment
        circuit_adj = 0.0
        if d.get("avgCircuitFinish") is not None:
            # If driver excels at this circuit compared to their overall average, positive adjustment
            circuit_adj = (d["avgFinish"] - d["avgCircuitFinish"]) * 20.0

        # Recent-form adjustment
        form_adj = 0.0
        form = d["recentForm"]
        # W (Win) -> +30, POD (Podium) -> +15, PTS (Points) -> +5, OUT -> -10
        for token in form.split("-"):
            if token == "W":
                form_adj += 30.0
            elif token == "POD":
                form_adj += 15.0
            elif token == "PTS":
                form_adj += 5.0
            elif token == "OUT":
                form_adj -= 10.0

        # Final compounded rating
        final_rating = base_driver_rating * 0.5 + c_rating * 0.3 + circuit_adj + form_adj

        rated_competitors.append({
            "driverId": d_id,
            "driverName": d["driverName"],
            "constructorId": c_id,
            "constructorName": d["constructorName"],
            "baseDriverRating": round(base_driver_rating, 1),
            "constructorRating": round(c_rating, 1),
            "circuitAdjustment": round(circuit_adj, 1),
            "recentFormAdjustment": round(form_adj, 1),
            "finalRating": round(final_rating, 1),
            "dnfRate": d["dnfRate"],
            "recentQualifying": d["recentQualifyingPosition"],
        })

    # Sort by final rating descending
    return sorted(rated_competitors, key=lambda x: x["finalRating"], reverse=True)
