---
name: fleetflow-security-review
description: FleetFlow-specific security review of a fleetflow-api diff. Use before merging any change touching auth, permissions, tenant data, uploads, OTP/POD, public tracking, GPS, exports or integrations. Complements the built-in /security-review.
---

# FleetFlow security review

Review the diff (`git diff main...HEAD`) against each item. Report findings as `file:line: issue → fix`, most severe first. If nothing fails, say so explicitly.

**Tenancy**
- [ ] Every new queryset starts from the tenant-scoped manager; no `.objects` on tenant models in views/services/selectors
- [ ] Company is never read from body, query or headers
- [ ] Cross-tenant IDs give 404; FK references in payloads are validated within the tenant
- [ ] New endpoint added to the tenant isolation sweep

**AuthN/AuthZ**
- [ ] Permission class on every view; object-level check in the service
- [ ] Driver endpoints restrict to the driver's own routes, stops and shipments
- [ ] No new unauthenticated endpoint except approved public tracking and health

**Data exposure**
- [ ] Serializers whitelist fields (no `fields = "__all__"`)
- [ ] Public tracking returns only approved fields; no driver PII, no GPS history
- [ ] No tokens, OTPs, passwords, full phones or addresses in logs or error details

**Inputs, files, abuse**
- [ ] Validation at the serializer plus DB constraints for invariants
- [ ] Uploads: presigned size and type conditions, magic-byte check, private bucket, short-lived read URLs
- [ ] CSV/XLSX export escapes formula prefixes; imports enforce size and row limits
- [ ] Throttles on login, refresh, OTP verify/resend, password reset, public tracking, pings
- [ ] OTP stored hashed, attempt-limited

**Integrity**
- [ ] State changes go through transition services, with `select_for_update`/version checks
- [ ] Idempotency keys honored on driver and creation mutations
- [ ] Audit log entries for sensitive actions (architecture §23)
- [ ] Raw SQL parameterized; no secrets in code; `pip-audit` clean
