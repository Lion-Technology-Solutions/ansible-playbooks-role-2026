# LionTech Academy Student Portal — Vercel

This is the Vercel application in the Ansible classroom repository. The original `roles/school_management` project remains an independent Ubuntu lab. The portal uses Flask and persistent PostgreSQL, provisioned through Vercel's Neon integration. It does not require Terraform servers, systemd, Nginx, or a writable local filesystem.

## Vercel configuration

- Team: `liontechs-projects`
- Project: `liontech-student-portal`
- Repository: `Lion-Technology-Solutions/ansible-playbooks-role-2026`
- Production branch: `main`
- Root directory: `school-portal`
- Framework: Flask
- Function region: `iad1`, matching the Neon database region
- Runtime dependency installation: `requirements.txt`

Environment variables, stored in Vercel rather than Git:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Pooled PostgreSQL connection, provisioned by Neon |
| `SCHOOL_SECRET_KEY` | High-entropy session signing secret |
| `SCHOOL_ADMIN_PASSWORD` | Initial administrator password; username is `admin` |
| `INITIAL_ANALYTICS_RECORDING_URL` | Optional supplied Zoom recording, seeded once; kept out of this public repository |

Production and preview need independently configured secrets. The initial portal deployment is production-only. Before using preview deployments with real students, connect an isolated preview database/branch and separate preview credentials. The current Marketplace connection can share its database between targets; do not use production data for unreviewed previews.

The application refuses to start without required variables. It requires PostgreSQL outside explicit test mode. SQLite is used only by automated tests, never as an ephemeral production fallback. Database schema creation and course seeding are additive, idempotent, and serialized with a PostgreSQL advisory lock. Future schema changes require reviewed migrations before rollout; `create_all` does not migrate existing columns.

## Enrollment and payment approval

1. Students register at `/register`, choose new/existing status, country, name, email, phone and password. Both types receive a permanent student number such as `LT-2026-000123`, an automatically active account, and access to `/portal` immediately. Existing database accounts are backfilled additively on first startup; numbers remain stable across redeployments.
2. At `/enrollments`, students choose from the published catalog. The initial catalog is DevOps, Cybersecurity, Cloud Computing and Data Analytics; administrators can create additional courses without a code change.
3. Every program offers new batches every Saturday and Sunday. The homepage and student account show exactly the next two future weekend dates using `America/Toronto`. Today is excluded because no universal batch cutoff time has been supplied. New students must select one of those dates; the server rejects expired, unoffered, or cross-program dates. Existing students retain their current batch and skip selection. Intakes are generated idempotently under a PostgreSQL advisory lock.
4. Program- and country-specific payment links appear after the enrollment request. New courses start with no checkout links and direct students to support until an administrator configures them. The existing courses retain their supplied Flutterwave links. Cameroon displays **2,000 FCFA toward the first installment**. Nigeria/Ghana use the supplied checkout links without inventing a fee. Other countries are directed to support to arrange payment.
5. Students submit their transaction reference and optional payer/date details. The account remains active; course enrollment and class access stay pending until payment is verified and enrollment is confirmed.
6. At `/admin`, the administrator checks the transaction and correct fee in the payment provider, then chooses **Verify payment & approve enrollment**. Alternatively, **Verify payment only** followed by **Approve enrollment** completes the same flow in two steps. The approval button is visible but disabled before verification; the server also rejects approval without verified payment. A submitted reference alone never unlocks course access. Reused references are rejected.

Payment verification is manual. There is no payment webhook/API credential configured. Opening checkout or submitting a reference never marks an account paid automatically. An existing-student declaration does not bypass payment verification or approval. Email and WhatsApp ownership are not automatically verified. Public signup creates an active account, never a paid enrollment. Existing account flags are backfilled to active without changing any payment or enrollment status.

## Program information and communication status

The student account displays each selected program's duration, country-specific tuition, schedule, curriculum and next two starts. Administrators maintain these fields under **Tuition, schedules & curriculum**. The initial Data Analytics fees come from the supplied country flyers: Cameroon 10,000 FCFA/month and 30,000 total with 2,000 toward the first installment; Nigeria ₦19,900/month and ₦2,500 enrollment; Ghana approximately GH₵198/month and GH₵594 total with GH₵20 toward the first installment (exchange-rate dependent). Other program prices, durations and class times remain explicitly unconfirmed until the administrator publishes them. Existing descriptions supply the initial curriculum topics; they are editable.

