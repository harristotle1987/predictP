from typing import Dict, Any, Optional
from backend.engine.historical_store import get_point_in_time_matches
from backend.utils.text_normalize import normalize_team_name

def build_football_features(home_team: str, away_team: str, cutoff_timestamp: str) -> Dict[str, Any]:
    """
    Builds football features strictly from real point-in-time historical records.
    Never substitutes missing statistics with arbitrary averages, constants, or zeros.
    Enforces minimum of 5 completed historical matches before cutoff_timestamp for each team.
    """
    home_matches = get_point_in_time_matches(cutoff_timestamp, "football", home_team)
    away_matches = get_point_in_time_matches(cutoff_timestamp, "football", away_team)

    has_sufficient_data = len(home_matches) >= 5 and len(away_matches) >= 5
    if not has_sufficient_data:
        return {
            "homeRecentXgAvg": None,
            "awayRecentXgAvg": None,
            "homeGoalsScoredAvg": None,
            "homeGoalsConcededAvg": None,
            "awayGoalsScoredAvg": None,
            "awayGoalsConcededAvg": None,
            "homeCleanSheets": None,
            "awayCleanSheets": None,
            "homeRecentWDL": None,
            "awayRecentWDL": None,
            "bttsRate": None,
            "bothTeamsScoredRecentRate": None,
            "avgMatchCorners": None,
            "homeMatchesCount": len(home_matches),
            "awayMatchesCount": len(away_matches),
            "hasSufficientData": False,
        }

    h_rec = home_matches[-5:]
    a_rec = away_matches[-5:]

    home_key = normalize_team_name(home_team)
    away_key = normalize_team_name(away_team)

    # xG calculation: Only calculate if real xG is recorded in historical data
    h_xg_vals = [m.get("homeXg") if m["homeTeamKey"] == home_key else m.get("awayXg") for m in h_rec]
    a_xg_vals = [m.get("homeXg") if m["homeTeamKey"] == away_key else m.get("awayXg") for m in a_rec]

    h_xg = round(sum(h_xg_vals) / len(h_xg_vals), 2) if all(v is not None for v in h_xg_vals) else None
    a_xg = round(sum(a_xg_vals) / len(a_xg_vals), 2) if all(v is not None for v in a_xg_vals) else None

    # Real goals scored / conceded from completed match scores
    h_scored = sum((m.get("homeScore") if m.get("homeTeamKey") == home_key else m.get("awayScore")) or 0 for m in h_rec) / len(h_rec)
    h_conceded = sum((m.get("awayScore") if m.get("homeTeamKey") == home_key else m.get("homeScore")) or 0 for m in h_rec) / len(h_rec)
    a_scored = sum((m.get("homeScore") if m.get("homeTeamKey") == away_key else m.get("awayScore")) or 0 for m in a_rec) / len(a_rec)
    a_conceded = sum((m.get("awayScore") if m.get("homeTeamKey") == away_key else m.get("homeScore")) or 0 for m in a_rec) / len(a_rec)

    # Clean sheets count
    h_cs = sum(1 for m in h_rec if ((m.get("awayScore") or 0) == 0 if m.get("homeTeamKey") == home_key else (m.get("homeScore") or 0) == 0))
    a_cs = sum(1 for m in a_rec if ((m.get("awayScore") or 0) == 0 if m.get("homeTeamKey") == away_key else (m.get("homeScore") or 0) == 0))

    # Recent W/D/L record
    def get_wdl(matches_list, team_key):
        w, d, l = 0, 0, 0
        for m in matches_list:
            team_score = (m.get("homeScore") if m.get("homeTeamKey") == team_key else m.get("awayScore")) or 0
            opp_score = (m.get("awayScore") if m.get("homeTeamKey") == team_key else m.get("homeScore")) or 0
            if team_score > opp_score:
                w += 1
            elif team_score == opp_score:
                d += 1
            else:
                l += 1
        return {"wins": w, "draws": d, "losses": l}

    h_wdl = get_wdl(h_rec, home_key)
    a_wdl = get_wdl(a_rec, away_key)

    # BTTS (Both Teams To Score) rate in recent matches
    all_recent = h_rec + a_rec
    btts_matches = sum(1 for m in all_recent if (m.get("homeScore") or 0) > 0 and (m.get("awayScore") or 0) > 0)
    btts_rate = round(btts_matches / len(all_recent), 2) if all_recent else None

    # Corners calculation: Only if real corners are recorded
    corner_pairs = [(m.get("homeCorners"), m.get("awayCorners")) for m in (h_rec + a_rec)]
    if all(hc is not None and ac is not None for hc, ac in corner_pairs):
        corners = round(sum(hc + ac for hc, ac in corner_pairs) / len(corner_pairs), 1)
    else:
        corners = None

    return {
        "homeRecentXgAvg": h_xg,
        "awayRecentXgAvg": a_xg,
        "homeGoalsScoredAvg": round(h_scored, 2),
        "homeGoalsConcededAvg": round(h_conceded, 2),
        "awayGoalsScoredAvg": round(a_scored, 2),
        "awayGoalsConcededAvg": round(a_conceded, 2),
        "homeCleanSheets": h_cs,
        "awayCleanSheets": a_cs,
        "homeRecentWDL": h_wdl,
        "awayRecentWDL": a_wdl,
        "bttsRate": btts_rate,
        "bothTeamsScoredRecentRate": btts_rate,
        "avgMatchCorners": corners,
        "homeMatchesCount": len(home_matches),
        "awayMatchesCount": len(away_matches),
        "hasSufficientData": True,
    }

