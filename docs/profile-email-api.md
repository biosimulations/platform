# Profile email updates

`PATCH /api/v1/me` accepts `name` and `email` only. Unknown fields return 422 before Auth0 is called, including mixed requests with an otherwise valid name. This changes the earlier behavior that silently ignored extra fields. Callers cannot set roles, identity selectors, or verification flags.

Omit `email` to leave it unchanged. Explicit null, empty or whitespace-only strings, malformed addresses, display-name mailboxes, non-string values, and addresses longer than 255 characters return 422. The validator checks syntax without DNS/delivery checks. Domain normalization preserves the local part's case. Existing name validation and null behavior are retained; an empty object performs no provider update.

The configured-tenant guard runs before Management API access. Email updates require a well-formed primary database subject. A mixed name/email request from a social account is rejected atomically; configured-tenant social users can still update their name alone.

Each supplied email is treated conservatively as potentially mail-producing, even when it matches the existing address. The server sends `email`, `email_verified: false`, and `verify_email: true`; these controls are not accepted from request JSON. Email-only and combined updates make one PATCH attempt on the pooled client within the shared operation deadline. Timeouts, transport loss, 5xx, redirects, and malformed success responses do not trigger another PATCH. Name-only updates keep their existing bounded retry policy.

| Condition | Response |
| --- | --- |
| Successful update | 200 with the provider-backed profile; the verification field is `emailVerified` |
| Auth0 email-update conflict (409) | Sanitized 409, `Email address is already in use` |
| Local validation or unknown property | 422 before Auth0 |
| Auth0 rejects profile fields (400/422) | Sanitized 422, `Auth0 rejected the profile update` |
| Foreign/missing issuer or unsupported email provider | Existing 403 or 400 guard response, before Auth0 |
| Auth0 throttles token acquisition or the update | 503 with a positive numeric `Retry-After` |
| Management API not configured | Existing 503 |
| Auth0 credential/scope failure, missing user, timeout, transport error, or other upstream failure | Sanitized 502; no automatic replay of an uncertain email PATCH |

Application error logs use fixed categories and do not include provider response bodies or exception text. PATCH and GET prefer Auth0's email over a stale JWT claim, with JWT email retained as the existing documented fallback when enrichment has no email. Updating the profile does not mutate the authenticated principal or rewrite an issued token.

Auth0's [update-user API](https://auth0.com/docs/api/management/v2/users/patch-users-by-id) documents the verification controls and primary/secondary identity restrictions. Local integration tests verify the outbound JSON and use one unchanged signed JWT for PATCH followed by GET, with the real token/update/read helpers over a mock transport. Those tests do not establish the real tenant's connection policy or prove email delivery. Disposable-account acceptance and the frontend migration remain separate plan gates.
