import os
import re
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
    from backend.app import app
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    TestClient = None
    app = None

from backend.config import settings


@unittest.skipIf(not HAS_FASTAPI, "fastapi not installed")
class AdminAuthenticationSecurityTestSuite(unittest.TestCase):
    """
    Production security test suite for administrator authentication,
    fail-closed enforcement, and credential isolation.
    """

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.test_secret = "test_verified_admin_token_98765"

    def setUp(self):
        # Save original configured key
        self._orig_admin_key = settings.admin_api_key
        # Configure a known test admin key for positive authorization tests
        settings.admin_api_key = self.test_secret

    def tearDown(self):
        # Restore original key
        settings.admin_api_key = self._orig_admin_key

    # =========================================================================
    # 1. Missing Admin Credential -> Rejected (401 Unauthorized)
    # =========================================================================
    def test_missing_admin_credential_rejected_on_all_admin_endpoints(self):
        """Every administrative endpoint must reject unauthenticated requests with 401."""
        admin_endpoints = [
            ("POST", "/api/admin/refresh", {}),
            ("POST", "/api/admin/sync-feed", {}),
            ("POST", "/api/model/config", {"activeModel": "ELO"}),
            ("POST", "/api/settings/model", {"activeModel": "POISSON"}),
            ("POST", "/api/v1/admin/sports-skills/sync", {}),
            ("GET", "/api/v1/admin/sports-skills/sync-runs", None),
            ("POST", "/api/v1/admin/sports-skills/reconcile", {}),
            ("POST", "/api/v1/admin/historical/ingest", None),
            ("GET", "/api/v1/admin/historical/datasets", None),
            ("GET", "/api/v1/admin/historical/coverage", None),
        ]

        for method, endpoint, payload in admin_endpoints:
            with self.subTest(endpoint=endpoint, method=method):
                if method == "POST":
                    resp = self.client.post(endpoint, json=payload or {})
                else:
                    resp = self.client.get(endpoint)

                self.assertEqual(
                    resp.status_code,
                    401,
                    f"Endpoint {method} {endpoint} must return 401 when credential is missing, got {resp.status_code}: {resp.text}",
                )
                self.assertIn("Unauthorized", resp.text)

    # =========================================================================
    # 2. Invalid Admin Credential -> Rejected (401 Unauthorized)
    # =========================================================================
    def test_invalid_admin_credential_rejected(self):
        """Invalid credentials via Header or Bearer token must be rejected with 401."""
        invalid_headers = [
            {"x-admin-api-key": "invalid_wrong_secret_123"},
            {"x-admin-api-key": "predictpro_admin_secret_key"},
            {"x-admin-api-key": "predictpro_admin_dev_key"},
            {"x-admin-api-key": ""},
            {"x-admin-api-key": " "},
            {"Authorization": "Bearer invalid_bearer_token_xyz"},
            {"Authorization": "Bearer "},
            {"Authorization": "Basic dXNlcjpwYXNz"},
        ]

        for headers in invalid_headers:
            with self.subTest(headers=headers):
                resp = self.client.post(
                    "/api/model/config",
                    json={"activeModel": "ELO"},
                    headers=headers,
                )
                self.assertEqual(
                    resp.status_code,
                    401,
                    f"Invalid header {headers} must be rejected with 401, got {resp.status_code}",
                )

                # Also test refresh
                resp_refresh = self.client.post(
                    "/api/admin/refresh",
                    headers=headers,
                )
                self.assertEqual(
                    resp_refresh.status_code,
                    401,
                    f"Invalid header {headers} on refresh must return 401, got {resp_refresh.status_code}",
                )

    # =========================================================================
    # 3. Valid Admin Credential -> Accepted
    # =========================================================================
    def test_valid_admin_credential_accepted_via_header_and_bearer(self):
        """Valid credentials via x-admin-api-key or Authorization Bearer must succeed."""
        # 1. Via x-admin-api-key header
        resp1 = self.client.post(
            "/api/model/config",
            json={"activeModel": "POISSON"},
            headers={"x-admin-api-key": self.test_secret},
        )
        self.assertEqual(resp1.status_code, 200, f"Valid header must return 200: {resp1.text}")
        data1 = resp1.json()
        self.assertEqual(data1["activeModel"], "POISSON")

        # 2. Via Authorization Bearer header
        resp2 = self.client.post(
            "/api/model/config",
            json={"activeModel": "ELO + POISSON"},
            headers={"Authorization": f"Bearer {self.test_secret}"},
        )
        self.assertEqual(resp2.status_code, 200, f"Valid Bearer token must return 200: {resp2.text}")
        data2 = resp2.json()
        self.assertEqual(data2["activeModel"], "ELO + POISSON")

    # =========================================================================
    # 4. Fail Closed: Unconfigured ADMIN_API_KEY -> 403 Forbidden
    # =========================================================================
    def test_unconfigured_admin_key_fails_closed(self):
        """When ADMIN_API_KEY is unset or empty, admin operations must fail closed with 403."""
        for empty_val in [None, "", "   "]:
            settings.admin_api_key = empty_val
            with self.subTest(empty_val=empty_val):
                # Even if caller provides a key or empty key, it must fail closed
                resp = self.client.post(
                    "/api/model/config",
                    json={"activeModel": "ELO"},
                    headers={"x-admin-api-key": "any_attempted_key"},
                )
                self.assertEqual(
                    resp.status_code,
                    403,
                    f"Unconfigured ADMIN_API_KEY must fail closed with 403, got {resp.status_code}: {resp.text}",
                )
                self.assertIn("disabled", resp.text.lower())

    # =========================================================================
    # 5. Normal Public Requests -> Do Not Receive Admin Privileges
    # =========================================================================
    def test_normal_public_requests_work_without_admin_privileges(self):
        """Public read-only endpoints function normally, while admin endpoints reject the same client."""
        # 1. Health check is public
        health_resp = self.client.get("/api/health")
        self.assertEqual(health_resp.status_code, 200)

        # 2. Predictions feed is public
        feed_resp = self.client.get("/api/predictions/feed?sport=football")
        self.assertEqual(feed_resp.status_code, 200)

        # 3. Available dates is public
        dates_resp = self.client.get("/api/predictions/available-dates")
        self.assertEqual(dates_resp.status_code, 200)

        # 4. Goals feed is public
        goals_resp = self.client.get("/api/predictions/goals")
        self.assertEqual(goals_resp.status_code, 200)

        # 5. Model config read-only is public
        model_get_resp = self.client.get("/api/model/config")
        self.assertEqual(model_get_resp.status_code, 200)

        # 6. But attempting to MODIFY model config without credentials fails
        model_post_resp = self.client.post("/api/model/config", json={"activeModel": "ELO"})
        self.assertEqual(model_post_resp.status_code, 401)

    # =========================================================================
    # 6. ADMIN_API_KEY Never Appears in Frontend or Public Output
    # =========================================================================
    def test_admin_api_key_never_appears_in_frontend_or_public_responses(self):
        """Verifies no admin keys are leaked to frontend files, bundles, or public responses."""
        root_dir = Path(__file__).resolve().parent.parent.parent

        # 1. Scan src/ directory
        src_dir = root_dir / "src"
        if src_dir.exists():
            for file_path in src_dir.rglob("*.ts*"):
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                self.assertNotIn(
                    "predictpro_admin_secret_key",
                    content,
                    f"Leaked secret in frontend source file: {file_path}",
                )
                self.assertNotIn(
                    "predictpro_admin_dev_key",
                    content,
                    f"Leaked secret in frontend source file: {file_path}",
                )
                # Ensure no VITE_ADMIN_API_KEY exists
                self.assertNotIn("VITE_ADMIN_API_KEY", content)

        # 2. Scan index.html
        index_html = root_dir / "index.html"
        if index_html.exists():
            html_content = index_html.read_text(encoding="utf-8")
            self.assertNotIn("predictpro_admin", html_content)
            self.assertNotIn("ADMIN_API_KEY", html_content)

        # 3. Check public API responses for secret leaks
        for endpoint in [
            "/api/health",
            "/api/predictions/feed",
            "/api/predictions/available-dates",
            "/api/model/config",
            "/api/settings/status",
            "/api/v1/health/data-sources",
        ]:
            resp = self.client.get(endpoint)
            self.assertNotIn(
                self.test_secret,
                resp.text,
                f"Secret leaked in public API response from {endpoint}",
            )
            self.assertNotIn("predictpro_admin_secret_key", resp.text)
            self.assertNotIn("predictpro_admin_dev_key", resp.text)

    # =========================================================================
    # 7. No Default Admin Secret in Codebase Configuration
    # =========================================================================
    def test_no_hardcoded_admin_secrets_in_server_or_config(self):
        """Verifies server.ts and backend/config.py contain no hardcoded fallback secrets."""
        root_dir = Path(__file__).resolve().parent.parent.parent

        # 1. Check server.ts
        server_ts = root_dir / "server.ts"
        if server_ts.exists():
            server_content = server_ts.read_text(encoding="utf-8")
            self.assertNotIn(
                "predictpro_admin_secret_key",
                server_content,
                "server.ts must not contain default admin secret",
            )
            self.assertNotIn(
                "proxyReq.setHeader('x-admin-api-key'",
                server_content,
                "server.ts must not auto-inject x-admin-api-key into proxy requests",
            )

        # 2. Check backend/config.py
        config_py = root_dir / "backend" / "config.py"
        if config_py.exists():
            config_content = config_py.read_text(encoding="utf-8")
            self.assertNotIn(
                "predictpro_admin_dev_key",
                config_content,
                "backend/config.py must not contain default dev admin key",
            )
            self.assertNotIn(
                "predictpro_admin_secret_key",
                config_content,
                "backend/config.py must not contain default admin secret",
            )


if __name__ == "__main__":
    unittest.main()
