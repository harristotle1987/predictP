import os
try:
    import httpx
except ImportError:
    httpx = None
try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    pa = None
    pq = None
from typing import Dict, Any, List
from backend.config import settings
from backend.db.duckdb_engine import duckdb_engine
from backend.db.r2_storage import r2_manager
from backend.utils.text_normalize import normalize_team_name

if pa is not None:
    HOCKEY_SCHEMA = pa.schema([
        ("match_id", pa.string()),
        ("match_date", pa.string()),
        ("league", pa.string()),
        ("home_team", pa.string()),
        ("away_team", pa.string()),
        ("home_team_key", pa.string()),
        ("away_team_key", pa.string()),
        ("home_score", pa.int32()),
        ("away_score", pa.int32()),
        ("status", pa.string()),
        ("home_shots", pa.int32()),
        ("away_shots", pa.int32()),
        ("home_pp_pct", pa.float32()),
        ("away_pp_pct", pa.float32()),
        ("home_pk_pct", pa.float32()),
        ("away_pk_pct", pa.float32()),
        ("elo1_pre", pa.float32()),
        ("elo2_pre", pa.float32()),
        ("source", pa.string()),
    ])
else:
    HOCKEY_SCHEMA = None

async def sync_hockey_historical_data() -> Dict[str, Any]:
    """
    Syncs real NHL hockey historical data into R2 Parquet and registers DuckDB views
    using official NHL historical schedules.
    """
    rows: List[Dict[str, Any]] = []
    seen_ids = set()

    # Query official NHL API across extensive multi-season regular season schedules
    # NHL weekly schedule endpoints return a 7-day gameWeek containing ~35-50 games each
    anchor_dates = [
        # 2023-24 Season
        "2023-10-16", "2023-10-23", "2023-10-30", "2023-11-06",
        "2023-11-13", "2023-11-20", "2023-11-27", "2023-12-04",
        "2023-12-11", "2023-12-18", "2023-12-25", "2024-01-01",
        "2024-01-08", "2024-01-15", "2024-01-22", "2024-01-29",
        "2024-02-05", "2024-02-12", "2024-02-19", "2024-02-26",
        "2024-03-04", "2024-03-11", "2024-03-18", "2024-03-25",
        "2024-04-01", "2024-04-08", "2024-04-15",
        # 2024-25 Season
        "2024-10-14", "2024-10-21", "2024-10-28", "2024-11-04",
        "2024-11-11", "2024-11-18", "2024-11-25", "2024-12-02",
        "2024-12-09", "2024-12-16", "2024-12-23", "2024-12-30",
        "2025-01-06", "2025-01-13", "2025-01-20", "2025-01-27",
    ]

    async with httpx.AsyncClient(timeout=15.0) as client:
        for anchor in anchor_dates:
            url = f"https://api-web.nhle.com/v1/schedule/{anchor}"
            try:
                res = await client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    game_weeks = data.get("gameWeek", [])
                    for gw in game_weeks:
                        for g in gw.get("games", []):
                            game_id = str(g.get("id", ""))
                            if not game_id or game_id in seen_ids:
                                continue

                            # Completed game states: FINAL, OFF
                            game_state = str(g.get("gameState", "")).upper()
                            if game_state not in ("FINAL", "OFF"):
                                continue

                            away_info = g.get("awayTeam", {})
                            home_info = g.get("homeTeam", {})

                            away_score = away_info.get("score")
                            home_score = home_info.get("score")
                            if away_score is None or home_score is None:
                                continue

                            away_name = (
                                away_info.get("placeName", {}).get("default", "") + " " +
                                away_info.get("commonName", {}).get("default", "")
                            ).strip()
                            home_name = (
                                home_info.get("placeName", {}).get("default", "") + " " +
                                home_info.get("commonName", {}).get("default", "")
                            ).strip()

                            if not away_name or not home_name:
                                away_name = away_info.get("abbrev", "Away")
                                home_name = home_info.get("abbrev", "Home")

                            start_utc = g.get("startTimeUTC", f"{gw.get('date', '2024-01-01')}T19:00:00Z")
                            seen_ids.add(game_id)

                            rows.append({
                                "match_id": f"nhl_{game_id}",
                                "match_date": start_utc,
                                "league": "National Hockey League (NHL)",
                                "home_team": home_name,
                                "away_team": away_name,
                                "home_team_key": normalize_team_name(home_name),
                                "away_team_key": normalize_team_name(away_name),
                                "home_score": int(home_score),
                                "away_score": int(away_score),
                                "status": "completed",
                                "home_shots": None,
                                "away_shots": None,
                                "home_pp_pct": None,
                                "away_pp_pct": None,
                                "home_pk_pct": None,
                                "away_pk_pct": None,
                                "elo1_pre": 1500.0,
                                "elo2_pre": 1500.0,
                                "source": "nhl-api-historical",
                            })
            except Exception as e:
                print(f"[HockeySync] Error fetching NHL week {anchor}: {e}")

    rows.sort(key=lambda x: x["match_date"])

    cache_dir = settings.parquet_cache_dir
    os.makedirs(cache_dir, exist_ok=True)
    parquet_filename = "hockey_v1_history.parquet"
    local_path = os.path.join(cache_dir, parquet_filename)

    table = pa.Table.from_pylist(rows, schema=HOCKEY_SCHEMA)
    pq.write_table(table, local_path)

    # Register in DuckDB
    duckdb_engine.register_parquet_view("hockey_matches", local_path)
    duckdb_engine.register_parquet_view("ice_hockey_matches", local_path)

    # Upload to R2 if configured
    try:
        r2_key = f"historical/{parquet_filename}"
        r2_manager.upload_file(local_path, r2_key)
    except Exception as e:
        print(f"[HockeySync] R2 upload notice: {e}")

    return {
        "status": "success",
        "sport": "hockey",
        "totalRows": len(rows),
        "parquetPath": local_path,
    }

class HockeyHistoricalSync:
    async def ingest_hockey(self, run_id: str = "default_run") -> Dict[str, Any]:
        return await sync_hockey_historical_data()

hockey_historical_sync = HockeyHistoricalSync()
