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

## Assignments, quizzes and results

Admins publish assignment or quiz instructions for a course, with an optional due date. Students submit text answers and an optional project/document/file link. Admins review, assign 0–100, and publish feedback. Results and a notification appear in the student's own portal. Updating an answer clears the previous grade and returns the submission for review. Quizzes are instructor-reviewed, not automatically scored multiple-choice exams. Uploaded files are represented by student-supplied sharing links; the portal does not store file binaries.

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

Tests explicitly use isolated SQLite databases and check country-specific payment routing, dates/new-vs-existing handling, approval gates, duplicate payment references, student isolation, CSRF, class registration, attendance uniqueness and WhatsApp payloads, scheduled recordings, notifications, submissions/results, password resets, revocation, stable and backfilled student numbers, Saturday/Sunday rollover (including Toronto midnight and year-end), editable country-specific program details, admin-only course/content publishing, private previews, duplicate URL labels, per-course checkout isolation, scheduled events, escaped announcements, preserving enrolled access after unpublishing, automatic account activation, per-course payment gates, direct protected-route checks, combined and separate admin approval, recording-notice revocation, class joining links, and idempotent saved support notifications. Deployed verification checks the real PostgreSQL-backed health endpoint and the critical live user flows.

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
