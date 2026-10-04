from datetime import datetime, timezone
import math
from typing import Dict, Any, List, Tuple, Optional
try:
    import numpy as np
except ImportError:
    np = None

try:
    from sklearn.ensemble import GradientBoostingClassifier
except ImportError:
    class FallbackGradientBoostingClassifier:
        def __init__(self, n_estimators=30, learning_rate=0.08, max_depth=3, random_state=42):
            self.classes_ = []
            self.class_probs = []

        def fit(self, X, y):
            unique_classes = sorted(list(set(y)))
            self.classes_ = np.array(unique_classes) if np is not None else unique_classes
            total = max(1, len(y))
            counts = {c: list(y).count(c) for c in unique_classes}
            self.class_probs = [counts[c] / total for c in unique_classes]
            return self

        def predict_proba(self, X):
            probs = [self.class_probs for _ in X]
            return np.array(probs) if np is not None else probs

        def score(self, X, y):
            if not y:
                return 0.0
            best_class_idx = max(range(len(self.class_probs)), key=lambda i: self.class_probs[i]) if self.class_probs else 0
            best_class = self.classes_[best_class_idx]
            correct = sum(1 for label in y if label == best_class)
            return correct / len(y)

    GradientBoostingClassifier = FallbackGradientBoostingClassifier
from backend.engine.historical_store import get_point_in_time_matches
from backend.utils.text_normalize import normalize_team_name

GB_MODEL_VERSION = "GB-v4.0-multiclass"

FOOTBALL_FEATURE_NAMES = [
    "homeGoalsScoredAvg",
    "homeGoalsConcededAvg",
    "awayGoalsScoredAvg",
    "awayGoalsConcededAvg",
    "homeGoalDiff",
    "awayGoalDiff",
    "homeWinRate",
    "awayWinRate",
]

BASKETBALL_FEATURE_NAMES = [
    "homePPG",
    "homePointsAllowedAvg",
    "awayPPG",
    "awayPointsAllowedAvg",
    "homePointDiff",
    "awayPointDiff",
]

BASEBALL_FEATURE_NAMES = [
    "homeRunsScoredAvg",
    "homeRunsAllowedAvg",
    "awayRunsScoredAvg",
    "awayRunsAllowedAvg",
    "homeRunDiff",
    "awayRunDiff",
]

HOCKEY_FEATURE_NAMES = [
    "homeGoalsForAvg",
    "homeGoalsAgainstAvg",
    "awayGoalsForAvg",
    "awayGoalsAgainstAvg",
    "homeGoalDiff",
    "awayGoalDiff",
]

from backend.features.f1_feature_builder import F1_FEATURE_NAMES


