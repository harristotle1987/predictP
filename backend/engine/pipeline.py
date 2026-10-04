import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger("predictpro.pipeline")

def log_candidate_rejection(
    sport: str,
    fixture_id: str,
    home_team: str,
    away_team: str,
    reason: str,
    home_history_count: int = 0,
    away_history_count: int = 0,
):
    logger.info(
        "prediction_candidate_rejected",
        extra={
            "sport": sport,
            "fixture_id": fixture_id,
            "home_team": home_team,
            "away_team": away_team,
            "reason": reason,
            "home_history_count": home_history_count,
            "away_history_count": away_history_count,
        },
    )
from backend.engine.feature_builders import (
    build_football_features,
    build_basketball_features,
    build_baseball_features,
    build_hockey_features,
)
from backend.engine.ensemble_engine import run_model_ensemble, is_production_model
from backend.engine.calibration import (
    calibrate_probability,
    to_calibrated_percentage,
    get_calibration_details,
)
from backend.engine.confidence import calculate_confidence
from backend.engine.validation_and_abstention import (
    validate_fixture_pre_conditions,
    validate_market_publication,
    filter_publishable_markets,
)
from backend.engine.ranking import rank_and_select_best_of_day

VALID_MODELS = [
    "ELO",
    "POISSON",
    "ELO + POISSON",
    "GLICKO2",
    "GRADIENT_BOOSTING",
    "GLICKO2 + GRADIENT_BOOSTING",
    "ELO + POISSON + GLICKO2",
    "ELO + POISSON + GLICKO2 + GRADIENT_BOOSTING",
]

