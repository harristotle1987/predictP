from typing import Dict, Any, List, Optional
from backend.engine.glicko2_engine import run_glicko2_engine
from backend.engine.gradient_boosting_engine import run_gradient_boosting_engine

PRODUCTION_MODELS = {"ELO", "POISSON", "ELO + POISSON", "F1RatingEngine + F1ProbabilityEngine", "F1_RATING_PROBABILITY", "F1"}
CHALLENGER_MODELS = {
    "GLICKO2",
    "GRADIENT_BOOSTING",
    "GLICKO2 + GRADIENT_BOOSTING",
    "ELO + POISSON + GLICKO2",
    "ELO + POISSON + GLICKO2 + GRADIENT_BOOSTING",
}

ALL_SUPPORTED_MODELS = PRODUCTION_MODELS | CHALLENGER_MODELS

def is_production_model(model: str) -> bool:
    return model in PRODUCTION_MODELS

def is_challenger_model(model: str) -> bool:
    return model in CHALLENGER_MODELS

def get_ensemble_weights(model: str) -> Optional[Dict[str, float]]:
    if model == "ELO":
        return {"elo": 1.0, "poisson": 0.0, "glicko2": 0.0, "gb": 0.0}
    elif model == "POISSON":
        return {"elo": 0.0, "poisson": 1.0, "glicko2": 0.0, "gb": 0.0}
    elif model == "ELO + POISSON":
        return {"elo": 0.50, "poisson": 0.50, "glicko2": 0.0, "gb": 0.0}
    elif model == "GLICKO2":
        return {"elo": 0.0, "poisson": 0.0, "glicko2": 1.0, "gb": 0.0}
    elif model == "GRADIENT_BOOSTING":
        return {"elo": 0.0, "poisson": 0.0, "glicko2": 0.0, "gb": 1.0}
    elif model == "GLICKO2 + GRADIENT_BOOSTING":
        return {"elo": 0.0, "poisson": 0.0, "glicko2": 0.50, "gb": 0.50}
    elif model == "ELO + POISSON + GLICKO2":
        return {"elo": 0.35, "poisson": 0.35, "glicko2": 0.30, "gb": 0.0}
    elif model == "ELO + POISSON + GLICKO2 + GRADIENT_BOOSTING":
        return {"elo": 0.30, "poisson": 0.30, "glicko2": 0.20, "gb": 0.20}
    else:
        return None

