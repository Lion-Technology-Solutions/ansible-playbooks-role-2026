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

### Direct notification delivery

Class scheduling sends eligible email and connected-calendar updates directly before the response returns. There is no queue switch or queue connection prerequisite. A bounded pool of eight independent database sessions processes students concurrently; scheduling limits dispatch to the saved class. The integration hub provides an administrator-reauthenticated retry for outstanding class updates. Each delivery rechecks access, publication, cohort, revision and preferences; email status and calendar event mappings prevent normal duplicate sends. Provider credentials can still be paused individually.

Lifecycle email and WhatsApp events are saved transactionally, then sent directly before the HTTP response returns. Rolled-back events are not sent. Verification/security mail is submitted before lifecycle alerts and never waits for the class pool. Failed sends retain durable records for review/retry, without automatic queue publication. Daily payment reminders still run through the authenticated scheduled endpoint; a daily reminder schedule is distinct from queueing an immediate account event. The old private subscriber remains compatible with already-published messages; normal user actions no longer publish messages. Provider acceptance is not confirmed inbox delivery; provider quotas, consent, approved templates and usage charges still apply. Transport uncertainty can result in duplicates; no exactly-once guarantee is claimed.

Additive tables: integration settings/audit, delivery control, student calendar connections, OAuth states, integration preferences and calendar-event links. Existing students, pricing, enrollment gates and payment records are preserved. Tests cover encrypted/masked settings, owner/provider binding, CSRF/admin/current-password boundaries, revision conflicts, redacted errors, all catalog test adapters, runtime credential overrides, pause/webhook behavior, Google/Microsoft OAuth consent flows, refresh/replay/expiry, event update isolation, opt-out/revocation and actual SDK send/dispatch against an embedded queue with mock outbound email.


### AI assistant: OpenAI configuration and reviewed updates

Admin → Integrations → **ChatGPT · OpenAI** accepts an OpenAI project API key and a Responses-compatible model (recommended `gpt-5.4-mini`). Settings use the existing authenticated encryption and admin password confirmation; the key is never returned in HTML. Save, then Test connection. That test checks key/model access without generating a paid response; the project must also permit Responses and have available billing/quota. Pause blocks new requests. An existing ChatGPT session or subscription is not an API credential; review your project's current credits/billing and spending limits in OpenAI.

**Admin → AI Assistant** (`/admin/assistant`) prepares one editable action per request: create/update/publish a program, update its curriculum/duration/schedule description, create/update lessons, create/update/reschedule live classes, add a cohort start date, or create a public announcement. Specify exact class dates, times and IANA timezones. The model returns structured data only, with no execution tools, shell, arbitrary HTTP requests, payment or identity actions. Current program/class/lesson IDs are available in its scoped context. For multi-step launches, review each change separately; configure tuition and uploads in the existing tools.

Drafts are session-bound, expire in 30 minutes, and must be reviewed with an explicit confirmation and current admin password. Review forms submit through the existing admin and class-scheduling handlers. Those handlers validate target/cohort/date/timezone, preserve paid access rules, and use the normal Zoom and direct notification adapters. A draft is consumed atomically with its successful database change; failed validation rolls back consumption. Target fingerprints reject drafts after the underlying record changes. Existing tuition and lesson attachments are preserved. Connected Zoom creates/updates require its configured credentials; eligible class emails/calendar updates follow the delivery settings in Integrations.

**Student → AI Assistant** (`/assistant`) is authenticated and read-only. It receives published program information, upcoming eligible schedules, published announcements and the signed-in student's own enrollment states/residence/language/timezone. It does not receive student identifiers, names, contact details, other student records, credentials, payment references, private files, lesson bodies or raw class joining links. Course curriculum remains visible before payment as elsewhere in the portal. The model cannot activate enrollment, verify payments or reveal protected materials. Support escalation uses the existing WhatsApp link and does not automatically send a message.

The page explains that questions and selected context are sent to OpenAI and asks users not to paste secrets/card details. Responses requests use `store: false`; this setting does not imply zero provider retention. Portal chat questions/answers are not saved as a transcript. Admin draft fields are retained up to seven days; request timestamps, hashed principal, status and token counts up to two days (cleaned on a subsequent request). Integration audit events record the confirmed action type without the prompt or generated content. AI text is escaped as plain text, never interpreted as HTML or executable instructions.

