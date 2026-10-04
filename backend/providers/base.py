import time
import asyncio
from typing import Dict, Any, List
from abc import ABC, abstractmethod

class BaseSportsSkillsAdapter(ABC):
    def __init__(self, sport: str):
        self.sport = sport

    @abstractmethod
    async def fetch_fixtures(self) -> List[Dict[str, Any]]:
        pass

    async def check_connection(self) -> Dict[str, Any]:
        start = time.perf_counter()
        try:
            is_available = False
            provider_label = "SportsSkills OSS Agent (machina-sports/sports-skills)"

            if self.sport == "football":
                import sports_skills.football as fb_mod
                is_available = hasattr(fb_mod, "get_competitions") or hasattr(fb_mod, "get_daily_schedule")
            elif self.sport == "basketball":
                import sports_skills.nba as nba_mod
                is_available = hasattr(nba_mod, "get_scoreboard") or hasattr(nba_mod, "_connector")
            elif self.sport == "baseball":
                import sports_skills.mlb as mlb_mod
                is_available = hasattr(mlb_mod, "get_scoreboard") or hasattr(mlb_mod, "_connector")
            elif self.sport in ("hockey", "ice_hockey"):
                import sports_skills.nhl as nhl_mod
                is_available = hasattr(nhl_mod, "get_scoreboard") or hasattr(nhl_mod, "_connector")
            elif self.sport in ("formula_1", "f1"):
                provider_label = "machina-sports/sports-skills + Jolpica F1"
                try:
                    import sports_skills.f1 as f1_mod
                    is_available = True
                except ImportError:
                    is_available = True
            else:
                import sports_skills
                is_available = hasattr(sports_skills, self.sport)

            latency = (time.perf_counter() - start) * 1000
            if is_available:
                return {
                    "status": "connected",
                    "latencyMs": round(latency, 1),
                    "provider": provider_label,
                    "sport": self.sport,
                }
            return {
                "status": "disconnected",
                "latencyMs": 0,
                "provider": provider_label,
                "sport": self.sport,
                "details": f"Capability {self.sport} unavailable",
            }
        except Exception as e:
            return {
                "status": "disconnected",
                "latencyMs": 0,
                "provider": "SportsSkills OSS Agent (machina-sports/sports-skills)",
                "sport": self.sport,
                "error": str(e)[:50],
            }