def run_model_ensemble(
    active_model: str,
    sport: str,
    home_team: str,
    away_team: str,
    cutoff_timestamp: str,
    pit_features: Dict[str, Any],
) -> Dict[str, Any]:
    sport_norm = sport.lower().strip() if sport else "football"
    if sport_norm == "ice_hockey":
        sport_norm = "hockey"

    if sport_norm in ("formula_1", "f1"):
        from backend.engine.f1_probability_engine import run_f1_probability_engine
        from backend.markets.f1_markets import build_f1_markets
        from backend.engine.calibration import calibrate_probability
        from backend.engine.confidence import calculate_confidence

        f1_prob_res = run_f1_probability_engine("formula_1", cutoff_timestamp)
        if not f1_prob_res.get("hasSufficientData", False):
            return {
                "markets": [],
                "hasSufficientData": False,
                "modelMode": "F1RatingEngine + F1ProbabilityEngine",
                "f1Drivers": [],
                "metadata": f1_prob_res.get("metadata", {
                    "sport": "formula_1",
                    "model_name": "F1RatingEngine + F1ProbabilityEngine",
                    "model_version": "2.0.0",
                    "training_window": "all",
                    "training_match_count": 0,
                    "feature_timestamp": cutoff_timestamp,
                    "prediction_timestamp": cutoff_timestamp,
                }),
            }

        raw_markets = build_f1_markets(f1_prob_res.get("drivers", []))
        calibrated_markets = []
        for m in raw_markets:
            p_cal = calibrate_probability(m["rawProbability"], "formula_1", model="F1RatingEngine + F1ProbabilityEngine")
            if p_cal is None:
                # Production calibration required: fail closed on uncalibrated market
                continue
            pct = round(p_cal * 100.0, 1)
            conf_res = calculate_confidence(p_cal, {"hasSufficientData": True}, 10)
            conf = conf_res["score"]
            rating_label = conf_res["rating"]
            calibrated_markets.append({
                "marketName": m["marketName"],
                "selection": m["selection"],
                "rawProbability": m["rawProbability"],
                "calibratedProbability": p_cal,
                "calibratedPercentage": pct,
                "confidenceScore": conf,
                "confidenceRating": rating_label,
                "modelSource": m["modelSource"],
                "marketCategory": m["marketCategory"],
                "hasSufficientData": True,
            })

        return {
            "markets": calibrated_markets,
            "hasSufficientData": True,
            "modelMode": "F1RatingEngine + F1ProbabilityEngine",
            "f1Drivers": f1_prob_res.get("drivers", []),
            "metadata": f1_prob_res.get("metadata", {}),
            "eloSuccessful": False,
            "poissonSuccessful": False,
            "glickoSuccessful": False,
            "gbSuccessful": False,
        }

    weights = get_ensemble_weights(active_model)
    if weights is None:
        # Invalid mode strictly fails closed
        return {
            "markets": [],
            "hasSufficientData": False,
            "modelMode": active_model,
        }

    elo_res = None
    poisson_res = None

    if weights["elo"] > 0:
        if sport_norm == "football":
            from backend.engine.football_elo_engine import run_football_elo_engine
            elo_res = run_football_elo_engine("football", home_team, away_team, cutoff_timestamp)
        elif sport_norm == "basketball":
            from backend.engine.basketball_elo_engine import run_basketball_elo_engine
            elo_res = run_basketball_elo_engine("basketball", home_team, away_team, cutoff_timestamp)
        elif sport_norm == "baseball":
            from backend.engine.baseball_elo_engine import run_baseball_elo_engine
            elo_res = run_baseball_elo_engine("baseball", home_team, away_team, cutoff_timestamp)
        elif sport_norm == "hockey":
            from backend.engine.hockey_elo_engine import run_hockey_elo_engine
            elo_res = run_hockey_elo_engine(sport, home_team, away_team, cutoff_timestamp)
        else:
            raise ValueError(f"Mismatched sport '{sport}' for ELO engine.")

    if weights["poisson"] > 0:
        if sport_norm == "football":
            from backend.engine.football_poisson_engine import run_football_poisson_engine
            poisson_res = run_football_poisson_engine("football", home_team, away_team, cutoff_timestamp)
        elif sport_norm == "basketball":
            from backend.engine.basketball_poisson_engine import run_basketball_poisson_engine
            poisson_res = run_basketball_poisson_engine("basketball", home_team, away_team, cutoff_timestamp)
        elif sport_norm == "baseball":
            from backend.engine.baseball_poisson_engine import run_baseball_poisson_engine
            poisson_res = run_baseball_poisson_engine("baseball", home_team, away_team, cutoff_timestamp)
        elif sport_norm == "hockey":
            from backend.engine.hockey_poisson_engine import run_hockey_poisson_engine
            poisson_res = run_hockey_poisson_engine(sport, home_team, away_team, cutoff_timestamp)
        else:
            raise ValueError(f"Mismatched sport '{sport}' for Poisson engine.")

    glicko_res = run_glicko2_engine(sport, home_team, away_team, cutoff_timestamp) if weights["glicko2"] > 0 else None
    gb_res = run_gradient_boosting_engine(sport, home_team, away_team, cutoff_timestamp, pit_features) if weights["gb"] > 0 else None

    elo_ok = bool(elo_res and elo_res.get("hasSufficientData") and elo_res.get("markets"))
    poisson_ok = bool(poisson_res and poisson_res.get("hasSufficientData") and poisson_res.get("markets"))
    glicko_ok = bool(glicko_res and glicko_res.get("hasSufficientData") and glicko_res.get("markets"))
    gb_ok = bool(gb_res and gb_res.get("hasSufficientData") and gb_res.get("markets"))

    model_name_parts = []
    total_training_matches = 0
    if weights["elo"] > 0 and elo_res:
        m_name = elo_res.get("metadata", {}).get("model_name") or f"{sport_norm.capitalize()}Elo"
        model_name_parts.append(m_name)
        total_training_matches = max(total_training_matches, elo_res.get("metadata", {}).get("training_match_count", 0))
    if weights["poisson"] > 0 and poisson_res:
        m_name = poisson_res.get("metadata", {}).get("model_name") or f"{sport_norm.capitalize()}Poisson"
        model_name_parts.append(m_name)
        total_training_matches = max(total_training_matches, poisson_res.get("metadata", {}).get("training_match_count", 0))
    if weights["glicko2"] > 0 and glicko_res:
        m_name = glicko_res.get("metadata", {}).get("model_name") or f"{sport_norm.capitalize()}Glicko2"
        model_name_parts.append(m_name)
        total_training_matches = max(total_training_matches, glicko_res.get("metadata", {}).get("training_match_count", 0))
    if weights["gb"] > 0 and gb_res:
        m_name = gb_res.get("metadata", {}).get("model_name") or f"{sport_norm.capitalize()}GradientBoosting"
        model_name_parts.append(m_name)
        total_training_matches = max(total_training_matches, gb_res.get("metadata", {}).get("training_match_count", 0))

    resolved_model_name = " + ".join(model_name_parts) if model_name_parts else active_model

    metadata = {
        "sport": sport_norm,
        "model_name": resolved_model_name,
        "model_version": "2.0.0",
        "training_window": "all",
        "training_match_count": total_training_matches,
        "feature_timestamp": cutoff_timestamp,
        "prediction_timestamp": cutoff_timestamp,
    }

    active_results = [r for r in [elo_res, poisson_res, glicko_res, gb_res] if r is not None]
    for r in active_results:
        if not r.get("hasSufficientData", False):
            return {
                "markets": [],
                "hasSufficientData": False,
                "modelMode": active_model,
                "eloSuccessful": False,
                "poissonSuccessful": False,
                "glickoSuccessful": False,
                "gbSuccessful": False,
                "metadata": metadata,
            }

    # Distribution-Aware Ensemble Combination
    # Groups by complete market, applies model weights across full outcome vectors,
    # and normalizes mutually-exclusive distributions to 1.0.
    engines_data = [
        ("elo", elo_res, weights["elo"]),
        ("poisson", poisson_res, weights["poisson"]),
        ("glicko2", glicko_res, weights["glicko2"]),
        ("gb", gb_res, weights["gb"]),
    ]

    # Group markets by marketName
    # market_groups[market_name] = {
    #    "category": category,
    #    "selections": { selection_name: [ (weight, prob) ] }
    # }
    market_groups: Dict[str, Dict[str, Any]] = {}

    for eng_name, res, w in engines_data:
        if w <= 0 or not res or not res.get("markets"):
            continue
        for m in res.get("markets", []):
            m_name = m.get("marketName")
            sel = m.get("selection")
            cat = m.get("marketCategory", "General")
            raw_p = float(m.get("rawProbability", 0.0))

            # Skip invalid / NaN / negative probabilities
            if raw_p != raw_p or raw_p < 0.0:
                continue

            if m_name not in market_groups:
                market_groups[m_name] = {
                    "category": cat,
                    "selections": {},
                }
            if sel not in market_groups[m_name]["selections"]:
                market_groups[m_name]["selections"][sel] = []
            market_groups[m_name]["selections"][sel].append((w, raw_p))

    merged: List[Dict[str, Any]] = []

    for m_name, g_data in market_groups.items():
        cat = g_data["category"]
        sels = g_data["selections"]

        # Calculate weighted combination for each outcome
        combined_probs: Dict[str, float] = {}
        for sel, w_p_list in sels.items():
            tot_w = sum(w for w, _ in w_p_list)
            if tot_w > 0:
                weighted_sum = sum(w * p for w, p in w_p_list)
                combined_probs[sel] = weighted_sum / tot_w
            else:
                combined_probs[sel] = 0.0

        # Normalization
        # Mutually exclusive markets must sum to 1.0
        # Double Chance sums to 2.0 (covers 2 outcomes per selection)
        is_double_chance = any(term in m_name.lower() or term in cat.lower() for term in ["double chance", "doublechance"])
        target_sum = 2.0 if is_double_chance else 1.0

        current_sum = sum(combined_probs.values())
        if current_sum > 0:
            scale = target_sum / current_sum
            for sel in combined_probs:
                p_norm = combined_probs[sel] * scale
                # Clamp within [0.01, 0.99]
                p_norm = max(0.005, min(0.995, p_norm))
                combined_probs[sel] = p_norm

            # Re-verify and final adjustment to guarantee exact sum
            final_sum = sum(combined_probs.values())
            if final_sum > 0 and not is_double_chance:
                # Distribute slight residual difference to highest probability selection
                top_sel = max(combined_probs.keys(), key=lambda k: combined_probs[k])
                residual = 1.0 - final_sum
                combined_probs[top_sel] = max(0.01, min(0.99, combined_probs[top_sel] + residual))

        for sel, prob in combined_probs.items():
            # Validate probability is strictly valid
            if prob == prob and 0.0 <= prob <= 1.0:
                merged.append({
                    "marketName": m_name,
                    "selection": sel,
                    "rawProbability": round(prob, 4),
                    "modelSource": active_model,
                    "marketCategory": cat,
                })

    return {
        "markets": merged,
        "hasSufficientData": True,
        "modelMode": active_model,
        "eloSuccessful": elo_ok,
        "poissonSuccessful": poisson_ok,
        "glickoSuccessful": glicko_ok,
        "gbSuccessful": gb_ok,
        "metadata": metadata,
    }