Limits are 3,000 characters per question, 60,000 characters of portal context, 2,500 output tokens, 20 requests per principal/hour and 200 across the portal per rolling 24 hours. Attempts, including failures, consume the request limit. PostgreSQL advisory locking makes reservations atomic. These are request caps, not a guaranteed dollar limit; configure project budgets/alerts with the provider. Requests use a fixed server-side OpenAI URL and a bounded timeout. Failures return a generic error and no portal mutations. Tests mock provider output and external Zoom/email calls; live generation requires the administrator's own configured API credentials.


OpenAI troubleshooting: a successful **Test connection** verifies the key and model lookup, while **Test AI response** exercises Responses generation and the same structured-output schema using a short test prompt with no portal/student context. The generation test uses billable API tokens and the same request caps. Both settings actions require the current admin password. AI errors now distinguish exhausted API quota from temporary rate limits, permission/authentication issues, unsupported requests/models, timeouts, refusals and incomplete output. Only allowlisted diagnostic categories are audited/logged; provider bodies, prompts, key values and raw refusal text are not exposed. The assistant badge says Key verified rather than implying generation has succeeded. GPT-5.4-mini requests explicitly use its supported reasoning effort `none`; other configured models receive no assumed reasoning settings. No failures are retried automatically, and partial/incomplete output is never applied.


### Secure sign-in, activation, two-factor and recovery

Open **Admin → Sign-in settings** (`/admin/security`). Defaults enable username, student-number and email password login, email verification, email password recovery, and mandatory MFA after **7 days**. Google/Facebook remain off until their dedicated integration cards are saved/tested and an admin enables them. Policy writes require CSRF, the current admin password and the current settings revision. Keep at least one portal password method enabled for recovery. The administrator's separate login remains available.

New students choose a case-insensitive unique username, receive the existing stable student number, and verify a six-digit email code before activation. Codes expire in 10 minutes, allow five attempts, are session-bound, and resend invalidates the previous code. No administrator account approval is needed; existing payment/enrollment checks still control class registration, joining and materials. Guided enrollment returns to its selected enrollment's payment page after successful activation/MFA. All sign-in methods use a server-side session, bound to the current password hash and security version; signed browser cookies contain only opaque flow/session identifiers, not OTP secrets.

Existing students keep their student numbers and enrollments. On first sign-in/session upgrade, a missing security profile is created with a visible email-verification/MFA transition deadline using the configured grace period. Existing accounts retain access during that interval; new registrations have no email-verification grace. At the deadline, unverified email and missing-factor gates are enforced before protected routes. Once any factor is enrolled, that factor or another registered method/recovery code is required on every new primary sign-in, even during the grace period and even after social sign-in. A pending session cannot add another factor to bypass an existing factor. Factor changes need a recent primary sign-in and, if a factor exists, MFA verified within five minutes. Last-factor removal is blocked while MFA is required.

Available methods:

- **Authenticator app:** locally generated TOTP secret encrypted with the existing integration encryption key; same-origin QR/manual setup key; code verification before activation; a monotonic counter rejects code reuse.
- **Passkey:** native browser WebAuthn, verified with pinned `webauthn`; production RP `www.liontechacademy.com`, fixed HTTPS origin, one-time expiring server challenge, user presence and user verification required, account-bound credential ID and stored public key/sign counter. The device handles face/fingerprint/PIN; biometric data never reaches the portal. Passkeys are the selected second factor after portal password/social primary authentication. Preview/custom domains require deliberate RP/origin configuration, not a wildcard.
- **Phone SMS:** dedicated **SMS OTP · Twilio Verify** integration with account SID, auth token and Verify service SID. Codes are sent to and checked against a server-bound destination. Setup requires a verified code before storing the encrypted phone factor. SMS delivery requires funded/approved Twilio destinations and any trial number restrictions; configure geographic permissions and Fraud Guard. It does not use the WhatsApp sender.
- **Recovery codes:** ten random single-use codes, shown once, stored only as account-bound keyed hashes. Regeneration invalidates the old set; use is consumed transactionally. First TOTP/SMS setup displays them; after passkey setup, use Generate recovery codes in Account security. No security secrets/codes are placed in cookies or logs.

