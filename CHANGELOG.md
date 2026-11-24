# Changelog

API contract changes, newest first. Every change to `openapi/schema.yaml`, an enum, an error code or auth behaviour gets an entry tagged `api-additive` or `api-breaking` (architecture §25). Consumers: fleetflow-admin, fleetflow-driver-app.

## Unreleased

### api-additive: uploads (Phase 3 PR 1)

First use of the §19 upload flow. Files go straight to private storage with a presigned URL; the API verifies them at confirm.

- **Start.** `POST /api/v1/uploads/` `{purpose, content_type, size}` (`uploads.create`) → `201` `{id, upload_url, method: "PUT", headers, expires_at}`. Send the file with `method` to `upload_url` with exactly these `headers` (the size is signed too) before `expires_at` (5 minutes). Limited to 60 requests per hour per user.
  - `purpose`: `fleet_document` (more arrive with their features). `content_type`: `image/jpeg`, `image/png` or `application/pdf`. `size` in bytes: at most 10 MB for images and 20 MB for PDFs, otherwise 400 `upload.too_large`. An unsupported type or purpose is a normal 400 `validation.invalid`.
- **Confirm.** `POST /api/v1/uploads/{id}/confirm/` (`uploads.create`; only the user who started it, anyone else gets 404) → `200` `{id, purpose, content_type, size, status, confirmed_at}`. The API checks the stored file and removes EXIF, XMP and other metadata from JPEG and PNG (the returned `size` is the cleaned file's). Repeating a successful confirm returns the same result. A rejected file is deleted and the upload must be started again.
- **Uploads that nothing uses are deleted after 24 hours.**
- **Enums:** `UploadPurposeEnum`, `UploadContentTypeEnum`, `UploadStatusEnum` (`pending`, `confirmed`).
- **New error codes:**
  - `upload.too_large` (400)
  - `upload.size_mismatch` (400: the stored file is not the declared size)
  - `upload.type_mismatch` (400: the content is not the declared type, or is malformed)
  - `upload.not_found` (400: nothing was uploaded yet; upload, then confirm again)
  - `upload.not_confirmed` and `upload.in_use` (409; returned by the endpoints that attach uploads, from PR 3)
  - `storage.unavailable` (503: retry later)
- **New permission in `/me`:** `uploads.create` for owner, admin, ops_manager and fleet_manager.
- **Readiness:** `GET /health/ready` now includes `checks.storage`.

### api-additive: shipment status changes, timeline and public tracking (Phase 2 PR 3)

- **Transitions.** `POST /api/v1/shipments/{public_id}/transitions/` `{to, expected_status, reason?}` → `200` with the shipment detail. Optional `Idempotency-Key` (a repeat with the same key replays the result).
  - `to: "created"` from `draft` confirms a draft (needs `shipments.create`; the draft is re-validated, so a stale one answers the same 400 codes as create).
  - `to: "cancelled"` from `created`, `sorted`, `assigned` or `delivery_failed` cancels (needs `shipments.cancel`, `reason` required).
  - `to: "created"` from `cancelled` recovers (needs `shipments.recover`, `reason` required, audited).
  - `expected_status` is the status the client last saw; if it changed meanwhile the answer is 409 `shipment.status_conflict`. Other `to` values answer 400; the remaining lifecycle edges arrive with their phases (routes, warehouse, delivery).
- **Corrections.** `POST /api/v1/shipments/{public_id}/corrections/` `{to, expected_status, reason}` (`shipments.correct_status`) → `200`. Any status except `draft` and the current one, including out of `delivered`. Appends a non-public `correction` event and an audit entry; history is never rewritten.
- **Timeline.** `GET /api/v1/shipments/{public_id}/events/` (`shipments.view`): cursor-paginated, oldest first (`occurred_at`, then `recorded_at`). Each event: `type` (`status_change` or `correction`), `status_after`, `occurred_at`, `recorded_at`, `source` (`admin`), `public`, `reason`, `actor{public_id,full_name}` or null.
- **Public tracking.** `GET /api/v1/track/{tracking_token}/`, no authentication (any `Authorization` header is ignored), 60 requests/min per IP. Returns only `tracking_number`, `status`, `carrier` (company name), `service` (service type name), `delivery{city,country}`, `scheduled_pickup_date` and `events[{status,occurred_at}]` (public events only). Unknown or rotated tokens, drafts and suspended companies answer 404.
- **Enums:** `TrackingEventTypeEnum` (`status_change`, `correction`) and `TrackingEventSourceEnum` (`admin`). Later phases add values (additive).
- **New error codes:** `shipment.invalid_transition` (409: the edge is not in the lifecycle, e.g. from `delivered`) and `shipment.status_conflict` (409: `expected_status` no longer holds).
- **New permissions in `/me`:**
  - `shipments.cancel`: owner, admin, ops_manager, dispatcher, customer_service
  - `shipments.recover` and `shipments.correct_status`: owner, admin, ops_manager

### api-additive: shipments (Phase 2 PR 2)

- **Shipment list.** `GET /api/v1/shipments/` (`shipments.view`): cursor-paginated, newest first. Users see only their own branches unless they hold `shipments.all_branches`.
  - Each row: `public_id`, `tracking_number`, `status`, `priority`, `branch{public_id,code}`, `customer{public_id,name}`, `service_type` (code), `scheduled_pickup_date`, `external_reference`, `total_weight_kg`, `created_at`, `updated_at`.
  - Filters: `status` (repeatable), `branch`, `customer` (public IDs), `service_type` (code), `priority`, `created_after` (inclusive), `created_before` (exclusive), `search` (case-insensitive prefix of the tracking number or external reference). An invalid filter answers 400.
- **Create.** `POST /api/v1/shipments/` (`shipments.create`). The `Idempotency-Key` header is **required**.
  - Body: `branch_id`, `customer_id` (the sender), `service_type` (code), `pickup`, `delivery`, `items[]` (1–500), optional `priority` (`normal` default, `high`, `urgent`), `scheduled_pickup_date`, `external_reference`, `draft` (default false).
  - `pickup` and `delivery` are each `{"address_id": "<address-book public ID>"}` or an inline address (`contact_name` required, `phone`, `line1`, `line2`, `city`, `region`, `postal_code`, `country`, optional `latitude`/`longitude`). Either way the shipment keeps its own copy; later address-book edits do not change it.
  - Each item (one parcel): `weight_kg`, `length_cm`, `width_cm`, `height_cm` (decimal strings), optional `description`. Each parcel must fit the service type's limits; the total weight must be above 0 and at most 30,000 kg.
  - → `201` with the detail body. Status is `created`, or `draft` with `"draft": true`.
- **Detail.** `GET /api/v1/shipments/{public_id}/` (`shipments.view`): the list fields plus `tracking_token`, `pickup`, `delivery` (with `geocode_confidence` and `source_address_id`) and `items[]` (with `barcode`, `<tracking_number>-001` onwards).
- **Drafts.** `PATCH /api/v1/shipments/{public_id}/` (`shipments.create`, optional `Idempotency-Key`): any create field except `draft`; `items` replaces all parcels; moving to another branch issues a new tracking number. `DELETE` → `204`. Both answer 409 `shipment.not_draft` for any other status.
- **Tracking token.** `POST /api/v1/shipments/{public_id}/tracking-token/` (`shipments.create`) → `200 {tracking_token}`. The old token stops working. The public tracking endpoint arrives in PR 3.
- **Enums:** `ShipmentStatusEnum` (all 12 lifecycle statuses from architecture §11; only `draft` and `created` occur until PR 3) and `ShipmentPriorityEnum`.
- **New error codes:**
  - `idempotency.key_required` (400)
  - `customer.inactive` (400: archived sender)
  - `shipment.invalid_weight` (400: a parcel weight ≤ 0 or a total over 30,000 kg)
  - `shipment.unsupported_type` (400: unknown or inactive service type)
  - `shipment.past_schedule` (400: date before today in the company timezone)
  - `shipment.duplicate_reference` (409)
  - `shipment.not_draft` (409)
  - Unknown or another company's `branch_id`, `customer_id` or `address_id` answer `validation.invalid_reference` (400). Parcels over the service-type limits answer `validation.invalid` with errors keyed by item index.
- **New permissions in `/me`:**
  - `shipments.view`: every role except driver
  - `shipments.create`: owner, admin, ops_manager, dispatcher, customer_service
  - `shipments.all_branches`: owner, admin, ops_manager
- **Onboarding (not an API change):** the `create_company` command now requires `--branch-name` and `--branch-code` and makes the owner a member of that first branch.

### api-additive: customers and address book (Phase 2 PR 1)

- **Customers.** Each response has `public_id`, `kind` (`individual` or `business`), `name`, `email`, `phone`, `archived_at`, `created_at` and `updated_at`.
  - `GET /api/v1/customers/` (`customers.view`): cursor-paginated, newest first. Archived customers are hidden unless `?include_archived=true`. `?search=` matches the name case-insensitively.
  - `POST /api/v1/customers/` (`customers.manage`) `{kind, name, email?, phone?}` → `201`. Optional `Idempotency-Key`.
  - `GET /api/v1/customers/{public_id}/` (`customers.view`).
  - `PATCH /api/v1/customers/{public_id}/` (`customers.manage`), any of the create fields → `200`. Optional `Idempotency-Key`.
  - `POST /api/v1/customers/{public_id}/archive/` (`customers.manage`) → `200`. Also archives all of the customer's addresses. Repeating it is a no-op. There is no hard delete.
- **Addresses.** Each response has `public_id`, `label`, `contact_name`, `phone`, `line1`, `line2`, `city`, `region`, `postal_code`, `country`, `latitude`, `longitude`, `geocode_confidence`, `archived_at`, `created_at` and `updated_at`.
  - `GET /api/v1/customers/{customer_id}/addresses/` (`customers.view`): paginated. Archived addresses are hidden unless `?include_archived=true`.
  - `POST /api/v1/customers/{customer_id}/addresses/` (`customers.manage`) → `201`.
  - `GET` and `PATCH /api/v1/customers/{customer_id}/addresses/{public_id}/`.
  - `POST /api/v1/customers/{customer_id}/addresses/{public_id}/archive/` → `200`, idempotent.
- **Formats** (no geocoding provider yet):
  - `phone` uses E.164 (`+14155550123`) or is empty.
  - `country` is ISO 3166-1 alpha-2. Input is case-insensitive; responses are uppercase.
  - `postal_code` is free text.
  - `latitude` and `longitude` are optional numbers. Send both or neither. On PATCH, both `null` removes them, and omitting both keeps the stored point.
  - `geocode_confidence` is `manual` when coordinates were supplied and `none` otherwise. A geocoding provider may add other values later.
- **New error codes:** `customer.archived` (409: change to an archived customer, or a new address for one) and `address.archived` (409).
- **New permissions in `/me`:**
  - `customers.view`: owner, admin, ops_manager, dispatcher, customer_service, finance and viewer
  - `customers.manage`: owner, admin, ops_manager and customer_service

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