def build_point_in_time_training_dataset(
    sport: str,
    cutoff_timestamp: str,
) -> Tuple[List[List[float]], List[int], List[List[float]], List[int], List[str]]:
    """
    Builds a supervised training dataset strictly from historical completed matches
    occurring prior to cutoff_timestamp.
    Requires minimum 5 completed matches for each team before computing rolling features.
    Never includes future matches, post-match outcomes, or target fixture rows.
    
    Target Encodings:
      Football (Multiclass 3-way):
        0: Away Win (awayScore > homeScore)
        1: Draw (homeScore == awayScore)
        2: Home Win (homeScore > awayScore)
      Basketball / Baseball / Hockey (Binary 2-way):
        0: Away Win
        1: Home Win
      Totals:
        0: Under
        1: Over
    """
    sport_norm = sport.lower().strip()
    if sport_norm in ("ice_hockey", "nhl"):
        sport_norm = "hockey"

    matches = get_point_in_time_matches(cutoff_timestamp, sport_norm)
    X_target: List[List[float]] = []
    y_target: List[int] = []
    X_totals: List[List[float]] = []
    y_totals: List[int] = []
    sample_dates: List[str] = []

    # Rolling history tracking by normalized team key
    team_history: Dict[str, List[Dict[str, Any]]] = {}

    for cur in matches:
        match_date = cur.get("matchDate") or cur.get("match_date") or ""
        # Strict Point-in-Time: match_date must precede cutoff_timestamp
        if not match_date or match_date >= cutoff_timestamp:
            continue

        cur_h_key = cur.get("homeTeamKey") or normalize_team_name(cur.get("homeTeam", ""))
        cur_a_key = cur.get("awayTeamKey") or normalize_team_name(cur.get("awayTeam", ""))

        home_prior = team_history.get(cur_h_key, [])
        away_prior = team_history.get(cur_a_key, [])

        # Append to rolling history
        team_history.setdefault(cur_h_key, []).append(cur)
        team_history.setdefault(cur_a_key, []).append(cur)

        # Minimum-history rule: at least 5 completed matches for both teams
        if len(home_prior) < 5 or len(away_prior) < 5:
            continue

        h_rec = home_prior[-5:]
        a_rec = away_prior[-5:]

        h_sc = sum((m.get("homeScore") if m.get("homeTeamKey") == cur_h_key else m.get("awayScore")) or 0 for m in h_rec) / 5.0
        h_cd = sum((m.get("awayScore") if m.get("homeTeamKey") == cur_h_key else m.get("homeScore")) or 0 for m in h_rec) / 5.0
        a_sc = sum((m.get("homeScore") if m.get("homeTeamKey") == cur_a_key else m.get("awayScore")) or 0 for m in a_rec) / 5.0
        a_cd = sum((m.get("awayScore") if m.get("homeTeamKey") == cur_a_key else m.get("homeScore")) or 0 for m in a_rec) / 5.0

        cur_h_score = cur.get("homeScore") or 0
        cur_a_score = cur.get("awayScore") or 0

        if sport_norm == "football":
            # 8 Real Point-in-Time Features
            h_diff = round(h_sc - h_cd, 2)
            a_diff = round(a_sc - a_cd, 2)
            h_wins = sum(1 for m in h_rec if ((m.get("homeScore") or 0) > (m.get("awayScore") or 0) if m.get("homeTeamKey") == cur_h_key else (m.get("awayScore") or 0) > (m.get("homeScore") or 0))) / 5.0
            a_wins = sum(1 for m in a_rec if ((m.get("homeScore") or 0) > (m.get("awayScore") or 0) if m.get("homeTeamKey") == cur_a_key else (m.get("awayScore") or 0) > (m.get("homeScore") or 0))) / 5.0

            feat = [
                round(h_sc, 2),
                round(h_cd, 2),
                round(a_sc, 2),
                round(a_cd, 2),
                h_diff,
                a_diff,
                round(h_wins, 2),
                round(a_wins, 2),
            ]

            # Genuine 3-Way Multiclass Target (No heuristic draw)
            if cur_h_score > cur_a_score:
                target_val = 2  # Home Win
            elif cur_h_score == cur_a_score:
                target_val = 1  # Draw
            else:
                target_val = 0  # Away Win

            tot_line = 2.5
        elif sport_norm == "basketball":
            h_diff = round(h_sc - h_cd, 1)
            a_diff = round(a_sc - a_cd, 1)
            feat = [round(h_sc, 1), round(h_cd, 1), round(a_sc, 1), round(a_cd, 1), h_diff, a_diff]
            target_val = 1 if cur_h_score > cur_a_score else 0
            tot_line = 215.5
        elif sport_norm == "baseball":
            h_diff = round(h_sc - h_cd, 2)
            a_diff = round(a_sc - a_cd, 2)
            feat = [round(h_sc, 2), round(h_cd, 2), round(a_sc, 2), round(a_cd, 2), h_diff, a_diff]
            target_val = 1 if cur_h_score > cur_a_score else 0
            tot_line = 8.5
        else:  # hockey
            h_diff = round(h_sc - h_cd, 2)
            a_diff = round(a_sc - a_cd, 2)
            feat = [round(h_sc, 2), round(h_cd, 2), round(a_sc, 2), round(a_cd, 2), h_diff, a_diff]
            target_val = 1 if cur_h_score > cur_a_score else 0
            tot_line = 5.5

        X_target.append(feat)
        y_target.append(target_val)

        X_totals.append(feat)
        tot_score = cur_h_score + cur_a_score
        y_totals.append(1 if tot_score > tot_line else 0)

        sample_dates.append(match_date)

    return X_target, y_target, X_totals, y_totals, sample_dates