**Forgot password** (`/password/forgot`) requires both the exact student number and registered email. Matched/mismatched requests return the same generic page. A matching request sends a fixed-origin link expiring in 30 minutes. Its token is hashed in the database, single-use and bound to the current password hash. Reset submission changes the password and invalidates active/pending sessions; factors, recovery codes, social identities, email verification and paid enrollment remain intact. Password reset does not bypass MFA. Public recovery/activation endpoints, password sign-in, SMS sending and MFA verification have persistent database throttles; Vercel's platform-supplied client-IP header is used only on Vercel. CSRF, no-store and no-referrer protect security forms.

**Email prerequisites:** configure and test the Email · SendGrid integration and authenticate the From sender/domain. Activation/reset messages are encrypted in durable mail records and submitted directly to SendGrid during the request. Provider failures are reported immediately; students can request a new code rather than waiting for a retry queue. Successful submissions clear encrypted bodies. Verification codes expire after 10 minutes, reset links after 30; consumed or superseded challenges cannot be delivered by legacy retry consumers. Receiving-mailbox delays remain outside the portal's control. No delivery status activates an account without a valid code/link.


**Social prerequisites:** configure **Google Sign-in** and **Facebook Sign-in** separately from calendar/Page integrations. Register these exact Web callbacks:

- `https://www.liontechacademy.com/auth/google/callback`
- `https://www.liontechacademy.com/auth/facebook/callback`

Google uses Authlib authorization-code/PKCE with a server-stored verifier, nonce, one-time session-bound state, fixed token/JWKS endpoints, signature/issuer/audience/expiration validation and stable `sub` identity. Authoritative verified Gmail/Workspace email can satisfy activation; other email requires the portal code. Facebook validates the token against the configured application and app-scoped user ID with an app-secret proof; it requires independent portal email verification. Provider settings changed during a flow invalidate that flow. New social users complete residence, phone and username; social identities are linked by provider subject, never automatically by an email match. Existing users must sign in and explicitly link from Account security, under the same fresh-auth/MFA rules. Complete the provider's OAuth consent/live-mode/review and privacy/data-deletion configuration as required; saving client credentials alone does not verify a real social sign-in. No social access/refresh tokens are retained.

Tests exercise verified activation, the seven-day gate, preserved payment access, single-use reset/recovery, current-session revocation, code replay, SMS destination binding, Google signed ID-token validation, social account collision/state replay, and cryptographically signed WebAuthn registration/authentication with wrong-origin/user-verification failures. External email/SMS/OAuth calls are mocked; real delivery and user-device passkey UI depend on provider setup and browser support.


Production domain: `https://www.liontechacademy.com` is attached to the student-portal Vercel project. Canonical OAuth callbacks, Stripe checkout return/webhook setup links, calendar descriptions, class emails and password-reset links use this address. The bare `liontechacademy.com` remains on its existing separate project. The original `liontech-student-portal.vercel.app` continues serving the portal. Cookies are host-only, so a new address requires a new sign-in. Google/Facebook and calendar provider callback registrations must use the new canonical address before enabling those connections. Update any existing Stripe webhook endpoint in Stripe to `https://www.liontechacademy.com/webhooks/stripe`.

Passkeys have exact hostname/RP bindings. New registrations use the current allowlisted hostname: `www.liontechacademy.com` or the original Vercel hostname. Previously stored credentials without RP metadata are treated as original-host credentials and remain usable there. A challenge records its exact expected origin/RP and cannot be replayed across the two hosts. A legacy passkey cannot be used directly on the new hostname; use a recovery code, SMS or TOTP there, then register a new passkey. Preview and unrelated Host headers cannot create a passkey ceremony. The public canonical OAuth origin is fixed rather than inferred from request headers.


## Public terms and privacy

- Terms: https://www.liontechacademy.com/terms
- Privacy: https://www.liontechacademy.com/privacy
- Complete versioned text is maintained in `legal_content.py`; bump `POLICY_VERSION` when publishing changes.
- Password and social account creation require an unchecked acknowledgment and the current version. The server rejects absent or stale acknowledgments before creating a student.
- `portal_policy_acceptances` is an additive table recording student, version, acceptance time and registration method in the account creation transaction. Existing accounts are not backfilled with invented consent.
- Optional marketing, AI use and calendar authorization remain separate choices. Public policy pages and footer links are accessible without signing in.

