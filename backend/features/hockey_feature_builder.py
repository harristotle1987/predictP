from typing import Dict, Any, List, Optional
from backend.engine.historical_store import get_point_in_time_matches
from backend.utils.text_normalize import normalize_team_name

def build_hockey_features(home_team: str, away_team: str, cutoff_timestamp: str) -> Dict[str, Any]:
    """
    Builds hockey (NHL) features strictly from real point-in-time historical records.
    Requires at least 5 completed historical matches before cutoff_timestamp for each team.
    """
    home_matches = get_point_in_time_matches(cutoff_timestamp, "hockey", home_team)
    away_matches = get_point_in_time_matches(cutoff_timestamp, "hockey", away_team)

    if len(home_matches) < 5 or len(away_matches) < 5:
        # Also try "ice_hockey" key if "hockey" key yields < 5
        home_matches_alt = get_point_in_time_matches(cutoff_timestamp, "ice_hockey", home_team)
        away_matches_alt = get_point_in_time_matches(cutoff_timestamp, "ice_hockey", away_team)
        if len(home_matches_alt) >= len(home_matches):
            home_matches = home_matches_alt
        if len(away_matches_alt) >= len(away_matches):
            away_matches = away_matches_alt

    has_sufficient_data = len(home_matches) >= 5 and len(away_matches) >= 5
    if not has_sufficient_data:
        return {
            "homeGamesPlayed": len(home_matches),
            "awayGamesPlayed": len(away_matches),
            "homeWins": None,
            "homeLosses": None,
            "homeOtLosses": None,
            "awayWins": None,
            "awayLosses": None,
            "awayOtLosses": None,
            "homeGoalsForAvg": None,
            "homeGoalsAgainstAvg": None,
            "awayGoalsForAvg": None,
            "awayGoalsAgainstAvg": None,
            "homeGoalDiff": None,
            "awayGoalDiff": None,
            "homeShotsAvg": None,
            "homeShotsAgainstAvg": None,
            "awayShotsAvg": None,
            "awayShotsAgainstAvg": None,
            "homePowerPlayPct": None,
            "awayPowerPlayPct": None,
            "homePenaltyKillPct": None,
            "awayPenaltyKillPct": None,
            "homeHomeWins": None,
            "awayAwayWins": None,
            "homeRecentForm": None,
            "awayRecentForm": None,
            "homeMatchesCount": len(home_matches),
            "awayMatchesCount": len(away_matches),
            "hasSufficientData": False,
        }

    h_rec = home_matches[-10:]
    a_rec = away_matches[-10:]

    home_key = normalize_team_name(home_team)
    away_key = normalize_team_name(away_team)

    def calc_stats(matches_list: List[Dict[str, Any]], team_key: str, is_home_team: bool):
        gp = len(matches_list)
        wins, losses, ot_losses = 0, 0, 0
        goals_for, goals_against = 0, 0
        shots_for_list, shots_against_list = [], []
        pp_goals_list, pk_stops_list = [], []
        home_wins, away_wins = 0, 0
        form_tokens = []

        for m in matches_list:
            is_h = (m.get("homeTeamKey") == team_key)
            sc_for = m.get("homeScore") if is_h else m.get("awayScore")
            sc_ag = m.get("awayScore") if is_h else m.get("homeScore")
            is_ot = bool(m.get("isOt") or m.get("overtime"))

            if sc_for is not None and sc_ag is not None:
                goals_for += sc_for
                goals_against += sc_ag
                if sc_for > sc_ag:
                    wins += 1
                    form_tokens.append("W")
                    if is_h:
                        home_wins += 1
                    else:
                        away_wins += 1
                elif is_ot:
                    ot_losses += 1
                    form_tokens.append("OTL")
                else:
                    losses += 1
                    form_tokens.append("L")

            # Shots & PP/PK stats if present
            s_for = m.get("homeShots" if is_h else "awayShots")
            s_ag = m.get("awayShots" if is_h else "homeShots")
            if s_for is not None:
                shots_for_list.append(s_for)
            if s_ag is not None:
                shots_against_list.append(s_ag)

            pp_pct = m.get("homePpPct" if is_h else "awayPpPct")
            if pp_pct is not None:
                pp_goals_list.append(pp_pct)

            pk_pct = m.get("homePkPct" if is_h else "awayPkPct")
            if pk_pct is not None:
                pk_stops_list.append(pk_pct)

        gf_avg = round(goals_for / gp, 2) if gp > 0 else 0.0
        ga_avg = round(goals_against / gp, 2) if gp > 0 else 0.0
        g_diff = goals_for - goals_against

        shots_avg = round(sum(shots_for_list) / len(shots_for_list), 1) if shots_for_list else None
        shots_ag_avg = round(sum(shots_against_list) / len(shots_against_list), 1) if shots_against_list else None
        pp_avg = round(sum(pp_goals_list) / len(pp_goals_list), 3) if pp_goals_list else None
        pk_avg = round(sum(pk_stops_list) / len(pk_stops_list), 3) if pk_stops_list else None

        return {
            "gp": gp,
            "wins": wins,
            "losses": losses,
            "otLosses": ot_losses,
            "gfAvg": gf_avg,
            "gaAvg": ga_avg,
            "gDiff": g_diff,
            "shotsAvg": shots_avg,
            "shotsAgAvg": shots_ag_avg,
            "ppPct": pp_avg,
            "pkPct": pk_avg,
            "homeWins": home_wins,
            "awayWins": away_wins,
            "form": "".join(form_tokens[-5:]),
        }

    h_st = calc_stats(h_rec, home_key, True)
    a_st = calc_stats(a_rec, away_key, False)

    return {
        "homeGamesPlayed": h_st["gp"],
        "awayGamesPlayed": a_st["gp"],
        "homeWins": h_st["wins"],
        "homeLosses": h_st["losses"],
        "homeOtLosses": h_st["otLosses"],
        "awayWins": a_st["wins"],
        "awayLosses": a_st["losses"],
        "awayOtLosses": a_st["otLosses"],
        "homeGoalsForAvg": h_st["gfAvg"],
        "homeGoalsAgainstAvg": h_st["gaAvg"],
        "awayGoalsForAvg": a_st["gfAvg"],
        "awayGoalsAgainstAvg": a_st["gaAvg"],
        "homeGoalsScoredAvg": h_st["gfAvg"],  # aliases for market compatibility
        "awayGoalsScoredAvg": a_st["gfAvg"],
        "homeGoalsConcededAvg": h_st["gaAvg"],
        "awayGoalsConcededAvg": a_st["gaAvg"],
        "homeGoalDiff": h_st["gDiff"],
        "awayGoalDiff": a_st["gDiff"],
        "homeShotsAvg": h_st["shotsAvg"],
        "homeShotsAgainstAvg": h_st["shotsAgAvg"],
        "awayShotsAvg": a_st["shotsAvg"],
        "awayShotsAgainstAvg": a_st["shotsAgAvg"],
        "homePowerPlayPct": h_st["ppPct"],
        "awayPowerPlayPct": a_st["ppPct"],
        "homePenaltyKillPct": h_st["pkPct"],
        "awayPenaltyKillPct": a_st["pkPct"],
        "homeHomeWins": h_st["homeWins"],
        "awayAwayWins": a_st["awayWins"],
        "homeRecentForm": h_st["form"],
        "awayRecentForm": a_st["form"],
        "homeMatchesCount": len(home_matches),
        "awayMatchesCount": len(away_matches),
        "hasSufficientData": True,
    }
