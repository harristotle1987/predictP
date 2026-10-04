import os
import time
from typing import Optional, Dict, Any
try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError
except ImportError:
    boto3 = None
    Config = None
    ClientError = Exception

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    pa = None
    pq = None
from datetime import datetime, timedelta
from backend.config import settings
from backend.utils.text_normalize import normalize_team_name

class R2StorageManager:
    def __init__(self):
        self.bucket = settings.r2_bucket
        self.cache_dir = settings.parquet_cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        self._s3_client = None

    def get_client(self):
        if boto3 is None or Config is None:
            return None
        if not settings.r2_endpoint or not settings.r2_access_key_id or not settings.r2_secret_access_key:
            return None
        if self._s3_client is None:
            try:
                self._s3_client = boto3.client(
                    "s3",
                    endpoint_url=settings.r2_endpoint,
                    aws_access_key_id=settings.r2_access_key_id,
                    aws_secret_access_key=settings.r2_secret_access_key,
                    region_name=settings.r2_region,
                    config=Config(signature_version="s3v4", connect_timeout=3, read_timeout=5),
                )
            except Exception as e:
                print(f"[R2] Failed to initialize client: {e}")
                self._s3_client = None
        return self._s3_client

    def check_connection(self) -> Dict[str, Any]:
        client = self.get_client()
        if not client:
            return {
                "status": "disconnected",
                "latencyMs": 0,
                "syncedArtifacts": 0,
                "bucketName": settings.r2_bucket,
                "details": "R2 credentials or endpoint not configured",
            }
        try:
            start = time.perf_counter()
            # Perform head_bucket and list_objects_v2 to get real artifact count
            client.head_bucket(Bucket=self.bucket)
            list_resp = client.list_objects_v2(Bucket=self.bucket, MaxKeys=100)
            latency = (time.perf_counter() - start) * 1000

            contents = list_resp.get("Contents", [])
            artifact_count = len(contents)
            
            if artifact_count > 0:
                details = f"Connected with {artifact_count} artifacts"
            else:
                details = "Connected with zero artifacts"

            return {
                "status": "connected",
                "latencyMs": round(latency, 1),
                "syncedArtifacts": artifact_count,
                "bucketName": self.bucket,
                "details": details,
            }
        except ClientError as e:
            error_code = str(e.response.get("Error", {}).get("Code", ""))
            http_status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            
            if error_code in ("403", "AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch") or http_status == 403:
                return {
                    "status": "authentication_failure",
                    "latencyMs": 0,
                    "syncedArtifacts": 0,
                    "bucketName": self.bucket,
                    "details": f"Authentication failure: {error_code or 'Access Denied'}",
                }
            elif error_code in ("404", "NoSuchBucket", "NotFound") or http_status == 404:
                return {
                    "status": "bucket_not_found",
                    "latencyMs": 0,
                    "syncedArtifacts": 0,
                    "bucketName": self.bucket,
                    "details": f"Bucket not found: {self.bucket}",
                }
            else:
                return {
                    "status": "degraded",
                    "latencyMs": 0,
                    "syncedArtifacts": 0,
                    "bucketName": self.bucket,
                    "details": f"Client error: {error_code or str(e)[:50]}",
                }
        except Exception as e:
            err_str = str(e).lower()
            if "endpoint" in err_str or "connect" in err_str or "timeout" in err_str or "dns" in err_str:
                status_type = "endpoint_failure"
            else:
                status_type = "disconnected"
            return {
                "status": status_type,
                "latencyMs": 0,
                "syncedArtifacts": 0,
                "bucketName": self.bucket,
                "details": f"{status_type.replace('_', ' ').title()}: {str(e)[:50]}",
            }

    def get_local_parquet_path(self, sport: str, version: str = "v1") -> str:
        """
        Returns local cached path to versioned historical Parquet dataset.
        Checks local cache and bundled repository directory first, then downloads from R2 if present.
        Raises RuntimeError if unavailable from both local bundled store and R2.
        """
        local_path = os.path.join(self.cache_dir, f"{sport}_{version}_history.parquet")
        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            return local_path

        # Check bundled parquet_cache directory in repository
        repo_bundled = [
            os.path.join(os.getcwd(), "parquet_cache", f"{sport}_{version}_history.parquet"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "parquet_cache", f"{sport}_{version}_history.parquet"),
        ]
        if sport in ("formula1", "f1"):
            repo_bundled.extend([
                os.path.join(os.getcwd(), "parquet_cache", f"formula_1_{version}_history.parquet"),
                os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "parquet_cache", f"formula_1_{version}_history.parquet"),
            ])

        for bp in repo_bundled:
            if os.path.exists(bp) and os.path.getsize(bp) > 0:
                return bp

        client = self.get_client()
        if client:
            s3_key = f"datasets/{version}/{sport}/history.parquet"
            try:
                print(f"[R2] Downloading {s3_key} from R2 bucket {self.bucket}...")
                client.download_file(self.bucket, s3_key, local_path)
                if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
                    return local_path
            except ClientError as e:
                print(f"[R2] ClientError downloading from R2: {e}.")
            except Exception as e:
                print(f"[R2] Could not download from R2: {e}.")

        raise RuntimeError(
            f"Required real historical {sport} Parquet dataset is unavailable from R2 and local cache"
        )

    def upload_file(self, local_path: str, s3_key: str):
        """Uploads a file to R2."""
        client = self.get_client()
        if not client:
            raise RuntimeError("R2 client not configured")
        
        try:
            client.upload_file(local_path, self.bucket, s3_key)
        except ClientError as e:
            print(f"[R2] ClientError uploading {local_path} to {s3_key}: {e}")
            raise e
        except Exception as e:
            print(f"[R2] Failed to upload {local_path} to {s3_key}: {e}")
            raise e

r2_manager = R2StorageManager()
