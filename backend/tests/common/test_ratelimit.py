"""
Tests for the workflow-starting-endpoint rate limiter (TODO P1 #10).

Reuses tests/fixtures/jwks_fixtures.py's FakeClock -- ratelimit.py calls
time.time() via the module-global `time` name specifically so this fixture,
built for auth0.py, is directly reusable here unmodified.
"""

from typing import Iterator

import pytest
from fastapi import HTTPException, Request

from biosim_server.common import ratelimit as ratelimit_module
from biosim_server.common.auth.auth0 import AuthenticatedUser
from biosim_server.config import get_settings
from tests.fixtures.jwks_fixtures import FakeClock


def _make_request(client_host: str = "203.0.113.5", forwarded_for: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    scope = {
        "type": "http",
        "headers": headers,
        "client": (client_host, 12345),
    }
    return Request(scope)

@pytest.fixture(autouse=True)
def _reset_ratelimit_state() -> Iterator[None]:
    ratelimit_module._reset_rate_limit_state()
    yield
    ratelimit_module._reset_rate_limit_state()

@pytest.fixture(autouse=True)
def _restore_ratelimit_state() -> Iterator[None]:
    settings = get_settings().ratelimit
    original = (
        settings.enabled,
        settings.window_seconds,
        settings.authenticated_per_window,
        settings.anonymous_per_window,
    )
    yield
    (
        settings.enabled,
        settings.window_seconds,
        settings.authenticated_per_window,
        settings.anonymous_per_window,
    ) = original


class TestQuotaExhaustion:

    def test_exhausting_anonymous_quota_returns_429_with_retry_after(self) -> None:

        settings = get_settings().ratelimit
        settings.anonymous_per_window = 3
        settings.window_seconds = 60
        request = _make_request()

        for _ in range(3):
            ratelimit_module.workflow_rate_limit(request=request, user=None)

        with pytest.raises(HTTPException) as exc_info:
            ratelimit_module.workflow_rate_limit(request=request, user=None)
        assert exc_info.value.status_code == 429
        assert exc_info.value.headers is not None
        assert "Retry-After" in exc_info.value.headers
        assert int(exc_info.value.headers["Retry-After"]) > 0

class TestAuthenticatedVsAnonymousQuotas:
    def test_authenticated_quota_is_materially_higher_than_anonymous(self) -> None:
        settings = get_settings().ratelimit
        settings.anonymous_per_window = 2
        settings.authenticated_per_window = 10
        request = _make_request()
        user = AuthenticatedUser(sub="auth0|abc123", email="researcher@example.com")

        for _ in range(10):
            ratelimit_module.workflow_rate_limit(request=request, user=user)

        with pytest.raises(HTTPException) as exc_info:
            ratelimit_module.workflow_rate_limit(request=request, user=user)
        assert exc_info.value.status_code == 429

    def test_authenticated_and_anonymous_do_not_share_a_bucket(self) -> None:
        """
        A `sub`-keyed identity and an IP-keyed identity must never
        collide, even coincidentally -- the "sub:"/"ip:" namespacing in
        client_identity() is what this test pins.
        """
        settings = get_settings().ratelimit
        settings.anonymous_per_window = 1
        settings.authenticated_per_window = 1
        request = _make_request()
        user = AuthenticatedUser(sub="203.0.113.5", email=None)

        ratelimit_module.workflow_rate_limit(request=request, user=None)

        ratelimit_module.workflow_rate_limit(request=request, user=user)

class TestWindowReset:
    def test_quota_resets_after_the_window_elapses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = FakeClock(start=1_700_000_000.0)
        monkeypatch.setattr(ratelimit_module, "time", clock)
        settings = get_settings().ratelimit
        settings.anonymous_per_window = 2
        settings.window_seconds = 60
        request = _make_request()

        ratelimit_module.workflow_rate_limit(request=request, user=None)
        ratelimit_module.workflow_rate_limit(request=request, user=None)
        with pytest.raises(HTTPException):
            ratelimit_module.workflow_rate_limit(request=request, user=None)
        
        clock.advance(61)

        ratelimit_module.workflow_rate_limit(request=request, user=None)
        

class TestPerKeyIsolation:
    def test_one_ip_does_not_consume_another_ips_quota(self) -> None:
        settings = get_settings().ratelimit
        settings.anonymous_per_window = 1
        request_a = _make_request(client_host="10.0.0.1")
        request_b = _make_request(client_host="10.0.0.2")

        ratelimit_module.workflow_rate_limit(request=request_a, user=None)
        ratelimit_module.workflow_rate_limit(request=request_b, user=None)

        with pytest.raises(HTTPException):
            ratelimit_module.workflow_rate_limit(request=request_a, user=None)

    def test_two_subs_do_not_share_a_bucket(self) -> None:
        settings = get_settings().ratelimit
        settings.authenticated_per_window = 1
        request = _make_request()
        user_a = AuthenticatedUser(sub="auth0|user-a", email=None)
        user_b = AuthenticatedUser(sub="auth0|user-b", email=None)

        ratelimit_module.workflow_rate_limit(request=request, user=user_a)
        ratelimit_module.workflow_rate_limit(request=request, user=user_b)

        with pytest.raises(HTTPException):
            ratelimit_module.workflow_rate_limit(request=request, user=user_a)

class TestXForwardedFor:
    def test_first_hop_of_x_forwarded_for_is_used_when_present(self) -> None:
        settings = get_settings().ratelimit
        settings.anonymous_per_window = 1
        request = _make_request(client_host="10.0.0.1", forwarded_for="203.0.113.9, 10.0.0.1")

        key, authenticated = ratelimit_module.client_identity(None, request)
        assert authenticated is False
        assert key == "ip:203.0.113.9"

    def test_falls_back_to_asgi_client_host_when_header_absent(self) -> None:
        request = _make_request(client_host="10.0.0.1", forwarded_for=None)
        key, authenticated = ratelimit_module.client_identity(None, request)
        assert authenticated is False
        assert key == "ip:10.0.0.1"

    def test_public_peer_does_not_trust_client_supplied_x_forwarded_for(self) -> None:
        """A caller who reached the process directly can set X-Forwarded-For
        to anything; that header is ignored unless the ASGI peer is a proxy.

        Use a globally-routable address (not RFC 5737 documentation space):
        Python 3.13+ classifies 203.0.113.0/24 as is_private.
        """
        request = _make_request(client_host="8.8.8.8", forwarded_for="198.51.100.9")
        key, authenticated = ratelimit_module.client_identity(None, request)
        assert authenticated is False
        assert key == "ip:8.8.8.8"


class TestKillSwitch:
    def test_rate_limit_enabled_false_disables_enforcement(self) -> None:
        settings = get_settings().ratelimit
        settings.enabled = False
        settings.anonymous_per_window = 1
        request = _make_request()

        for _ in range(5):
            ratelimit_module.workflow_rate_limit(request=request, user=None)


class TestEviction:
    def test_stale_buckets_are_evicted_after_the_window_elapses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = FakeClock(start=1_700_000_000.0)
        monkeypatch.setattr(ratelimit_module, "time", clock)
        settings = get_settings().ratelimit
        settings.anonymous_per_window = 100
        settings.window_seconds = 60

        for i in range(5):
            ratelimit_module.workflow_rate_limit(
                request=_make_request(client_host=f"10.0.0.{i + 1}"), user=None
            )
        assert len(ratelimit_module._rate_limit_buckets) == 5

        clock.advance(61)
        # Force a sweep: eviction runs every N checks, so drive past that.
        for i in range(ratelimit_module._EVICT_EVERY_N_CHECKS):
            ratelimit_module.workflow_rate_limit(
                request=_make_request(client_host=f"10.0.1.{i + 1}"), user=None
            )
        stale = [
            key
            for key, bucket in ratelimit_module._rate_limit_buckets.items()
            if str(key).startswith("ip:10.0.0.")
        ]
        assert stale == []


class TestConcurrency:
    def test_concurrent_hits_do_not_undercount_the_quota(self) -> None:
        import threading

        settings = get_settings().ratelimit
        settings.anonymous_per_window = 50
        request = _make_request()
        denied = 0
        lock = threading.Lock()

        def _hit() -> None:
            nonlocal denied
            try:
                ratelimit_module.workflow_rate_limit(request=request, user=None)
            except HTTPException as exc:
                assert exc.status_code == 429
                with lock:
                    denied += 1

        threads = [threading.Thread(target=_hit) for _ in range(80)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert denied == 30
        bucket = next(iter(ratelimit_module._rate_limit_buckets.values()))
        assert int(bucket["count"]) == 80


class TestCompatibilityQuotaIsIndependent:
    def test_compat_bucket_does_not_share_workflow_budget(self) -> None:
        settings = get_settings().ratelimit
        settings.anonymous_per_window = 1
        settings.window_seconds = 60
        request = _make_request()

        ratelimit_module.workflow_rate_limit(request=request, user=None)
        with pytest.raises(HTTPException) as exc_info:
            ratelimit_module.workflow_rate_limit(request=request, user=None)
        assert exc_info.value.status_code == 429

        # Compatibility checks use a separate key prefix, so exhausting the
        # workflow budget must not deny the run wizard.
        ratelimit_module.compatibility_rate_limit(request=request, user=None)

        with pytest.raises(HTTPException) as exc_info:
            ratelimit_module.compatibility_rate_limit(request=request, user=None)
        assert exc_info.value.status_code == 429
