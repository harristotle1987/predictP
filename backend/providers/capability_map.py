from typing import Dict, Any, List

class ProviderCapability:
    def __init__(
        self,
        sport: str,
        provider: str,
        supports_discovery: bool = True,
        supports_date_schedule: bool = True,
        supports_scoreboard: bool = True,
        supports_game_detail: bool = True,
        supports_standings: bool = True,
        supports_live_scores: bool = True,
        endpoint_labels: Dict[str, str] = None,
    ):
        self.sport = sport
        self.provider = provider
        self.supports_discovery = supports_discovery
        self.supports_date_schedule = supports_date_schedule
        self.supports_scoreboard = supports_scoreboard
        self.supports_game_detail = supports_game_detail
        self.supports_standings = supports_standings
        self.supports_live_scores = supports_live_scores
        self.endpoint_labels = endpoint_labels or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sport": self.sport,
            "provider": self.provider,
            "supportsDiscovery": self.supports_discovery,
            "supportsDateSchedule": self.supports_date_schedule,
            "supportsScoreboard": self.supports_scoreboard,
            "supportsGameDetail": self.supports_game_detail,
            "supportsStandings": self.supports_standings,
            "supportsLiveScores": self.supports_live_scores,
            "endpointLabels": self.endpoint_labels,
        }

PROVIDER_CAPABILITY_MAP: Dict[str, ProviderCapability] = {
    "football": ProviderCapability(
        sport="football",
        provider="machina-sports/sports-skills",
        supports_discovery=True,
        supports_date_schedule=True,
        supports_scoreboard=True,
        supports_game_detail=True,
        supports_standings=True,
        supports_live_scores=True,
        endpoint_labels={
            "discovery": "sports_skills.football.get_competitions()",
            "schedule": "sports_skills.football.get_daily_schedule(date=...)",
            "event_summary": "sports_skills.football.get_event_summary(event_id=...)",
        },
    ),
    "basketball": ProviderCapability(
        sport="basketball",
        provider="machina-sports/sports-skills",
        supports_discovery=True,
        supports_date_schedule=True,
        supports_scoreboard=True,
        supports_game_detail=True,
        supports_standings=True,
        supports_live_scores=True,
        endpoint_labels={
            "scoreboard": "sports_skills.nba.get_scoreboard(date=...)",
            "live_scoreboard": "sports_skills.nba.get_live_scoreboard()",
            "game_summary": "sports_skills.nba.get_game_summary(game_id=...)",
        },
    ),
    "baseball": ProviderCapability(
        sport="baseball",
        provider="machina-sports/sports-skills",
        supports_discovery=True,
        supports_date_schedule=True,
        supports_scoreboard=True,
        supports_game_detail=True,
        supports_standings=True,
        supports_live_scores=True,
        endpoint_labels={
            "scoreboard": "sports_skills.mlb.get_scoreboard(date=...)",
            "game_summary": "sports_skills.mlb.get_game_summary(game_id=...)",
        },
    ),
    "hockey": ProviderCapability(
        sport="hockey",
        provider="machina-sports/sports-skills",
        supports_discovery=True,
        supports_date_schedule=True,
        supports_scoreboard=True,
        supports_game_detail=True,
        supports_standings=True,
        supports_live_scores=True,
        endpoint_labels={
            "scoreboard": "sports_skills.nhl.get_scoreboard(date=...)",
            "game_summary": "sports_skills.nhl.get_game_summary(game_id=...)",
        },
    ),
    "formula_1": ProviderCapability(
        sport="formula_1",
        provider="machina-sports/sports-skills + jolpica-f1",
        supports_discovery=True,
        supports_date_schedule=True,
        supports_scoreboard=False,
        supports_game_detail=True,
        supports_standings=True,
        supports_live_scores=False,
        endpoint_labels={
            "schedule": "jolpica.ergast.f1/{year}/races/ or sports_skills.f1.get_race_schedule()",
            "results": "jolpica.ergast.f1/{year}/{round}/results.json",
        },
    ),
}

def get_provider_capability(sport: str) -> ProviderCapability:
    norm = (sport or "").lower().strip()
    if norm in ("ice_hockey",):
        norm = "hockey"
    elif norm in ("f1",):
        norm = "formula_1"
    return PROVIDER_CAPABILITY_MAP.get(norm, ProviderCapability(norm, "unknown"))
