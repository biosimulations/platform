import hashlib
import logging
from typing import Callable, Coroutine, Any, Iterable, Protocol

from fastapi import Depends, HTTPException, status

from biosim_server.common.auth.auth0 import AuthenticatedUser, get_current_user

logger = logging.getLogger(__name__)


def _log_authorization_denial(reason: str, user: AuthenticatedUser) -> None:
    logger.info(
        "Authorization denied",
        extra={
            "auth_outcome": "forbidden",
            "auth_reason": reason,
            "auth_subject_hash": hashlib.sha256(user.sub.encode()).hexdigest()[:12],
        },
    )

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
            _log_authorization_denial("missing_required_role", user)
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires one of role(s): {', '.join(allowed_roles)}",
            )
        return user

    return _check_roles

class _OwnedRecord(Protocol):
    """
    Structural type for anything require_owner_or_admin can check ownership
    against -- SimulationRunRecord satisfies this without importing it here
    (roles.py stays independent of the simulations package).
    """

    owner_sub: str | None
    email: str | None

def is_owner(user: AuthenticatedUser, record: _OwnedRecord) -> bool:
    """
    True if `user` owns `record`, independent of admin status.

    Primary key: owner_sub, set only from a verified token (P1 #7) -- never
    client-suppliable. Falls back to a verified-email match ONLY for legacy
    records with no owner_sub (P1 #8's dependency: an unverified email must
    never grant ownership, or #7's whole point is undermined by a caller who
    simply claims someone else's email).
    """
    if record.owner_sub is not None:
        return user.sub == record.owner_sub
    if not user.email_verified or user.email is None:
        return False
    return record.email == user.email


def require_owner_or_admin(
    user: AuthenticatedUser, records: Iterable[_OwnedRecord], *, action: str
) -> None:
    """Raises 403 unless `user` is an admin or owns every record.

    Called inline from a handler body (not a Depends factory) since it needs
    already-fetched records to check ownership against, not just the token.
    Ownership is `is_owner` (owner_sub, then verified-email fallback) -- not
    a raw email comparison, which would ignore owner_sub and treat an
    unverified email as proof of ownership.
    """
    if ADMIN_ROLE in user.roles:
        return
    owned = list(records)
    # all([]) is True in Python -- fail closed on an empty set rather than
    # vacuously allowing a non-admin. Current call sites 404 first, but
    # GET /results and /logs share this helper and must not inherit a
    # fail-open default.
    if not owned or not all(is_owner(user, record) for record in owned):
        _log_authorization_denial("ownership_required", user)
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Only the owner or an admin can {action} this simulation run",
        )

def is_ownerless(record: _OwnedRecord) -> bool:
    """
    True for a genuinely anonymous record: no owner_sub, and no email (or
    an email that was never captured as verified -- this codebase doesn't
    persist email_verified per-record, only per-request, so an ownerless
    record is judged purely on whether any identity was ever attached).
    Used by #11 to decide whether results/logs stay publicly readable.
    """

    return record.owner_sub is None and not record.email
