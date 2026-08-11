from typing import Callable, Coroutine, Any, Iterable

from fastapi import Depends, HTTPException, status

from biosim_server.common.auth.auth0 import AuthenticatedUser, get_current_user

# Shared role names for the Auth0Settings.roles_claim custom claim -- see
# config.py for the Auth0 Action setup that populates it.
ADMIN_ROLE = "admin"
PUBLISHER_ROLE = "publisher"
USER_ROLE = "user"


def require_roles(*allowed_roles: str) -> Callable[..., Coroutine[Any, Any, AuthenticatedUser]]:
    """FastAPI dependency factory gating a route to callers holding at least one of `allowed_roles`.

    Requires a valid bearer token (via get_current_user) *and* a non-empty
    intersection with AuthenticatedUser.roles, which is populated from the
    Auth0Settings.roles_claim custom claim -- see config.py for the Auth0
    Action setup this depends on.
    """

    async def _check_roles(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if not set(user.roles) & set(allowed_roles):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires one of role(s): {', '.join(allowed_roles)}",
            )
        return user

    return _check_roles


def require_owner_or_admin(user: AuthenticatedUser, owner_emails: Iterable[str | None], *, action: str) -> None:
    """Raises 403 unless `user` is an admin or owns every record (email in owner_emails).

    Called inline from a handler body (not a Depends factory) since it needs
    already-fetched records to check ownership against, not just the token.
    """
    if ADMIN_ROLE in user.roles:
        return
    if user.email is not None and any(email == user.email for email in owner_emails):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        f"Only the owner or an admin can {action} this simulation run",
    )
