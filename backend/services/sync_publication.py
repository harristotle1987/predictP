"""
Sync Publication Service.
Handles reliable persistence of validated predictions to operational datastores
with full model governance tracking: prediction_run_id, model_version, feature_version,
data_cutoff, raw_probability, calibrated_probability, confidence, and publication_status.
"""
import uuid
import time
from typing import List, Dict, Any
from datetime import datetime, timezone

def attach_prediction_governance_metadata(
    predictions: List[Dict[str, Any]],
    active_model: str,
    feature_version: str = "2.0.0",
    run_id: str = None,
) -> str:
    """
    Enriches predictions with standard governance and audit fields before publication.
    Returns the unique prediction_run_id.
    """
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prediction_run_id = run_id or f"pred_run_{uuid.uuid4().hex[:12]}_{int(time.time())}"

    for p in predictions:
        p["prediction_run_id"] = prediction_run_id
        p["run_id"] = prediction_run_id
        p["model_version"] = p.get("modelVersion") or active_model
        p["feature_version"] = p.get("featureVersion") or feature_version
        p["data_cutoff"] = now_iso
        
        # Calculate raw and calibrated probabilities
        prob = None
        if p.get("calibratedPercentage") is not None:
            prob = float(p["calibratedPercentage"]) / 100.0 if float(p["calibratedPercentage"]) > 1.0 else float(p["calibratedPercentage"])
        elif p.get("percentage") is not None:
            prob = float(p["percentage"]) / 100.0 if float(p["percentage"]) > 1.0 else float(p["percentage"])
        elif p.get("highestPercentagePrediction") and isinstance(p["highestPercentagePrediction"], dict):
            p_val = p["highestPercentagePrediction"].get("percentage")
            if p_val is not None:
                prob = float(p_val) / 100.0 if float(p_val) > 1.0 else float(p_val)
        if prob is None:
            prob = 0.50

        raw_p = p.get("rawProbability") or p.get("raw_probability")
        if raw_p is None:
            raw_p = prob
        elif float(raw_p) > 1.0:
            raw_p = float(raw_p) / 100.0

        p["raw_probability"] = round(float(raw_p), 4)
        p["calibrated_probability"] = round(float(prob), 4)
        p["confidence"] = str(p.get("confidence") or p.get("confidenceLevel") or "HIGH").upper()
        p["publication_status"] = "published"
        p["published"] = True
        p["validationStatus"] = "validated"
        p["validation_status"] = "validated"

    return prediction_run_id
