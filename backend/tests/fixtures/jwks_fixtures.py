"""
Local RSA keys, JWKS documents, and a fake httpx client for JWKS-path tests.

Lets tests exercise common/auth/auth0.py's JWKS caching, backoff, and rotation
logic without a container and without network access -- complementing (not
replacing) the live-Keycloak tests in tests/common/test_auth0.py and
tests/rbac_demo/test_keycloak_integration.py, which stay the source of truth
for real end-to-end verification.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt  # type: ignore[import-untyped]

ISSUER = "https://test-tenant.auth0.com/"
AUDIENCE = "https://api.example.com/"

@dataclass(frozen=True)
class TestKey:
    """An RSA keypair plus the JWKS entry and tokens derived from it."""

    kid: str
    private_pem: str
    jwk_entry: dict[str, str]

    def token(
        self,
        *,
        sub: Any = "auth0|test-user",
        issuer: str = ISSUER,
        audience: str = AUDIENCE,
        expires_in: int = 3600,
        extra_claims: dict[str, Any] | None = None,
    ) -> str:
        claims: dict[str, Any] = {
            "iss": issuer,
            "aud": audience,
            "iat": int(time.time()),
            "exp": int(time.time()) + expires_in,
        }
        if sub is not None:
            claims["sub"] = sub
        if extra_claims:
            claims.update(extra_claims)
        return str(
            jwt.encode(claims, self.private_pem, algorithm="RS256", headers={"kid": self.kid})
        )

def make_key(kid: str) -> TestKey:
    """
    Generate a 2048-bit RSA key and its RFC 7517 public JWK entry.

    python-jose's public_key().to_dict() returns only alg/kty/n/e -- no `kid`
    and no `use` -- which is exactly why _select_rsa_key must tolerate a
    missing `use` rather than indexing it.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_jwk = jwk.construct(private_pem, algorithm="RS256").public_key().to_dict()
    entry = {
        "kty": str(public_jwk["kty"]),
        "kid": kid,
        "alg": "RS256",
        "n": str(public_jwk["n"]),
        "e": str(public_jwk["e"]),
    }
    return TestKey(kid=kid, private_pem=private_pem, jwk_entry=entry)

def jwks_document(*keys: TestKey) -> dict[str, Any]:
    """Assemble a JWKS document from the given keys."""
    return {"keys": [k.jwk_entry for k in keys]}

class FakeClock:
    """
    Controllable stand-in for the `time` module inside auth0.py.

    auth0.py calls the module-global name `time`, so
    `monkeypatch.setattr(auth0_module, "time", FakeClock())` swaps the clock
    for that module alone -- no global time patching, no sleeping in tests.
    """

    def __init__(self, start: float = 1_000_000.0) -> None:
        self._now = start

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

@dataclass
class FakeJwksEndpoint:
    """
    Scripted replacement for httpx.AsyncClient inside _fetch_jwks_locked.

    `responses` is a queue of callables; each call to .get() pops the next one
    (the last is reused once exhausted, so a steady state can be expressed with
    a single entry). A callable may return a JWKS document or raise.
    """
    responses: list[Callable[[], dict[str, Any]]]
    calls: list[str] = field(default_factory=list)

    def client_factory(self) -> Callable[..., "FakeJwksEndpoint"]:
        """Return something usable as 'httpx.AsyncClient' itself."""

        def _factory(*_args: Any, **_kwargs: Any) -> "FakeJwksEndpoint":
            return self

        return _factory

    async def __aenter__(self) -> "FakeJwksEndpoint":
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    async def get(self, url: str, timeout: float | None = None) -> "FakeJwksResponse":
        self.calls.append(url)
        produce = self.responses[0] if len(self.responses) == 1 else self.responses.pop(0)
        return FakeJwksResponse(produce())

    @property
    def call_count(self) -> int:
        return len(self.calls)

@dataclass
class FakeJwksResponse:
    payload: dict[str, Any]

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload

def connect_error() -> dict[str, Any]:
    """A 'responses' entry that simulates the idP being unreachable."""
    raise httpx.ConnectError("simulated idP outage")

def http_500() -> dict[str, Any]:
    """A 'responses' entry that simulates the idP returning 5xx."""
    raise httpx.HTTPStatusError(
        "simulated 500",
        request=httpx.Request("GET", "https://idp.invalid/.well-known/jwks.json"),
        response=httpx.Response(500),
    )