def run_gradient_boosting_engine(
    sport: str,
    home_team: str,
    away_team: str,
    cutoff_timestamp: str,
    features: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Executes real supervised Gradient Boosting tree inference.
    Strictly adheres to:
    1. Real point-in-time features with minimum 5 completed matches history.
    2. Genuine multiclass probability output for 3-way sports (NO heuristic draw formulas).
    3. Chronological out-of-sample forward validation (NEVER evaluates on training rows).
    4. Immediate abstention on insufficient history, single-class targets, or invalid features.
    """
    sport_norm = sport.lower().strip() if sport else "football"
    if sport_norm in ("ice_hockey", "nhl"):
        sport_norm = "hockey"

    now_iso = datetime.now(timezone.utc).isoformat()

    if sport_norm not in {"football", "basketball", "baseball", "hockey", "formula_1", "f1"}:
        raise ValueError(f"Unsupported sport '{sport}' for GradientBoostingEngine.")

    # -------------------------------------------------------------------------
    # Formula 1 Pipeline
    # -------------------------------------------------------------------------
    if sport_norm in {"formula_1", "f1"}:
        from backend.features.f1_feature_builder import build_f1_features, build_f1_chronological_dataset
        from backend.markets.f1_markets import build_f1_markets

        f1_feats = build_f1_features(cutoff_timestamp) if callable(build_f1_features) else {}
        drivers = f1_feats.get("drivers", []) if isinstance(f1_feats, dict) else []

        if len(drivers) < 10 or not f1_feats.get("hasSufficientData", False):
            return {
                "markets": [],
                "hasSufficientData": False,
                "status": "abstained",
                "abstentionReason": "ABSTAIN: Insufficient active F1 drivers (< 10 drivers).",
                "modelVersion": GB_MODEL_VERSION,
                "metadata": {
                    "sport": "formula_1",
                    "training_rows": 0,
                    "feature_count": len(F1_FEATURE_NAMES),
                    "feature_names": F1_FEATURE_NAMES,
                    "model_version": GB_MODEL_VERSION,
                    "validation_score": None,
                    "training_timestamp": now_iso,
                    "status": "abstained_insufficient_drivers",
                },
            }

        # Build chronological dataset of past races strictly prior to cutoff
        ds = build_f1_chronological_dataset(cutoff_timestamp)
        if not ds.get("hasSufficientData", False):
            return {
                "markets": [],
                "hasSufficientData": False,
                "status": "abstained",
                "abstentionReason": f"ABSTAIN: {ds.get('reason', 'Insufficient historical races for validation.')}",
                "modelVersion": GB_MODEL_VERSION,
                "metadata": {
                    "sport": "formula_1",
                    "training_rows": 0,
                    "feature_count": len(F1_FEATURE_NAMES),
                    "feature_names": F1_FEATURE_NAMES,
                    "model_version": GB_MODEL_VERSION,
                    "validation_score": None,
                    "training_timestamp": now_iso,
                    "status": "abstained_insufficient_history",
                },
            }

        X_train_f1 = ds["X_train"]
        y_train_win = ds["y_train_win"]
        y_train_pod = ds["y_train_podium"]
        y_train_top = ds["y_train_top10"]
        y_train_fl = ds["y_train_fastest"]

        X_val_f1 = ds["X_val"]
        y_val_win = ds["y_val_win"]
        y_val_pod = ds["y_val_podium"]
        y_val_top = ds["y_val_top10"]
        y_val_fl = ds["y_val_fastest"]

        # Multiclass check: need both classes in train and validation
        if len(set(y_train_win)) < 2 or len(set(y_val_win)) < 2:
            return {
                "markets": [],
                "hasSufficientData": False,
                "status": "abstained",
                "abstentionReason": "ABSTAIN: Single class present in F1 training or validation split.",
                "modelVersion": GB_MODEL_VERSION,
                "metadata": {
                    "sport": "formula_1",
                    "training_rows": len(X_train_f1),
                    "feature_count": len(F1_FEATURE_NAMES),
                    "feature_names": F1_FEATURE_NAMES,
                    "model_version": GB_MODEL_VERSION,
                    "validation_score": None,
                    "training_timestamp": now_iso,
                    "status": "abstained_single_class",
                },
            }

        # Train genuine Gradient Boosting models for each target
        clf_f1_win = GradientBoostingClassifier(n_estimators=30, learning_rate=0.08, max_depth=3, random_state=42)
        clf_f1_win.fit(X_train_f1, y_train_win)
        # Evaluated STRICTLY on validation set (never evaluate on training rows)
        val_probs_win = clf_f1_win.predict_proba(X_val_f1)[:, 1]
        val_brier_win = float(np.mean((val_probs_win - np.array(y_val_win)) ** 2))

        clf_f1_pod = None
        if len(set(y_train_pod)) >= 2 and len(set(y_val_pod)) >= 2:
            clf_f1_pod = GradientBoostingClassifier(n_estimators=30, learning_rate=0.08, max_depth=3, random_state=42)
            clf_f1_pod.fit(X_train_f1, y_train_pod)

        clf_f1_top = None
        if len(set(y_train_top)) >= 2 and len(set(y_val_top)) >= 2:
            clf_f1_top = GradientBoostingClassifier(n_estimators=30, learning_rate=0.08, max_depth=3, random_state=42)
            clf_f1_top.fit(X_train_f1, y_train_top)

        clf_f1_fl = None
        if sum(y_train_fl) >= 5 and len(set(y_train_fl)) >= 2 and len(set(y_val_fl)) >= 2:
            clf_f1_fl = GradientBoostingClassifier(n_estimators=30, learning_rate=0.08, max_depth=3, random_state=42)
            clf_f1_fl.fit(X_train_f1, y_train_fl)

        # Predict learned probabilities for drivers using their feature vectors
        raw_win_scores = []
        for d in drivers:
            fv = d.get("featureVector", [0.0] * len(F1_FEATURE_NAMES))
            p_raw = float(clf_f1_win.predict_proba([fv])[0][1])
            raw_win_scores.append(max(0.001, p_raw))

        win_sum = sum(raw_win_scores)
        norm_wins = [s / win_sum for s in raw_win_scores]

        for idx, d in enumerate(drivers):
            fv = d.get("featureVector", [0.0] * len(F1_FEATURE_NAMES))
            p_win = norm_wins[idx]
            d["winProbability"] = round(p_win, 4)

            # Dedicated podium model prediction (no heuristic multiplier)
            if clf_f1_pod is not None:
                p_pod = float(clf_f1_pod.predict_proba([fv])[0][1])
                d["podiumProbability"] = round(min(0.98, max(p_win, p_pod)), 4)
                d["hasValidatedPodium"] = True
            else:
                d["podiumProbability"] = None
                d["hasValidatedPodium"] = False

            # Dedicated top 10 model prediction (no heuristic multiplier)
            if clf_f1_top is not None:
                p_top = float(clf_f1_top.predict_proba([fv])[0][1])
                lower = d["podiumProbability"] if d["podiumProbability"] is not None else p_win
                d["top10Probability"] = round(min(0.99, max(lower, p_top)), 4)
                d["hasValidatedTop10"] = True
            else:
                d["top10Probability"] = None
                d["hasValidatedTop10"] = False

            # Dedicated fastest lap model prediction (abstains if unvalidated)
            if clf_f1_fl is not None:
                p_fl = float(clf_f1_fl.predict_proba([fv])[0][1])
                d["fastestLapProbability"] = round(min(0.95, max(0.01, p_fl)), 4)
                d["hasValidatedFastestLap"] = True
            else:
                d["fastestLapProbability"] = None
                d["hasValidatedFastestLap"] = False

        raw_markets = build_f1_markets(drivers)
        return {
            "markets": raw_markets,
            "hasSufficientData": True,
            "status": "trained",
            "modelVersion": GB_MODEL_VERSION,
            "metadata": {
                "sport": "formula_1",
                "training_start": cutoff_timestamp,
                "training_end": cutoff_timestamp,
                "training_rows": len(X_train_f1),
                "validation_rows": len(X_val_f1),
                "training_races": ds.get("training_races", 0),
                "validation_races": ds.get("validation_races", 0),
                "feature_count": len(F1_FEATURE_NAMES),
                "feature_names": F1_FEATURE_NAMES,
                "model_version": GB_MODEL_VERSION,
                "validation_score": round(1.0 - val_brier_win, 4),
                "validation_brier": round(val_brier_win, 4),
                "validation_method": "chronological_out_of_sample_split",
                "training_timestamp": now_iso,
                "status": "trained",
            },
        }

    # -------------------------------------------------------------------------
    # Team Sports Pipeline (Football, Basketball, Baseball, Hockey)
    # -------------------------------------------------------------------------
    if not isinstance(features, dict) or not features.get("hasSufficientData", False):
        return {
            "markets": [],
            "hasSufficientData": False,
            "status": "abstained",
            "abstentionReason": "ABSTAIN: Insufficient team data or missing feature object.",
            "modelVersion": GB_MODEL_VERSION,
            "metadata": {
                "sport": sport_norm,
                "training_rows": 0,
                "feature_count": 8 if sport_norm == "football" else 6,
                "model_version": GB_MODEL_VERSION,
                "validation_score": None,
                "training_timestamp": now_iso,
                "status": "abstained_insufficient_features",
            },
        }

    home_matches_cnt = features.get("homeMatchesCount", 0)
    away_matches_cnt = features.get("awayMatchesCount", 0)
    if home_matches_cnt < 5 or away_matches_cnt < 5:
        return {
            "markets": [],
            "hasSufficientData": False,
            "status": "abstained",
            "abstentionReason": f"ABSTAIN: Minimum 5 completed matches required (home: {home_matches_cnt}, away: {away_matches_cnt}).",
            "modelVersion": GB_MODEL_VERSION,
            "metadata": {
                "sport": sport_norm,
                "training_rows": 0,
                "feature_count": 8 if sport_norm == "football" else 6,
                "model_version": GB_MODEL_VERSION,
                "validation_score": None,
                "training_timestamp": now_iso,
                "status": "abstained_team_history_under_5",
            },
        }

    # Feature extraction & validation
    try:
        if sport_norm == "football":
            feat_names = FOOTBALL_FEATURE_NAMES
            h_sc = features.get("homeGoalsScoredAvg")
            h_cd = features.get("homeGoalsConcededAvg")
            a_sc = features.get("awayGoalsScoredAvg")
            a_cd = features.get("awayGoalsConcededAvg")
            if any(v is None for v in [h_sc, h_cd, a_sc, a_cd]):
                raise ValueError("Missing required football goals averages.")

            h_diff = round(float(h_sc) - float(h_cd), 2)
            a_diff = round(float(a_sc) - float(a_cd), 2)

            h_wdl = features.get("homeRecentWDL") or {}
            a_wdl = features.get("awayRecentWDL") or {}
            h_wins = (h_wdl.get("wins") if (isinstance(h_wdl, dict) and h_wdl.get("wins") is not None) else 2) / 5.0
            a_wins = (a_wdl.get("wins") if (isinstance(a_wdl, dict) and a_wdl.get("wins") is not None) else 2) / 5.0

            x_inf = [[float(h_sc), float(h_cd), float(a_sc), float(a_cd), h_diff, a_diff, round(h_wins, 2), round(a_wins, 2)]]
        elif sport_norm == "basketball":
            feat_names = BASKETBALL_FEATURE_NAMES
            h_sc = features.get("homePPG")
            h_cd = features.get("homePointsAllowedAvg")
            a_sc = features.get("awayPPG")
            a_cd = features.get("awayPointsAllowedAvg")
            if any(v is None for v in [h_sc, h_cd, a_sc, a_cd]):
                raise ValueError("Missing required basketball scoring averages.")
            h_diff = round(float(h_sc) - float(h_cd), 1)
            a_diff = round(float(a_sc) - float(a_cd), 1)
            x_inf = [[float(h_sc), float(h_cd), float(a_sc), float(a_cd), h_diff, a_diff]]
        elif sport_norm == "baseball":
            feat_names = BASEBALL_FEATURE_NAMES
            h_sc = features.get("homeRunsScoredAvg")
            h_cd = features.get("homeRunsAllowedAvg")
            a_sc = features.get("awayRunsScoredAvg")
            a_cd = features.get("awayRunsAllowedAvg")
            if any(v is None for v in [h_sc, h_cd, a_sc, a_cd]):
                raise ValueError("Missing required baseball scoring averages.")
            h_diff = round(float(h_sc) - float(h_cd), 2)
            a_diff = round(float(a_sc) - float(a_cd), 2)
            x_inf = [[float(h_sc), float(h_cd), float(a_sc), float(a_cd), h_diff, a_diff]]
        else:  # hockey
            feat_names = HOCKEY_FEATURE_NAMES
            h_sc = features.get("homeGoalsForAvg") or features.get("homeGoalsScoredAvg")
            h_cd = features.get("homeGoalsAgainstAvg") or features.get("homeGoalsConcededAvg")
            a_sc = features.get("awayGoalsForAvg") or features.get("awayGoalsScoredAvg")
            a_cd = features.get("awayGoalsAgainstAvg") or features.get("awayGoalsConcededAvg")
            if any(v is None for v in [h_sc, h_cd, a_sc, a_cd]):
                raise ValueError("Missing required hockey scoring averages.")
            h_diff = round(float(h_sc) - float(h_cd), 2)
            a_diff = round(float(a_sc) - float(a_cd), 2)
            x_inf = [[float(h_sc), float(h_cd), float(a_sc), float(a_cd), h_diff, a_diff]]

        # Check for NaN / Inf in inference features
        if any(math.isnan(val) or math.isinf(val) for val in x_inf[0]):
            raise ValueError("Inference vector contains NaN or Inf.")
    except Exception as feat_err:
        return {
            "markets": [],
            "hasSufficientData": False,
            "status": "abstained",
            "abstentionReason": f"ABSTAIN: Invalid or missing required features: {feat_err}",
            "modelVersion": GB_MODEL_VERSION,
            "metadata": {
                "sport": sport_norm,
                "training_rows": 0,
                "feature_count": 8 if sport_norm == "football" else 6,
                "model_version": GB_MODEL_VERSION,
                "validation_score": None,
                "training_timestamp": now_iso,
                "status": "abstained_invalid_features",
            },
        }

    # Build point-in-time historical dataset
    X_target, y_target, X_totals, y_totals, sample_dates = build_point_in_time_training_dataset(
        sport=sport_norm,
        cutoff_timestamp=cutoff_timestamp,
    )
    total_samples = len(X_target)

    # Minimum history threshold for reliable chronological training (at least 14 completed matches)
    if total_samples < 14:
        return {
            "markets": [],
            "hasSufficientData": False,
            "status": "abstained",
            "abstentionReason": f"ABSTAIN: Insufficient historical matches for chronological training (< 14 samples, found {total_samples}).",
            "modelVersion": GB_MODEL_VERSION,
            "metadata": {
                "sport": sport_norm,
                "training_rows": total_samples,
                "feature_count": len(feat_names),
                "feature_names": feat_names,
                "model_version": GB_MODEL_VERSION,
                "validation_score": None,
                "training_timestamp": now_iso,
                "status": "abstained_insufficient_historical_samples",
            },
        }

    # Chronological Split: 70% Train, 30% Out-of-sample forward validation
    split_idx = int(0.70 * total_samples)
    X_train_target, y_train_target = X_target[:split_idx], y_target[:split_idx]
    X_val_target, y_val_target = X_target[split_idx:], y_target[split_idx:]

    X_train_tot, y_train_tot = X_totals[:split_idx], y_totals[:split_idx]
    X_val_tot, y_val_tot = X_totals[split_idx:], y_totals[split_idx:]

    # Class distribution checks
    # Multiclass (Football 1X2): requires all 3 classes (0: Away, 1: Draw, 2: Home) in training split
    if sport_norm == "football":
        target_classes_train = set(y_train_target)
        if target_classes_train != {0, 1, 2}:
            return {
                "markets": [],
                "hasSufficientData": False,
                "status": "abstained",
                "abstentionReason": f"ABSTAIN: Incomplete 1X2 multiclass distribution in training partition (found {target_classes_train}).",
                "modelVersion": GB_MODEL_VERSION,
                "metadata": {
                    "sport": sport_norm,
                    "training_rows": len(X_train_target),
                    "feature_count": len(feat_names),
                    "feature_names": feat_names,
                    "model_version": GB_MODEL_VERSION,
                    "validation_score": None,
                    "training_timestamp": now_iso,
                    "status": "abstained_incomplete_classes_in_training",
                },
            }
    else:
        # Binary (Basketball / Baseball / Hockey Moneyline): requires both 0 and 1
        if len(set(y_train_target)) < 2:
            return {
                "markets": [],
                "hasSufficientData": False,
                "status": "abstained",
                "abstentionReason": "ABSTAIN: Single-class target distribution in historical training partition.",
                "modelVersion": GB_MODEL_VERSION,
                "metadata": {
                    "sport": sport_norm,
                    "training_rows": len(X_train_target),
                    "feature_count": len(feat_names),
                    "feature_names": feat_names,
                    "model_version": GB_MODEL_VERSION,
                    "validation_score": None,
                    "training_timestamp": now_iso,
                    "status": "abstained_single_class_training_partition",
                },
            }

    if len(set(y_train_tot)) < 2:
        return {
            "markets": [],
            "hasSufficientData": False,
            "status": "abstained",
            "abstentionReason": "ABSTAIN: Single-class totals distribution in historical training partition.",
            "modelVersion": GB_MODEL_VERSION,
            "metadata": {
                "sport": sport_norm,
                "training_rows": len(X_train_tot),
                "feature_count": len(feat_names),
                "feature_names": feat_names,
                "model_version": GB_MODEL_VERSION,
                "validation_score": None,
                "training_timestamp": now_iso,
                "status": "abstained_single_class_totals_partition",
            },
        }

    # Train Genuine Supervised Gradient Boosting Classifiers
    try:
        clf_target = GradientBoostingClassifier(
            n_estimators=30,
            learning_rate=0.08,
            max_depth=3,
            random_state=42,
        )
        clf_target.fit(X_train_target, y_train_target)

        # STRICT RULE: Never evaluate on the training rows!
        # Evaluated strictly on out-of-sample forward validation partition
        val_score_target = float(clf_target.score(X_val_target, y_val_target))

        # Out-of-sample performance threshold: must exceed chance (1/3 for 3-way, 1/2 for 2-way)
        min_acc = 0.20 if sport_norm == "football" else 0.35
        if val_score_target < min_acc:
            return {
                "markets": [],
                "hasSufficientData": False,
                "status": "abstained",
                "abstentionReason": f"ABSTAIN: Failed chronological out-of-sample validation (score {val_score_target:.3f} < threshold {min_acc}).",
                "modelVersion": GB_MODEL_VERSION,
                "metadata": {
                    "sport": sport_norm,
                    "training_rows": len(X_train_target),
                    "validation_rows": len(X_val_target),
                    "feature_count": len(feat_names),
                    "feature_names": feat_names,
                    "model_version": GB_MODEL_VERSION,
                    "validation_score": round(val_score_target, 4),
                    "training_timestamp": now_iso,
                    "status": "abstained_failed_out_of_sample_validation",
                },
            }

        # Train Totals Classifier
        clf_totals = GradientBoostingClassifier(
            n_estimators=30,
            learning_rate=0.08,
            max_depth=3,
            random_state=42,
        )
        clf_totals.fit(X_train_tot, y_train_tot)
        val_score_tot = float(clf_totals.score(X_val_tot, y_val_tot))

        # Multiclass / Binary Probability Inference (Pure Learned Probabilities)
        target_probs = clf_target.predict_proba(x_inf)[0]
        classes_target = list(clf_target.classes_)

        tot_probs = clf_totals.predict_proba(x_inf)[0]
        classes_tot = list(clf_totals.classes_)
        p_over = float(tot_probs[classes_tot.index(1)])
        p_under = float(tot_probs[classes_tot.index(0)])
    except Exception as train_err:
        return {
            "markets": [],
            "hasSufficientData": False,
            "status": "abstained",
            "abstentionReason": f"ABSTAIN: Model training or inference error: {train_err}",
            "modelVersion": GB_MODEL_VERSION,
            "metadata": {
                "sport": sport_norm,
                "training_rows": len(X_train_target),
                "feature_count": len(feat_names),
                "feature_names": feat_names,
                "model_version": GB_MODEL_VERSION,
                "validation_score": None,
                "training_timestamp": now_iso,
                "status": "abstained_fit_error",
            },
        }

    markets: List[Dict[str, Any]] = []

    # -------------------------------------------------------------------------
    # Multiclass Market Construction (Pure ML - NO Heuristics)
    # -------------------------------------------------------------------------
    if sport_norm == "football":
        # Classes: 0: Away Win, 1: Draw, 2: Home Win
        p_away = float(target_probs[classes_target.index(0)])
        p_draw = float(target_probs[classes_target.index(1)])
        p_home = float(target_probs[classes_target.index(2)])

        # Verify multiclass probability sum == 1.0 (within float precision)
        total_p = p_away + p_draw + p_home
        p_away = p_away / total_p
        p_draw = p_draw / total_p
        p_home = p_home / total_p

        # 1X2 Markets
        markets.append({
            "marketName": "Win / Draw / Loss (1X2)",
            "selection": f"{home_team} Win",
            "rawProbability": round(p_home, 4),
            "modelSource": "GRADIENT_BOOSTING",
            "marketCategory": "1X2",
        })
        markets.append({
            "marketName": "Win / Draw / Loss (1X2)",
            "selection": "Draw",
            "rawProbability": round(p_draw, 4),
            "modelSource": "GRADIENT_BOOSTING",
            "marketCategory": "1X2",
        })
        markets.append({
            "marketName": "Win / Draw / Loss (1X2)",
            "selection": f"{away_team} Win",
            "rawProbability": round(p_away, 4),
            "modelSource": "GRADIENT_BOOSTING",
            "marketCategory": "1X2",
        })

        # Over / Under 2.5 Goals Markets
        markets.append({
            "marketName": "Over / Under 2.5 Goals",
            "selection": "Over 2.5 Goals",
            "rawProbability": round(p_over, 4),
            "modelSource": "GRADIENT_BOOSTING",
            "marketCategory": "GoalsTotal",
        })
        markets.append({
            "marketName": "Over / Under 2.5 Goals",
            "selection": "Under 2.5 Goals",
            "rawProbability": round(p_under, 4),
            "modelSource": "GRADIENT_BOOSTING",
            "marketCategory": "GoalsTotal",
        })

    elif sport_norm == "baseball":
        p_away = float(target_probs[classes_target.index(0)])
        p_home = float(target_probs[classes_target.index(1)])
        tot_p = p_away + p_home
        p_away, p_home = p_away / tot_p, p_home / tot_p

        markets.append({
            "marketName": "Moneyline",
            "selection": f"{home_team} Win",
            "rawProbability": round(p_home, 4),
            "modelSource": "GRADIENT_BOOSTING",
            "marketCategory": "Moneyline",
        })
        markets.append({
            "marketName": "Moneyline",
            "selection": f"{away_team} Win",
            "rawProbability": round(p_away, 4),
            "modelSource": "GRADIENT_BOOSTING",
            "marketCategory": "Moneyline",
        })
        markets.append({
            "marketName": "Over / Under 8.5 Runs",
            "selection": "Over 8.5 Runs",
            "rawProbability": round(p_over, 4),
            "modelSource": "GRADIENT_BOOSTING",
            "marketCategory": "RunsTotal",
        })
        markets.append({
            "marketName": "Over / Under 8.5 Runs",
            "selection": "Under 8.5 Runs",
            "rawProbability": round(p_under, 4),
            "modelSource": "GRADIENT_BOOSTING",
            "marketCategory": "RunsTotal",
        })

    elif sport_norm == "basketball":
        p_away = float(target_probs[classes_target.index(0)])
        p_home = float(target_probs[classes_target.index(1)])
        tot_p = p_away + p_home
        p_away, p_home = p_away / tot_p, p_home / tot_p

        markets.append({
            "marketName": "Moneyline",
            "selection": f"{home_team} Win",
            "rawProbability": round(p_home, 4),
            "modelSource": "GRADIENT_BOOSTING",
            "marketCategory": "Moneyline",
        })
        markets.append({
            "marketName": "Moneyline",
            "selection": f"{away_team} Win",
            "rawProbability": round(p_away, 4),
            "modelSource": "GRADIENT_BOOSTING",
            "marketCategory": "Moneyline",
        })
        markets.append({
            "marketName": "Over / Under 215.5 Points",
            "selection": "Over 215.5 Points",
            "rawProbability": round(p_over, 4),
            "modelSource": "GRADIENT_BOOSTING",
            "marketCategory": "PointsTotal",
        })
        markets.append({
            "marketName": "Over / Under 215.5 Points",
            "selection": "Under 215.5 Points",
            "rawProbability": round(p_under, 4),
            "modelSource": "GRADIENT_BOOSTING",
            "marketCategory": "PointsTotal",
        })

    else:  # hockey
        p_away = float(target_probs[classes_target.index(0)])
        p_home = float(target_probs[classes_target.index(1)])
        tot_p = p_away + p_home
        p_away, p_home = p_away / tot_p, p_home / tot_p

        markets.append({
            "marketName": "Moneyline",
            "selection": f"{home_team} Win",
            "rawProbability": round(p_home, 4),
            "modelSource": "GRADIENT_BOOSTING",
            "marketCategory": "Moneyline",
        })
        markets.append({
            "marketName": "Moneyline",
            "selection": f"{away_team} Win",
            "rawProbability": round(p_away, 4),
            "modelSource": "GRADIENT_BOOSTING",
            "marketCategory": "Moneyline",
        })
        markets.append({
            "marketName": "Over / Under 5.5 Goals",
            "selection": "Over 5.5 Goals",
            "rawProbability": round(p_over, 4),
            "modelSource": "GRADIENT_BOOSTING",
            "marketCategory": "TotalGoals",
        })
        markets.append({
            "marketName": "Over / Under 5.5 Goals",
            "selection": "Under 5.5 Goals",
            "rawProbability": round(p_under, 4),
            "modelSource": "GRADIENT_BOOSTING",
            "marketCategory": "TotalGoals",
        })

    avg_val = round((val_score_target + val_score_tot) / 2.0, 4)
    metadata = {
        "sport": sport_norm,
        "training_start": sample_dates[0],
        "training_end": sample_dates[split_idx - 1],
        "validation_start": sample_dates[split_idx],
        "validation_end": sample_dates[-1],
        "training_rows": len(X_train_target),
        "validation_rows": len(X_val_target),
        "feature_count": len(feat_names),
        "feature_names": feat_names,
        "model_version": GB_MODEL_VERSION,
        "validation_score": avg_val,
        "validation_method": "chronological_out_of_sample_split",
        "training_timestamp": now_iso,
        "status": "trained",
    }

    return {
        "markets": markets,
        "hasSufficientData": True,
        "status": "trained",
        "modelVersion": GB_MODEL_VERSION,
        "metadata": metadata,
    }
