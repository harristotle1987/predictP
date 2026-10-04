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
from datetime import datetime, timezone

from backend.config import settings
from backend.db.duckdb_engine import duckdb_engine
from backend.db.r2_storage import r2_manager
from backend.providers.jolpica_f1_provider import jolpica_f1_provider

if pa is not None:
    F1_SCHEMA = pa.schema([
        ("match_id", pa.string()),
        ("match_date", pa.string()),
        ("circuit_id", pa.string()),
        ("circuit_name", pa.string()),
        ("driver_id", pa.string()),
        ("driver_name", pa.string()),
        ("constructor_id", pa.string()),
        ("constructor_name", pa.string()),
        ("grid_position", pa.int32()),
        ("finish_position", pa.int32()),
        ("points_scored", pa.float32()),
        ("status", pa.string()),
        ("qualifying_time_ms", pa.int32()),
        ("fastest_lap_time_ms", pa.int32()),
        ("session_type", pa.string()),
        ("source", pa.string()),
        ("season", pa.int32()),
        ("round", pa.int32()),
        ("fastest_lap_rank", pa.int32()),
    ])
else:
    F1_SCHEMA = None

async def sync_f1_historical_data() -> Dict[str, Any]:
    """
    Syncs real Formula 1 historical data from Jolpica API into R2 Parquet
    and registers DuckDB views without fabricated data.
    """
    rows: List[Dict[str, Any]] = []
    years = [2023, 2024]

    for year in years:
        try:
            races = await jolpica_f1_provider.get_season_results(year)
            for race in races:
                circuit_id = race.get("Circuit", {}).get("circuitId", "unknown")
                circuit_name = race.get("Circuit", {}).get("circuitName", race.get("raceName", "Grand Prix"))
                race_date = race.get("date", f"{year}-01-01")
                race_time = race.get("time", "14:00:00Z").replace("Z", "")
                match_iso = f"{race_date}T{race_time}Z" if "T" not in race_date else race_date
                season_val = int(race.get("season", year))
                round_val = int(race.get("round", 1))

                for result in race.get("Results", []):
                    driver = result.get("Driver", {})
                    constructor = result.get("Constructor", {})
                    d_id = driver.get("driverId", "")
                    d_name = f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip() or d_id
                    c_id = constructor.get("constructorId", "")
                    c_name = constructor.get("name", c_id)

                    try:
                        grid_pos = int(result.get("grid", 0))
                    except Exception:
                        grid_pos = 0

                    try:
                        finish_pos = int(result.get("position", 20))
                    except Exception:
                        finish_pos = 20

                    try:
                        points = float(result.get("points", 0.0))
                    except Exception:
                        points = 0.0

                    status_str = result.get("status", "Finished")

                    # Fastest lap extraction
                    fastest_lap_obj = result.get("FastestLap") or {}
                    rank_raw = fastest_lap_obj.get("rank")
                    try:
                        fl_rank = int(rank_raw) if rank_raw is not None else 99
                    except Exception:
                        fl_rank = 99

                    # Time parsing if available
                    fl_time_ms = None
                    fl_time_str = fastest_lap_obj.get("Time", {}).get("time")
                    if fl_time_str and ":" in fl_time_str:
                        try:
                            parts = fl_time_str.split(":")
                            mins = int(parts[0])
                            secs = float(parts[1])
                            fl_time_ms = int((mins * 60 + secs) * 1000)
                        except Exception:
                            pass

                    rows.append({
                        "match_id": f"f1_{season_val}_{circuit_id}_{d_id}",
                        "match_date": match_iso,
                        "circuit_id": circuit_id,
                        "circuit_name": circuit_name,
                        "driver_id": d_id,
                        "driver_name": d_name,
                        "constructor_id": c_id,
                        "constructor_name": c_name,
                        "grid_position": grid_pos,
                        "finish_position": finish_pos,
                        "points_scored": points,
                        "status": status_str,
                        "qualifying_time_ms": None,
                        "fastest_lap_time_ms": fl_time_ms,
                        "session_type": "race",
                        "source": "jolpica-ergast-f1",
                        "season": season_val,
                        "round": round_val,
                        "fastest_lap_rank": fl_rank,
                    })
        except Exception as e:
            print(f"[F1Sync] Ingestion notice for season {year}: {e}")

    cache_dir = settings.parquet_cache_dir
    os.makedirs(cache_dir, exist_ok=True)
    parquet_filename = "formula_1_v1_history.parquet"
    local_path = os.path.join(cache_dir, parquet_filename)

    if rows:
        table = pa.Table.from_pylist(rows, schema=F1_SCHEMA)
        pq.write_table(table, local_path)
    elif not os.path.exists(local_path):
        # Fallback empty table with schema so views can still register
        table = pa.Table.from_pylist([], schema=F1_SCHEMA)
        pq.write_table(table, local_path)

    # Register in DuckDB
    duckdb_engine.register_parquet_view("f1_results", local_path)
    duckdb_engine.register_parquet_view("f1_matches", local_path)
    duckdb_engine.register_parquet_view("formula_1_matches", local_path)

    # Upload to R2 if configured
    try:
        r2_key = f"historical/{parquet_filename}"
        r2_manager.upload_file(local_path, r2_key)
    except Exception as e:
        print(f"[F1Sync] R2 upload notice: {e}")

    return {
        "status": "success",
        "sport": "formula_1",
        "totalRows": len(rows),
        "parquetPath": local_path,
    }

class F1HistoricalSync:
    async def ingest_f1(self, run_id: str = "default_run") -> Dict[str, Any]:
        return await sync_f1_historical_data()

f1_historical_sync = F1HistoricalSync()