def build_basketball_features(home_team: str, away_team: str, cutoff_timestamp: str) -> Dict[str, Any]:
    """
    Builds basketball features strictly from real point-in-time historical records.
    Never substitutes missing statistics with arbitrary numbers.
    Enforces minimum of 5 completed historical games before cutoff_timestamp for each team.
    """
    home_matches = get_point_in_time_matches(cutoff_timestamp, "basketball", home_team)
    away_matches = get_point_in_time_matches(cutoff_timestamp, "basketball", away_team)

    has_sufficient_data = len(home_matches) >= 5 and len(away_matches) >= 5
    if not has_sufficient_data:
        return {
            "pace": None,
            "homePPG": None,
            "awayPPG": None,
            "homePointsAllowedAvg": None,
            "awayPointsAllowedAvg": None,
            "reboundDifferential": None,
            "homeMatchesCount": len(home_matches),
            "awayMatchesCount": len(away_matches),
            "hasSufficientData": False,
        }

    h_rec = home_matches[-5:]
    a_rec = away_matches[-5:]

    home_key = normalize_team_name(home_team)
    away_key = normalize_team_name(away_team)

    # Pace: only from real recorded pace values
    pace_vals = [m.get("pace") for m in (h_rec + a_rec)]
    pace = round(sum(pace_vals) / len(pace_vals), 1) if all(p is not None for p in pace_vals) else None

    # Points scored and allowed from real final match scores
    h_ppg = sum((m.get("homeScore") if m.get("homeTeamKey") == home_key else m.get("awayScore")) or 0 for m in h_rec) / len(h_rec)
    h_allowed = sum((m.get("awayScore") if m.get("homeTeamKey") == home_key else m.get("homeScore")) or 0 for m in h_rec) / len(h_rec)

    a_ppg = sum((m.get("homeScore") if m.get("homeTeamKey") == away_key else m.get("awayScore")) or 0 for m in a_rec) / len(a_rec)
    a_allowed = sum((m.get("awayScore") if m.get("homeTeamKey") == away_key else m.get("homeScore")) or 0 for m in a_rec) / len(a_rec)

    # Rebound differential: only from real recorded rebound differentials
    reb_vals = [m.get("homeReboundDiff") for m in h_rec]
    reb_diff = round(sum(reb_vals) / len(reb_vals), 1) if all(r is not None for r in reb_vals) else None

    return {
        "pace": pace,
        "homePPG": round(h_ppg, 1),
        "awayPPG": round(a_ppg, 1),
        "homePointsAllowedAvg": round(h_allowed, 1),
        "awayPointsAllowedAvg": round(a_allowed, 1),
        "reboundDifferential": reb_diff,
        "homeMatchesCount": len(home_matches),
        "awayMatchesCount": len(away_matches),
        "hasSufficientData": True,
    }

