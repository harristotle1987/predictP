from typing import List, Dict, Any, Optional
from backend.db.duckdb_engine import duckdb_engine
from backend.utils.text_normalize import normalize_team_name

def get_point_in_time_matches(arg1: str, arg2: str = "football", team: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    DuckDB Point-in-Time historical query layer over active Parquet datasets.
    Supports:
      get_point_in_time_matches(cutoff_timestamp, sport, team)
      get_point_in_time_matches(sport, cutoff_timestamp, team)

    Enforces normalized team key lookup:
      live SportsSkills team name -> normalize_team_name() -> *_team_key -> DuckDB -> historical records.

    Strictly enforces: WHERE match_date < cutoff_timestamp AND status = 'completed'.
    Zero hardcoded fixtures. Zero lookahead leakage.
    """
    if any(ch.isdigit() for ch in arg1) and ("T" in arg1 or "-" in arg1):
        cutoff_timestamp = arg1
        sport = arg2
    elif any(ch.isdigit() for ch in arg2) and ("T" in arg2 or "-" in arg2):
        sport = arg1
        cutoff_timestamp = arg2
    elif arg1 in {"football", "basketball", "baseball", "hockey", "ice_hockey"}:
        sport = arg1
        cutoff_timestamp = arg2
    else:
        cutoff_timestamp = arg1
        sport = arg2

    sport = sport.lower().strip()
    if sport == "ice_hockey":
        sport = "hockey"

    if team:
        team_key = normalize_team_name(team)
        raw_matches = duckdb_engine.get_team_recent_history(sport, team_key, cutoff_timestamp, limit=50)
    else:
        raw_matches = duckdb_engine.get_point_in_time_matches(sport, cutoff_timestamp)

    # Normalize fields for engine consumers with strict None preservation on missing features
    normalized: List[Dict[str, Any]] = []
    for m in raw_matches:
        home_score = m.get("home_score")
        away_score = m.get("away_score")
        if home_score is None or away_score is None:
            continue

        home_team_name = m.get("home_team", "")
        away_team_name = m.get("away_team", "")

        normalized.append({
            "id": m.get("match_id", ""),
            "sport": sport,
            "league": m.get("league", ""),
            "matchDate": m.get("match_date", ""),
            "homeTeam": home_team_name,
            "awayTeam": away_team_name,
            "homeTeamKey": normalize_team_name(home_team_name),
            "awayTeamKey": normalize_team_name(away_team_name),
            "homeScore": int(home_score),
            "awayScore": int(away_score),
            "status": "completed",
            "homeXg": float(m["home_xg"]) if m.get("home_xg") is not None else None,
            "awayXg": float(m["away_xg"]) if m.get("away_xg") is not None else None,
            "homeCorners": int(m["home_corners"]) if m.get("home_corners") is not None else None,
            "awayCorners": int(m["away_corners"]) if m.get("away_corners") is not None else None,
            "pace": float(m["pace"]) if m.get("pace") is not None else None,
            "homeReboundDiff": int(m["home_rebound_diff"]) if m.get("home_rebound_diff") is not None else None,
            "homeEra": float(m["home_era"]) if m.get("home_era") is not None else None,
            "awayEra": float(m["away_era"]) if m.get("away_era") is not None else None,
            "bullpenWhip": float(m["bullpen_whip"]) if m.get("bullpen_whip") is not None else None,
            "battingAvg": float(m["batting_avg"]) if m.get("batting_avg") is not None else None,
            "homeShots": int(m["home_shots"]) if m.get("home_shots") is not None else None,
            "awayShots": int(m["away_shots"]) if m.get("away_shots") is not None else None,
            "isOvertime": bool(m["is_overtime"]) if m.get("is_overtime") is not None else None,
            "otStatus": str(m["ot_status"]) if m.get("ot_status") is not None else None,
            "homePowerPlayPct": float(m["home_power_play_pct"]) if m.get("home_power_play_pct") is not None else None,
            "awayPowerPlayPct": float(m["away_power_play_pct"]) if m.get("away_power_play_pct") is not None else None,
            "homePenaltyKillPct": float(m["home_penalty_kill_pct"]) if m.get("home_penalty_kill_pct") is not None else None,
            "awayPenaltyKillPct": float(m["away_penalty_kill_pct"]) if m.get("away_penalty_kill_pct") is not None else None,
        })

    return normalized
