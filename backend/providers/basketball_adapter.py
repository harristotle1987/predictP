import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any
try:
    import sports_skills.nba as nba_data
except ImportError:
    try:
        import sports_skills.nba_data as nba_data
    except ImportError:
        nba_data = None

from backend.providers.base import BaseSportsSkillsAdapter
from backend.config import settings

class BasketballAdapter(BaseSportsSkillsAdapter):
    def __init__(self):
        super().__init__("basketball")
        self.provider_name = "machina-sports/sports-skills"

    def _transform_event(self, ev: Dict[str, Any], now_iso: str) -> Dict[str, Any]:
        ev_id = str(ev.get("id") or "").strip()
        if not ev_id:
            raise ValueError("Basketball event missing source event id")

        start_time = ev.get("start_time") or now_iso
        raw_status = str(ev.get("status") or "not_started").lower()

        status = "scheduled"
        if any(x in raw_status for x in ["in_progress", "live", "quarter", "q1", "q2", "q3", "q4"]):
            status = "live"
        elif any(x in raw_status for x in ["finished", "completed", "final", "closed"]):
            status = "completed"

        competitors = ev.get("competitors") or []
        home_team = None
        away_team = None
        h_id, a_id = "h1", "a1"
        h_score, a_score = 0, 0

        for c in competitors:
            qualifier = str(c.get("home_away") or c.get("qualifier") or "").lower()
            team_info = c.get("team") or {}
            t_name = (team_info.get("name") or "").strip()
            t_id = str(team_info.get("id") or "").strip()
            sc = int(c.get("score") or 0)

            if qualifier == "home":
                home_team = t_name
                h_id = t_id or "h1"
                h_score = sc
            elif qualifier == "away":
                away_team = t_name
                a_id = t_id or "a1"
                a_score = sc

        if not home_team and len(competitors) > 0:
            c0 = competitors[0]
            home_team = (c0.get("team") or {}).get("name")
            h_id = str((c0.get("team") or {}).get("id") or "h1")
            h_score = int(c0.get("score") or 0)
        if not away_team and len(competitors) > 1:
            c1 = competitors[1]
            away_team = (c1.get("team") or {}).get("name")
            a_id = str((c1.get("team") or {}).get("id") or "a1")
            a_score = int(c1.get("score") or 0)

        if not home_team or not away_team or home_team == "Home Team" or away_team == "Away Team":
            raise ValueError(f"Basketball event {ev_id} missing real team names")

        comp = ev.get("competition") or ev.get("tournament") or ev.get("league") or {}
        if isinstance(comp, dict):
            league_name = str(comp.get("name") or comp.get("title") or ev.get("league_name") or "").strip()
            comp_id = str(comp.get("id") or comp.get("slug") or "").strip().lower()
        else:
            league_name = str(comp).strip() if comp else ""
            comp_id = league_name.lower().replace(" ", "-") if league_name else ""

        if not league_name or not comp_id:
            raise ValueError(f"Basketball event {ev_id} missing provider competition identity")

        season_val = str(ev.get("season") or (comp.get("season") if isinstance(comp, dict) else None) or "2026/2027")

        score_obj = None
        if status in ("live", "completed"):
            score_obj = {
                "home": h_score,
                "away": a_score,
                "display": f"{h_score} - {a_score}",
            }

        return {
            "id": f"bk_{ev_id}",
            "fixture_id": f"bk_{ev_id}",
            "source_event_id": ev_id,
            "sport": "basketball",
            "league": league_name,
            "competition_id": comp_id,
            "competition_name": league_name,
            "season": season_val,
            "fixture_date": start_time,
            "home": home_team,
            "away": away_team,
            "competitionTier": "primary" if comp_id == "nba" else "secondary",
            "homeTeam": home_team,
            "awayTeam": away_team,
            "home_team_id": h_id,
            "away_team_id": a_id,
            "kickoffUtc": start_time,
            "scheduled_at": start_time,
            "status": status,
            "currentScore": score_obj,
            "finalScore": score_obj if status == "completed" else None,
            "source_updated_at": now_iso,
            "provider": self.provider_name,
        }

    async def fetch_fixtures(self, date_str: str = None) -> List[Dict[str, Any]]:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        timeout_sec = min(4.0, max(1.0, getattr(settings, 'sports_skills_timeout_ms', 4000) / 1000.0))

        if nba_data is not None and hasattr(nba_data, "get_scoreboard"):
            try:
                kwargs = {}
                if date_str:
                    kwargs["date"] = date_str
                res = await asyncio.wait_for(
                    asyncio.to_thread(nba_data.get_scoreboard, **kwargs),
                    timeout=timeout_sec
                )
                if isinstance(res, dict):
                    events = res.get("data", {}).get("events", [])
                    if isinstance(events, list) and events:
                        fixtures: List[Dict[str, Any]] = []
                        for ev in events:
                            try:
                                fixtures.append(self._transform_event(ev, now_iso))
                            except ValueError:
                                continue
                        if fixtures:
                            return fixtures
            except Exception:
                pass

        from backend.providers.api_sports_provider import api_sports_provider
        return await api_sports_provider.fetch_fixtures("basketball", date_str=date_str)

    def get_game_summary(self, game_id: str) -> Dict[str, Any]:
        if hasattr(nba_data, "get_game_summary"):
            return nba_data.get_game_summary(game_id=game_id)
        elif hasattr(nba_data, "get_live_boxscore"):
            return nba_data.get_live_boxscore(game_id=game_id)
        return {}

basketball_adapter = BasketballAdapter()