Each signup saves an `AccountRequest` in the same database transaction as the student account. Existing accounts receive one backfilled request each; repeat startups and duplicate signups do not create duplicate requests. The admin **Account notifications** section lists every saved request with a WhatsApp send link addressed to +1 437-575-6656. Messages include student number, name, contact details, country and student type, but no password. Students also have a support notification link. Both require opening WhatsApp and tapping Send. These are saved notifications, **not** automatic message delivery; the UI states that the automatic connection is pending.

**Pending external setup:** The owner selected Twilio for WhatsApp notifications and OTP, and Twilio SendGrid for approval/payment-link emails. Neither service is connected to this Vercel project. No OTP generation/verification endpoint, automatic WhatsApp sender, or approval email delivery is implemented or claimed active in this version. There is no fake-code or email-sent fallback. Complete provider setup before implementing and enabling these flows. Use Twilio Verify's WhatsApp channel with send/check rate limits; use an approved WhatsApp notification template for administrator account requests. SendGrid needs a verified sender/domain. Configure credentials privately in Vercel, never in Git or chat.

Any future automatic email or WhatsApp integration must preserve the active-account / paid-enrollment boundary. Account activation must never be treated as proof of payment or permission to access classes.

## Course, event and announcement publishing

From the admin dashboard:

1. **Courses & publishing → Add a new course**: enter the name, optional URL label, overview, key skills, duration, schedule and curriculum. Save a draft or create and publish immediately.
2. Preview drafts using the private **Preview course** link. Draft public URLs return 404; drafts are absent from the catalog and reject new enrollment POSTs too.
3. Set country-specific tuition and checkout URLs in **Tuition, schedules & curriculum**. Set Zoom/resource links in the existing links section. New courses never inherit another program's checkout link.
4. **Publish course** exposes `/courses/<slug>`, adds the homepage card, and makes the program available on student accounts immediately. No rebuild is needed for content edits. **Save draft / unpublish** hides it from new applicants while preserving existing enrollments, resources, recordings, assignments and student access.
5. **Events & news** supports events/webinars/workshops and announcements/news. Enter a title, summary, full description, optional registration URL and location. Events require a start time and an end after the start when supplied. All editor times are UTC; visitors also see their local event time.
6. Save drafts, preview privately, publish immediately, or choose a future **Publish at** time. Scheduled and draft items return 404 publicly until eligible. Public content appears at `/updates` and `/updates/<slug>`. The homepage highlights upcoming/ongoing events and the latest announcements. Past events remain in the updates archive with a past-event label and no registration button.

Content fields are escaped plain text, with line breaks preserved; arbitrary HTML/scripts cannot be published. URL labels are unique and remain stable after creation. Every write requires an authenticated administrator session and CSRF token. There is no destructive content-delete action.

Publishing uses additive `portal_course_publications` and `portal_public_updates` tables. Existing courses are backfilled as published once; later cold starts never republish an unpublished course or overwrite edited profile data. Course and content changes persist in PostgreSQL across redeployments.

## Classes, resources and recordings

- All signed-in students access `/portal`. They see selected programs, tuition, payment methods, curriculum, published class schedules and public announcements before payment. Only courses with both verified payment and confirmed enrollment unlock registration, joining, materials, recordings and coursework. Access to one course never grants access to another.
- The supplied Data Analytics Zoom registration and Drive resources are installed as course links.
- Administrators can schedule classes with a date/time in **UTC** and a Zoom registration link. Students also see their local class time.
- Administrators can set separate class registration and direct meeting-joining URLs when scheduling a class or under **Class registration & joining links**. Students register in the portal and complete any Zoom registration separately. The app does not call Zoom's API or claim that portal registration automatically registers someone in Zoom.
- Admins publish Zoom recording links now or at a future UTC availability time. The link is shown only to paid, confirmed students enrolled in that course.
- Each publication creates in-portal notifications for enrolled students. Newly confirmed enrollments receive existing recording notifications too. Recording notifications are hidden again when course access is revoked. Future recordings/notifications stay hidden until their availability time.
- The supplied latest recording is seeded privately through the environment variable. Updating the seed variable later does not replace an existing recording: publish a new recording through the admin dashboard instead.

