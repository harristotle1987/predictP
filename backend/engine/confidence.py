from typing import Dict, Any

def calculate_confidence(
    calibrated_prob: float,
    pit_features: Dict[str, Any],
    min_completed_matches: int
) -> Dict[str, Any]:
    sample_factor = min(1.0, max(0.0, 0.40 + (min_completed_matches / 10.0) * 0.60))
    completeness_factor = 0.95 if pit_features.get("hasSufficientData", False) else 0.50
    certainty_margin = abs(calibrated_prob - 0.50)
    certainty_factor = min(1.0, certainty_margin / 0.28)

    raw_score = (
        0.35 * sample_factor +
        0.25 * completeness_factor +
        0.40 * certainty_factor
    )
    score = round(raw_score, 2)

    rating = "Moderate"
    if score >= 0.72:
        rating = "High"
    elif score >= 0.58:
        rating = "Solid"

    return {"score": score, "rating": rating}
