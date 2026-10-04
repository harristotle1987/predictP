import os
import math
from typing import Dict, Any, List, Optional, Tuple
from backend.db.duckdb_engine import duckdb_engine
from backend.config import settings

F1_FEATURE_NAMES = [
    "qualifying_avg_grid",        # qualifying performance: avg historical grid position
    "recent_qualifying_position", # qualifying performance: most recent qualifying/grid position
    "recent_avg_finish",          # recent race performance: avg finish over last 5 races
    "last_finish_position",       # recent race performance: finish position in last race
    "driver_recent_points_avg",   # driver form: avg points scored per race in last 5 races
    "driver_recent_podium_rate",  # driver form: fraction of last 5 races finishing on podium
    "driver_recent_win_rate",     # driver form: fraction of last 5 races winning
    "driver_recent_top10_rate",   # driver form: fraction of last 5 races finishing in top 10
    "constructor_avg_finish",     # constructor performance: constructor avg finish position
    "constructor_avg_qual",       # constructor performance: constructor avg grid position
    "circuit_avg_finish",         # circuit history: driver avg finish at this circuit where valid
    "circuit_experience",         # circuit history: number of prior starts at this circuit
    "grid_position",              # starting grid position for upcoming race
    "driver_dnf_rate",            # reliability: driver DNF rate across prior races
    "constructor_dnf_rate",       # reliability: constructor DNF rate across prior races
    "weather_condition",          # weather: 0.0 for dry/normal, 1.0 for wet where legitimately available
    "season_progression",         # season progression: round fraction (e.g. round / 24.0)
]

DNF_KEYWORDS = {
    "retired", "accident", "collision", "engine", "gearbox", "suspension",
    "hydraulics", "brakes", "power unit", "dnf", "damage", "spins off",
    "electrical", "mechanical", "transmission", "overheating", "puncture",
}

def is_dnf_status(status_str: Optional[str]) -> bool:
    if not status_str:
        return False
    s = str(status_str).lower().strip()
    return any(k in s for k in DNF_KEYWORDS)

def _ensure_f1_duckdb_view() -> bool:
    view_name = "f1_results"
    if view_name in duckdb_engine._registered_views:
        return True
    cache_dir = settings.parquet_cache_dir
    local_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "parquet_cache")
    possible_paths = [
        os.path.join(cache_dir, "formula_1_v1_history.parquet"),
        os.path.join(cache_dir, "f1_v1_history.parquet"),
        os.path.join(local_dir, "formula_1_v1_history.parquet"),
        os.path.join(local_dir, "f1_v1_history.parquet"),
    ]
    p = next((path for path in possible_paths if os.path.exists(path)), None)
    if p:
        duckdb_engine.register_parquet_view(view_name, p)
        duckdb_engine.register_parquet_view("f1_matches", p)
        duckdb_engine.register_parquet_view("formula_1_matches", p)
        return True
    return False

