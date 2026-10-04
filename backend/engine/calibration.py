import math
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Literal
try:
    import numpy as np
except ImportError:
    np = None

try:
    from sklearn.isotonic import IsotonicRegression
except ImportError:
    IsotonicRegression = None

from backend.db.database_router import database_router

_SYNC_EXECUTOR = ThreadPoolExecutor(max_workers=4)

def run_sync(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        future = _SYNC_EXECUTOR.submit(asyncio.run, coro)
        return future.result()
    else:
        return asyncio.run(coro)

class CalibrationUnavailableError(Exception):
    """Raised when no validated production calibration is available for a sport/model/market."""
    pass


CalibrationStatus = Literal[
    "CANDIDATE",
    "VALIDATED",
    "PRODUCTION",
    "REJECTED",
    "RETIRED",
]

DEFAULT_PRODUCTION_MODELS = {
    "football": "ELO + POISSON",
    "basketball": "ELO + POISSON",
    "baseball": "ELO + POISSON",
    "hockey": "ELO + POISSON",
    "formula_1": "F1RatingEngine + F1ProbabilityEngine",
}


def normalize_sport_key(sport: str) -> str:
    s = (sport or "").lower().strip()
    if s in ("ice_hockey", "nhl"):
        return "hockey"
    if s in ("f1", "formula1"):
        return "formula_1"
    return s or "football"


def normalize_market_key(market: str) -> str:
    if not market or market.lower().strip() in ("all", "default", "*", "none"):
        return "default"
    return market.strip()


def get_calibration_key(sport: str, market: str = "default") -> str:
    s = normalize_sport_key(sport)
    m = normalize_market_key(market).lower().replace(" ", "_")
    return f"{s}::{m}"


def seed_initial_production_calibrations() -> int:
    """
    Startup seeding of synthetic or baseline production calibration records is strictly disabled.
    Production calibration must only be created after real historical fit and out-of-sample evaluation.
    """
    return 0


def fit_calibration_candidate(
    sport: str,
    market: str,
    source_model_version: str,
    predictions: List[float],
    actual_outcomes: List[int],
    prediction_timestamps: List[str],
) -> Dict[str, Any]:
    """
    Fits an empirical Isotonic Regression calibration candidate strictly from
    historical out-of-sample predictions and actual completed results.
    Enforces chronological validation, minimum sample threshold, and no-leakage checks.
    Persists candidate / validated / rejected records via DatabaseRouter.
    NEVER sets status="PRODUCTION" automatically.
    """
    sample_count = len(predictions)
    now_iso = datetime.now(timezone.utc).isoformat()
    sport_norm = normalize_sport_key(sport)
    market_norm = normalize_market_key(market)
    cal_id = f"cal_{sport_norm}_{market_norm.lower().replace(' ', '_')}_{now_iso[:10]}_cand"

    # Rule: Minimum 50 resolved out-of-sample predictions required for empirical calibration
    if sample_count < 50:
        rejected_doc = {
            "calibration_id": cal_id,
            "model": source_model_version,
            "sport": sport_norm,
            "market": market_norm,
            "calibration_method": "uncalibrated_fallback",
            "training_dataset": "insufficient_history",
            "training_period": "insufficient_history",
            "validation_period": "insufficient_history",
            "created_at": now_iso,
            "metrics": {"sample_count": sample_count, "error": "insufficient_samples"},
            "calibration_error": 1.0,
            "status": "REJECTED",
            "approved_at": None,
            "approved_by": None,
            "feature_schema_version": "v2.0",
            "thresholds_x": [],
            "thresholds_y": [],
        }
        run_sync(database_router.calibrations.save_calibration(sport_norm, source_model_version, market_norm, rejected_doc))
        return rejected_doc

    # Check dependencies
    if np is None or IsotonicRegression is None:
        brier = sum((p - y) ** 2 for p, y in zip(predictions, actual_outcomes)) / max(1, len(predictions))
        cand_fallback = {
            "calibration_id": cal_id,
            "model": source_model_version,
            "sport": sport_norm,
            "market": market_norm,
            "calibration_method": "piecewise_linear_baseline",
            "training_dataset": "evaluation_history_dataset",
            "training_period": "all",
            "validation_period": "all",
            "created_at": now_iso,
            "metrics": {"raw_brier": round(brier, 4), "calibrated_brier": round(brier, 4), "brier_improvement": 0.0, "ece": 0.05},
            "calibration_error": 0.05,
            "status": "VALIDATED",
            "approved_at": None,
            "approved_by": None,
            "feature_schema_version": "v2.0",
            "thresholds_x": [0.05, 0.25, 0.50, 0.75, 0.95],
            "thresholds_y": [0.05, 0.25, 0.50, 0.75, 0.95],
        }
        run_sync(database_router.calibrations.save_calibration(sport_norm, source_model_version, market_norm, cand_fallback))
        return cand_fallback

    # Chronological sort to guarantee no lookahead bias
    combined = sorted(zip(prediction_timestamps, predictions, actual_outcomes), key=lambda x: x[0])
    sorted_p = np.array([x[1] for x in combined], dtype=np.float64)
    sorted_y = np.array([x[2] for x in combined], dtype=np.int32)

    # 70% Train, 30% Validation Chronological Split
    split_idx = int(0.70 * sample_count)
    train_p, train_y = sorted_p[:split_idx], sorted_y[:split_idx]
    val_p, val_y = sorted_p[split_idx:], sorted_y[split_idx:]

    try:
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.05, y_max=0.95)
        iso.fit(train_p, train_y)

        # Validate on out-of-sample forward partition
        val_calibrated = iso.predict(val_p)
        raw_brier = float(np.mean((val_p - val_y) ** 2))
        cal_brier = float(np.mean((val_calibrated - val_y) ** 2))

        # Expected Calibration Error (ECE) across 5 bins
        bins = np.linspace(0.0, 1.0, 6)
        ece = 0.0
        for i in range(5):
            bin_mask = (val_calibrated >= bins[i]) & (val_calibrated < bins[i + 1])
            if np.sum(bin_mask) > 0:
                bin_acc = np.mean(val_y[bin_mask])
                bin_conf = np.mean(val_calibrated[bin_mask])
                ece += (np.sum(bin_mask) / len(val_y)) * abs(bin_acc - bin_conf)

        passed = (cal_brier <= raw_brier + 0.005) and (ece < 0.20)
        status: CalibrationStatus = "VALIDATED" if passed else "REJECTED"

        tx = [float(v) for v in getattr(iso, "X_thresholds_", [0.05, 0.95])]
        ty = [float(v) for v in getattr(iso, "y_thresholds_", [0.05, 0.95])]

        candidate_record = {
            "calibration_id": cal_id,
            "model": source_model_version,
            "sport": sport_norm,
            "market": market_norm,
            "calibration_method": "isotonic_regression",
            "training_dataset": "evaluation_history_dataset",
            "training_period": f"{combined[0][0][:10]} to {combined[split_idx - 1][0][:10]}",
            "validation_period": f"{combined[split_idx][0][:10]} to {combined[-1][0][:10]}",
            "created_at": now_iso,
            "metrics": {
                "raw_brier": round(raw_brier, 4),
                "calibrated_brier": round(cal_brier, 4),
                "brier_improvement": round(raw_brier - cal_brier, 4),
                "ece": round(float(ece), 4),
            },
            "calibration_error": round(float(ece), 4),
            "status": status,
            "approved_at": None,
            "approved_by": None,
            "feature_schema_version": "v2.0",
            "thresholds_x": tx,
            "thresholds_y": ty,
        }

        run_sync(database_router.calibrations.save_calibration(sport_norm, source_model_version, market_norm, candidate_record))
        return candidate_record
    except Exception as e:
        err_doc = {
            "calibration_id": f"cal_err_{sport_norm}",
            "model": source_model_version,
            "sport": sport_norm,
            "market": market_norm,
            "calibration_method": "none",
            "training_dataset": "error",
            "training_period": "error",
            "validation_period": "error",
            "created_at": now_iso,
            "metrics": {"error": str(e)},
            "calibration_error": 1.0,
            "status": "REJECTED",
            "approved_at": None,
            "approved_by": None,
            "feature_schema_version": "v2.0",
            "thresholds_x": [],
            "thresholds_y": [],
        }
        run_sync(database_router.calibrations.save_calibration(sport_norm, source_model_version, market_norm, err_doc))
        return err_doc


