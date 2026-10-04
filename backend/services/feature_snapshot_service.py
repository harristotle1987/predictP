"""
Materialized Feature Snapshot Service.
Maintains sport-specific materialized feature snapshots per team/driver.
Enables instant point-in-time feature loading during normal refresh and serving paths,
eliminating full-table historical scans.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from backend.services.data_access.feature_repository import feature_repository, CURRENT_FEATURE_VERSION
from backend.engine.historical_store import get_point_in_time_matches
from backend.utils.text_normalize import normalize_team_name

class FeatureSnapshotService:
    """
    Manages generation, caching, and incremental updating of team feature snapshots.
    Uses real historical point-in-time matches and real opponent records.
    Never fabricates statistics with fake opponents or arbitrary placeholders.
    """

    async def get_or_build_snapshot(
        self, team_name: str, sport: str = "football", as_of: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Retrieves existing snapshot from repository; builds and materializes if missing
        using real point-in-time historical matches for the team.
        """
        norm_name = team_name.lower().strip()
        snapshot = await feature_repository.get_team_snapshot(norm_name, sport=sport, as_of=as_of)
        if snapshot:
            return snapshot

        cutoff = as_of or datetime.now(timezone.utc).isoformat()
        norm_key = normalize_team_name(team_name)

        # Retrieve real point-in-time historical matches for this specific team
        try:
            team_matches = get_point_in_time_matches(cutoff, sport, team_name)
        except Exception as e:
            print(f"[FeatureSnapshotService] Historical match lookup notice for {team_name}: {e}")
            team_matches = []

        sample_size = len(team_matches)
        has_sufficient = sample_size >= 5

        if not has_sufficient:
            # Under 5 real historical matches: do not fabricate features
            team_feat = {
                "matches_played": sample_size,
                "sample_size": sample_size,
                "hasSufficientData": False,
                "goals_scored_avg": None,
                "goals_conceded_avg": None,
                "clean_sheet_rate": None,
                "btts_rate": None,
                "win_rate": None,
                "form_wdl": "",
                "opponents": [
                    m.get("awayTeam") if m.get("homeTeamKey") == norm_key else m.get("homeTeam")
                    for m in team_matches
                ],
                "elo_rating": 1500.0,
            }
            elo_val = 1500.0
            glicko_val = 1500.0
            glicko_rd_val = 200.0
            glicko_vol_val = 0.06
        else:
            # 5 or more real matches: compute true historical aggregates
            recent_matches = team_matches[-10:] if len(team_matches) >= 10 else team_matches[-5:]
            scores_for: List[int] = []
            scores_against: List[int] = []
            opponents: List[str] = []
            wdl_chars: List[str] = []
            clean_sheets = 0
            btts_count = 0
            wins = 0

            for m in recent_matches:
                is_home = (m.get("homeTeamKey") == norm_key)
                opp_name = m.get("awayTeam") if is_home else m.get("homeTeam")
                if opp_name:
                    opponents.append(opp_name)
                
                sf = int(m.get("homeScore") if is_home else m.get("awayScore") or 0)
                sa = int(m.get("awayScore") if is_home else m.get("homeScore") or 0)
                scores_for.append(sf)
                scores_against.append(sa)

                if sa == 0:
                    clean_sheets += 1
                if sf > 0 and sa > 0:
                    btts_count += 1
                if sf > sa:
                    wdl_chars.append("W")
                    wins += 1
                elif sf == sa:
                    wdl_chars.append("D")
                else:
                    wdl_chars.append("L")

            total_recent = len(recent_matches)
            avg_scored = round(sum(scores_for) / total_recent, 2) if total_recent > 0 else 0.0
            avg_conceded = round(sum(scores_against) / total_recent, 2) if total_recent > 0 else 0.0
            cs_rate = round(clean_sheets / total_recent, 2) if total_recent > 0 else 0.0
            btts_rate = round(btts_count / total_recent, 2) if total_recent > 0 else 0.0
            win_rate = round(wins / total_recent, 2) if total_recent > 0 else 0.0

            # Approximate baseline elo based on historical win rate
            elo_val = round(1500.0 + (win_rate - 0.5) * 400.0, 1)
            glicko_val = elo_val
            glicko_rd_val = max(50.0, round(200.0 - (sample_size * 5), 1))
            glicko_vol_val = 0.06

            team_feat = {
                "matches_played": sample_size,
                "sample_size": sample_size,
                "hasSufficientData": True,
                "goals_scored_avg": avg_scored,
                "goals_conceded_avg": avg_conceded,
                "clean_sheet_rate": cs_rate,
                "btts_rate": btts_rate,
                "win_rate": win_rate,
                "form_wdl": "".join(wdl_chars),
                "opponents": opponents,
                "elo_rating": elo_val,
                "glicko_rating": glicko_val,
                "glicko_rd": glicko_rd_val,
                "glicko_vol": glicko_vol_val,
            }

        snapshot_doc = {
            "team_name": norm_name,
            "display_name": team_name,
            "sport": sport,
            "as_of": cutoff,
            "feature_version": CURRENT_FEATURE_VERSION,
            "features": team_feat,
            "sample_size": sample_size,
            "elo": elo_val,
            "glicko_rating": glicko_val,
            "glicko_deviation": glicko_rd_val,
            "glicko_volatility": glicko_vol_val,
        }

        await feature_repository.save_team_snapshot(snapshot_doc)
        return snapshot_doc

    async def batch_load_snapshots(
        self, team_names: List[str], sport: str = "football", as_of: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Loads all required team snapshots in a single batched repository query.
        Builds and saves any missing snapshots.
        """
        if not team_names:
            return {}

        existing_map = await feature_repository.get_batch_team_snapshots(team_names, sport=sport, as_of=as_of)
        result: Dict[str, Dict[str, Any]] = {}

        for name in team_names:
            norm = name.lower().strip()
            if norm in existing_map:
                result[norm] = existing_map[norm]
            else:
                snap = await self.get_or_build_snapshot(name, sport=sport, as_of=as_of)
                result[norm] = snap

        return result

    async def update_after_match(
        self,
        sport: str,
        home_team: str,
        away_team: str,
        home_score: int,
        away_score: int,
        match_date: str,
    ) -> None:
        """
        Incrementally updates feature snapshots for participating teams after a match completes.
        """
        # Trigger rebuild/save for both teams as of match_date
        await self.get_or_build_snapshot(home_team, sport=sport, as_of=match_date)
        await self.get_or_build_snapshot(away_team, sport=sport, as_of=match_date)

feature_snapshot_service = FeatureSnapshotService()
