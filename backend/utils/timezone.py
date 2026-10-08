from datetime import datetime, timezone, timedelta
from typing import Union, Any

LAGOS_TZ = timezone(timedelta(hours=1))

def get_lagos_date_str(val: Any) -> str:
    if not val:
        return ""
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val.astimezone(LAGOS_TZ).strftime("%Y-%m-%d")
    if isinstance(val, str):
        try:
            clean = val.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(LAGOS_TZ).strftime("%Y-%m-%d")
        except Exception:
            return val[:10]
    return ""

def get_current_lagos_today() -> str:
    return datetime.now(LAGOS_TZ).strftime("%Y-%m-%d")
