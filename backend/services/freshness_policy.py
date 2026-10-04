from typing import Dict, Any, Optional
from datetime import datetime, timezone

class FreshnessPolicy:
    """
    Sport-specific and status-specific freshness evaluation.
    Avoids universal timeouts by enforcing domain-tailored TTLs.
    """
    TTL_CONFIG: Dict[str, Dict[str, int]] = {
        "football": {
            "live": 60,            # 1 minute
            "upcoming_today": 900,  # 15 minutes
            "upcoming_future": 3600,# 1 hour
            "completed": 86400,    # 24 hours
            "registry": 43200,     # 12 hours
        },
        "basketball": {
            "live": 45,            # 45 seconds (fast quarter updates)
            "upcoming_today": 900,  # 15 minutes
            "upcoming_future": 3600,# 1 hour
            "completed": 86400,
            "registry": 43200,
        },
        "baseball": {
            "live": 60,
            "upcoming_today": 900,
            "upcoming_future": 3600,
            "completed": 86400,
            "registry": 43200,
        },
        "hockey": {
            "live": 60,
            "upcoming_today": 900,
            "upcoming_future": 3600,
            "completed": 86400,
            "registry": 43200,
        },
        "formula_1": {
            "live": 120,
            "upcoming_today": 1800, # 30 minutes during race weekend
            "upcoming_future": 43200,# 12 hours
            "off_week": 86400,      # 24 hours
            "completed": 86400,
            "registry": 86400,
        },
    }

    @classmethod
    def get_ttl(cls, sport: str, status_category: str = "upcoming_today") -> int:
        sport_norm = sport.lower().strip()
        if sport_norm in ("ice_hockey",):
            sport_norm = "hockey"
        elif sport_norm in ("f1",):
            sport_norm = "formula_1"
        sport_ttls = cls.TTL_CONFIG.get(sport_norm, cls.TTL_CONFIG["football"])
        return sport_ttls.get(status_category, 900)

    @classmethod
    def is_fresh(cls, last_fetched_iso: Optional[str], sport: str, status_category: str = "upcoming_today") -> bool:
        if not last_fetched_iso:
            return False
        try:
            last_dt = datetime.fromisoformat(last_fetched_iso.replace("Z", "+00:00"))
            age_seconds = (datetime.now(timezone.utc) - last_dt).total_seconds()
            ttl = cls.get_ttl(sport, status_category)
            return age_seconds < ttl
        except Exception:
            return False

freshness_policy = FreshnessPolicy()
