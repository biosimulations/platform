/**
 * Auth0 Post-Login Action — biosimulations Platform.
 *
 * SOURCE OF TRUTH. This file is the reviewed, version-controlled copy of an
 * Action that actually runs inside Auth0. Committing it here does NOT deploy
 * it: Auth0 Actions are dashboard-managed. See ../README.md for the deploy
 * and reconciliation procedure.
 *
 * WHY IT EXISTS
 * -------------
 * Auth0 access tokens carry neither role assignments nor the user's email by
 * default. The Platform backend needs both:
 *   - roles  -> common/auth/roles.py :: require_roles, require_owner_or_admin
 *   - email  -> require_owner_or_admin's ownership check
 * Without this Action every role-gated endpoint returns 403, no admin exists,
 * and owners cannot act on their own simulation runs. The backend logs a
 * warning when it sees a validated token with no roles claim -- see
 * common/auth/auth0.py :: _warn_roles_claim_absent.
 *
 * IT ALSO ASSIGNS A DEFAULT ROLE
 * ------------------------------
 * A brand-new Auth0 user has no roles at all. This Action assigns the tenant's
 * "user" role on first login via the Management API, so a new sign-up can use
 * the product immediately instead of 403ing until an admin intervenes.
 *
 * REQUIRED ACTION SECRETS (Auth0 Dashboard -> Actions -> this Action -> Secrets).
 * NEVER commit their values:
 *   AUTH0_DOMAIN       tenant domain, e.g. tenant.us.auth0.com
 *   M2M_CLIENT_ID      client id of an M2M application authorized for the
 *                      Management API with scopes: read:roles, create:role_members
 *   M2M_CLIENT_SECRET  that application's secret
 *   DEFAULT_ROLE_ID    the id (rol_...) of the tenant's "user" Role
 *
 * REQUIRED ACTION DEPENDENCY (Auth0 Action editor -> Dependencies):
 *   auth0  (any 4.x)
 *
 * CLAIM NAMESPACE
 * ---------------
 * NAMESPACE below must match the backend's AUTH0_ROLES_CLAIM,
 * AUTH0_EMAIL_CLAIM, and AUTH0_EMAIL_VERIFIED_CLAIM settings exactly
 * (biosim_server/config.py -- defaults "https://api.biosimulations.org/roles",
 * ".../email", and ".../email_verified"). It is a namespace URI, not a URL:
 * nothing dereferences it, and it does NOT need to change if the Auth0 tenant
 * changes. If you change it here, change it in every overlay's api.env in the
 * same commit.
 *
 * The claim names below MUST use backtick template literals. Single-quoted
 * '${NAMESPACE}/...' is a literal string in JavaScript, not interpolation --
 * Auth0 would stamp a claim named ${NAMESPACE}/roles, which the backend never
 * reads.
 */

const { ManagementClient } = require("auth0");

const NAMESPACE = "https://api.biosimulations.org";

exports.onExecutePostLogin = async (event, api) => {
    let roles = (event.authorization && event.authorization.roles) || [];

    if (roles.length === 0) {
        const management = new ManagementClient({
            domain: event.secrets.AUTH0_DOMAIN,
            clientId: event.secrets.M2M_CLIENT_ID,
            clientSecret: event.secrets.M2M_CLIENT_SECRET,
        });
        try {
            await management.users.assignRoles(
                { id: event.user.user_id },
                { roles: [event.secrets.DEFAULT_ROLE_ID]}
            );
            roles = ["user"];
        } catch (e) {
            // Deliberately non-fatal: a Management API blip must not block login.
            // The user gets a token with no roles and 403s on role-gated endpoints
            // until their next login. The backend logs the empty claim.
            console.log("default role assignment failed", e && e.message);
        }
    }

    api.accessToken.setCustomClaim(`${NAMESPACE}/roles`, roles);
    api.idToken.setCustomClaim(`${NAMESPACE}/roles`, roles);
    api.accessToken.setCustomClaim(`${NAMESPACE}/email`, event.user.email);
    api.accessToken.setCustomClaim(`${NAMESPACE}/email_verified`, event.user.email_verified);
};
