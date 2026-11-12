# Program levels and scheduled labs

Public discovery starts at `/programs`. Cybersecurity, Data Analytics and Cloud Computing have separate Foundation, Intermediate and Advanced pages, complete curriculum lists, level-specific enrollment, resource tabs and lab submissions. DevOps retains its existing implementation.

Cybersecurity has eight weeks per level: three live lecture hours and six hours of instructor-guided labs/projects weekly (72 learning hours per level). Lectures are Monday, Wednesday and Thursday respectively, 6–9 p.m. America/Toronto. Foundation starts on the last Monday monthly from January 25, 2027. Instructors publish actual lab times separately.

Internal Cybersecurity tuition is USD 199 / 399 / 401. New enrollments can select full payment or two equal installments. Installments are due on the 7th of the intake month and the next month; a passed first deadline requires immediate payment. Enrollment-specific snapshots and existing payment records are preserved. Student displays and checkout use the existing server-side currency quote and payment verification systems.

Data Analytics retains its existing curricula, durations and administrative pricing. Existing payment plans are not rewritten.

Cloud Computing is an AWS-led pathway with Azure and Google Cloud comparisons. Each level is 12 weeks, with 12 lesson outlines and 108 learning hours (36 live + 72 guided practical). Tuition is USD 199 / 299 / 399. Monthly amounts are 66.33 + 66.33 + 66.34; 99.67 + 99.67 + 99.66; and 133 + 133 + 133. New plans support full payment or three monthly installments due on the 7th of the intake month and next two months. Actual Cloud class times are published by the instructor; no new weekday/time is assumed. Existing saved plans remain unchanged.

## Teaching workflow

- Admin: Materials → Manage & schedule labs, or `/teaching/labs`.
- Active, verified staff with the Instructor role: Staff navigation → Schedule labs & review submissions.
- Choose the course level. Cybersecurity includes ten sample lab drafts per level. Customize the instructions before publishing. Create Data Analytics labs using the same form.
- Set cohort (or all level cohorts), start time, time zone, duration, meeting link and a separate submission deadline.
- Drafts do not expose content or send notifications. Publishing creates/updates a scoped class/calendar entry, portal notices, existing email delivery jobs and 20-minute reminders. Delivery depends on the configured providers; failures remain tracked for retry.
- Review submissions from the lab editor. Save a score and feedback to notify the student.

Students need approved, payment-verified enrollment in that level and matching cohort to access a published lab. Evidence files are private and available to their owner and authorized instructors/admins. Submissions after the deadline are accepted and labelled late for instructor review. Updates clear the previous grade.

## Additive bootstrap

New tables: CybersecurityUnit, CloudUnit, LabExercise, LabEvidence. Cold-start initialization holds the existing PostgreSQL advisory schema lock. Seeding inserts missing stage prices/policies, 24 Cybersecurity draft lesson outlines, 36 Cloud draft lesson outlines and 30 Cybersecurity draft labs without resetting customized content. No actual meetings, enrollments, grades or payments are fabricated. Sample labs remain drafts until an instructor publishes them.

## Validation

`pytest -q tests/test_pathways.py` covers public discovery, localized pricing, last-Monday intakes, payment snapshots, draft and level/cohort access controls, scheduling delivery records, private evidence, grading, instructor authorization and idempotent bootstrap. Existing analytics/payment/upload/session tests provide regression coverage.
