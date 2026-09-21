"""
Tests for require_reindex_token's constant-time comparison (TODO P1 #15).

Follows the existing test-suite convention (flagged, not fixed, by TODO #24)
of mutating the lru_cache'd Settings singleton directly rather than through a
dependency-injection seam -- get_settings() returns the same Settings
instance across a test session, so tests restore the field they touch.
"""

from typing import Iterator

import pytest
from fastapi import HTTPException

from biosim_server.config import get_settings
from biosim_server.projects.router import require_reindex_token

@pytest.fixture(autouse=True)
def _restore_reindex_token() -> Iterator[None]:
    settings = get_settings()
    original = settings.project_reindex_token
    yield
    settings.project_reindex_token = original

class TestRequireReindexToken:
    """
    Every branch require_reindex_token can take, including the branch
    the compare_digest fix specifically had to avoid introducing: a
    TypeError on a missing header, which FastAPI would otherwise surface
    to the caller as an unhandled 500 instead of a clean 401.
    """

    def test_correct_token_passes(self) -> None:
        get_settings().project_reindex_token = "s3cr3t-reindex-token"
        # Does not raise
        require_reindex_token(authorization="Bearer s3cr3t-reindex-token")

    def test_wrong_token_is_401(self) -> None:
        get_settings().project_reindex_token = "s3cr3t-reindex-token"
        with pytest.raises(HTTPException) as exc_info:
            require_reindex_token(authorization="Bearer wrong-token-value")
        assert exc_info.value.status_code == 401

    def test_missing_header_is_401_not_a_typeerror_crash(self) -> None:
        """
        The regression this fix targets: secrets.compare_digest(None, str)
        raises TypeError. A missing Authorization header -- the ordinary
        shape of an unauthenticated request -- must still produce a clean
        401, not an unhandled exception surfaced as a 500.
        """
        get_settings().project_reindex_token = "s3cr3t-reindex-token"
        with pytest.raises(HTTPException) as exc_info:
            require_reindex_token(authorization=None)
        assert exc_info.value.status_code == 401

    def test_no_token_configured_is_503(self) -> None:
        """Existing, pre-fix behavior -- must be unchanged by the
        compare_digest swap. The endpoint stays disabled by default."""
        get_settings().project_reindex_token = ""
        with pytest.raises(HTTPException) as exc_info:
            require_reindex_token(authorization="Bearer anything-at-all")
        assert exc_info.value.status_code == 503

    def test_no_token_configured_is_503_even_with_no_header(self) -> None:
        """
        The 503 (disabled) check must run before the comparison, so a
        probe with no header at all against a disabled endpoint still gets
        503, not 401 -- the response should not leak whether the endpoint is
        merely mis-authenticated versus entirely turned off... actually it
        already does leak that distinction (503 vs 401), which is accepted:
        this is an internal operational endpoint, not a security boundary
        that depends on response-code indistinguishability.
        """
        get_settings().project_reindex_token = ""
        with pytest.raises(HTTPException) as exc_info:
            require_reindex_token(authorization=None)
        assert exc_info.value.status_code == 503

    def test_non_ascii_header_is_401_not_a_typeerror_crash(self) -> None:
        """Starlette decodes headers as latin-1; compare_digest(str) rejects
        non-ASCII and would 500 if we compared strings directly."""
        get_settings().project_reindex_token = "s3cr3t-reindex-token"
        with pytest.raises(HTTPException) as exc_info:
            require_reindex_token(authorization="Bearer tokën")
        assert exc_info.value.status_code == 401