Automatic delivery is inside the portal. Email, push and WhatsApp Business delivery, and automatic Zoom import, are not configured. Admins publish recordings after class or schedule availability in advance.

### Server-side access checks

The portal never renders unpaid course registration, joining, resources or recording URLs. The routes `/course/<id>/register`, `/course/<id>/materials`, `/class/<id>/join`, `/class/<id>/register`, `/recording/<id>/watch`, assessment submission and attendance each enforce the student session and course-specific paid enrollment. Private recording availability times remain enforced. Revoking a course locks it immediately while preserving the active account, public information and schedules.

New additive tables `portal_account_requests` and `portal_class_join_links` preserve existing data. The existing `Student.approved` column now represents automatic account activation and is not a class-access authorization check.

## Attendance and support

At `/attendance`, students can record attendance only for a paid, confirmed course and a class that has started. The server stores one attendance record per student/class, and administrators can see the register. It then displays a WhatsApp report link for **+1 437-575-6656**. The student opens WhatsApp and taps **Send**; the app does not report WhatsApp delivery as successful or silently message anyone.

All support and project-completed links use the same supplied WhatsApp number. Attendance is stored even if WhatsApp is unavailable. No automated WhatsApp Business API is configured.

## Lessons, uploaded files and branding

The administrator manages **Lessons**, **File library**, and **Branding** from the dashboard. Files are stored in the private Vercel Blob store `liontech-portal-files` (`store_5FD6wadWX2PK1f5h`, iad1); `BLOB_READ_WRITE_TOKEN` was provisioned by the store connection. File and lesson metadata remain in PostgreSQL. No upload bytes, credentials or teaching materials are stored in Git or the function filesystem.

### Workflow

1. Open **File library**. Select **Lesson materials** or **Branding images**, choose one or more files, and keep the page open until uploads finish. Progress is shown; failed parts retry up to twice. Completed files remain saved if another file fails. An interrupted incomplete upload can be archived and restarted.
2. The library supports title changes, administrator downloads/previews, and reversible archive/restore. Archiving hides a lesson file from students immediately. It does not permanently delete its stored bytes.
3. Create an ordered lesson under a course, add module/unit and notes, select completed library files, then save a draft or publish/schedule it. One file can be reused in several lessons. Private admin previews work before publication.
4. Paid, confirmed students see eligible lessons under **My lessons & files**. Images display inline; audio and video have browser players with seeking. Office documents and PDFs download to the student's device. Media whose codec is unsupported in a browser can still be downloaded; the app does not transcode videos or render Office documents.
5. Under **Branding**, set the portal name, footer tagline, primary/accent colors, logo and homepage banner. Upload PNG/JPG/WebP using the branding purpose first. Selecting a logo/banner explicitly publishes only that selected image through `/brand/logo` or `/brand/banner`; unrelated assets stay private. Removing the selection restores the original default. Remove a selected image from branding before archiving it.

Supported formats: PDF, XLSX/XLS/CSV, PPTX/PPT, DOCX/DOC/TXT, PNG/JPG/JPEG/WebP/GIF, MP4/WebM/MOV/M4V, MP3/WAV/OGG/M4A/AAC/FLAC. Documents are limited to 50 MiB, images to 10 MiB and 20 million pixels, and audio/video to 250 MiB per file. Branding accepts PNG/JPG/WebP only. Images are decoded and checked before they become usable. Upload extensions/MIME types are allowlisted; files are never executed by the application. No malware scanner is configured.

### Upload and delivery design

The synchronous Python SDK does not support client upload token generation or signed URLs. The app uses its real private Blob put/get operations, storing each upload as ordered 3 MiB objects. Each upload request stays below Vercel's request payload limit. The server assigns storage paths, verifies per-part sizes and SHA-256 hashes, accepts identical part retries idempotently, and marks an asset ready only after all parts are present. Client completion requests cannot register arbitrary external URLs or storage paths.

