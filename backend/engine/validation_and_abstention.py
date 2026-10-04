from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from backend.engine.ensemble_engine import is_production_model

# Africa/Lagos is West Africa Time (WAT), UTC+1
LAGOS_TZ = timezone(timedelta(hours=1))

ALLOWED_SPORT_CATEGORIES = {
    "football": {"1X2", "DoubleChance", "BTTS", "GoalsTotal", "Corners"},
    "basketball": {"Moneyline", "Spread", "PointsTotal"},
    "baseball": {"Moneyline", "RunLine", "RunsTotal"},
    "hockey": {"Moneyline", "PuckLine", "TotalGoals", "OverUnderGoals"},
    "ice_hockey": {"Moneyline", "PuckLine", "TotalGoals", "OverUnderGoals"},
    "formula_1": {"RaceWinner", "Podium", "Top10", "H2H", "FastestLap"},
    "f1": {"RaceWinner", "Podium", "Top10", "H2H", "FastestLap"},
}

def get_lagos_date_str(val: Any) -> str:
    """
    Extracts YYYY-MM-DD in Africa/Lagos timezone from an ISO UTC string or datetime.
    """
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val.astimezone(LAGOS_TZ).strftime("%Y-%m-%d")
    if isinstance(val, str):
        try:
            clean = val.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(LAGOS_TZ).strftime("%Y-%m-%d")
        except Exception:
            return val[:10]
    return ""

def get_current_lagos_today() -> str:
    return datetime.now(LAGOS_TZ).strftime("%Y-%m-%d")

def validate_market_for_sport(sport: str, market_name: str, market_category: str) -> bool:
    """
    Strictly verifies that a market category and naming convention belong to the specific sport.
    Never allows cross-sport leakage (e.g., football markets on hockey, basketball or baseball).
    """
    sport_key = (sport or "").lower().strip()
    if sport_key == "ice_hockey":
        sport_key = "hockey"

    if sport_key not in ALLOWED_SPORT_CATEGORIES:
        return False

    allowed_cats = ALLOWED_SPORT_CATEGORIES[sport_key]
    if market_category not in allowed_cats:
        return False

    name_lower = (market_name or "").lower()

    if sport_key == "football":
        # Football cannot contain basketball/baseball/hockey terms
        if any(term in name_lower for term in ["runs", "run line", "points total", "puck line", "nba", "mlb", "nhl"]):
            return False
        return True

    if sport_key == "basketball":
        # Basketball cannot contain football/baseball/hockey terms
        if any(term in name_lower for term in ["goal", "btts", "both teams", "draw", "1x2", "clean sheet", "corner", "run", "puck"]):
            return False
        return True

    if sport_key == "baseball":
        # Baseball cannot contain football/basketball/hockey terms
        if any(term in name_lower for term in ["goal", "btts", "both teams", "draw", "1x2", "clean sheet", "corner", "points", "puck"]):
            return False
        return True

    if sport_key in {"hockey", "ice_hockey"}:
        # Hockey cannot contain football/basketball/baseball specific terms
        if any(term in name_lower for term in ["btts", "both teams", "1x2 (draw)", "clean sheet", "corner", "points total", "run line", "runs"]):
            return False
        return True

    if sport_key in {"formula_1", "f1"}:
        # Formula 1 cannot contain football/basketball/baseball/hockey specific terms
        if any(term in name_lower for term in ["btts", "both teams", "1x2", "clean sheet", "corner", "points total", "puck line", "run line", "runs", "goals"]):
            return False
        return True

    return False