### Registration graphics and social sharing

Public home, registration, student sign-in, program, announcement and legal pages render Open Graph and large-image card metadata on the server. The share image is a public, versioned 1200 × 630 JPEG under `/static/brand/`; no login or JavaScript is required to retrieve it. Public share URLs use the fixed canonical academy origin and exclude request query strings. Private pages receive no social URL metadata and have `noindex,nofollow`. WhatsApp, Facebook, LinkedIn, native device sharing and copy-link controls share only the public `/register` invitation after user interaction. They do not automatically send messages or load tracking widgets. Browser icons and Apple touch icons use the new academy mark. Only versioned static brand files get public immutable caching; account pages remain no-store. Third-party preview caches and recipient settings may delay or suppress a preview; already-sent messages are not rewritten by a deployment.

### Flutterwave V3 enrollment payments

Configure the existing Flutterwave integration with its V3 secret API key, an independently generated random webhook secret hash, and an optional merchant-enabled checkout currency (USD by default). Register `https://www.liontechacademy.com/webhooks/flutterwave` in Flutterwave's **V3** webhook settings for `charge.completed`; V4 HMAC signatures are not accepted by this V3 endpoint. Store the same hash in the portal and Flutterwave; never use an API key as the webhook hash. The authenticated checkout route creates a server-priced enrollment-fee reference, saves the expected amount/currency/FX snapshot, and redirects only to an allowlisted Flutterwave checkout host. Non-USD amounts require a recent exchange-rate snapshot. Tuition links and existing external Flutterwave payment links are separate and are not automatically matched to portal invoices.

Both the signed V3 webhook and authenticated return handler query Flutterwave's transaction verification endpoint and require an exact reference, successful status, matching transaction ID, amount and currency. Browser query parameters and webhook amounts are not payment proof. Transactions are deduplicated, sandbox checkouts do not create real ledger entries or access, and test/live credential changes cannot verify checkouts from the opposite environment. Live late/duplicate payments are flagged for review; revoked enrollment is not automatically restored. Pause prevents new checkout but keeps configured credentials available for verification of in-flight transactions. Checkout and verification tests mock provider calls; production credentials, merchant approval, supported currencies, sender/provider limits and real payment completion must be verified by the account owner.

### Data Analytics external-link payment plan

A one-time policy bootstrap configures future Data Analytics pricing at USD 59.97: USD 10.00 before the first class, USD 9.99 balance of the first payment, USD 19.99 second payment, USD 19.99 final payment. The initial USD 10.00 is included in the total and credited toward the first USD 19.99; it is not an additional fee. Every stage uses https://flutterwave.com/pay/liontechpaymentplan. The hosted page accepts a custom USD amount and a student number, but at verification time its description still advertised an older USD 69.97 total; the merchant must update that description in Flutterwave. The portal explicitly discloses the mismatch until the hosted page is corrected.

Eligible unpaid enrollments receive immutable schedule snapshots and a USD 10.00 initial invoice. Existing paid, submitted, provider-checkout or saved BillingSelection agreements retain their prior terms. New link-plan enrollments bypass Stripe/API Flutterwave checkout and use private receipt uploads instead. No provider API connection is required. Staff must reauthenticate and verify actual funds and the exact staged USD amount before approving. A verified initial payment unlocks eligible access, counts toward tuition once, and preserves manual holds, rejected enrollments and suspended accounts. Later receipts reduce the same USD 59.97 balance; completion records tuition as completed. Duplicate references/repeated approvals cannot double-credit funds. Receipt files require owner/admin authorization. Admins receive a saved review email event; delivery requires the configured email service.

Each student’s selected cohort start date anchors the schedule: USD10 before the first class, USD9.99 seven days later, USD19.99 one calendar month after that balance date, and USD19.99 two calendar months after that balance date. Calendar-month offsets clamp to the last valid day of shorter months without drifting subsequent months. Dates stay fixed when payments are late or reviewed later. Previously unspecified dates on this same four-stage plan are filled from its stored first-class date (or assigned cohort); amounts and custom deadlines are preserved. Students awaiting a cohort see the timing rules until a class is assigned. All deadlines are academy calendar dates (America/Toronto), not UTC timestamps. Existing daily tuition reconciliation and reminder jobs now include staged plans, count the initial deposit once, and treat a payment as overdue only after its due date. Manual access holds and administrator-completed tuition remain respected. Existing generic plans retain their original monthly schedules. Financial forecasts count the credited deposit once. Tests opt into the policy explicitly with DATA_ANALYTICS_LINK_PLAN=True; legacy fixtures disable bootstrap to continue verifying preserved pre-existing contracts.