def build_baseball_features(home_team: str, away_team: str, cutoff_timestamp: str) -> Dict[str, Any]:
    """
    Builds baseball features strictly from real point-in-time historical records.
    Never substitutes missing statistics with arbitrary numbers.
    Enforces minimum of 5 completed historical games before cutoff_timestamp for each team.
    """
    home_matches = get_point_in_time_matches(cutoff_timestamp, "baseball", home_team)
    away_matches = get_point_in_time_matches(cutoff_timestamp, "baseball", away_team)

    has_sufficient_data = len(home_matches) >= 5 and len(away_matches) >= 5
    if not has_sufficient_data:
        return {
            "homeRunsScoredAvg": None,
            "awayRunsScoredAvg": None,
            "homeRunsAllowedAvg": None,
            "awayRunsAllowedAvg": None,
            "homeERA": None,
            "awayERA": None,
            "bullpenWHIP": None,
            "battingAvg": None,
            "homeMatchesCount": len(home_matches),
            "awayMatchesCount": len(away_matches),
            "hasSufficientData": False,
        }

    h_rec = home_matches[-5:]
    a_rec = away_matches[-5:]

    home_key = normalize_team_name(home_team)
    away_key = normalize_team_name(away_team)

    # Runs scored and allowed from real completed game scores
    h_runs_scored = sum((m.get("homeScore") if m.get("homeTeamKey") == home_key else m.get("awayScore")) or 0 for m in h_rec) / len(h_rec)
    h_runs_allowed = sum((m.get("awayScore") if m.get("homeTeamKey") == home_key else m.get("homeScore")) or 0 for m in h_rec) / len(h_rec)

    a_runs_scored = sum((m.get("homeScore") if m.get("homeTeamKey") == away_key else m.get("awayScore")) or 0 for m in a_rec) / len(a_rec)
    a_runs_allowed = sum((m.get("awayScore") if m.get("homeTeamKey") == away_key else m.get("homeScore")) or 0 for m in a_rec) / len(a_rec)

    # ERA from real records
    h_era_vals = [m.get("homeEra") if m["homeTeamKey"] == home_key else m.get("awayEra") for m in h_rec]
    a_era_vals = [m.get("homeEra") if m["homeTeamKey"] == away_key else m.get("awayEra") for m in a_rec]

    h_era = round(sum(h_era_vals) / len(h_era_vals), 2) if all(v is not None for v in h_era_vals) else None
    a_era = round(sum(a_era_vals) / len(a_era_vals), 2) if all(v is not None for v in a_era_vals) else None

    # Bullpen WHIP from real records
    whip_vals = [m.get("bullpenWhip") for m in h_rec]
    whip = round(sum(whip_vals) / len(whip_vals), 2) if all(v is not None for v in whip_vals) else None

    # Team Batting Average from real records
    ba_vals = [m.get("battingAvg") for m in h_rec]
    batting_avg = round(sum(ba_vals) / len(ba_vals), 3) if all(v is not None for v in ba_vals) else None

    return {
        "homeRunsScoredAvg": round(h_runs_scored, 2),
        "awayRunsScoredAvg": round(a_runs_scored, 2),
        "homeRunsAllowedAvg": round(h_runs_allowed, 2),
        "awayRunsAllowedAvg": round(a_runs_allowed, 2),
        "homeERA": h_era,
        "awayERA": a_era,
        "bullpenWHIP": whip,
        "battingAvg": batting_avg,
        "homeMatchesCount": len(home_matches),
        "awayMatchesCount": len(away_matches),
        "hasSufficientData": True,
    }

from backend.features.hockey_feature_builder import build_hockey_features
