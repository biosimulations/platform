# Verification email API

`POST /api/v1/me/resend-verification` requires a bearer token from the configured Auth0 tenant and a primary database account. Send no query string or request body, including `{}`. The API selects the account from the authenticated subject and checks the live Auth0 record before submitting a job.

- `202`: `{"outcome":"accepted","message":"Verification email request accepted. Check your inbox."}`. Auth0 acknowledged the job; delivery and verification are not confirmed.
- `200`: `{"outcome":"already_verified","message":"Your email is already verified."}`. No job is submitted.
- `400`: unsupported account; `401`: authentication failed; `403`: foreign tenant; `422`: unexpected input.
- `429`: local verification quota exhausted. `Retry-After` gives positive integer seconds.
- `503`: Management API is unconfigured, authentication is temporarily unavailable, or Auth0 throttled the operation. Throttling includes `Retry-After`.
- `502`: upstream failure, invalid response, or uncertain submission. A job is never automatically replayed. Clients must not automatically retry an uncertain submission.

All route responses carry `Cache-Control: no-store`; dependency failures retain their authentication challenge or retry hint. No address, job identifier, or verification link is returned. Router-level 404/405 responses are outside this route handler's policy.

## Verification quota

| Setting | Default |
| --- | --- |
| `RATE_LIMIT_VERIFICATION_EMAIL_PER_WINDOW` | 3 |
| `RATE_LIMIT_VERIFICATION_EMAIL_WINDOW_SECONDS` | 600 |

Both settings must be positive integers. The issuer/subject bucket is separate from password reset, workflows, compatibility, validation, pages, and legacy traffic. Attempts are charged after input, configuration, and tenant checks, before account eligibility and all upstream work. Unsupported accounts, already-verified accounts, and failed attempts are not refunded. Refreshing a token does not reset the bucket. `RATE_LIMIT_ENABLED=false` disables it alongside the other buckets.

These are engineering defaults, not measured Auth0 capacity. Counters are in memory per process, reset on restart, and can burst across fixed-window boundaries. Effective capacity multiplies with workers and replicas. This is not a distributed deduplication guarantee.

Transport logs are suppressed only within the resend operation's asynchronous context so HTTP client lookup URLs cannot disclose subjects. Concurrent unrelated requests retain their normal logging. Application failure logs contain fixed categories, without exception text or provider bodies.

The local test suite uses mocked transports. Tenant scopes, connection configuration, email delivery, and browser behavior require separate acceptance checks; the frontend migration remains a later plan phase.