### Preferred intake calendar (2026–2027)
Data Analytics offers every Saturday from the current academy date through December 31, 2027. `/apply` and new program enrollment show an accessible month calendar, a month jump selector, selected-date feedback and a live payment preview. Without JavaScript, the labeled start-date dropdown remains available. Other programs retain their published next two weekend intakes. Server checks independently reject past, Sunday, out-of-range and cross-program dates at selection, account creation and signed-in enrollment. Existing students starting Data Analytics choose a date too; existing enrollments are retained.

New Data Analytics plans record the USD10 enrollment payment due immediately on enrollment (Toronto date), independently of the selected start. The remaining deadlines are start +7 days, then +1 and +2 calendar months from that balance date. The first-class anchor is stored separately in the schedule JSON, so a fee due immediately cannot shift later dates. Previously agreed schedules keep their existing deadlines. Intake dates are enrollment planning dates; actual Zoom lessons still require scheduling/publishing. Payment eligibility remains subject to administrator verification while the payment-link flow is used.


### Automatic local pricing and scheduling
The request country is read from Vercel's country header only on Vercel (or the explicit test trust switch). Confirmed country corrections and profile preferences take precedence. Missing/invalid detection shows an inline country correction and a pricing-unavailable message; it never defaults the student to the internal price currency. Server templates render localized tuition, enrollment, installments, balances, payment summaries and receipt history before client scripts run. The pricing API, student assistant context and public language catalog do not expose internal prices. Authoritative numeric prices and existing ledger contracts remain unchanged, with administrative access to their original amounts.

Rates are fetched server-side from the existing fixed exchange-rate provider and cached centrally, with at most one refresh per day and an hourly circuit breaker on failures. Rates older than 48 hours are not used for new local quotes. No fictional rate or alternate charge currency is substituted. Decimal arithmetic and currency precision are applied to cumulative installment boundaries so the displayed installments sum to the displayed total, including zero-decimal currencies. Local checkout quotes are immutable, enrollment/stage/owner bound, and valid for 30 minutes. Their internal credit, local tender, exchange rate and timestamp are stored separately. Country changes and stale quotes require a fresh checkout. API-created checkouts carry the exact saved local amount and currency; provider verification independently checks them. Expired/duplicate/mismatched or sandbox payments cannot grant automatic access. Existing legacy provider webhooks remain supported. Manual approval checks the linked local tender and credits the original internal ledger once.

The old shared merchant payment link and prearranged fixed-currency URLs remain saved in administrative configuration but are not offered as localized student checkout: they cannot guarantee the displayed local currency. Where a connected provider cannot accept the local currency, students are directed to confirmed local payment instructions and receipt review. No conversion preview is represented as a provider connection or real payment test.

Vercel's time-zone header supplies the initial server time zone. Browser IANA detection refines the visitor zone and saves it to the authenticated profile; a deliberate manual choice is retained. Recipients' own confirmed zones and currencies are used in student emails and WhatsApp class messages. UTC instants stay canonical; weekly scheduling expands the academy wall time in its IANA zone separately for every occurrence (maximum 26), rejecting DST gaps/ambiguities before provider calls. Financial date deadlines are interpreted as end-of-day in America/Toronto and then localized. An intake without a class time remains an explicitly labeled academy calendar date, rather than inventing a session instant. Local-calendar payment previews use server-generated deadline instants. HTML responses remain no-store to prevent cross-country price caching. Countries whose own currency is the dollar see their local dollar symbol; internal pricing labels are omitted from student views.


## Staff-prepared enrollment review

Assign a student or payment record from **Team → Task dashboard**. The active, verified staff member assigned to that task can open **Prepare enrollment for review**, save a draft, and submit the proposed program, cohort, new/existing status, tuition, reported paid-to-date, installment schedule and contact/evidence notes. Submitting does not create an enrollment, verify a payment, post opening credit or grant access.