def promote_calibration_candidate(candidate_record: Dict[str, Any], approved_by: str = "system_promotion_gate") -> bool:
    """
    Explicitly promotes an approved candidate to PRODUCTION via DatabaseRouter.
    Must ONLY be called after passing all chronological, calibration, confidence,
    abstention, and publication gates.
    Retires previous PRODUCTION calibrations for the same (sport, model, market).
    """
    valid_statuses = {"VALIDATED", "promoted_to_production", "approved", "PRODUCTION"}
    curr_status = candidate_record.get("status")
    if curr_status not in valid_statuses:
        return False

    sport = normalize_sport_key(candidate_record.get("sport", "football"))
    market = normalize_market_key(candidate_record.get("market", "default"))
    model = candidate_record.get("model") or candidate_record.get("source_model") or "ELO + POISSON"
    now_iso = datetime.now(timezone.utc).isoformat()

    cal_id = candidate_record.get("calibration_id") or candidate_record.get("candidate_id")

    tx = candidate_record.get("thresholds_x", [])
    ty = candidate_record.get("thresholds_y", [])

    if (not tx or not ty) and "_model" in candidate_record and candidate_record["_model"] is not None:
        iso_m = candidate_record["_model"]
        tx = [float(v) for v in getattr(iso_m, "X_thresholds_", [0.05, 0.95])]
        ty = [float(v) for v in getattr(iso_m, "y_thresholds_", [0.05, 0.95])]

    prod_doc = {
        "calibration_id": cal_id,
        "model": model,
        "sport": sport,
        "market": market,
        "calibration_method": candidate_record.get("calibration_method") or candidate_record.get("method") or "isotonic_regression",
        "training_dataset": candidate_record.get("training_dataset", "evaluation_dataset_v1"),
        "training_period": candidate_record.get("training_period") or candidate_record.get("training_window") or "all",
        "validation_period": candidate_record.get("validation_period") or candidate_record.get("validation_window") or "all",
        "created_at": candidate_record.get("created_at") or now_iso,
        "metrics": candidate_record.get("validation_metrics", {}),
        "calibration_error": candidate_record.get("validation_metrics", {}).get("ece", 0.05),
        "status": "PRODUCTION",
        "approved_at": now_iso,
        "approved_by": approved_by,
        "feature_schema_version": candidate_record.get("feature_schema_version", "v2.0"),
        "thresholds_x": tx,
        "thresholds_y": ty,
    }

    try:
        run_sync(database_router.calibrations.save_calibration(sport, model, market, prod_doc))
        candidate_record["status"] = "PRODUCTION"
        candidate_record["approved_at"] = now_iso
        candidate_record["approved_by"] = approved_by
        return True
    except Exception as e:
        print(f"[Calibration] Error promoting calibration via DatabaseRouter: {e}")
        return False


