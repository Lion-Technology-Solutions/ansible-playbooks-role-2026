# LionTech Christmas Bonanza

Production campaign: `/christmas-bonanza`. The default campaign window is November 13, 2026 through February 28, 2027 inclusive, in America/Toronto. The exclusive deadline is March 1, 2027 at 00:00 Toronto time (05:00 UTC). `BONANZA_ENABLED=False` hides and stops new claims without invalidating already confirmed plans.

## Commercial terms

Only DevOps, Cloud Computing and Cybersecurity qualify. Each selected program is USD 67 per month. The USD 67 enrollment payment **is the first month's tuition**, not an additional fee. Secure checkout converts the server-owned USD amount into the student's selected local currency using the existing quote flow. Later local-currency amounts may change with exchange rates.

| Program | Monthly payments | Total USD | Enrollment coverage |
| --- | --- | --- | --- |
| DevOps | 6 | 402 | One six-month program covering all three levels; selected level records the starting point |
| Cloud Computing | 3 | 201 | Selected twelve-week level |
| Cybersecurity | 2 | 134 | Selected eight-week level |

Original totals are read from current course policy. Savings and struck-through totals appear only when the promotional total is lower. In particular Cloud Foundation's standard USD 199 total is lower than the USD 201 monthly offer, and the review screen explicitly discloses this.

Month one is payable immediately. Its teaching period starts with the selected cohort. Remaining payments are due on monthly anniversaries of the cohort start, clamped for shorter months. A January 23 cohort has subsequent payments on February 23, March 23, etc. No recurring card subscription or automatic charge is created.

A qualifying first payment by the deadline locks the monthly rate for this selected program's duration, including a later cohort. A date selection or account creation alone does not confirm enrollment. Data Analytics and existing agreed paid plans retain their terms.

## Enrollment experience

Course → yellow-highlighted calendar → level → public lessons and full pricing/payment schedule → explicit terms → account creation or sign-in → secure payment. Standard entry points lead to review during the promotion. Calendar intake choices for the three program families are the fourth Saturday of each month from January 2027, over a rolling two-year horizon. Existing class sessions and student cohort assignments are not moved.

The public review contains lesson titles, not private lesson resources, recordings, class schedules or access tokens. Email verification, MFA, ownership checks, course publication checks, CSRF and existing access holds continue to apply. The global support control remains available.

## Storage and payment safety

The additive `portal_bonanza_enrollments` table binds the program, level, date, duration, original comparison, expiry and confirmation to a specific enrollment. The existing enrollment payment schedule stores immutable USD 67 stages. No existing paid plans are migrated. Existing payment attempts, receipts, billing selections or verified enrollments prevent repricing.

Only verified live Stripe/Flutterwave results credit the existing ledger and confirm the offer. Regional provider routing is unchanged: Flutterwave in Africa, Stripe elsewhere. Test payments do not confirm enrollment. Quote amounts, currency, student identity, stage and ownership are server checked; return-page parameters cannot set the amount.

An unconfirmed first payment received after the campaign cutoff is held for review, with no automatic access grant or substitution of a higher price. To credit a delayed callback, an administrator must verify actual funds, reauthenticate and explicitly attest that the first payment was paid before the deadline. If it was paid later, reconcile or refund through the provider. Already confirmed plans can continue paying after the campaign closes.

Schema creation is additive through the portal's existing metadata initialization. Rollback can disable new claims with `BONANZA_ENABLED=False`; confirmed schedules must be preserved. Do not delete payment or binding records during rollback.

## Verification

Automated coverage includes calendar validity, monthly payment dates, public/private gating, all three durations, first-month credit, honest comparisons, registration and returning login, email verification, forged dates and programs, existing payment preservation, regional handoffs and exact provider amounts, live versus sandbox settlement, duplicate callbacks, Toronto deadline boundaries, continuation of confirmed plans and explicit late-payment reconciliation. Existing portal regression and CI checks also run before release.
