# Member profiles, operational contacts and staff task alerts

## Administrator workflow

Use **Member profiles & identity** from the profile menu or Team sidebar. Select an existing student, staff member or the original administrator. Link only accounts confirmed to belong to the same individual. Existing email matches never link accounts automatically. Each person can have one student account, one staff account and the original administrator account; extra Instructor and Administrator workspaces can be assigned to their staff identity.

New Student profiles receive a student number and their own learning record; no enrollment, payment waiver or class access is created. The owner accepts student terms and completes existing email/MFA requirements. New Staff profiles use the existing internal invitation and email-verification process; they remain inactive until accepted. A signed-in student can accept an invitation explicitly linked to their own person without signing out; destination email verification and staff policy acceptance still apply. Existing multi-account groups are not silently merged. A standalone account with no additional responsibilities can be explicitly attached; existing group assignments must be reviewed and detached first.

Changes require administrator password confirmation and a same-person attestation. Name corrections propagate across linked identities; usernames apply to the selected account. Staff usernames work alongside work email and staff number. The original administrator username can also be changed. Students cannot change their own name or username through either UI or direct requests. Avatar and verified email-change workflows remain available. Submitted report snapshots retain their original names.

Additional responsibilities do not replace a staff account's primary role. Change primary roles in Team management. Grant teaching courses in **Lesson studio** after assigning the Instructor profile. Administrator is broad portal administration, while HR and payroll continue to require their separately assigned staff role and scope.

## Real profile switching

**Switch my profile** opens real, personal workspaces; the old administrator preview remains a separate sample-data tool. Destination ownership is verified using an email code when required, bound to the source session, selected target and current membership revision. A recent staff verification can authorize switching between that staff identity's assigned workspaces. Student email verification, registered MFA and payment/enrollment gates still apply; a switch does not create MFA proof.

Only one role's authentication is active at a time. Session and CSRF nonces rotate, stale forms fail, and other open tabs move to the profile chooser after observing a changed session channel. Switching retains the original four-hour absolute session cap and 15-minute inactivity limit. Pending student MFA has a 15-minute completion cap. Source password/security-version changes, staff suspension, membership detachment or role revocation invalidate linked access. No role is selected through query strings, local storage or an email address match.

Delegated administrators use their own account name and password confirmation, and audit events identify the underlying account (for example admin:staff:24). They cannot change shared administrator recovery/password/security settings; those require original administrator sign-in. Returning to Student does not retain administrator or staff powers. Private HR/payroll visibility stays separate.

## Contact details

All profiles link to **Contact details**. Store a direct call number and a separate WhatsApp number in international +country-code format. Work contact details are separate from encrypted HR/payroll records. Assigned student records show authorized WhatsApp, mailto and tel actions with icons. Administrator student overviews, staff records and profile pages provide the same contact actions where a number exists. Invalid legacy local numbers are not guessed into international contact links. Public visitors cannot query other members' contact information.

Staff enable task WhatsApp alerts themselves using the explicit consent checkbox. They can withdraw consent at any time. A work time-zone selector makes date-only task deadlines explicit: **5:00 p.m. in that staff member's saved time zone**.

## Task notifications

Events: assignment, 24 hours before the deadline, one hour before the deadline, and a submission receipt when the task is marked completed. **Submit completed task** requires an outcome and records completion. Existing assignment emails continue; reminder and submission emails use the encrypted staff outbox. WhatsApp is additional and requires staff consent and the dedicated approved template below.

The existing authenticated daily maintenance route arms reminders within six days; assignment/progress/contact actions also arm immediately. Delivery uses the existing Vercel Queue topic and worker. Stable database event keys suppress duplicates; current task owner, assignment revision, status, recipient activation and consent are checked before sending. Reassigned, completed or stale reminder jobs are cancelled. Time-zone changes re-arm pending due jobs. Provider acceptance is reported separately from delivery; ambiguous network outcomes are marked Uncertain and are not automatically resent.

**WhatsApp task notifications & delivery status** shows pending setup, consent, queued, accepted, cancelled and uncertain records. Staff see only their own notifications; administrators can inspect the queue. No real recipient is used in automated release tests.

### One-time Twilio setup

In WhatsApp integration settings, configure **Approved staff task-notification template SID** (`TWILIO_TASK_TEMPLATE_SID`). Use an approved text-only utility template with these exact five variables:

```
Hello {{1}},
{{2}}: {{3}}.
Due: {{4}}.
View your task in the portal: {{5}}
LionTech Academy
```

1. Staff name
2. Event (New task assigned / Task due in 24 hours / Task due in one hour / Task submission received)
3. Task title
4. Due date, time and time zone
5. Secure task dashboard URL

A class-update template is not reused for staff tasks. Until a valid approved SID and staff consent are configured, records show Awaiting template or Awaiting consent and do not claim successful WhatsApp delivery. This release does not create or approve a Twilio/Meta template, change provider credentials, or send a live WhatsApp test message.

## Instructor-only teaching

Video uploads and YouTube management now require an active Instructor profile and the relevant course grant; administrator mode alone cannot use the lesson studio's media endpoints or upload videos through the generic file library. Authorized enrolled students can still watch and download released resources. Administrators manage teaching grants and can inspect existing resources.

Instructor **Teaching workspace** schedules manual Zoom classes, links lessons/cohorts, records selected eligible students as present, creates assignments/quizzes and reviews submissions. Attendance opens 15 minutes before a class. Scheduling uses the existing class notification/reminder pipeline. Course checks apply to classes, attendance, assessments and lab review/evidence routes. To submit their own assignments, an instructor switches to their Student profile; teaching mode does not submit as a student.

Existing presenter limits remain: browser-native PDFs/images/video/audio; PowerPoint downloads rather than native slide conversion; YouTube iframe after a click; Zoom external meeting links. No native conferencing or automatic video transcoding is added.

## Data and validation

Seven additive tables: portal_member_contacts, portal_member_people, portal_profile_memberships, portal_extra_profile_roles, portal_profile_handles, staff_task_alert_state and staff_task_notices. No alterations to existing tables or new dependencies. Existing startup metadata initialization creates the tables.

Tests cover switching and permission isolation, destination ownership codes, source/revision revocation, idle/absolute expiry, existing student MFA, admin-only identity changes, duplicate username atomicity, delegated audit attribution, contact normalization and access, task event deduplication, due reminders, stale/reassigned jobs, missing template/consent, uncertain provider acceptance, instructor-only media, scoped scheduling/attendance/reviews, and legacy profile/file behavior. Visual browser verification remains limited by the previously observed browser accessibility failure; no live provider delivery or authenticated production identity is created for release testing.