Administrators use **Enrollment submissions** to independently verify the packet, return it with correction notes, or decline the packet. Final approval uses the existing password-confirmed tuition review and requires program, tuition and payment-eligibility confirmations. Approval applies the verified agreement, grants eligible program access, completes the staff task and preserves both the original staff proposal and final approved figures. Returning or declining a packet does not revoke existing approved access. Account, email-verification and MFA requirements continue to apply.

Submitted versions are immutable. Corrections create a new version. Reassigned or inactive staff cannot edit or approve earlier packets. Changed enrollment/payment snapshots require resubmission. A submitted packet prevents a payment callback from bypassing administrator review, including when the packet is returned or reassigned; existing paid access stays available. Only an active, currently assigned pending submission protects an account from the payment-deletion sweep. Staff drafts do not pause that policy. Submission notifications go to the configured administrator notification address through the existing email outbox, with a secure review link and no payment amounts.


## Specific lesson task assignments and publication review

From **Team → Assign lesson tasks**, select exactly one lesson, an active verified staff member, due date and action: content, recordings, schedule or view. Each task has a stable number such as `LTA-TSK-000042`, derived from its immutable task ID; existing task numbers also appear throughout the task dashboard and alerts. Assignments do not create course-wide teaching grants. Staff use **My assigned lessons**; reassignment immediately revokes access for the former assignee, including draft downloads.

Content submissions require five separate document files: lesson outline, flyer, presentation, student manual and lesson manual. File storage accepts the existing lesson-resource formats and size limits. Raw video, YouTube and Zoom recording links are optional for the initial content package. A recordings-only task allows these to be submitted after class without requiring the five documents again. Draft resources stay outside the live lesson attachments; submitted versions are immutable. Administrators independently review documents/media, confirm content and reauthenticate to approve and publish, request corrections, or decline. Approved content becomes available under existing program enrollment/payment rules. Existing published content remains available while a revision is pending. All legacy staff media/upload mutations for a task-managed lesson are blocked so they cannot bypass this review path.

Scheduling tasks are pre-authorized for their exact lesson, using an existing Zoom meeting link or the connected Zoom account. A task publishes one class only; provider timeouts preserve an uncertain state and prevent automatic duplicate meeting creation. The operator must check Zoom and attach an existing meeting link to resolve uncertainty. View-only tasks cannot edit, upload or schedule.

Administrator email and WhatsApp review notices have separate durable delivery records. Email uses the configured SendGrid sender and administrator notification recipient. Configure **Integrations → WhatsApp → Administrator WhatsApp number for lesson-review alerts** and **Approved lesson-review WhatsApp template SID**. The approved plain-text template uses `1 task number, 2 lesson, 3 program, 4 submitting staff name, 5 authenticated administrator review URL`; sample copy is available in the review queue. Missing provider/template/recipient setup is visible and does not lose the submitted packet. Pending setup notices are retried by the authenticated daily maintenance job or the queue's password-confirmed check action; ambiguous provider responses require checking provider activity before any resend. Test requests never send real notifications unless explicitly configured with mocked delivery.


## Portal quality and performance review — November 13, 2026

The review covers 45 public, student, staff, instructor and administrator screen routes, plus a cross-feature journey from signup and audited tuition approval through private lesson preparation, administrator publication, class scheduling, unpaid-access restrictions and subsequent balance updates. Historical production errors were from a superseded deployment; no error groups were attributed to the live deployment at the start of this review.

Changes: translation dictionaries are fetched only when needed and reused in browser session storage with content-version and audience-specific keys. Public UI assets receive content-hashed URLs and immutable caching; unversioned assets revalidate. Static files do not emit or refresh account cookies or activity records. Authenticated HTML, private files and account/payment responses retain `no-store`. Staff workspace CSS/JS now follow template inheritance, including administrator review and instructor screens. Student progress reuses the released lesson list and batches completion lookup across programs. Reminder test fixtures use the academy’s Toronto calendar date, avoiding failures between UTC midnight and local midnight; payment-access tests check the real checkout link rather than a hidden dictionary entry.

