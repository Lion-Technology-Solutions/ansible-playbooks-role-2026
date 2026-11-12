# Student notifications

The portal uses the authenticated SendGrid sender saved in Integrations. This change does not replace it with a Gmail From address. Signup review alerts and email replies use `liontechacademy@gmail.com` by default (`ADMIN_NOTIFICATION_EMAIL` can override the recipient in application configuration).

| Event | Recipient and channel |
| --- | --- |
| New account | Welcome email to the student; review email to the administrator |
| Published class or schedule change | Existing class-update email/calendar flow and opted-in student WhatsApp alert |
| Class reminder | Email and opted-in WhatsApp to eligible paid, approved students, targeted 20 minutes before the class |
| CRM/HubSpot WhatsApp signup alert | Disabled, including unsent alerts created by older releases |

Existing security emails, tuition notices, staff report copies, calendar connections and student payment-alert consent remain supported. Student class preferences live under Connected accounts. WhatsApp reminders use the optional approved `TWILIO_CLASS_REMINDER_TEMPLATE_SID`; if absent, the existing approved class-update template is used with a reminder in the class-title variable. Both templates must have the same four fields: student number, class title, localized class time, secure portal class URL. No private Zoom URL is inserted into the message.

## Reminder delivery

`ClassReminderJob` is an additive table keyed by class ID and schedule revision. Publishing a class creates a durable job in the same database transaction. Only committed schedules are armed for delivery. A Vercel Queue message is delayed until class start minus twenty minutes, with seven-day retention. To stay within that retention, the scheduler arms classes inside a six-day horizon. Existing authenticated daily maintenance, class scheduling, and administrator dashboard/notification visits arm outstanding jobs. Role previews do not arm jobs. The existing daily cron is retained; frequent paid cron scheduling is not required. Production already has a CRON_SECRET configured; its value was not read or changed.

The queue consumer resolves recipients at delivery time. Rescheduled, unpublished or started classes are cancelled. Enrollment, cohort, account state, archive state, email preferences and WhatsApp consent are rechecked before provider submission. Event keys and row locks prevent repeated worker delivery from resending already accepted messages. Provider acceptance is not confirmation of inbox/handset delivery; reminders target the twenty-minute point, with actual arrival subject to worker and provider latency.

Immediate signup/class updates continue to use direct delivery. The delayed queue is used for timed reminders. The existing queue subscriber in integration_jobs.py handles the new class_reminder payload containing only the durable job ID. Scheduling failures remain visible under Admin → Notifications and are retried on later administrator visits or daily maintenance, up to five attempts; the queue consumer also retries failed handoffs. No live student messages are used in automated tests.

## Validation

Tests cover sender/recipient separation, cancellation of old CRM alerts, the twenty-minute delay, duplicate worker invocation, current enrollment/cohort and opt-out checks, rescheduling, unpublishing, already-started classes, failed scheduling, partial-delivery retries, long-horizon scheduling, transaction rollback and read-only role previews. Full regression CI installs dependencies from the same pyproject.toml used by Vercel.