`/files/<asset_id>` authenticates the administrator or checks that the student has a verified, approved enrollment in a course with a published, released lesson referencing that active asset. Unpaid, cross-course, draft, scheduled and archived content is denied. HTTP byte ranges and HEAD are supported for media seeking; body data streams in bounded chunks. Raw Blob paths and credentials never go to students. Every new content/upload mutation requires an administrator session and CSRF token. Selected branding images are the explicit public exception.

New tables are additive: `portal_assets`, `portal_asset_chunks`, `portal_lessons`, `portal_lesson_files`, and `portal_site_branding`. First startup seeds default branding once; future deployments preserve administrator edits.

## Assignments, quizzes and results

Admins publish assignment or quiz instructions for a course, with an optional due date. Students submit text answers and an optional project/document/file link. Admins review, assign 0–100, and publish feedback. Results and a notification appear in the student's own portal. Updating an answer clears the previous grade and returns the submission for review. Quizzes are instructor-reviewed, not automatically scored multiple-choice exams. Student assignment submissions still use sharing links. Administrator lesson materials use the uploaded-file library described above; student binary assignment uploads are not enabled.

## Authentication and administration

Student passwords are hashed with PBKDF2, sessions use secure/HTTP-only/SameSite cookies, forms require CSRF tokens, and queries use SQLAlchemy parameter binding. Student identity comes from the signed session, not form IDs. Password resets invalidate the student's existing sessions. Authentication attempts are rate-limited using the persistent database. An admin password rotation invalidates admin sessions; rotate `SCHOOL_ADMIN_PASSWORD` and redeploy. Never put credentials or private Zoom recording links in Git or build logs.

Admin functions include reviewing pending enrollments, payment verification, approving/revoking course access, editing program tuition, duration, schedule, curriculum and course links, scheduling classes, publishing recordings and assessments, grading submissions, viewing attendance, and resetting student passwords.

## Tests

From the repository root:

```bash
source .venv/bin/activate
pip install -r school-portal/requirements.txt pytest==8.4.2
pytest school-portal/tests -q
```

Tests explicitly use isolated SQLite databases and check country-specific payment routing, dates/new-vs-existing handling, approval gates, duplicate payment references, student isolation, CSRF, class registration, attendance uniqueness and WhatsApp payloads, scheduled recordings, notifications, submissions/results, password resets, revocation, stable and backfilled student numbers, Saturday/Sunday rollover (including Toronto midnight and year-end), editable country-specific program details, admin-only course/content publishing, private previews, duplicate URL labels, per-course checkout isolation, scheduled events, escaped announcements, preserving enrolled access after unpublishing, automatic account activation, per-course payment gates, direct protected-route checks, combined and separate admin approval, recording-notice revocation, class joining links, idempotent saved support notifications, real-storage adapter boundaries, chunk assembly/retry/integrity, byte-range responses, lesson scheduling/access, file archive/restore and public-branding isolation. Deployed verification checks the real PostgreSQL-backed health endpoint and the critical live user flows.

## Local development

Provision a separate development Postgres database. Link this exact project, pull Development variables, and set separate development credentials rather than copying Production Secrets:

```bash
cd school-portal
vercel link --yes --scope liontechs-projects --project liontech-student-portal
vercel env pull .env.local --yes --environment=development
# Export the required development variables through your normal environment loader.
flask --app app run
```

Use `https` locally or explicitly adjust the secure-cookie flag in a local-only test configuration. Do not deploy with `SCHOOL_TESTING=1`.

## Recovery

The destroyed Terraform servers' SQLite files are not available to this Vercel application. Previously stored data can be restored only from an existing backup through a reviewed importer; no automatic recovery or migration of destroyed server data is claimed. The new PostgreSQL database persists independently of application redeployments. Configure appropriate Neon backups and retention for live student records.


## Dashboard and guided enrollment

The responsive portal follows the LionTech navy, blue, yellow and red dashboard design. `/apply` selects a published program and one of the next two Toronto Saturday/Sunday batches, `/register` creates the automatically active account and enrollment, and `/apply/payment/<id>` shows the student's own order and payment options. Existing accounts reuse their student number and enrollment. The dashboard uses actual released-lesson completion for progress; unpaid students see public curriculum and schedules, while lesson content, files, registrations and joining links retain server-side course/payment gates.

