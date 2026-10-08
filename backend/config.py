import os
from typing import Optional
try:
    from pydantic import BaseModel
except ImportError:
    class BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

try:
    from dotenv import load_dotenv
    if os.path.exists("/.env"):
        load_dotenv(dotenv_path="/.env")
    load_dotenv()
except ImportError:
    pass

class Settings(BaseModel):
    # MongoDB Atlas Free (Operational / Current Data Only - Primary)
    mongodb_uri: Optional[str] = os.getenv("MONGODB_URI")
    mongodb_database: str = os.getenv("MONGODB_DATABASE", "predictpro")

    # Neon PostgreSQL (Sole Primary Operational Store)
    neon_database_url: Optional[str] = os.getenv("NEON_DATABASE_URL")
    database_routing_mode: str = os.getenv("DATABASE_ROUTING_MODE", "NEON_ONLY")  # NEON_ONLY
    neon_budget_max_queries_per_min: int = int(os.getenv("NEON_BUDGET_MAX_QUERIES_PER_MIN", "120"))
    neon_budget_max_connections: int = int(os.getenv("NEON_BUDGET_MAX_CONNECTIONS", "5"))
    neon_replication_batch_size: int = int(os.getenv("NEON_REPLICATION_BATCH_SIZE", "50"))
    neon_replication_enabled: bool = os.getenv("NEON_REPLICATION_ENABLED", "true").lower() in ("true", "1", "yes")

    # Automatic Failover Thresholds and Timers
    db_failover_auto_enabled: bool = os.getenv("DB_FAILOVER_AUTO_ENABLED", "true").lower() in ("true", "1", "yes")
    db_failover_failure_threshold: int = int(os.getenv("DB_FAILOVER_FAILURE_THRESHOLD", "3"))
    db_failover_recovery_threshold: int = int(os.getenv("DB_FAILOVER_RECOVERY_THRESHOLD", "3"))
    db_failover_cooldown_seconds: int = int(os.getenv("DB_FAILOVER_COOLDOWN_SECONDS", "60"))
    db_failover_max_lag_seconds: float = float(os.getenv("DB_FAILOVER_MAX_LAG_SECONDS", "300.0"))

    # Neon Resource Protection Safety Thresholds (Configurable, leaving significant headroom below Free quotas)
    neon_storage_limit_mb: int = int(os.getenv("NEON_STORAGE_LIMIT_MB", "500"))
    neon_warning_threshold_percent: float = float(os.getenv("NEON_WARNING_THRESHOLD_PERCENT", "75.0"))
    neon_hard_stop_threshold_percent: float = float(os.getenv("NEON_HARD_STOP_THRESHOLD_PERCENT", "90.0"))
    neon_egress_soft_limit_bytes: int = int(os.getenv("NEON_EGRESS_SOFT_LIMIT_BYTES", "52428800"))    # 50 MB
    neon_egress_hard_limit_bytes: int = int(os.getenv("NEON_EGRESS_HARD_LIMIT_BYTES", "104857600"))   # 100 MB
    neon_storage_soft_limit_bytes: int = int(os.getenv("NEON_STORAGE_SOFT_LIMIT_BYTES", str(int(int(os.getenv("NEON_STORAGE_LIMIT_MB", "500")) * float(os.getenv("NEON_WARNING_THRESHOLD_PERCENT", "75.0")) / 100 * 1024 * 1024)))) # Default 75% of limit
    neon_storage_hard_limit_bytes: int = int(os.getenv("NEON_STORAGE_HARD_LIMIT_BYTES", str(int(int(os.getenv("NEON_STORAGE_LIMIT_MB", "500")) * float(os.getenv("NEON_HARD_STOP_THRESHOLD_PERCENT", "90.0")) / 100 * 1024 * 1024)))) # Default 90% of limit
    neon_query_max_rows: int = int(os.getenv("NEON_QUERY_MAX_ROWS", "500"))
    neon_query_timeout_ms: int = int(os.getenv("NEON_QUERY_TIMEOUT_MS", "4000"))
    neon_api_key: Optional[str] = os.getenv("NEON_API_KEY")  # Optional authoritative Neon Management API token

    # Upstash Redis (Published / Live Feed and Cache)
    upstash_redis_rest_url: Optional[str] = os.getenv("UPSTASH_REDIS_REST_URL")
    upstash_redis_rest_token: Optional[str] = os.getenv("UPSTASH_REDIS_REST_TOKEN")

    # Cloudflare R2 (Historical Parquet Datasets)
    r2_account_id: Optional[str] = os.getenv("R2_ACCOUNT_ID")
    r2_bucket: str = os.getenv("R2_BUCKET", "predictpro-datasets")
    r2_endpoint: Optional[str] = os.getenv("R2_ENDPOINT")
    r2_access_key_id: Optional[str] = os.getenv("R2_ACCESS_KEY_ID")
    r2_secret_access_key: Optional[str] = os.getenv("R2_SECRET_ACCESS_KEY")
    r2_region: str = os.getenv("R2_REGION", "auto")

    # Operational Fixture Horizon and Ingestion Batching
    operational_fixture_horizon_days: int = int(os.getenv("OPERATIONAL_FIXTURE_HORIZON_DAYS", "14"))
    fixture_upsert_batch_size: int = int(os.getenv("FIXTURE_UPSERT_BATCH_SIZE", "100"))
    duckdb_path: str = os.getenv("DUCKDB_PATH", os.path.join(os.getcwd(), "data", "predictpro_operational.duckdb"))
    DUCKDB_PATH: str = os.getenv("DUCKDB_PATH", os.path.join(os.getcwd(), "data", "predictpro_operational.duckdb"))

    # Environment mode: development, test, production
    environment: str = os.getenv("ENVIRONMENT", os.getenv("NODE_ENV", "development"))

    # Admin Key for Synchronizations & Model Configuration (fail-closed if unset)
    admin_api_key: Optional[str] = os.getenv("ADMIN_API_KEY")

    # SportsSkills
    sports_skills_timeout_ms: int = int(os.getenv("SPORTS_SKILLS_TIMEOUT_MS", "5000"))
    sports_skills_base_url: str = os.getenv("SPORTS_SKILLS_BASE_URL", "https://api.sportsskills.io/v1")

    # Local Cache & Storage Paths for DuckDB and Parquet datasets (dynamically resolved)
    duckdb_path: str = os.getenv("DUCKDB_PATH") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "predictpro_persistent.duckdb")
    duckdb_parquet_path: str = os.getenv("DUCKDB_PARQUET_PATH") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "parquet")
    parquet_cache_dir: str = os.getenv("PARQUET_CACHE_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "parquet_cache")

    @property
    def DUCKDB_PATH(self) -> str:
        return self.duckdb_path

    @property
    def DUCKDB_PARQUET_PATH(self) -> str:
        return self.duckdb_parquet_path

    # Internal FastAPI port
    api_port: int = int(os.getenv("API_PORT", "8100"))

settings = Settings()
DATABASE_ROUTING_MODE = "NEON_ONLY"