def execute_prediction_pipeline(
    active_model: str,
    is_subscriber_feed: bool = True,
    sport_filter: Optional[str] = None,
    league_filter: Optional[str] = None,
    custom_fixtures: Optional[List[Dict[str, Any]]] = None,
    prediction_contexts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if active_model not in VALID_MODELS:
        raise ValueError(f"Invalid model mode '{active_model}'. Pipeline fails closed.")

    # Rule: Challenger models are evaluation-only until validated.
    # If the subscriber feed is requested with an unvalidated challenger model, fail closed.
    if is_subscriber_feed and not is_production_model(active_model):
        return {
            "modelVersion": active_model,
            "totalCandidates": 0,
            "validatedCount": 0,
            "abstainedCount": 0,
            "isSubscriberFeed": True,
            "publishedFeed": [],
            "allResults": [],
        }

    # Zero demo/fake candidates: only use provided operational fixtures
    candidates = custom_fixtures if custom_fixtures is not None else []

    if sport_filter and sport_filter.lower() != "all":
        candidates = [c for c in candidates if c.get("sport", "").lower() == sport_filter.lower()]
    if league_filter and league_filter.lower() != "all":
        candidates = [c for c in candidates if c.get("league", "").lower() == league_filter.lower()]

    execution_results: List[Dict[str, Any]] = []

    fixtures_eligible = 0
    fixtures_rejected_invalid = 0
    fixtures_rejected_past = 0
    fixtures_rejected_completed = 0
    fixtures_insufficient_history = 0
    fixtures_model_executed = 0
    elo_successful = 0
    poisson_successful = 0
    ensemble_successful = 0
    markets_generated = 0
    markets_rejected = 0
    calibrated_count = 0
    confidence_passed_count = 0
    validation_passed_count = 0
    candidate_stop_reasons: Dict[str, int] = {}

    def record_stop_reason(reason: str):
        # Normalize to concise snake_case code
        norm = (
            "insufficient_history" if reason in ("INSUFFICIENT_HISTORY",)
            else "invalid_fixture" if reason in ("REJECTED_INVALID", "REJECTED_PAST", "REJECTED_COMPLETED", "INVALID_PRECONDITION")
            else "unsupported_market" if reason in ("INVALID_MARKET", "SPORT_MISMATCH")
            else "model_failure" if reason in ("MODEL_NO_OUTPUT", "UNVALIDATED_MODEL")
            else "calibration_unavailable" if reason in ("UNCALIBRATED_ABSTAIN", "REJECTED_CALIBRATION", "CANDIDATE_NOT_PRODUCTION")
            else "confidence_gate_failed" if reason in ("LOW_CONFIDENCE",)
            else "publication_gate_failed" if reason in ("LOW_PROBABILITY", "INVALID_PROBABILITY")
            else "published" if reason in ("PUBLISHED",)
            else "validated" if reason in ("VALIDATED",)
            else "abstained"
        )
        candidate_stop_reasons[norm] = candidate_stop_reasons.get(norm, 0) + 1
        if reason and reason != norm:
            candidate_stop_reasons[reason] = candidate_stop_reasons.get(reason, 0) + 1

    for fix in candidates:
        sport = (fix.get("sport") or "football").lower().strip()
        if sport == "ice_hockey":
            sport = "hockey"
        home = fix.get("homeTeam", "")
        away = fix.get("awayTeam", "")
        cutoff = fix.get("kickoffUtc") or fix.get("scheduled_at") or datetime.now(timezone.utc).isoformat()
        current_score = fix.get("currentScore")
        raw_status = str(fix.get("status", "scheduled")).lower().strip()
        match_status = (
            "live" if raw_status == "live"
            else "completed" if raw_status == "completed"
            else "upcoming"
        )

        # 1. Structural Pre-Condition Validation
        pre_val = validate_fixture_pre_conditions(fix)
        if not pre_val.get("isValid", False):
            stop_code = pre_val.get("stopReason", "REJECTED_INVALID")
            if stop_code == "REJECTED_PAST":
                fixtures_rejected_past += 1
            elif stop_code == "REJECTED_COMPLETED":
                fixtures_rejected_completed += 1
            else:
                fixtures_rejected_invalid += 1
            record_stop_reason(stop_code)

            log_candidate_rejection(
                sport=sport,
                fixture_id=fix.get("id", ""),
                home_team=home,
                away_team=away,
                reason=stop_code,
                home_history_count=0,
                away_history_count=0,
            )

            execution_results.append({
                "fixtureId": fix.get("id", ""),
                "sport": sport,
                "league": fix.get("league", ""),
                "homeTeam": home,
                "awayTeam": away,
                "kickoffUtc": cutoff,
                "status": match_status,
                "currentScore": current_score,
                "features": {},
                "markets": [],
                "isBestOfDay": False,
                "validationStatus": "abstained",
                "stopReason": stop_code,
                "abstentionDiagnostics": [pre_val.get("reason", "ABSTAIN: Invalid fixture precondition.")],
                "modelVersion": active_model,
            })
            continue

        fixtures_eligible += 1

        # 2. Point-in-time historical features extraction (strictly matchDate < kickoffUtc)
        f_id = fix.get("id", "")
        if prediction_contexts and f_id in prediction_contexts:
            ctx = prediction_contexts[f_id]
            pit_features = ctx.build_engine_features() if hasattr(ctx, "build_engine_features") else ctx.get("features", {})
        elif sport == "football":
            pit_features = build_football_features(home, away, cutoff)
        elif sport == "basketball":
            pit_features = build_basketball_features(home, away, cutoff)
        elif sport == "baseball":
            pit_features = build_baseball_features(home, away, cutoff)
        elif sport in {"hockey", "ice_hockey"}:
            pit_features = build_hockey_features(home, away, cutoff)
        elif sport in {"formula_1", "f1"}:
            from backend.features.f1_feature_builder import build_f1_features
            pit_features = build_f1_features(cutoff)
        else:
            pit_features = {"hasSufficientData": False, "homeMatchesCount": 0, "awayMatchesCount": 0}

        if sport in {"formula_1", "f1"}:
            min_matches = len(pit_features.get("drivers", []))
        else:
            min_matches = min(pit_features.get("homeMatchesCount") or 0, pit_features.get("awayMatchesCount") or 0)

        # Rule: Both teams (or F1 drivers field) must have at least 5 completed historical matches before kickoffUtc
        if not pit_features.get("hasSufficientData", False) or min_matches < 5:
            fixtures_insufficient_history += 1
            record_stop_reason("INSUFFICIENT_HISTORY")

            log_candidate_rejection(
                sport=sport,
                fixture_id=f_id,
                home_team=home,
                away_team=away,
                reason="INSUFFICIENT_HISTORY",
                home_history_count=pit_features.get("homeMatchesCount", 0),
                away_history_count=pit_features.get("awayMatchesCount", 0),
            )

            execution_results.append({
                "fixtureId": fix["id"],
                "sport": sport,
                "league": fix.get("league", ""),
                "homeTeam": home,
                "awayTeam": away,
                "kickoffUtc": cutoff,
                "status": match_status,
                "currentScore": current_score,
                "features": pit_features,
                "markets": [],
                "isBestOfDay": False,
                "validationStatus": "insufficient_data",
                "stopReason": "INSUFFICIENT_HISTORY",
                "abstentionDiagnostics": [f"ABSTAIN: Insufficient point-in-time matches ({min_matches} < 5)"],
                "modelVersion": active_model,
            })
            continue

        # 3. Model execution
        fixtures_model_executed += 1
        ensemble_out = run_model_ensemble(active_model, sport, home, away, cutoff, pit_features)

        model_meta = ensemble_out.get("metadata", {})
        training_sport = model_meta.get("sport")
        
        # Hard validation: if training_sport != fixture_sport: fail closed
        if training_sport and training_sport != sport:
            record_stop_reason("SPORT_MISMATCH")
            log_candidate_rejection(
                sport=sport,
                fixture_id=f_id,
                home_team=home,
                away_team=away,
                reason="ENGINE_FAILURE",
                home_history_count=pit_features.get("homeMatchesCount", 0),
                away_history_count=pit_features.get("awayMatchesCount", 0),
            )
            execution_results.append({
                "fixtureId": fix["id"],
                "sport": sport,
                "league": fix.get("league", ""),
                "homeTeam": home,
                "awayTeam": away,
                "kickoffUtc": cutoff,
                "status": match_status,
                "currentScore": current_score,
                "features": pit_features,
                "markets": [],
                "isBestOfDay": False,
                "validationStatus": "abstained",
                "stopReason": "SPORT_MISMATCH",
                "abstentionDiagnostics": [f"FAIL_CLOSED: Training sport {training_sport} does not match fixture sport {sport}"],
                "modelVersion": active_model,
            })
            continue

        if ensemble_out.get("eloSuccessful"):
            elo_successful += 1
        if ensemble_out.get("poissonSuccessful"):
            poisson_successful += 1

        if not ensemble_out.get("hasSufficientData", False) or not ensemble_out.get("markets"):
            record_stop_reason("MODEL_NO_OUTPUT")
            log_candidate_rejection(
                sport=sport,
                fixture_id=f_id,
                home_team=home,
                away_team=away,
                reason="ENGINE_FAILURE",
                home_history_count=pit_features.get("homeMatchesCount", 0),
                away_history_count=pit_features.get("awayMatchesCount", 0),
            )

            execution_results.append({
                "fixtureId": fix["id"],
                "sport": sport,
                "league": fix.get("league", ""),
                "homeTeam": home,
                "awayTeam": away,
                "kickoffUtc": cutoff,
                "status": match_status,
                "currentScore": current_score,
                "features": pit_features,
                "markets": [],
                "isBestOfDay": False,
                "validationStatus": "abstained",
                "stopReason": "MODEL_NO_OUTPUT",
                "abstentionDiagnostics": ["ABSTAIN: Ensemble produced zero valid probabilities."],
                "modelVersion": active_model,
                "metadata": model_meta,
            })
            continue

        ensemble_successful += 1

        # 4. Calibration & Confidence Calculation & Publication Validation
        calibrated_markets: List[Dict[str, Any]] = []
        abstention_reasons: List[str] = []
        market_stop_codes: List[str] = []

        raw_markets = ensemble_out["markets"]
        markets_generated += len(raw_markets)

        for raw_m in raw_markets:
            cal_details = get_calibration_details(
                raw_prob=raw_m["rawProbability"],
                sport=sport,
                model=active_model,
                market=raw_m.get("marketName", "default"),
                allow_baseline=(not is_subscriber_feed),
            )
            cal_prob = cal_details.get("calibrated_probability")
            cal_pct = cal_details.get("calibrated_percentage")
            conf = calculate_confidence(cal_prob if cal_prob is not None else raw_m["rawProbability"], pit_features, min_matches)

            if cal_details.get("is_calibrated") and cal_prob is not None:
                calibrated_count += 1
            if conf.get("score", 0.0) >= 0.50:
                confidence_passed_count += 1

            # Publication validation gate
            val = validate_market_publication(
                {
                    "marketName": raw_m["marketName"],
                    "selection": raw_m["selection"],
                    "rawProbability": raw_m["rawProbability"],
                    "calibratedProbability": cal_prob,
                    "calibratedPercentage": cal_pct,
                    "confidenceScore": conf["score"],
                    "hasSufficientData": True,
                    "marketCategory": raw_m["marketCategory"],
                    "isCalibrated": cal_details["is_calibrated"],
                    "calibrationStatus": cal_details["calibration_status"],
                    "calibrationId": cal_details["calibration_id"],
                    "isBaseline": cal_details["is_baseline"],
                },
                sport=sport,
                is_subscriber_feed=is_subscriber_feed,
                active_model=active_model,
            )

            if val["isPublishable"]:
                validation_passed_count += 1
            else:
                markets_rejected += 1
                m_stop = val.get("stopReason", "INVALID_MARKET")
                market_stop_codes.append(m_stop)
                if val.get("abstentionReason"):
                    abstention_reasons.append(val["abstentionReason"])

            calibrated_markets.append({
                "marketName": raw_m["marketName"],
                "selection": raw_m["selection"],
                "calibratedProbability": cal_prob,
                "calibratedPercentage": cal_pct,
                "confidenceScore": conf["score"],
                "confidenceRating": conf["rating"],
                "isPublishable": val["isPublishable"],
                "abstentionReason": val.get("abstentionReason"),
                "marketCategory": raw_m["marketCategory"],
            })

        valid_markets = filter_publishable_markets(calibrated_markets, is_subscriber_feed)
        is_val = len(valid_markets) > 0

        if not is_val:
            if any(code in ("UNCALIBRATED_ABSTAIN", "REJECTED_CALIBRATION", "CANDIDATE_NOT_PRODUCTION", "NO_VALIDATED_PRODUCTION_CALIBRATION") for code in market_stop_codes) or not market_stop_codes:
                fixture_stop_reason = "NO_VALIDATED_PRODUCTION_CALIBRATION"
            else:
                fixture_stop_reason = market_stop_codes[0]
        else:
            fixture_stop_reason = "VALIDATED"
        record_stop_reason(fixture_stop_reason)

        execution_results.append({
            "fixtureId": fix["id"],
            "sport": sport,
            "league": fix.get("league", ""),
            "homeTeam": home,
            "awayTeam": away,
            "kickoffUtc": cutoff,
            "status": match_status,
            "currentScore": current_score,
            "features": pit_features,
            "markets": valid_markets,
            "isBestOfDay": False,
            "validationStatus": "validated" if is_val else "abstained",
            "stopReason": fixture_stop_reason,
            "abstentionDiagnostics": None if is_val else abstention_reasons,
            "modelVersion": active_model,
            "metadata": model_meta,
            "modelMetadata": model_meta,
        })

    # 5. Filter strictly to validated fixtures only for the published feed
    validated_candidates = [r for r in execution_results if r.get("validationStatus") == "validated" and r.get("markets")]

    # 6. Ranking and Best of Day Selection (Strict maximum of 20 published predictions)
    ranked = rank_and_select_best_of_day(validated_candidates, max_results=20)
    published_ids = {r["fixtureId"] for r in ranked if r.get("markets")}

    # Update stop reasons from VALIDATED to PUBLISHED for items in publishedFeed
    for r in execution_results:
        if r.get("fixtureId") in published_ids and r.get("stopReason") == "VALIDATED":
            candidate_stop_reasons["VALIDATED"] = max(0, candidate_stop_reasons.get("VALIDATED", 1) - 1)
            record_stop_reason("PUBLISHED")
            r["stopReason"] = "PUBLISHED"

    # 7. Transform to publication schema
    published_feed: List[Dict[str, Any]] = []
    for r in ranked:
        if not r.get("markets"):
            continue

        market_items = []
        for idx, m in enumerate(r["markets"]):
            market_items.append({
                "id": f"{r['fixtureId']}-m{idx+1}",
                "marketName": m["marketName"],
                "selection": m["selection"],
                "probabilityPercentage": m["calibratedPercentage"],
                "confidenceRating": m["confidenceRating"],
                "sportSpecificCategory": m["marketCategory"],
                "isValidated": True,
            })

        sport_stats: Dict[str, Any] = {}
        if r["sport"] == "football":
            sport_stats["xGRecentHome"] = r["features"].get("homeRecentXgAvg")
            sport_stats["xGRecentAway"] = r["features"].get("awayRecentXgAvg")
            sport_stats["homeGoalsScoredAvg"] = r["features"].get("homeGoalsScoredAvg")
            sport_stats["awayGoalsScoredAvg"] = r["features"].get("awayGoalsScoredAvg")
            sport_stats["awayGoalsConcededAvg"] = r["features"].get("awayGoalsConcededAvg")
            sport_stats["homeCleanSheets"] = r["features"].get("homeCleanSheets")
            sport_stats["awayCleanSheets"] = r["features"].get("awayCleanSheets")
            sport_stats["avgMatchCorners"] = r["features"].get("avgMatchCorners")
            btts = r["features"].get("bothTeamsScoredRecentRate")
            sport_stats["bothTeamsScoredRecentRate"] = btts if btts is not None else r["features"].get("bttsRate")
        elif r["sport"] == "basketball":
            sport_stats["pace"] = r["features"].get("pace")
            sport_stats["homePPG"] = r["features"].get("homePPG")
            sport_stats["awayPPG"] = r["features"].get("awayPPG")
            sport_stats["reboundDifferential"] = r["features"].get("reboundDifferential")
        elif r["sport"] == "baseball":
            sport_stats["homeERA"] = r["features"].get("homeERA")
            sport_stats["awayERA"] = r["features"].get("awayERA")
            sport_stats["bullpenWHIP"] = r["features"].get("bullpenWHIP")
            sport_stats["battingAvg"] = r["features"].get("battingAvg")
        elif r["sport"] in {"hockey", "ice_hockey"}:
            sport_stats["homeGoalsScoredAvg"] = r["features"].get("homeGoalsScoredAvg")
            sport_stats["awayGoalsScoredAvg"] = r["features"].get("awayGoalsScoredAvg")
            sport_stats["homeGoalsConcededAvg"] = r["features"].get("homeGoalsConcededAvg")
            sport_stats["awayGoalsConcededAvg"] = r["features"].get("awayGoalsConcededAvg")
            sport_stats["homeShotsAvg"] = r["features"].get("homeShotsAvg")
            sport_stats["awayShotsAvg"] = r["features"].get("awayShotsAvg")
            sport_stats["homeRecentForm"] = r["features"].get("homeRecentForm")
            sport_stats["awayRecentForm"] = r["features"].get("awayRecentForm")
        elif r["sport"] in {"formula_1", "f1"}:
            drivers = r["features"].get("drivers", [])
            sport_stats["f1Drivers"] = drivers
            if drivers:
                top_d = drivers[0]
                sport_stats["gridPosition"] = top_d.get("recentQualifyingPosition")
                sport_stats["constructorStanding"] = top_d.get("constructorName")
                sport_stats["poleConversionRate"] = top_d.get("poleConversionRate")

        highest_m = market_items[0]
        meta = r.get("metadata") or {}
        first_m_raw = r["markets"][0].get("calibratedProbability", 0.5) if r.get("markets") else 0.5

        # Format event_date_lagos
        event_date_lagos = ""
        try:
            from datetime import timedelta
            lagos_tz = timezone(timedelta(hours=1))
            dt = datetime.fromisoformat(r["kickoffUtc"].replace("Z", "+00:00"))
            event_date_lagos = dt.astimezone(lagos_tz).strftime("%Y-%m-%d")
        except Exception:
            event_date_lagos = str(r["kickoffUtc"])[:10]

        published_feed.append({
            "id": r["fixtureId"],
            "fixture_id": r["fixtureId"],
            "sport": r["sport"],
            "league": r["league"],
            "event_date_lagos": event_date_lagos,
            "homeTeam": r["homeTeam"],
            "awayTeam": r["awayTeam"],
            "home_team": r["homeTeam"],
            "away_team": r["awayTeam"],
            "driver": r["homeTeam"] if r["sport"] in {"formula_1", "f1"} else None,
            "opponent": r["awayTeam"] if r["sport"] not in {"formula_1", "f1"} else None,
            "kickoffUtc": r["kickoffUtc"],
            "status": r["status"],
            "currentScore": r["currentScore"],
            "market": highest_m["marketName"],
            "selection": highest_m["selection"],
            "raw_probability": first_m_raw,
            "calibrated_probability": first_m_raw,
            "percentage": highest_m["probabilityPercentage"],
            "model": meta.get("model_name", r["modelVersion"]),
            "model_version": meta.get("model_version", "v1"),
            "training_match_count": meta.get("training_match_count", min_matches),
            "feature_timestamp": meta.get("feature_timestamp", cutoff),
            "prediction_timestamp": meta.get("prediction_timestamp", datetime.now(timezone.utc).isoformat()),
            "highestPercentagePrediction": {
                "marketName": highest_m["marketName"],
                "selection": highest_m["selection"],
                "percentage": highest_m["probabilityPercentage"],
            },
            "validatedMarkets": market_items,
            "sportStats": sport_stats,
            "validationStatus": "validated",
            "modelVersion": r["modelVersion"],
            "isBestOfDay": r.get("isBestOfDay", False),
            "modelMetadata": meta,
            "metadata": meta,
            "model_name": meta.get("model_name"),
            "training_window": meta.get("training_window"),
        })

    return {
        "modelVersion": active_model,
        "isProductionModel": is_production_model(active_model),
        "totalCandidates": len(candidates),
        "validatedCount": len(published_feed),
        "abstainedCount": len(candidates) - len(published_feed),
        "isSubscriberFeed": is_subscriber_feed,
        "publishedFeed": published_feed[:20],
        "allResults": execution_results,
        "diagnostics": {
            "fixturesEligible": fixtures_eligible,
            "fixturesEligibleForPrediction": fixtures_eligible,
            "fixturesRejectedInvalid": fixtures_rejected_invalid,
            "fixturesRejectedPast": fixtures_rejected_past,
            "fixturesRejectedCompleted": fixtures_rejected_completed,
            "fixturesInsufficientHistory": fixtures_insufficient_history,
            "fixturesModelExecuted": fixtures_model_executed,
            "eloSuccessful": elo_successful,
            "poissonSuccessful": poisson_successful,
            "ensembleSuccessful": ensemble_successful,
            "calibrated": calibrated_count,
            "confidencePassed": confidence_passed_count,
            "abstained": len(candidates) - len(published_feed),
            "validationPassed": validation_passed_count,
            "marketsGenerated": markets_generated,
            "marketsRejected": markets_rejected,
            "validatedFixtures": len(validated_candidates),
            "predictionsPublished": len(published_feed),
            "publishedPredictions": len(published_feed),
            "candidateStopReasons": candidate_stop_reasons,
        },
    }