| Representative page | Previous decoded HTML bytes | Updated decoded HTML bytes | Previous local median ms | Updated local median ms |
|---|---:|---:|---:|---:|
| Public login | 310,853 | 36,119 | 12.63 | 6.68 |
| Student dashboard | 337,549 | 63,404 | 36.57 | 28.63 |
| Student lesson | 328,041 | 53,744 | 22.11 | 16.09 |
| Administrator lesson assignment | 295,463 | 21,030 | 14.09 | 15.44 |
| Administrator lesson reviews | 288,115 | 13,663 | 10.33 | 10.65 |
| Staff dashboard | 310,574 | 36,003 | 14.18 | 8.29 |
| Staff task board | 311,134 | 36,601 | 15.45 | 10.19 |
| Student directory with 500 example accounts | 350,840 | 76,573 | 36.06 | 36.67 |

These are paired local samples against disposable file-backed SQLite fixtures with provider test doubles, not production latency or capacity guarantees. Each bounded read run completed 24/24 requests with four workers. Updated p95 was 75.62 ms versus 90.58 ms previously; throughput varied from 65.29 to 60.65 requests/second, so no throughput gain is claimed. The substantial verified gain is response-size reduction and cacheability; heavy directory/server work is not assumed solved by smaller HTML.

Reproduce with the repository dependencies plus pytest installed:

```bash
python scripts/portal_benchmark.py --students 500 --samples 5 --output /tmp/portal-benchmark.json
pytest school-portal/tests -q
```

The benchmark forcibly uses a temporary SQLite test database, synthetic accounts and mocked mail/FX/storage. It does not use production credentials or modify live records. Browser initialization was blocked by a browser cookie API error, so visual desktop/mobile rendering and browser Core Web Vitals remain unverified. Real payment charges, provider delivery and production stress load were not exercised. WhatsApp lesson-review delivery still requires the configured administrator recipient and approved review template; code-level integration tests do not substitute for that setup.


## Automatic student advisors

All active, verified staff have the additional Student Advisor responsibility without replacing their underlying staff permissions. Configure the server-only `STUDENT_ADVISORS` JSON list with initial identity anchors (`staff_number`, exact `email`, international `whatsapp`) and `STUDENT_ADVISOR_ADMIN_WHATSAPP` with comma-separated administrator numbers. Personal recipient configuration belongs in encrypted deployment variables, never in source control. Identity-anchor mismatches fail closed. Newly activated staff join the rotation automatically when their operational WhatsApp contact is available.

New password and social accounts allocate one advisor in the account transaction; additional enrollments retain that advisor. A database singleton lock serializes the round-robin cursor across workers. Failed signup transactions roll back the cursor, record-linked task and notification records together. Existing, non-archived active accounts are adopted in bounded automatic batches (five per pass) and by authenticated daily maintenance. Missing/inactive staff leave recoverable pending assignments. Suspended advisors are replaced automatically, retaining the account task and moving its follow-ups. Repeat passes do not duplicate tasks or welcome messages.

Students see the advisor card in their dashboard and a branded welcome email with student number, username, selected program (or a clear not-yet-selected message), advisor contact/button and academy support/operations contacts. Existing accounts receive the same introduction after adoption. Staff use **My students** and the task board to contact students and save the onboarding checklist. Existing tuition packets require administrator approval; new students are guided to secure portal checkout. Manual student/payment record allocation and reassignment of automatic advisor tasks are blocked while the service is enabled; lesson and other operational task workflows retain their existing controls.

Payment-due and recoverable-deletion warning events attach deduplicated follow-up tasks to the existing advisor. Assignment reminders target paid, approved, active enrollments with unsubmitted work due within 24 hours; reminders are deduplicated and cancelled if the work is submitted or its deadline changes. Student notices include advisor and important contact details. The daily notification cron drives scheduled reminders; request traffic also advances the bounded account backlog.

Student/advisor email and administrator email/WhatsApp confirmations have independent durable delivery records. Advisor WhatsApp notices honor the staff member’s saved task-alert consent for the matching number. Automated WhatsApp delivery uses the existing approved five-variable `TWILIO_TASK_TEMPLATE_SID`: `1 recipient name, 2 event, 3 task/student summary, 4 due date, 5 authenticated portal link`. Missing integration/template/consent remains visible in **Team → Automatic student advisors**; accepted means provider acceptance, not confirmed inbox delivery. Ambiguous WhatsApp handoffs are marked uncertain and never automatically resent. Administrator retries require current-password confirmation. Assignment data remains available even if a channel is not configured.
