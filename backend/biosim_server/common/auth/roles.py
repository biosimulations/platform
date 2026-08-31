from typing import TYPE_CHECKING, Callable, Coroutine, Any, Iterable, Literal

from fastapi import Depends, HTTPException, status

from biosim_server.common.auth.auth0 import AuthenticatedUser, get_current_user

if TYPE_CHECKING:
    # Type-only: at module level this would be a cycle, since simulations/__init__.py
    # pulls in simulations/database.py, which imports ADMIN_ROLE back from here.
    # auth is the lower layer of the two, so it must not import a domain package
    # to run -- only to type-check.
    from biosim_server.simulations.models import SimulationRunRecord

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


def _effective_visibility(record: "SimulationRunRecord") -> Literal["public", "private"]:
    """Resolve a record's visibility, applying legacy migration semantics.

    Only an explicit ``"private"`` is private. A missing/None visibility is a
    legacy document written before the field existed; those were world-readable
    and stay public (audit plan, section 7.3 -- "missing visibility: public, for
    legacy compatibility"). Note this is *not* the same as inferring visibility
    from the owner: an explicitly private record with no owner is still private,
    and therefore denied to everyone (see can_view_simulation_run).
    """
    return "private" if record.visibility == "private" else "public"


def can_view_simulation_run(user: AuthenticatedUser | None, record: "SimulationRunRecord") -> bool:
    """Public records are visible to anyone; private records to owner (sub) or admin."""
    if _effective_visibility(record) == "public":
        return True
    if user is None or not user.sub:
        return False
    if ADMIN_ROLE in user.roles:
        return True
    return record.owner_sub is not None and record.owner_sub == user.sub


def authorize_simulation_run_access(
    user: AuthenticatedUser | None,
    records: list["SimulationRunRecord"],
    *,
    action: str = "view",
) -> None:
    """Fail-closed view gate. 404 (not 403) so private IDs are not confirmed."""
    del action  # reserved for callers; existence is hidden on denial
    if not records:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Simulation run not found")
    if not all(can_view_simulation_run(user, record) for record in records):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Simulation run not found")


def authorize_simulation_run_mutation(
    user: AuthenticatedUser,
    records: Iterable["SimulationRunRecord"],
    *,
    action: str,
    allow_legacy_email: bool = False,
) -> None:
    """Admin always; else every record's owner_sub must match user.sub.

    Email is contact metadata, not an authorization identity: matching on it is
    off unless ``allow_legacy_email`` is explicitly True, which no caller does.
    The flag exists only so a deliberate, reviewed legacy-compatibility decision
    has somewhere to live -- it is not a default anywhere.
    """
    if ADMIN_ROLE in user.roles:
        return
    owned = list(records)
    if not user.sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authenticated identity missing subject")
    if owned and all(record.owner_sub is not None and record.owner_sub == user.sub for record in owned):
        return
    if (
        allow_legacy_email
        and user.email
        and owned
        and all(record.email == user.email for record in owned)
    ):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        f"Only the owner or an admin can {action} this simulation run",
    )
