"""Worked example of Auth0-role-based access control for REST endpoints.

Three endpoints, in increasing order of restriction:
  - GET /api/v1/demo/public        -- no auth required
  - GET /api/v1/demo/private/me    -- any authenticated user; echoes back who the token identifies
  - GET /api/v1/demo/private/animal -- authenticated *and* holding one of admin/publisher/user

Role membership is read from AuthenticatedUser.roles, which get_current_user
populates from a custom claim on the access token (see Auth0Settings.roles_claim
in config.py) -- gating happens via the require_roles(...) dependency factory in
common/auth/roles.py, not by hand-checking user.roles in each handler body.
"""

from fastapi import APIRouter, Depends

from biosim_server.common.auth.auth0 import AuthenticatedUser, get_current_user
from biosim_server.common.auth.roles import require_roles
from biosim_server.rbac_demo.models import PublicMessage, RoleAnimalResponse, WhoAmIResponse

router = APIRouter(prefix="/api/v1/demo", tags=["RBAC Demo"])

ADMIN_ROLE = "admin"
PUBLISHER_ROLE = "publisher"
USER_ROLE = "user"

# Checked in this order so a caller holding multiple roles (e.g. admin +
# user) gets the most-privileged animal rather than an arbitrary one.
_ROLE_ANIMALS = [
    (ADMIN_ROLE, "Zebra"),
    (PUBLISHER_ROLE, "Giraffe"),
    (USER_ROLE, "Tiger"),
]


@router.get(
    "/public",
    response_model=PublicMessage,
    operation_id="demo-public",
    summary="Public endpoint -- accessible to everyone, no bearer token required",
)
async def public_endpoint() -> PublicMessage:
    return PublicMessage(message="This endpoint is public. Anyone can call it.")


@router.get(
    "/private/me",
    response_model=WhoAmIResponse,
    operation_id="demo-private-whoami",
    summary="Private endpoint -- any authenticated user, returns who the bearer token identifies",
)
async def whoami_endpoint(user: AuthenticatedUser = Depends(get_current_user)) -> WhoAmIResponse:
    return WhoAmIResponse(name=user.email or user.sub)


@router.get(
    "/private/animal",
    response_model=RoleAnimalResponse,
    operation_id="demo-private-animal",
    summary="Private endpoint -- requires admin, publisher, or user role; response varies by role",
)
async def animal_endpoint(
    user: AuthenticatedUser = Depends(require_roles(ADMIN_ROLE, PUBLISHER_ROLE, USER_ROLE)),
) -> RoleAnimalResponse:
    for role, animal in _ROLE_ANIMALS:
        if role in user.roles:
            return RoleAnimalResponse(role=role, animal=animal)
    # Unreachable: require_roles already guarantees one of the roles above is present.
    raise AssertionError("require_roles let through a user without admin/publisher/user role")
