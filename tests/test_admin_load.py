"""
Admin endpoint load test — 50-100 concurrent requests to /admin/login,
/admin/settings/test, and /admin/suppliers/api. Verifies rate limiting
and CSRF hold under pressure with zero gate bypasses.
"""
import concurrent.futures
import json
import time
import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import app
from app.core.database import get_db
from app.core.models import Base
from app.core.security_middleware import limiter

BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) LoadTest"
CONCURRENT = 60


@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def client(db_engine):
    Session = sessionmaker(bind=db_engine)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    limiter.reset()
    with TestClient(
        app, base_url="http://127.0.0.1:8000", follow_redirects=False
    ) as c:
        c.headers.update({"User-Agent": BROWSER_UA})
        yield c
    app.dependency_overrides.clear()
    limiter.reset()


def _concurrent(client, method, path, total, data=None, headers=None):
    """Run `total` concurrent requests through the SAME client instance."""
    codes = []
    times_list = []

    def _hit():
        start = time.monotonic()
        try:
            if method == "GET":
                r = client.get(path, headers=headers or {})
            else:
                r = client.post(path, data=data or {}, headers=headers or {})
            return r.status_code, time.monotonic() - start
        except Exception:
            return 0, time.monotonic() - start

    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENT) as pool:
        futures = [pool.submit(_hit) for _ in range(total)]
        for f in concurrent.futures.as_completed(futures):
            code, dur = f.result()
            codes.append(code)
            times_list.append(dur)

    p50 = sorted(times_list)[len(times_list) // 2] if times_list else 0
    return {
        "label": path,
        "total": total,
        "codes": {c: codes.count(c) for c in sorted(set(codes))},
        "p50_ms": round(p50 * 1000),
    }


class TestAdminLoad:
    @pytest.mark.slow
    def test_login_blocks_after_rate_limit(self, client):
        """Pre-exhaust 10/min then 70 concurrent -> all 429, zero 200s."""
        for _ in range(10):
            client.post("/admin/login",
                        data={"email": "admin", "password": "wrong"})
        rep = _concurrent(
            client, "POST", "/admin/login", 70,
            data={"email": "admin", "password": "wrong"},
        )
        c = rep["codes"]
        assert c.get(200, 0) == 0, f"ZERO 200s: {c}"
        assert c.get(429, 0) == 70, f"All 429: {c}"

    @pytest.mark.slow
    def test_login_never_returns_200_under_50_concurrent(self, client):
        """50 concurrent wrong logins -> 401 or 429, never 200."""
        limiter.reset()
        rep = _concurrent(
            client, "POST", "/admin/login", 50,
            data={"email": "admin", "password": "wrong"},
        )
        c = rep["codes"]
        assert c.get(200, 0) == 0, f"ZERO 200s: {c}"
        assert c.get(401, 0) + c.get(429, 0) == 50

    @pytest.mark.slow
    def test_settings_test_csrf_blocks_all_80(self, client):
        """80 concurrent -> /admin/settings/test/ai -> all 401."""
        rep = _concurrent(
            client, "POST", "/admin/settings/test/ai", 80,
            data={"GOOGLE_API_KEY": "fake"},
        )
        c = rep["codes"]
        assert c.get(200, 0) == 0
        assert c.get(401, 0) == 80, f"All 401: {c}"

    @pytest.mark.slow
    def test_suppliers_api_blocks_all_80(self, client):
        """80 concurrent -> /admin/suppliers/api -> all 401."""
        rep = _concurrent(client, "GET", "/admin/suppliers/api", 80)
        c = rep["codes"]
        assert c.get(200, 0) == 0
        assert c.get(401, 0) == 80, f"All 401: {c}"

    @pytest.mark.slow
    def test_basic_auth_bypasses_csrf_consistently(self, client):
        """50 concurrent with valid Basic -> 400 or 429, never 200.
        With valid Basic auth the CSRF gate is bypassed and the handler
        runs (rejecting the missing csrf_token -> 400). After 10 requests
        the rate limiter kicks in -> 429. The critical invariant: ZERO 200s."""
        creds = base64.b64encode(b"admin:12345").decode()
        rep = _concurrent(
            client, "POST", "/admin/settings/test/ai", 50,
            data={"GOOGLE_API_KEY": "fake"},
            headers={"Authorization": f"Basic {creds}"},
        )
        c = rep["codes"]
        assert c.get(200, 0) == 0, f"ZERO 200s: {c}"
        # 400 (missing csrf_token) or 429 (rate limited) — both safe
        assert c.get(400, 0) + c.get(429, 0) == 50, f"400+429 = 50: {c}"
