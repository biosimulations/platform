"""A local RS256 signing key + JWKS, for JWT-verification negative tests.

The real-provider path is covered by the Keycloak container tests (see
tests/rbac_demo/test_keycloak_integration.py and the Keycloak half of
tests/common/test_auth0.py). What a live IdP *can't* hand us on demand is an
already-expired token, a token signed by the wrong key, or one signed with a
disallowed algorithm -- so those cases are signed here with a real RSA key and
verified through the real ``get_current_user`` code path, JWKS fetch included.
This is not a shortcut around verification: the signature, the algorithm
allowlist, the audience, the issuer and the expiry are all genuinely checked.
"""

import base64
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt  # type: ignore[import-untyped]

TEST_ISSUER = "https://test-idp.example.com/"
TEST_AUDIENCE = "https://api.biosimulations.org"


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass
class SigningKey:
    """One RSA keypair, its JWKS entry, and a token minter."""

    kid: str
    _private: rsa.RSAPrivateKey = field(repr=False)

    @classmethod
    def generate(cls, kid: str = "test-key-1") -> "SigningKey":
        return cls(kid=kid, _private=rsa.generate_private_key(public_exponent=65537, key_size=2048))

    @property
    def pem(self) -> str:
        return self._private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")

    def jwk(self) -> dict[str, str]:
        numbers = self._private.public_key().public_numbers()
        return {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": self.kid,
            "n": _b64url_uint(numbers.n),
            "e": _b64url_uint(numbers.e),
        }

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self.jwk()]}

    def token(
        self,
        *,
        sub: str | None = "auth0|alice",
        audience: str = TEST_AUDIENCE,
        issuer: str = TEST_ISSUER,
        expires_in: timedelta = timedelta(minutes=5),
        algorithm: str = "RS256",
        kid: str | None = None,
        extra_claims: dict[str, Any] | None = None,
    ) -> str:
        now = datetime.now(timezone.utc)
        claims: dict[str, Any] = {
            "aud": audience,
            "iss": issuer,
            "iat": int(now.timestamp()),
            "exp": int((now + expires_in).timestamp()),
        }
        if sub is not None:
            claims["sub"] = sub
        claims.update(extra_claims or {})
        return str(jwt.encode(claims, self.pem, algorithm=algorithm, headers={"kid": kid or self.kid}))