Admins navigate Students, Enrollments, Payments, Programs, Classes, Materials, Announcements, Reports and Settings. Programs contains numeric country-specific tuition, currency, monthly installment count and full/first-installment checkout links. These are configured offers, never guessed prices. A student's saved selection snapshots terms and checkout URL, so later offer edits do not silently change their agreement. Monthly dates clamp to month end and the final amount absorbs cent rounding. Future installment collection depends on the configured provider checkout; this app does not create automatic recurring charges. Subsequent receipts go to support for manual recording.

Payments records actual amounts, currency, receipt reference and received time. References are idempotent and conflicting retries are rejected. Recording a payment marks it verified; enrollment approval remains an explicit administrator decision after checking the qualifying payment. Reports export the actual ledger to CSV with spreadsheet formula protection. Dashboard program/country/enrollment-year filters apply to the overview and financial report, with a separate currency selection and no FX conversion. Collections include only recorded amounts; outstanding is the unpaid balance of saved plans and forecast is collected plus that balance. Legacy verified records without ledger amounts are explicitly excluded from collection totals. Enrollment conversion is a count ratio, not a monetary collection rate. The editable 2027 target defaults to the requested 5,000; no sample students or simulated revenue are seeded.

Students can save English/Spanish navigation and timezone preferences; program and instructor content remains in its authored language. Released lessons can be marked complete idempotently, driving progress and resume links. Admins may issue a printable completion certificate only for a verified approved enrollment after all currently released lessons are completed; issuance is instructor-controlled and does not claim external accreditation. Certificates are private to the owner and admin.

Additive tables: `portal_student_preferences`, `portal_lesson_completions`, `portal_course_prices`, `portal_billing_selections`, `portal_payment_entries`, `portal_growth_goals`, `portal_certificates`. No existing student, course, lesson, branding or payment data is deleted. Additional tests cover guided enrollment ownership, plan snapshots, monetary validation/rounding, real ledger totals and currency isolation, duplicate transaction handling, preferences, progress/access revocation and certificate permissions.


## Worldwide residence and USD localization

The canonical enrollment fee is **US$5.00 per program enrollment**, separate from full tuition. Residence selection covers all 249 ISO countries/territories plus Other, using pinned pycountry/Babel and a bundled, source-attributed Unicode CLDR territory-currency snapshot from November 9, 2026. The snapshot tracks current preferred tender (including recent transitions) and its source commit is stored alongside the data. Unicode’s license is included. Suggested English/French/Spanish navigation and IANA timezones may be changed separately. Existing country strings and legacy configuration field indexes are preserved. There is no IP geolocation or assumption that a language identifies a currency.

`/localization?country=...` returns only the requested residence’s US$5 reference quote. ExchangeRate-API’s open USD reference is fetched server-side, validated and cached persistently for 24 hours under a PostgreSQL advisory lock (failures retry after an hour). Source timestamps are shown. A last-known rate older than 48 hours is labeled stale; unavailable rates explicitly fall back to the USD price, never a fabricated 1:1 conversion. Every quote page attributes the source. No full rates feed is redistributed. Minor-unit formatting uses CLDR precision (including JPY/XAF zero decimals and KWD three decimals). The source currently covers 151 of the 152 mapped currencies; availability may change.

Admins can set global USD tuition under Programs. This takes precedence over legacy per-country offers for new selections. Saved plan terms and checkout links, old regional prices and actual ledger currencies are preserved. Global tuition and its student-visible equivalents are not guessed from legacy free-text prices. Financial reports default to USD and keep other currencies separately selectable; reference conversion never rewrites collections or settlement data.

`/payments/enrollment/<id>` binds the student number, enrollment and immutable fee invoice. Existing verified approved enrollments are grandfathered without fabricating a paid fee entry. Manual submissions require a matching student number on the **authenticated account**, actual amount/currency/reference, and a PDF/JPG/PNG receipt up to 2 MB. Receipts are private Blob objects accessible only to their owner and admin; type signatures and image content are checked. Admin approval explicitly confirms receipt/funds and records the fee in USD at its invoice face value, preserving the original tender details on the manual receipt record. Fee-ledger links keep enrollment fees from reducing full-tuition balances. Request-more-information creates an in-portal notice. Student number is a human identifier, never a substitute for authentication.

