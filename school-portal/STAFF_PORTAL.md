# Invitation-only staff portal

The People & Operations workspace provides a separate staff identity and sign-in flow. It uses the existing Flask, PostgreSQL, private Blob storage and SendGrid infrastructure.

## Entry points

- Administrator: `/admin/team` (also available through the Team navigation item).
- Staff: `/staff/sign-in`; accounts activate only from an administrator invitation.
- Staff workspace: `/staff`, `/staff/tasks`, `/staff/reports`, `/staff/profile`.
- Supervisor review queue: `/staff/review`.

## Identity and access

Invitations expire after 72 hours; their database records contain SHA-256 token digests. Acceptance requires a single-use email verification code, a matching password of 12–128 characters and acceptance of the current portal policies. Sign-in requires the password and a fresh email code. Verification codes expire after ten minutes, allow five failed attempts and are bound to their account, purpose and security revision.

Role, scope, supervisor and status changes require administrator password confirmation and invalidate existing staff sessions. Staff sessions use the existing server-enforced 15-minute inactivity timeout and four-hour absolute expiry. Account recovery invalidates earlier sessions.

| Role | Permitted records beyond own account |
| --- | --- |
| Staff / Instructor | None; own assigned tasks, reports and profile |
| Supervisor | Work reports, supporting report documents and task assignment within scope |
| HR | Personal profile, government ID, resume and employment documents within scope |
| Payroll | Payroll profile and bank confirmation within scope |
| Administrator | Team invitation/status/role assignment, work reports and tasks; no automatic private HR/payroll document access |

Scope is assigned records/direct reports, department or organization. Scope never grants capabilities belonging to another role. Payroll and government-ID/bank-document access require password plus email-code verification within the previous five minutes. This is email verification, not phishing-resistant MFA or an enterprise SSO integration.

## Tasks and reports

Administrators and permitted supervisors assign tasks with due date and priority. Assignees update progress and record completion outcomes. Daily reports support five work-report sections, private drafts, optional uploaded support documents and draft PDF preview.

Submitting a report creates an immutable version. Supervisors may comment, mark reviewed or request a revision. Revisions create new versions. Stale review submissions are rejected. Draft content is visible only to its author. Submitted versions and decisions remain in the portal.

Each submission queues PDF copies for the verified staff email and assigned supervisor; unassigned staff reports go to the registered administrator. Delivery rechecks recipient activity, current supervisor assignment and scope. Supporting documents stay in the portal and are not emailed with the PDF. Provider acceptance is recorded as such; it is not a delivery or inbox guarantee.

## Restricted records and delivery

Personal/payroll JSON and document bytes are encrypted with the existing integration encryption key. Documents are stored in private Blob storage and downloaded only after server-side authorization; uploads are appended rather than overwritten. Allowed file types are PDF, PNG, JPG, DOCX and text, up to 2 MB. There is no antivirus scanning service in this change.

Email payloads are encrypted in a durable outbox and cleared after provider acceptance or cancellation. Failed email handoffs do not discard reports. Authorized retries and the existing authenticated daily maintenance job drain the outbox, up to five attempts per message. Security codes must be requested afresh rather than retried manually. SendGrid, the integration encryption key and private Blob storage must remain configured. No production invitation or report email was sent as part of development testing.

Access, task, document and report events are recorded in a metadata-only audit log. Reports can contain information typed by staff; staff should avoid including private HR, banking or student payment credentials in work reports intended for email.

## Design and release

Editable Figma views: https://www.figma.com/design/KEjpn4ZX7yaYecgGf6nZ2F?node-id=77-276

The implementation uses the supplied navy/blue/yellow references, local Inter font, responsive layouts and existing SVG icons. The design contains Team Management, Daily Report/Supervisor Review, and HR/Payroll Profile frames with editable text and component instances.

All schema changes are additive tables, created by the existing PostgreSQL advisory-lock bootstrap; no student records are migrated. Reverting the code leaves additive staff records intact. Do not remove encrypted records or rotate away the integration key during rollback.

Validation: full portal regression suite plus focused staff authorization, invitation, report-version, encryption, OTP, inactivity and delivery-retry tests. Browser QA is currently unverified because the preview browser fails before tab creation with `Storage.setCookies: Invalid cookie fields`. Recheck desktop/mobile layout and a real authorized invitation when browser infrastructure is available.

CI installs dependencies directly from Vercel's `pyproject.toml` manifest, and a regression check keeps local `requirements.txt` synchronized with it. A missing PDF dependency in that deployment manifest caused the initial startup failure; production was rolled back before applying this correction.
