import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any
try:
    import sports_skills.mlb as mlb_data
except ImportError:
    try:
        import sports_skills.mlb_data as mlb_data
    except ImportError:
        mlb_data = None

from backend.providers.base import BaseSportsSkillsAdapter
from backend.config import settings

class BaseballAdapter(BaseSportsSkillsAdapter):
    def __init__(self):
        super().__init__("baseball")
        self.provider_name = "machina-sports/sports-skills"

    def _transform_event(self, ev: Dict[str, Any], now_iso: str) -> Dict[str, Any]:
        ev_id = str(ev.get("id") or "").strip()
        if not ev_id:
            raise ValueError("Baseball event missing source event id")

        start_time = ev.get("start_time") or now_iso
        raw_status = str(ev.get("status") or "not_started").lower()

        status = "scheduled"
        if any(x in raw_status for x in ["in_progress", "live", "inning", "top", "bottom"]):
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
            raise ValueError(f"Baseball event {ev_id} missing real team names")

        return {
            "id": f"bb_{ev_id}",
            "source_event_id": ev_id,
            "sport": "baseball",
            "league": "Major League Baseball (MLB)",
            "competition_id": "mlb",
            "competitionTier": "primary",
            "homeTeam": home_team,
            "awayTeam": away_team,
            "home_team_id": h_id,
            "away_team_id": a_id,
            "kickoffUtc": start_time,
            "scheduled_at": start_time,
            "status": status,
            "currentScore": {
                "home": h_score,
                "away": a_score,
                "display": f"{h_score} - {a_score}",
            },
            "source_updated_at": now_iso,
            "provider": "machina-sports/sports-skills",
        }

    async def fetch_fixtures(self, date_str: str = None) -> List[Dict[str, Any]]:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        timeout_sec = min(4.0, max(1.0, getattr(settings, 'sports_skills_timeout_ms', 4000) / 1000.0))

        try:
            kwargs = {}
            if date_str:
                kwargs["date"] = date_str
            res = await asyncio.wait_for(
                asyncio.to_thread(mlb_data.get_scoreboard, **kwargs),
                timeout=timeout_sec
            )
        except Exception as e:
            raise RuntimeError(f"PROVIDER_ERROR: SportsSkills Baseball (sports_skills.mlb_data) get_scoreboard() error: {str(e)}") from e

        if not isinstance(res, dict):
            raise RuntimeError(f"PROVIDER_ERROR: Baseball provider returned unexpected response type: {type(res)}")

        events = res.get("data", {}).get("events", [])
        if not isinstance(events, list):
            events = []

        fixtures: List[Dict[str, Any]] = []
        for ev in events:
            try:
                fixtures.append(self._transform_event(ev, now_iso))
            except ValueError:
                continue

        return fixtures

    def get_game_summary(self, game_id: str) -> Dict[str, Any]:
        if hasattr(mlb_data, "get_game_summary"):
            return mlb_data.get_game_summary(game_id=game_id)
        elif hasattr(mlb_data, "get_mlbstats_boxscore"):
            return mlb_data.get_mlbstats_boxscore(game_id=game_id)
        return {}

baseball_adapter = BaseballAdapter()