Card payment is disabled until `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` are configured. Register the production `/webhooks/stripe` endpoint for `checkout.session.completed` and `checkout.session.async_payment_succeeded`. At checkout the server reads the connected Stripe account’s country spec and can charge a fresh local equivalent in a supported zero- or two-decimal presentment currency. ISK/UGX use Stripe’s special minor-unit rules. Unsupported/unavailable/three-decimal presentment falls back to a clearly disclosed USD charge. Displaying a currency never implies the gateway supports charging it. Local rate, amount and currency are snapshotted on the checkout, while the enrollment-fee ledger records the USD face value. No claim of guaranteed bank FX, fees or exact USD settlement proceeds is made. Checkout amount/invoice/student number originate on the server. Retries reuse the invoice checkout’s idempotency key. Webhooks validate the raw-body signature and timestamp, retrieve the session from Stripe and validate paid status, the exact snapshotted amount and currency, student reference and invoice metadata before recording the payment and automatically granting access. Browser success parameters cannot grant access. The endpoint is the only CSRF exception; signed provider events replace CSRF there. Row locks, unique references and invoice state guard repeated deliveries. Duplicate incoming payments are retained for administrator review, and late card events do not override revoked access. Expired checkout sessions currently require support reopening rather than silently creating another payable invoice.

## Zoom scheduling and calendar delivery

`/admin/live-classes` adds timezone-aware schedules, explicit cohort audiences, drafts, instructor, duration and 0–60 minute join windows. Direct route checks enforce the same paid-approved course, cohort, published state and join window as the student UI. Legacy published class links retain their existing joining behavior. Calendar ICS downloads are authenticated per course/cohort, contain the portal URL rather than private Zoom URLs, and retain a stable event UID with increasing revisions. Students must add/accept the invitation in their calendar; no Google/Outlook account connection is claimed.

Manual Zoom joining links work without API credentials. Connected creation/updates require a Zoom Server-to-Server OAuth app with meeting create/update scopes, `ZOOM_ACCOUNT_ID`, `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`, and `ZOOM_HOST_USER`. Updates PATCH the existing Zoom meeting ID. API-created meetings enable a waiting room and disable joining before host. Portal restrictions do not prevent an authorized attendee from sharing a meeting URL; use Zoom host controls to enforce admission.

Publishing a schedule/update immediately creates eligible students’ portal notices and durable email/calendar delivery records. `SENDGRID_API_KEY` and a verified `SENDGRID_FROM_EMAIL` are required for sending. The admin delivery button processes up to 20 pending jobs with retries; superseded revisions and revoked enrollment are checked before sending. **A continuous automatic email queue worker/calendar-account sync is not enabled by this deployment.** Portal notifications and private calendar downloads work independently. No messages are marked sent until the provider call succeeds. At large cohorts, provision a durable worker before enabling unattended delivery; do not fan out thousands of email requests inside one schedule-saving request.

New additive tables include FX snapshots, global tuition, enrollment-fee invoices, manual fee submissions, fee ledger allocations, card checkouts, scheduled class details and class delivery jobs. No existing application tables are altered destructively. Tests use mock providers and memory storage, never real payments or emails.


## Browser-managed Integrations Hub

Admins open `/admin/integrations` from the sidebar to search/filter provider cards, enter setup values, validate credentials, pause/resume new actions and inspect a sanitized activity history. Stripe, Zoom, SendGrid, Google Calendar, Microsoft calendars, Flutterwave, Twilio WhatsApp, Facebook, YouTube, Instagram and LinkedIn each have allowlisted fields and provider-specific setup guidance. The interface explicitly distinguishes usable portal capabilities from configuration/authentication-only integrations. Flutterwave checkout, WhatsApp outbound messaging/OTP, social sign-in/publishing/lead ingestion, and OneDrive/Teams features are not enabled merely by saving their credentials.