def build_f1_features(cutoff_timestamp: str, circuit_id: str = "all") -> Dict[str, Any]:
    """
    Computes point-in-time F1 features for drivers, constructors, and circuits.
    Guarantees zero future-looking data leakage by strictly filtering match_date < cutoff_timestamp.
    Uses real point-in-time historical data.
    """
    has_f1 = _ensure_f1_duckdb_view()
    if not has_f1:
        return {
            "drivers": [],
            "constructors": [],
            "circuit_stats": {},
            "hasSufficientData": False,
        }

    # Query all historical results prior to cutoff
    query = """
    SELECT *
    FROM f1_results
    WHERE match_date < ?
    ORDER BY match_date DESC
    """
    try:
        rel = duckdb_engine.con.execute(query, [cutoff_timestamp])
        cols = [desc[0] for desc in rel.description] if rel.description else []
        rows = [dict(zip(cols, r)) for r in rel.fetchall()]
    except Exception as e:
        print(f"[F1FeatureBuilder] Query error: {e}")
        rows = []

    # Rule: minimum historical observation threshold (at least 3 completed races, ~60 driver-race rows)
    if len(rows) < 40:
        return {
            "drivers": [],
            "constructors": [],
            "circuit_stats": {},
            "hasSufficientData": False,
        }

    # Group by driver, constructor, and circuit
    driver_stats: Dict[str, List[Dict[str, Any]]] = {}
    constructor_stats: Dict[str, List[Dict[str, Any]]] = {}
    circuit_driver_stats: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    for r in rows:
        d_id = r["driver_id"]
        c_id = r["constructor_id"]
        circ_id = r.get("circuit_id", "unknown")

        driver_stats.setdefault(d_id, []).append(r)
        constructor_stats.setdefault(c_id, []).append(r)
        circuit_driver_stats.setdefault(circ_id, {}).setdefault(d_id, []).append(r)

    # Compute constructor features
    constructor_features: Dict[str, Dict[str, Any]] = {}
    for c_id, c_rows in constructor_stats.items():
        finishes = [r["finish_position"] for r in c_rows if (r.get("finish_position") or 0) > 0]
        grids = [r["grid_position"] for r in c_rows if (r.get("grid_position") or 0) > 0]
        dnfs = sum(1 for r in c_rows if is_dnf_status(r.get("status")))
        points = sum(float(r.get("points_scored") or 0.0) for r in c_rows)

        avg_race = round(sum(finishes) / len(finishes), 2) if finishes else 20.0
        avg_qual = round(sum(grids) / len(grids), 2) if grids else 20.0
        dnf_rate = round(dnfs / len(c_rows), 3) if c_rows else 0.0
        pts_per_race = round(points / len(c_rows), 2) if c_rows else 0.0

        constructor_features[c_id] = {
            "constructorId": c_id,
            "constructorName": c_rows[0].get("constructor_name", c_id),
            "racePerformanceAvg": avg_race,
            "qualifyingPerformanceAvg": avg_qual,
            "dnfRate": dnf_rate,
            "pointsPerRace": pts_per_race,
            "observationCount": len(c_rows),
        }

    # Compute point-in-time driver features
    driver_features = []
    for d_id, d_rows in driver_stats.items():
        # d_rows is already ordered match_date DESC
        recent_rows = d_rows[:5]  # latest 5 races
        finishes = [r["finish_position"] for r in d_rows if (r.get("finish_position") or 0) > 0]
        recent_finishes = [r["finish_position"] for r in recent_rows if (r.get("finish_position") or 0) > 0]
        grids = [r["grid_position"] for r in d_rows if (r.get("grid_position") or 0) > 0]
        recent_grids = [r["grid_position"] for r in recent_rows if (r.get("grid_position") or 0) > 0]
        recent_points = [float(r.get("points_scored") or 0.0) for r in recent_rows]

        all_points = sum(float(r.get("points_scored") or 0.0) for r in d_rows)
        dnf_count = sum(1 for r in d_rows if is_dnf_status(r.get("status")))
        dnf_rate = round(dnf_count / len(d_rows), 3) if d_rows else 0.0

        avg_finish = round(sum(finishes) / len(finishes), 2) if finishes else 20.0
        recent_avg_finish = round(sum(recent_finishes) / len(recent_finishes), 2) if recent_finishes else avg_finish
        last_finish = recent_finishes[0] if recent_finishes else 20
        best_recent_finish = min(recent_finishes) if recent_finishes else 20

        qual_avg_grid = round(sum(grids) / len(grids), 2) if grids else 20.0
        recent_qual_pos = recent_grids[0] if recent_grids else qual_avg_grid

        recent_points_avg = round(sum(recent_points) / len(recent_points), 2) if recent_points else 0.0
        recent_podium_rate = round(sum(1 for f in recent_finishes if f <= 3) / len(recent_finishes), 3) if recent_finishes else 0.0
        recent_win_rate = round(sum(1 for f in recent_finishes if f == 1) / len(recent_finishes), 3) if recent_finishes else 0.0
        recent_top10_rate = round(sum(1 for f in recent_finishes if f <= 10) / len(recent_finishes), 3) if recent_finishes else 0.0

        # Form token
        form_tokens = []
        for r in recent_rows[:3]:
            pos = r.get("finish_position") or 20
            if pos == 1:
                form_tokens.append("W")
            elif pos <= 3:
                form_tokens.append("POD")
            elif pos <= 10:
                form_tokens.append("PTS")
            else:
                form_tokens.append("OUT")

        # Specific circuit history for this driver
        circ_rows = circuit_driver_stats.get(circuit_id, {}).get(d_id, [])
        circ_finishes = [r["finish_position"] for r in circ_rows if (r.get("finish_position") or 0) > 0]
        circuit_avg_finish = round(sum(circ_finishes) / len(circ_finishes), 2) if circ_finishes else avg_finish
        circuit_experience = len(circ_rows)

        # Constructor data
        c_id = d_rows[0].get("constructor_id", "unknown")
        c_stats = constructor_features.get(c_id, {})
        constructor_avg_finish = c_stats.get("racePerformanceAvg", 20.0)
        constructor_avg_qual = c_stats.get("qualifyingPerformanceAvg", 20.0)
        constructor_dnf_rate = c_stats.get("dnfRate", 0.0)

        # Starting grid position (if known, else recent qual pos)
        grid_pos = recent_qual_pos

        # Season progression from recent row
        recent_round = int(recent_rows[0].get("round") or 1) if recent_rows else 1
        season_progression = round(min(1.0, max(0.04, recent_round / 24.0)), 3)

        # Feature vector adhering to F1_FEATURE_NAMES
        feat_vector = [
            float(qual_avg_grid),
            float(recent_qual_pos),
            float(recent_avg_finish),
            float(last_finish),
            float(recent_points_avg),
            float(recent_podium_rate),
            float(recent_win_rate),
            float(recent_top10_rate),
            float(constructor_avg_finish),
            float(constructor_avg_qual),
            float(circuit_avg_finish),
            float(circuit_experience),
            float(grid_pos),
            float(dnf_rate),
            float(constructor_dnf_rate),
            0.0,  # weather_condition (dry standard)
            float(season_progression),
        ]

        driver_features.append({
            "driverId": d_id,
            "driverName": d_rows[0].get("driver_name", d_id),
            "constructorId": c_id,
            "constructorName": d_rows[0].get("constructor_name", c_id),
            "recentFinishingPosition": last_finish,
            "recentQualifyingPosition": recent_qual_pos,
            "totalPoints": all_points,
            "avgFinish": avg_finish,
            "recentAvgFinish": recent_avg_finish,
            "dnfRate": dnf_rate,
            "recentForm": "-".join(form_tokens),
            "avgCircuitFinish": circuit_avg_finish if circ_rows else None,
            "circuitExperience": circuit_experience,
            "observationCount": len(d_rows),
            "featureVector": feat_vector,
            "qualifyingAvgGrid": qual_avg_grid,
            "constructorAvgFinish": constructor_avg_finish,
            "constructorAvgQual": constructor_avg_qual,
            "constructorDnfRate": constructor_dnf_rate,
            "seasonProgression": season_progression,
        })

    return {
        "drivers": sorted(driver_features, key=lambda x: x["avgFinish"]),
        "constructors": list(constructor_features.values()),
        "circuitStats": {"circuitId": circuit_id},
        "hasSufficientData": len(driver_features) >= 10,
    }

