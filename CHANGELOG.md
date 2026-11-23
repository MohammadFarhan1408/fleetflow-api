# Changelog

API contract changes, newest first. Every change to `openapi/schema.yaml`, an enum, an error code or auth behaviour gets an entry tagged `api-additive` or `api-breaking` (architecture §25). Consumers: fleetflow-admin, fleetflow-driver-app.

## Unreleased

### api-additive: users API and idempotency (Phase 1)

- `GET /api/v1/users/` (`users.view`) → cursor-paginated `{next, previous, results[]}`, newest first, `?page_size=` up to 100. Each result has:
  - `public_id`, `email`, `full_name`, `is_active`
  - `roles[]`
  - `branches[{public_id,name}]`
  - `last_login`, `created_at`
- `GET /api/v1/users/{public_id}/` (`users.view`). A user of another company returns 404.
- `POST /api/v1/users/` (`users.manage`) `{email, full_name, roles[], branches[]?}` → `201`. The user receives an email to set their password.
- `PATCH /api/v1/users/{public_id}/` (`users.manage`), any of `{full_name, roles[], branches[], is_active}` → `200`. Deactivation ends the user's sessions immediately.
- New error codes:
  - `user.email_taken` (409). Emails are unique across all companies.
  - `user.cannot_modify_self` (409): changing your own roles, branches or status.
  - `user.owner_required` (403): only owners grant or revoke owner, or change an owner's access.
  - `user.last_owner` (409)
  - `validation.invalid_reference` (400): branch not in your company.
  - `idempotency.invalid_key` (400)
  - `idempotency.key_reuse` (422)
- **`Idempotency-Key` header** (optional for now; architecture §20) on `POST /users/` and `PATCH /users/{id}/`:
  - Keys are scoped per user and kept 72 h.
  - A retry replays the first successful response with `Idempotent-Replayed: true`.
  - The same key with a different method, path or body gets 422.
  - Failed requests store nothing, so they can be retried with the same key.
- Methods a route does not implement answer `405 method_not_allowed`.

### api-additive: authentication and session bootstrap (Phase 1)

- `POST /api/v1/auth/login/` `{email, password}` → `200 {access, refresh}`. The email is case-insensitive.
- `POST /api/v1/auth/refresh/` `{refresh}` → `200 {access, refresh}`. Each refresh token rotates. Presenting an already-rotated token within 30 s returns a fresh pair; after 30 s it ends the whole session with `401 auth.token_invalid`.
- `POST /api/v1/auth/logout/` `{refresh}` → `204`. Ends that device's session, including its access token, immediately. Other devices stay logged in. Idempotent.
- `POST /api/v1/auth/password-reset/` `{email}` → `202`, whether or not the account exists.
- `POST /api/v1/auth/password-reset/confirm/` `{uid, token, new_password}` → `204`. Also completes invitations. Ends every session of the user.
- `GET /api/v1/me/` → `{user{public_id,email,full_name}, company{public_id,name}, branches[{public_id,name}], roles[], permissions[]}`.
- Bearer access tokens last 15 minutes and refresh tokens 30 days. All paths end with a trailing slash.
- Error envelope (architecture §6) on every error, with these new codes:
  - `auth.invalid_credentials` (401)
  - `auth.user_disabled` (401)
  - `auth.company_suspended` (403)
  - `auth.token_expired` (401): refresh and retry. This is the only code that should trigger a refresh.
  - `auth.token_invalid` (401): log in again
  - `auth.not_authenticated` (401)
  - `auth.account_locked` (429, `Retry-After`)
  - `throttled` (429, `Retry-After`)
  - `auth.invalid_reset_token` (400)
  - `app.update_required` (426)
  - `app.invalid_version` (400)
  - `validation.invalid` (400, field `errors`)
- Driver app: send `X-App-Version: X.Y.Z` on every request. Builds older than the server's minimum get 426.