`INTEGRATIONS_ENCRYPTION_KEY` is the one bootstrap secret needed for storing credentials and student refresh tokens. Production was provisioned with a dedicated Fernet key. Preserve this key across deployments and backups; do not replace it with the session secret. Ciphertexts authenticate their provider and owner, preventing cross-provider or cross-student substitution. Optional `INTEGRATIONS_PREVIOUS_KEYS` permits controlled MultiFernet rotation; deploy retained old keys until records are re-encrypted, then remove old keys through an operator-managed rotation. Never expose these keys in client-side variables, logs, screenshots, Git or exported settings.

After bootstrap, provider keys are managed in the portal and **do not require an environment-variable edit or redeploy**. Empty secret fields retain saved values; secrets are never included in HTML/JSON responses. Saving, connection testing, pause/resume and delivery controls require an authenticated admin session, CSRF token and current admin password. Reauthentication failures are persistently rate-limited; optimistic revisions reject stale browser-tab overwrites. Database settings override environment fallback. Pausing stops new provider actions while retaining keys for verification of in-flight Stripe webhooks. Credential changes invalidate Stripe merchant-currency capability caching.

Connection checks use read-only/authentication provider APIs, not test payments, test meetings or test emails. A connected badge reflects the last successful authentication test, with its timestamp and explicit capability notes. OAuth client setup remains “Student authorization required” until each student authorizes access; merely filling a form never grants access to their account. SendGrid key testing verifies Mail Send scope; sender/domain verification must also be completed with SendGrid. Provider approval, billing activation and permissions remain provider-side prerequisites.

### Student calendar authorization

`/account/connections` lets each signed-in student connect Google or Microsoft calendars, sync eligible class events, disconnect and manage class reminder preferences. OAuth uses fixed registered callbacks at `https://liontech-student-portal.vercel.app/connections/google/callback` and `/connections/microsoft/callback`, session-bound one-time state, a 15-minute expiry, PKCE S256 and encrypted server-side offline tokens. Google requests `calendar.app.created` and creates a student-owned LionTech calendar; Microsoft uses delegated `Calendars.ReadWrite` and `offline_access`, also creating a separate LionTech calendar. No email-based automatic account linking, shared service-account impersonation or calendar access without student consent is used.

Tokens refresh on the server. Calendar links bind each provider event to the student, class, app configuration and actual connected calendar. Deterministic Google event IDs / Microsoft transaction IDs and retained event mappings avoid duplicate events on normal retries; class revisions update existing entries. Only paid, approved, published and cohort-matching classes sync. Calendar event descriptions contain the protected portal URL, not Zoom credentials. Disconnect removes the portal’s local token and retains existing remote calendar entries; students can also remove provider permissions from their provider account.

### Automatic delivery worker

The hub’s Background Delivery controls enable/disable automatic class updates and test queue connectivity. Automatic delivery defaults to Off. After enabling, publishing or updating classes queues a bounded drain message. `integration_jobs.py` declares a private Vercel Queues subscriber through `pyproject.toml`; no unauthenticated public worker endpoint exists. Root messages fan out database delivery IDs in bounded batches; credentials and message bodies are not included in queue payloads. Each job rechecks access, publication, cohort, current revision and email preference before sending. Calendar sync still updates opted-in calendars independently of email preferences.

The subscriber is limited to eight concurrent deliveries, five attempts and seven-day retention. It persists email success and uses per-student calendar event mappings so successful effects can be skipped on retry. Provider timeout uncertainty can still yield duplicate email delivery (at-least-once semantics); no exactly-once email guarantee is claimed. Failed queue publishes leave durable database records and display an unavailable state. Re-enabling delivery, testing a configured provider or reconnecting a calendar republishes a drain; the queue test records a worker confirmation timestamp only when the private subscriber actually runs. No connected providers means no real external delivery is performed. Hosting and provider quotas/usage charges still apply.

Additive tables: integration settings/audit, delivery control, student calendar connections, OAuth states, integration preferences and calendar-event links. Existing students, pricing, enrollment gates and payment records are preserved. Tests cover encrypted/masked settings, owner/provider binding, CSRF/admin/current-password boundaries, revision conflicts, redacted errors, all catalog test adapters, runtime credential overrides, pause/webhook behavior, Google/Microsoft OAuth consent flows, refresh/replay/expiry, event update isolation, opt-out/revocation and actual SDK send/dispatch against an embedded queue with mock outbound email.
