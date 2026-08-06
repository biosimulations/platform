from pydantic import BaseModel, ConfigDict, Field


class UserProfile(BaseModel):
    """API representation of the authenticated user, backing GET/PATCH /api/v1/me.

    `id`/`email` come from the validated JWT; `name`/`email_verified` are
    best-effort enrichment from the Auth0 Management API (None when the
    Management API isn't configured or the call fails)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str  # Auth0 `sub`, e.g. "auth0|abc123" or "google-oauth2|..."
    email: str | None = None
    name: str | None = None
    provider: str = Field(description="Auth0 connection derived from the `sub` prefix, e.g. 'google-oauth2'.")
    email_verified: bool | None = Field(default=None, serialization_alias="emailVerified")


class UpdateUserProfileRequest(BaseModel):
    name: str | None = None
