from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from pydantic.json_schema import SkipJsonSchema


class UserProfile(BaseModel):
    """API representation of the authenticated user, backing GET/PATCH /api/v1/me.

    `id` comes from the validated JWT. `email` is Auth0's current address when
    Management API enrichment succeeds (a token's email claim goes stale after an
    email change until the token is reissued) and the JWT's claim otherwise.
    `name`/`email_verified` are best-effort enrichment from the Auth0 Management
    API (None when the Management API isn't configured or the call fails)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str  # Auth0 `sub`, e.g. "auth0|abc123" or "google-oauth2|..."
    email: str | None = None
    name: str | None = None
    provider: str = Field(
        description="Auth0 connection derived from the `sub` prefix, e.g. 'google-oauth2'."
    )
    email_verified: bool | None = Field(
        default=None, serialization_alias="emailVerified"
    )


def _omit_email_default(schema: dict[str, Any]) -> None:
    # Omission is not a JSON null default that a generated client should send.
    schema.pop("default", None)


class UpdateUserProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    # Email/password (Auth0 database) accounts only; other providers get a 400.
    # None represents omission internally; explicit null fails the validator.
    email: EmailStr | SkipJsonSchema[None] = Field(
        default=None,
        min_length=3,
        max_length=255,
        description="New email address; omit to leave unchanged. Null is not accepted.",
        json_schema_extra=_omit_email_default,
    )

    @field_validator("email", mode="before")
    @classmethod
    def require_plain_email(cls, value: object) -> object:
        # EmailStr accepts display-name mailboxes; this API accepts an address only.
        if value is None:
            raise ValueError("Email must not be null")
        if isinstance(value, str) and (
            len(value) > 255 or "<" in value or ">" in value
        ):
            raise ValueError(
                "A plain email address of at most 255 characters is required"
            )
        return value


class PasswordResetResponse(BaseModel):
    url: str


class VerificationEmailResponse(BaseModel):
    outcome: Literal["accepted", "already_verified"]
    message: str