def build_f1_chronological_dataset(
    cutoff_timestamp: str,
    min_prior_races: int = 4,
) -> Dict[str, Any]:
    """
    Builds a chronological training and validation dataset of individual driver-race entries.
    Guarantees:
    1. Zero lookahead bias: each historical race row uses ONLY features from strictly prior races.
    2. Chronological splitting: training set consists of earlier races; validation set consists
       of later races strictly out-of-sample.
    3. Distinct target labels for every market:
       - y_win: finish_position == 1
       - y_podium: finish_position <= 3
       - y_top10: finish_position <= 10
       - y_fastest: fastest_lap_rank == 1
    """
    _ensure_f1_duckdb_view()

    # Query all historical races chronologically
    query = """
    SELECT *
    FROM f1_results
    WHERE match_date < ?
    ORDER BY match_date ASC
    """
    try:
        rel = duckdb_engine.con.execute(query, [cutoff_timestamp])
        cols = [desc[0] for desc in rel.description] if rel.description else []
        rows = [dict(zip(cols, r)) for r in rel.fetchall()]
    except Exception as e:
        print(f"[F1Dataset] Query error: {e}")
        rows = []

    if len(rows) < 50:
        return {
            "hasSufficientData": False,
            "reason": "Insufficient historical rows (< 50).",
            "X_train": [], "y_train_win": [], "y_train_podium": [], "y_train_top10": [], "y_train_fastest": [],
            "X_val": [], "y_val_win": [], "y_val_podium": [], "y_val_top10": [], "y_val_fastest": [],
            "feature_names": F1_FEATURE_NAMES,
        }

    # Group rows by distinct race (match_date, circuit_id)
    races_dict: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in rows:
        key = (r["match_date"], r.get("circuit_id", "unknown"))
        races_dict.setdefault(key, []).append(r)

    # Sort races chronologically
    sorted_races = sorted(races_dict.items(), key=lambda x: x[0][0])
    total_races = len(sorted_races)

    if total_races < (min_prior_races + 3):
        return {
            "hasSufficientData": False,
            "reason": f"Insufficient historical races ({total_races} < {min_prior_races + 3}).",
            "X_train": [], "y_train_win": [], "y_train_podium": [], "y_train_top10": [], "y_train_fastest": [],
            "X_val": [], "y_val_win": [], "y_val_podium": [], "y_val_top10": [], "y_val_fastest": [],
            "feature_names": F1_FEATURE_NAMES,
        }

    # Chronological race split: 70% train, 30% validation
    # Eligible races for sample generation are those with index >= min_prior_races
    eligible_races = sorted_races[min_prior_races:]
    n_eligible = len(eligible_races)
    split_race_idx = int(0.70 * n_eligible)

    train_race_keys = set(r[0] for r in eligible_races[:split_race_idx])
    val_race_keys = set(r[0] for r in eligible_races[split_race_idx:])

    # Track running historical records up to each race
    # To be perfectly efficient, we accumulate past rows as we advance through races
    past_rows: List[Dict[str, Any]] = []
    # Seed with pre-eligible races
    for (m_date, circ_id), race_rows in sorted_races[:min_prior_races]:
        past_rows.extend(race_rows)

    X_train, y_train_win, y_train_podium, y_train_top10, y_train_fastest = [], [], [], [], []
    X_val, y_val_win, y_val_podium, y_val_top10, y_val_fastest = [], [], [], [], []

    for (m_date, circ_id), race_rows in eligible_races:
        # Precompute driver and constructor point-in-time metrics using ONLY past_rows
        # Driver stats
        d_history: Dict[str, List[Dict[str, Any]]] = {}
        c_history: Dict[str, List[Dict[str, Any]]] = {}
        circ_history: Dict[str, List[Dict[str, Any]]] = {}

        for pr in past_rows:
            d_history.setdefault(pr["driver_id"], []).append(pr)
            c_history.setdefault(pr["constructor_id"], []).append(pr)
            if pr.get("circuit_id") == circ_id:
                circ_history.setdefault(pr["driver_id"], []).append(pr)

        is_train = (m_date, circ_id) in train_race_keys

        for r in race_rows:
            d_id = r["driver_id"]
            c_id = r["constructor_id"]
            d_rows = d_history.get(d_id, [])
            c_rows = c_history.get(c_id, [])
            circ_rows = circ_history.get(d_id, [])

            # Compute point-in-time features for this driver entering this race
            finishes = [p["finish_position"] for p in d_rows if (p.get("finish_position") or 0) > 0]
            grids = [p["grid_position"] for p in d_rows if (p.get("grid_position") or 0) > 0]
            recent_finishes = finishes[-5:] if finishes else []
            recent_grids = grids[-5:] if grids else []
            recent_points = [float(p.get("points_scored") or 0.0) for p in d_rows[-5:]] if d_rows else []

            avg_finish = sum(finishes) / len(finishes) if finishes else 20.0
            recent_avg_finish = sum(recent_finishes) / len(recent_finishes) if recent_finishes else avg_finish
            last_finish = finishes[-1] if finishes else 20.0
            qual_avg_grid = sum(grids) / len(grids) if grids else 20.0
            recent_qual_pos = grids[-1] if grids else qual_avg_grid

            recent_pts_avg = sum(recent_points) / len(recent_points) if recent_points else 0.0
            recent_pod_rate = sum(1 for f in recent_finishes if f <= 3) / len(recent_finishes) if recent_finishes else 0.0
            recent_win_rate = sum(1 for f in recent_finishes if f == 1) / len(recent_finishes) if recent_finishes else 0.0
            recent_t10_rate = sum(1 for f in recent_finishes if f <= 10) / len(recent_finishes) if recent_finishes else 0.0

            c_finishes = [p["finish_position"] for p in c_rows if (p.get("finish_position") or 0) > 0]
            c_grids = [p["grid_position"] for p in c_rows if (p.get("grid_position") or 0) > 0]
            c_dnfs = sum(1 for p in c_rows if is_dnf_status(p.get("status")))
            c_avg_finish = sum(c_finishes) / len(c_finishes) if c_finishes else 20.0
            c_avg_qual = sum(c_grids) / len(c_grids) if c_grids else 20.0
            c_dnf_rate = c_dnfs / len(c_rows) if c_rows else 0.0

            circ_finishes = [p["finish_position"] for p in circ_rows if (p.get("finish_position") or 0) > 0]
            circ_avg_finish = sum(circ_finishes) / len(circ_finishes) if circ_finishes else avg_finish
            circ_exp = len(circ_rows)

            dnf_count = sum(1 for p in d_rows if is_dnf_status(p.get("status")))
            d_dnf_rate = dnf_count / len(d_rows) if d_rows else 0.0

            grid_pos = float(r.get("grid_position") or recent_qual_pos)
            round_val = int(r.get("round") or 1)
            season_prog = min(1.0, max(0.04, round_val / 24.0))

            feat_vec = [
                float(qual_avg_grid),
                float(recent_qual_pos),
                float(recent_avg_finish),
                float(last_finish),
                float(recent_pts_avg),
                float(recent_pod_rate),
                float(recent_win_rate),
                float(recent_t10_rate),
                float(c_avg_finish),
                float(c_avg_qual),
                float(circ_avg_finish),
                float(circ_exp),
                float(grid_pos),
                float(d_dnf_rate),
                float(c_dnf_rate),
                0.0,
                float(season_prog),
            ]

            # Actual outcomes for this race
            finish_p = int(r.get("finish_position") or 20)
            fl_rank = int(r.get("fastest_lap_rank") or 99)

            y_w = 1 if finish_p == 1 else 0
            y_p = 1 if finish_p <= 3 else 0
            y_t = 1 if finish_p <= 10 else 0
            y_f = 1 if fl_rank == 1 else 0

            if is_train:
                X_train.append(feat_vec)
                y_train_win.append(y_w)
                y_train_podium.append(y_p)
                y_train_top10.append(y_t)
                y_train_fastest.append(y_f)
            else:
                X_val.append(feat_vec)
                y_val_win.append(y_w)
                y_val_podium.append(y_p)
                y_val_top10.append(y_t)
                y_val_fastest.append(y_f)

        # Advance past_rows to include this race
        past_rows.extend(race_rows)

    return {
        "hasSufficientData": len(X_train) >= 40 and len(X_val) >= 20,
        "training_races": len(train_race_keys),
        "validation_races": len(val_race_keys),
        "X_train": X_train,
        "y_train_win": y_train_win,
        "y_train_podium": y_train_podium,
        "y_train_top10": y_train_top10,
        "y_train_fastest": y_train_fastest,
        "X_val": X_val,
        "y_val_win": y_val_win,
        "y_val_podium": y_val_podium,
        "y_val_top10": y_val_top10,
        "y_val_fastest": y_val_fastest,
        "feature_names": F1_FEATURE_NAMES,
    }