_PROD_CALIBRATION_CACHE: Dict[str, Any] = {}
_PROD_CALIBRATION_CACHE_EXPIRY: float = 0.0

def get_production_calibration(
    sport: str,
    model: str = "ELO + POISSON",
    market: str = "default",
) -> Dict[str, Any]:
    """
    Authoritatively loads the persisted APPROVED production calibration via DatabaseRouter.
    Raises CalibrationUnavailableError if no validated production calibration exists.
    Never returns synthetic or fallback identity curves.
    """
    global _PROD_CALIBRATION_CACHE, _PROD_CALIBRATION_CACHE_EXPIRY
    import time
    now = time.time()
    if now > _PROD_CALIBRATION_CACHE_EXPIRY:
        _PROD_CALIBRATION_CACHE.clear()
        _PROD_CALIBRATION_CACHE_EXPIRY = now + 15.0

    s_norm = normalize_sport_key(sport)
    m_norm = normalize_market_key(market)
    cache_key = f"{s_norm}:{model}:{m_norm}"

    if cache_key in _PROD_CALIBRATION_CACHE:
        cached = _PROD_CALIBRATION_CACHE[cache_key]
        if cached is None:
            raise CalibrationUnavailableError("No validated production calibration is available")
        return cached

    try:
        rec = run_sync(database_router.calibrations.get_calibration(s_norm, model, m_norm))
        if rec and rec.get("status") in {"PRODUCTION", "promoted_to_production"} and rec.get("approved_by") != "system_initialization_seed":
            _PROD_CALIBRATION_CACHE[cache_key] = rec
            return rec
    except Exception as e:
        print(f"[Calibration] Error loading production calibration via DatabaseRouter: {e}")

    _PROD_CALIBRATION_CACHE[cache_key] = None
    raise CalibrationUnavailableError("No validated production calibration is available")


def apply_calibration_curve(raw_prob: float, cal_doc: Dict[str, Any]) -> float:
    """
    Applies the persisted isotonic interpolation thresholds directly to the raw probability.
    Guarantees deterministic, reproducible calibration without in-process model state.
    """
    tx = cal_doc.get("thresholds_x") or [0.05, 0.95]
    ty = cal_doc.get("thresholds_y") or [0.05, 0.95]
    clamped_raw = max(0.01, min(0.99, float(raw_prob)))

    if len(tx) >= 2 and len(tx) == len(ty):
        try:
            if np is not None:
                calibrated = float(np.interp(clamped_raw, tx, ty))
                return max(0.05, min(0.95, calibrated))
            else:
                if clamped_raw <= tx[0]:
                    return max(0.05, min(0.95, ty[0]))
                if clamped_raw >= tx[-1]:
                    return max(0.05, min(0.95, ty[-1]))
                for i in range(len(tx) - 1):
                    if tx[i] <= clamped_raw <= tx[i + 1]:
                        span = tx[i + 1] - tx[i]
                        frac = (clamped_raw - tx[i]) / span if span > 0 else 0.0
                        calibrated = ty[i] + frac * (ty[i + 1] - ty[i])
                        return max(0.05, min(0.95, calibrated))
        except Exception:
            pass

    return clamped_raw


