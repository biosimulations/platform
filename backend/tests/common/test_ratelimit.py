"""
Tests for the workflow-starting-endpoint rate limiter (TODO P1 #10).

Reuses tests/fixtures/jwks_fixtures.py's FakeClock -- ratelimit.py calls
time.time() via the module-global `time` name specifically so this fixture,
built for auth0.py, is directly reusable here unmodified.
"""

from collections.abc import Callable
from typing import Iterator

import pytest
from fastapi import HTTPException, Request
from pydantic import ValidationError

from biosim_server.common import ratelimit as ratelimit_module
from biosim_server.common.auth.auth0 import AuthenticatedUser
from biosim_server.config import RateLimitSettings, get_settings
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
        settings.password_reset_per_window,
        settings.password_reset_window_seconds,
        settings.page_per_window,
        settings.page_window_seconds,
        settings.legacy_per_window,
        settings.legacy_window_seconds,
    )
    yield
    (
        settings.enabled,
        settings.window_seconds,
        settings.authenticated_per_window,
        settings.anonymous_per_window,
        settings.password_reset_per_window,
        settings.password_reset_window_seconds,
        settings.page_per_window,
        settings.page_window_seconds,
        settings.legacy_per_window,
        settings.legacy_window_seconds,
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

    # Buckets with different window lengths share one dict, so a sweep triggered
    # by 60 s traffic must judge each bucket by its *own* expiry -- not by the
    # triggering request's window -- or it refills an exhausted 300 s reset quota.
    _RESET_USER = AuthenticatedUser(sub="auth0|abc", issuer="https://tenant.us.auth0.com/")
    _RESET_KEY = "password-reset:sub:https://tenant.us.auth0.com/:auth0|abc"

    @staticmethod
    def _configure_mixed_windows(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
        # Aligned to both 60 and 300, so every window boundary below is exact.
        clock = FakeClock(start=1_700_000_100.0)
        monkeypatch.setattr(ratelimit_module, "time", clock)
        settings = get_settings().ratelimit
        settings.password_reset_per_window = 5
        settings.password_reset_window_seconds = 300
        settings.legacy_per_window = 10_000
        settings.legacy_window_seconds = 60
        settings.page_per_window = 10_000
        settings.page_window_seconds = 60
        settings.anonymous_per_window = 10_000
        settings.window_seconds = 60
        return clock

    @staticmethod
    def _short_window_hit(source: str, client_host: str) -> None:
        request = _make_request(client_host=client_host)
        if source == "legacy":
            ratelimit_module.legacy_rate_limit(request)
        elif source == "page":
            ratelimit_module.page_rate_limit(request)
        else:
            ratelimit_module.workflow_rate_limit(request=request, user=None)

    def _drive_sweeps(self, source: str) -> None:
        # Twice the sampling interval guarantees at least one sweep runs.
        for i in range(2 * ratelimit_module._EVICT_EVERY_N_CHECKS):
            self._short_window_hit(source, f"198.51.100.{i}")

    def _assert_reset_denied(self, retry_after: str) -> None:
        with pytest.raises(HTTPException) as exc_info:
            ratelimit_module.password_reset_rate_limit(_make_request(), self._RESET_USER)
        assert exc_info.value.status_code == 429
        assert exc_info.value.headers == {"Retry-After": retry_after}

    @pytest.mark.parametrize("source", ["page", "workflow", "legacy"])
    def test_short_window_sweep_does_not_evict_an_active_reset_bucket(
        self, source: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = self._configure_mixed_windows(monkeypatch)
        sentinel_key = {"page": "pages:", "legacy": "legacy:", "workflow": ""}[source] + "ip:10.9.9.9"
        # A short-window bucket that expires at +60 s: its eviction proves a sweep ran.
        self._short_window_hit(source, "10.9.9.9")

        for _ in range(5):
            ratelimit_module.password_reset_rate_limit(_make_request(), self._RESET_USER)
        self._assert_reset_denied("301")

        clock.advance(60)
        self._drive_sweeps(source)
        assert sentinel_key not in ratelimit_module._rate_limit_buckets
        assert self._RESET_KEY in ratelimit_module._rate_limit_buckets
        self._assert_reset_denied("241")

        clock.advance(239)
        self._drive_sweeps(source)
        self._assert_reset_denied("2")

        # Usable again only at the reset bucket's own expiry boundary.
        clock.advance(1)
        ratelimit_module.password_reset_rate_limit(_make_request(), self._RESET_USER)

    def test_reset_bucket_is_evicted_once_its_own_window_expires(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = self._configure_mixed_windows(monkeypatch)
        for _ in range(5):
            ratelimit_module.password_reset_rate_limit(_make_request(), self._RESET_USER)
        self._assert_reset_denied("301")

        clock.advance(301)
        self._drive_sweeps("page")
        assert self._RESET_KEY not in ratelimit_module._rate_limit_buckets


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


class TestPasswordResetBudget:
    """AUTH-MIN-002: a dedicated ceiling/window, keyed by (issuer, subject)."""

    def test_reset_uses_its_own_ceiling_not_the_workflow_one(self) -> None:
        settings = get_settings().ratelimit
        settings.password_reset_per_window = 2
        settings.authenticated_per_window = 100
        request = _make_request()
        user = AuthenticatedUser(sub="auth0|abc", issuer="https://tenant.us.auth0.com/")

        for _ in range(2):
            ratelimit_module.password_reset_rate_limit(request, user)
        with pytest.raises(HTTPException) as exc_info:
            ratelimit_module.password_reset_rate_limit(request, user)
        assert exc_info.value.status_code == 429

        # Workflow starts are unaffected by the exhausted reset budget.
        ratelimit_module.workflow_rate_limit(request=request, user=user)

    def test_reset_uses_its_own_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Start exactly on a 300 s boundary so the elapsed-time arithmetic below
        # measures the window, not an unlucky phase offset.
        clock = FakeClock(start=1_700_000_100.0)
        monkeypatch.setattr(ratelimit_module, "time", clock)
        settings = get_settings().ratelimit
        settings.password_reset_per_window = 1
        settings.password_reset_window_seconds = 300
        request = _make_request()
        user = AuthenticatedUser(sub="auth0|abc", issuer="https://tenant.us.auth0.com/")

        ratelimit_module.password_reset_rate_limit(request, user)
        with pytest.raises(HTTPException):
            ratelimit_module.password_reset_rate_limit(request, user)

        # Still inside the reset window, though well past a 60 s workflow window.
        clock.advance(120)
        with pytest.raises(HTTPException):
            ratelimit_module.password_reset_rate_limit(request, user)

        clock.advance(181)
        ratelimit_module.password_reset_rate_limit(request, user)

    def test_reset_bucket_is_independent_of_the_workflow_bucket(self) -> None:
        settings = get_settings().ratelimit
        settings.password_reset_per_window = 1
        settings.authenticated_per_window = 1
        request = _make_request()
        user = AuthenticatedUser(sub="auth0|abc", issuer="https://tenant.us.auth0.com/")

        ratelimit_module.password_reset_rate_limit(request, user)
        with pytest.raises(HTTPException):
            ratelimit_module.password_reset_rate_limit(request, user)

        ratelimit_module.workflow_rate_limit(request=request, user=user)

    def test_subjects_are_scoped_by_issuer(self) -> None:
        """`auth0|abc` from two trusted issuers must not share a reset bucket."""
        settings = get_settings().ratelimit
        settings.password_reset_per_window = 1
        request = _make_request()
        tenant = AuthenticatedUser(sub="auth0|abc", issuer="https://tenant.us.auth0.com/")
        foreign = AuthenticatedUser(sub="auth0|abc", issuer="https://other.us.auth0.com/")

        ratelimit_module.password_reset_rate_limit(request, tenant)
        # A different issuer has its own budget; it does not consume the tenant's.
        ratelimit_module.password_reset_rate_limit(request, foreign)

        with pytest.raises(HTTPException):
            ratelimit_module.password_reset_rate_limit(request, foreign)

    def test_shared_workflow_budget_still_keys_on_subject_alone(self) -> None:
        """Only the sensitive reset budget needs (issuer, subject) granularity."""
        settings = get_settings().ratelimit
        settings.authenticated_per_window = 2
        request = _make_request()
        first = AuthenticatedUser(sub="auth0|abc", issuer="https://tenant.us.auth0.com/")
        second = AuthenticatedUser(sub="auth0|abc", issuer="https://other.us.auth0.com/")

        ratelimit_module.workflow_rate_limit(request=request, user=first)
        ratelimit_module.workflow_rate_limit(request=request, user=second)
        with pytest.raises(HTTPException):
            ratelimit_module.workflow_rate_limit(request=request, user=first)

    def test_reset_honours_the_kill_switch(self) -> None:
        settings = get_settings().ratelimit
        settings.enabled = False
        settings.password_reset_per_window = 1
        request = _make_request()
        user = AuthenticatedUser(sub="auth0|abc", issuer="https://tenant.us.auth0.com/")

        for _ in range(5):
            ratelimit_module.password_reset_rate_limit(request, user)


class TestPolicyValidation:
    """A malformed ceiling/window must fail where it is declared, not at 03:00."""

    @pytest.mark.parametrize(
        "overrides",
        [
            {"RATE_LIMIT_PASSWORD_RESET_PER_WINDOW": "0"},
            {"RATE_LIMIT_PASSWORD_RESET_PER_WINDOW": "-1"},
            {"RATE_LIMIT_PASSWORD_RESET_WINDOW_SECONDS": "0"},
            {"RATE_LIMIT_LEGACY_PER_WINDOW": "0"},
            {"RATE_LIMIT_LEGACY_PER_WINDOW": "-1"},
            {"RATE_LIMIT_LEGACY_WINDOW_SECONDS": "0"},
            {"RATE_LIMIT_LEGACY_WINDOW_SECONDS": "-1"},
            {"RATE_LIMIT_PAGE_PER_WINDOW": "0"},
            {"RATE_LIMIT_PAGE_WINDOW_SECONDS": "-5"},
        ],
    )
    def test_non_positive_reset_policy_values_are_rejected(self, overrides: dict[str, str]) -> None:
        with pytest.raises(ValidationError):
            RateLimitSettings(_env_file=None, **overrides)  # type: ignore[call-arg,arg-type]

    def test_reset_policy_defaults_are_conservative_and_distinct(self) -> None:
        settings = RateLimitSettings(_env_file=None)  # type: ignore[call-arg]
        assert settings.password_reset_per_window == 5
        assert settings.password_reset_window_seconds == 300
        assert settings.password_reset_per_window < settings.authenticated_per_window
        assert settings.password_reset_window_seconds > settings.window_seconds

    def test_page_policy_defaults_are_tunable_and_more_generous_than_a_workflow_start(self) -> None:
        settings = RateLimitSettings(_env_file=None)  # type: ignore[call-arg]
        assert settings.page_per_window == 60
        assert settings.page_window_seconds == 60
        # Browsing is the ordinary reading path, so a page view must be cheaper
        # than starting a workflow -- while still bounding the upstream fan-out.
        assert settings.page_per_window > settings.authenticated_per_window


class TestPageBudget:
    """The public page-aggregation bucket (addition to the audit's plan)."""

    def test_page_uses_its_own_ceiling_and_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Start exactly on a 60 s/300 s boundary so Retry-After is arithmetic, not luck.
        clock = FakeClock(start=1_700_000_100.0)
        monkeypatch.setattr(ratelimit_module, "time", clock)
        settings = get_settings().ratelimit
        settings.page_per_window = 2
        settings.page_window_seconds = 60
        settings.anonymous_per_window = 100
        request = _make_request()

        ratelimit_module.page_rate_limit(request)
        ratelimit_module.page_rate_limit(request)
        clock.advance(20)
        with pytest.raises(HTTPException) as exc_info:
            ratelimit_module.page_rate_limit(request)
        assert exc_info.value.status_code == 429
        assert exc_info.value.headers == {"Retry-After": "41"}

        # The exhausted page budget neither consumes nor is consumed by the
        # shared workflow budget, which is still untouched.
        ratelimit_module.workflow_rate_limit(request=request, user=None)

        # The window rolls over, so browsing resumes without any reset.
        clock.advance(40)
        ratelimit_module.page_rate_limit(request)

    def test_page_bucket_is_keyed_by_client_ip_only(self) -> None:
        """A token must not buy a larger page budget, nor reset an exhausted one."""
        settings = get_settings().ratelimit
        settings.page_per_window = 1
        request = _make_request()

        ratelimit_module.page_rate_limit(request)
        assert set(ratelimit_module._rate_limit_buckets) == {"pages:ip:203.0.113.5"}
        with pytest.raises(HTTPException):
            ratelimit_module.page_rate_limit(request)

    def test_one_ip_does_not_consume_another_ips_page_budget(self) -> None:
        settings = get_settings().ratelimit
        settings.page_per_window = 1

        ratelimit_module.page_rate_limit(_make_request(client_host="203.0.113.5"))
        ratelimit_module.page_rate_limit(_make_request(client_host="198.51.100.7"))


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


class TestLegacyBudget:
    def test_defaults_and_independent_environment_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RATE_LIMIT_LEGACY_PER_WINDOW", raising=False)
        monkeypatch.delenv("RATE_LIMIT_LEGACY_WINDOW_SECONDS", raising=False)
        settings = RateLimitSettings()
        assert (settings.legacy_per_window, settings.legacy_window_seconds) == (60, 60)
        monkeypatch.setenv("RATE_LIMIT_LEGACY_PER_WINDOW", "7")
        monkeypatch.setenv("RATE_LIMIT_LEGACY_WINDOW_SECONDS", "300")
        overridden = RateLimitSettings()
        assert (overridden.legacy_per_window, overridden.legacy_window_seconds) == (7, 300)
        assert overridden.page_per_window == settings.page_per_window
        assert overridden.page_window_seconds == settings.page_window_seconds

    def test_custom_window_and_ip_isolation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = get_settings().ratelimit
        settings.legacy_per_window = 1
        settings.legacy_window_seconds = 300
        clock = FakeClock(start=1_700_000_100.0)
        monkeypatch.setattr(ratelimit_module, "time", clock)
        request = _make_request()
        ratelimit_module.legacy_rate_limit(request)
        assert set(ratelimit_module._rate_limit_buckets) == {"legacy:ip:203.0.113.5"}
        ratelimit_module.legacy_rate_limit(_make_request("8.8.8.8"))
        clock.advance(60)
        with pytest.raises(HTTPException) as exc:
            ratelimit_module.legacy_rate_limit(request)
        assert exc.value.status_code == 429
        assert exc.value.headers == {"Retry-After": "241"}
        clock.advance(240)
        ratelimit_module.legacy_rate_limit(request)

    @pytest.mark.parametrize("legacy_first", [True, False])
    def test_all_other_budgets_are_independent(self, legacy_first: bool) -> None:
        settings = get_settings().ratelimit
        settings.legacy_per_window = 1
        settings.page_per_window = 1
        settings.anonymous_per_window = 1
        settings.password_reset_per_window = 1
        request = _make_request()
        user = AuthenticatedUser(sub="owner", issuer="https://issuer.test/")

        def exhaust_legacy() -> None:
            ratelimit_module.legacy_rate_limit(request)
            with pytest.raises(HTTPException) as exc:
                ratelimit_module.legacy_rate_limit(request)
            assert exc.value.status_code == 429

        if legacy_first:
            exhaust_legacy()
        operations: list[Callable[[], None]] = [
            lambda: ratelimit_module.page_rate_limit(request),
            lambda: ratelimit_module.workflow_rate_limit(request, None),
            lambda: ratelimit_module.compatibility_rate_limit(request, None),
            lambda: ratelimit_module.password_reset_rate_limit(request, user),
        ]
        for operation in operations:
            operation()
            with pytest.raises(HTTPException) as exc:
                operation()
            assert exc.value.status_code == 429
        if not legacy_first:
            exhaust_legacy()

    @pytest.mark.parametrize("trusted", [True, False])
    def test_forwarded_ip_uses_existing_trust_boundary(self, trusted: bool) -> None:
        get_settings().ratelimit.legacy_per_window = 1
        peer = "10.0.0.1" if trusted else "8.8.8.8"
        ratelimit_module.legacy_rate_limit(_make_request(peer, "1.1.1.1"))
        second = _make_request(peer, "9.9.9.9")
        if trusted:
            ratelimit_module.legacy_rate_limit(second)
        else:
            with pytest.raises(HTTPException) as exc:
                ratelimit_module.legacy_rate_limit(second)
            assert exc.value.status_code == 429