def validate_fixture_pre_conditions(fixture: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validates structural fixture requirements before prediction execution:
    1. Valid fixture ID and team names
    2. Supported sport
    3. Valid kickoff timestamp
    4. Not a completed match (completed matches show real score, not active predictions)
    5. Today or future Lagos calendar date
    """
    fix_id = fixture.get("id")
    if not fix_id or not fixture.get("homeTeam") or not fixture.get("awayTeam"):
        return {
            "isValid": False,
            "stopReason": "REJECTED_INVALID",
            "reason": "ABSTAIN: Incomplete fixture metadata (missing ID or team names).",
        }

    # Rule: Completed matches must not be presented as current predictions
    status = (fixture.get("status") or "").lower().strip()
    if status == "completed":
        return {
            "isValid": False,
            "stopReason": "REJECTED_COMPLETED",
            "reason": "ABSTAIN: Match is already completed with final score recorded.",
        }

    sport = (fixture.get("sport") or "").lower().strip()
    if sport not in {"football", "basketball", "baseball", "hockey", "ice_hockey", "formula_1", "f1"}:
        return {
            "isValid": False,
            "stopReason": "REJECTED_INVALID",
            "reason": f"ABSTAIN: Unsupported sport '{sport}'.",
        }

    kickoff_utc = fixture.get("kickoffUtc") or fixture.get("scheduled_at")
    if not kickoff_utc:
        return {
            "isValid": False,
            "stopReason": "REJECTED_INVALID",
            "reason": "ABSTAIN: Missing kickoff timestamp.",
        }

    # Verify parseable date
    lagos_date = get_lagos_date_str(kickoff_utc)
    if not lagos_date or len(lagos_date) < 10:
        return {
            "isValid": False,
            "stopReason": "REJECTED_INVALID",
            "reason": "ABSTAIN: Invalid kickoff timestamp format.",
        }

    # Rule: Predictions must be for today or future dates in Africa/Lagos timezone
    today_lagos = get_current_lagos_today()
    if lagos_date < today_lagos:
        return {
            "isValid": False,
            "stopReason": "REJECTED_PAST",
            "reason": f"ABSTAIN: Kickoff date {lagos_date} is in the past (Lagos today is {today_lagos}).",
        }

    return {"isValid": True}

def validate_market_publication(
    market: Dict[str, Any],
    sport: str = "football",
    is_subscriber_feed: bool = True,
    active_model: str = "ELO + POISSON",
) -> Dict[str, Any]:
    """
    Enforces publication gates on candidate markets:
    - Sport-specific market category and name validity
    - Sufficient point-in-time completed match data (>= 5 matches)
    - Production model check for subscriber feed
    - Calibrated confidence and probability thresholds
    """
    m_name = market.get("marketName", "")
    m_cat = market.get("marketCategory", "")

    # Gate 1: Sport-specific market validity
    if not validate_market_for_sport(sport, m_name, m_cat):
        return {
            "isPublishable": False,
            "stopReason": "INVALID_MARKET",
            "abstentionReason": f"ABSTAIN: Market '{m_name}' (category '{m_cat}') is invalid for sport '{sport}'.",
        }

    # Gate 2: Sufficient historical data
    if not market.get("hasSufficientData", False):
        return {
            "isPublishable": False,
            "stopReason": "INSUFFICIENT_HISTORY",
            "abstentionReason": "ABSTAIN: Insufficient point-in-time completed matches (< 5 matches).",
        }

    # Gate 3: Subscriber feed model qualification
    if is_subscriber_feed and not is_production_model(active_model):
        return {
            "isPublishable": False,
            "stopReason": "UNVALIDATED_MODEL",
            "abstentionReason": f"ABSTAIN: Model mode '{active_model}' is an evaluation challenger and cannot publish to subscriber feed.",
        }

    # Gate 4: Validated Production Calibration Check (No silent uncalibrated publication)
    is_calibrated = market.get("isCalibrated", False)
    cal_status = market.get("calibrationStatus", "")
    is_baseline = market.get("isBaseline", False)

    if is_subscriber_feed:
        if cal_status == "REJECTED":
            return {
                "isPublishable": False,
                "stopReason": "REJECTED_CALIBRATION",
                "abstentionReason": f"ABSTAIN: Market '{m_name}' has a REJECTED calibration and cannot publish.",
            }
        if cal_status == "CANDIDATE":
            return {
                "isPublishable": False,
                "stopReason": "CANDIDATE_NOT_PRODUCTION",
                "abstentionReason": f"ABSTAIN: Market '{m_name}' calibration is still a CANDIDATE and cannot publish to production.",
            }
        if not is_calibrated or cal_status != "PRODUCTION" or is_baseline:
            return {
                "isPublishable": False,
                "stopReason": "UNCALIBRATED_ABSTAIN",
                "abstentionReason": f"ABSTAIN: Market '{m_name}' lacks an approved PRODUCTION calibration (status: '{cal_status}'). Fails closed.",
            }

    cal_prob = market.get("calibratedProbability")
    cal_pct = market.get("calibratedPercentage")
    if cal_prob is None or cal_pct is None:
        return {
            "isPublishable": False,
            "stopReason": "UNCALIBRATED_ABSTAIN",
            "abstentionReason": f"ABSTAIN: Market '{m_name}' missing calibrated probability values.",
        }

    # Gate 5: Confidence score gate (min 0.50)
    conf_score = market.get("confidenceScore", 0.0)
    if conf_score < 0.50:
        return {
            "isPublishable": False,
            "stopReason": "LOW_CONFIDENCE",
            "abstentionReason": f"ABSTAIN: Confidence score {conf_score:.2f} is below minimum threshold 0.50.",
        }

    # Gate 5: Calibrated statistical percentage threshold
    cal_pct = market.get("calibratedPercentage", 0.0)
    is_3way = (m_cat == "1X2")
    sport_key = (sport or "").lower().strip()
    if sport_key in {"formula_1", "f1"}:
        min_thresh = 20.0 if m_cat in ("RaceWinner", "FastestLap") else 45.0
    else:
        min_thresh = 48.0 if is_3way else 53.0
    if cal_pct < min_thresh:
        return {
            "isPublishable": False,
            "stopReason": "LOW_PROBABILITY",
            "abstentionReason": f"ABSTAIN: Percentage {cal_pct:.1f}% is below statistical threshold {min_thresh}%.",
        }

    # Gate 6: Valid calibrated probability range
    cal_prob = market.get("calibratedProbability", 0.0)
    if cal_prob <= 0.0 or cal_prob >= 1.0:
        return {
            "isPublishable": False,
            "stopReason": "INVALID_PROBABILITY",
            "abstentionReason": f"ABSTAIN: Calibrated probability {cal_prob} is out of bounds (0, 1).",
        }

    return {"isPublishable": True}

def filter_publishable_markets(
    markets: List[Dict[str, Any]],
    is_subscriber_feed: bool
) -> List[Dict[str, Any]]:
    if is_subscriber_feed:
        return [m for m in markets if m.get("isPublishable", False)]
    return markets