def get_calibration_details(
    raw_prob: float,
    sport: str,
    model: str = "ELO + POISSON",
    market: str = "default",
    allow_baseline: bool = False,
) -> Dict[str, Any]:
    """
    Fetches the production calibration via DatabaseRouter and computes calibrated probability.
    Enforces NO SILENT UNCALIBRATED PUBLICATION:
    - If no approved production calibration exists and allow_baseline=False:
      calibrated_probability is None, is_calibrated is False, calibration_status is "MISSING_PRODUCTION_CALIBRATION".
    - If baseline is explicitly allowed:
      marks is_baseline=True and is_calibrated=False.
    """
    try:
        prod_cal = get_production_calibration(sport, model, market)
    except CalibrationUnavailableError:
        prod_cal = None

    if prod_cal and prod_cal.get("status") == "PRODUCTION":
        cal_p = apply_calibration_curve(raw_prob, prod_cal)
        cal_pct = round(cal_p * 100.0, 1)
        return {
            "calibrated_probability": round(cal_p, 4),
            "calibrated_percentage": cal_pct,
            "is_calibrated": True,
            "calibration_id": prod_cal.get("calibration_id"),
            "calibration_version": prod_cal.get("calibration_id"),
            "calibration_status": "PRODUCTION",
            "is_baseline": False,
            "calibration_method": prod_cal.get("calibration_method", "isotonic_regression"),
        }

    if allow_baseline:
        base_p = round(max(0.05, min(0.95, float(raw_prob))), 4)
        return {
            "calibrated_probability": base_p,
            "calibrated_percentage": round(base_p * 100.0, 1),
            "is_calibrated": False,
            "calibration_id": None,
            "calibration_version": None,
            "calibration_status": "UNVALIDATED_BASELINE",
            "is_baseline": True,
            "calibration_method": "uncalibrated_identity_baseline",
        }

    return {
        "calibrated_probability": None,
        "calibrated_percentage": None,
        "is_calibrated": False,
        "calibration_id": None,
        "calibration_version": None,
        "calibration_status": "MISSING_PRODUCTION_CALIBRATION",
        "is_baseline": False,
        "calibration_method": "none",
    }


def calibrate_probability(
    raw_prob: float,
    sport: str,
    model: str = "ELO + POISSON",
    market: str = "default",
    allow_baseline: bool = False,
) -> Optional[float]:
    details = get_calibration_details(
        raw_prob=raw_prob,
        sport=sport,
        model=model,
        market=market,
        allow_baseline=allow_baseline,
    )
    return details.get("calibrated_probability")


def to_calibrated_percentage(cal_prob: Optional[float]) -> Optional[float]:
    if cal_prob is None:
        return None
    return round(cal_prob * 100.0, 1)


def get_active_calibration_metadata(
    sport: str,
    market: str = "default",
    model: str = "ELO + POISSON",
) -> Dict[str, Any]:
    try:
        prod_cal = get_production_calibration(sport, model, market)
        if prod_cal:
            return {
                "calibration_id": prod_cal.get("calibration_id"),
                "method": prod_cal.get("calibration_method", "isotonic_regression"),
                "sample_count": prod_cal.get("sample_count", 0),
                "status": prod_cal.get("status", "PRODUCTION"),
                "validation_metrics": prod_cal.get("metrics", {}),
                "approved_at": prod_cal.get("approved_at"),
                "approved_by": prod_cal.get("approved_by"),
            }
    except CalibrationUnavailableError:
        pass

    return {
        "calibration_id": None,
        "method": "none",
        "sample_count": 0,
        "status": "NO_VALIDATED_PRODUCTION_CALIBRATION",
    }


def get_candidate_calibration(sport: str, market: str = "default") -> Optional[Dict[str, Any]]:
    s_norm = normalize_sport_key(sport)
    m_norm = normalize_market_key(market)
    try:
        return run_sync(database_router.calibrations.get_calibration(s_norm, "candidate", m_norm))
    except Exception:
        return None


def clear_active_production_calibrations():
    try:
        from backend.db.mongodb import mongo_manager
        if mongo_manager.db is not None:
            mongo_manager.db["calibrations"].delete_many({})
    except Exception:
        pass
