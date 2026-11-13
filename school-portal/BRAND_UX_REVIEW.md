# LionTech Academy: brand and UX review

## Scope and evidence

Reviewed all 129 HTML templates: 74 page templates and 55 shared fragments/base files. Every full HTML document uses `base.html`; staff pages inherit through `staff_base.html`. Compiled every template and exercised public, student, administration and staff page families with isolated local test accounts.

Live HTTP review before changes: `/`, `/programs`, `/student/login`, `/register`, `/updates`, `/terms`, `/privacy`, `/staff/sign-in` and `/admin/login` returned 200. The public program directory, staff sign-in and admin sign-in exposed support primarily in the footer. An unknown route returned 404. Authenticated production screens were not opened or modified through an administrator session.

Browser visual inspection could not run: the cloud browser failed with `Storage.setCookies: Invalid cookie fields`, and native Chromium control failed because its accessibility provider was unavailable. Editable desktop/mobile Figma views were visually inspected; this is not a claim of browser screenshot verification for every live screen.

Design reference: https://www.figma.com/design/KEjpn4ZX7yaYecgGf6nZ2F?node-id=104-127

## Improvements applied across page families

| Area | Finding | Change |
| --- | --- | --- |
| Identity | Public, student, admin and staff shells used different wordmarks or cropped logos. | One shared academy lockup with a role caption and support for the configured uploaded logo. |
| Colour | Navy, yellow, buttons and surfaces varied across separate themes. | Shared tokens, with configured primary/accent colours applied after component styles. Foreground colour adapts to light or dark brand settings. |
| Typography | Public/staff/student themes used separate font aliases and inconsistent control sizes. | Self-hosted Inter throughout, consistent heading hierarchy, readable form text, 48px primary controls, preserved larger-text preference. |
| Support | Help often lived in a footer or expanded menu. | Persistent keyboard-accessible Help disclosure on every HTML page, visible navigation links, and a new `/support` centre with WhatsApp, email and topic guidance. |
| Privacy | A global support action should not include private account or personnel details. | New support links use generic messages; no student/staff fields are added to outbound URLs. Existing explicit account-request flows remain separate. |
| Graphics | Program pages lacked distinct visual cues. | Five small decorative SVG illustrations for learning and the four program families; existing welcome imagery retained. |
| Navigation | Administration had a long flat list of links. | Primary work stays visible, secondary administration and curricula are expandable. Sign-out remains accessible. |
| Payment copy | Homepage instructions still requested a payment reference after manual submissions closed. | Enrollment guidance now points to secure provider checkout. Tuition, routing and verification logic are unchanged. |
| Legal pages | Inline layout styles conflicted with the existing Content Security Policy. | Layout moved into the shared stylesheet, with readable policy navigation. |
| Mobile/print | A floating support control must avoid mobile navigation and certificate printing. | Safe-area-aware positioning above learner bottom navigation; scrollable bounded panel; support controls hidden for print. |

## Interaction and release checks

- Native `<details>` support works without JavaScript. With JavaScript, close-button, outside click and Escape behaviour are available, with focus returned to the launch control when explicitly closed.
- Relevant icons use the existing SVG sprite; branding and program graphics are local assets with fixed dimensions and decorative empty alt text where adjacent copy provides the meaning.
- Public help and theme CSS remain reachable during email verification/MFA setup; they do not complete verification or grant learning access. Protected routes retain their existing checks. No payment, enrollment, HR permissions, session policies or notification delivery settings are changed.
- Regression coverage checks shared-shell reach, all-template compilation, brand-setting precedence, isolated support URLs, graphics and representative role pages. Provider/network operations remain mocked during local tests.
- Release requires the full portal test suite, repository CI, healthy production responses and the deployed support/brand assets. A manual browser review of desktop and narrow mobile authenticated pages remains a verification limitation in this environment.

## Page-template inventory

| Template | Inherits |
| --- | --- |
| `account_location.html` | `base.html` |
| `account_review.html` | `base.html` |
| `admin.html` | `base.html` |
| `admin_forgot_password.html` | `base.html` |
| `admin_password_verify.html` | `base.html` |
| `admin_profile.html` | `base.html` |
| `analytics_catalog.html` | `base.html` |
| `application.html` | `base.html` |
| `application_payment.html` | `base.html` |
| `assistant.html` | `base.html` |
| `assistant_review.html` | `base.html` |
| `attendance.html` | `base.html` |
| `attendance_saved.html` | `base.html` |
| `certificate.html` | `base.html` |
| `chatgpt_settings.html` | `base.html` |
| `class_registered.html` | `base.html` |
| `connected_accounts.html` | `base.html` |
| `course_page.html` | `base.html` |
| `devops_catalog.html` | `base.html` |
| `devops_workspace.html` | `base.html` |
| `enrollments.html` | `base.html` |
| `error.html` | `base.html` |
| `file_library.html` | `base.html` |
| `forgot_password.html` | `base.html` |
| `global_payment.html` | `base.html` |
| `home.html` | `base.html` |
| `integration_activity.html` | `base.html` |
| `integration_detail.html` | `base.html` |
| `integrations_hub.html` | `base.html` |
| `lab_page.html` | `base.html` |
| `lab_reviews.html` | `base.html` |
| `legal.html` | `base.html` |
| `lesson_page.html` | `base.html` |
| `level_enroll.html` | `base.html` |
| `link_payment_plan.html` | `base.html` |
| `live_classes.html` | `base.html` |
| `login.html` | `base.html` |
| `mfa_challenge.html` | `base.html` |
| `notification_admin.html` | `base.html` |
| `portal.html` | `base.html` |
| `profile_email_verify.html` | `base.html` |
| `program_catalog.html` | `base.html` |
| `program_level.html` | `base.html` |
| `program_levels.html` | `base.html` |
| `register.html` | `base.html` |
| `reset_password.html` | `base.html` |
| `role_preview.html` | `base.html` |
| `role_switcher.html` | `base.html` |
| `schedule_admin.html` | `base.html` |
| `security_account.html` | `base.html` |
| `security_codes.html` | `base.html` |
| `security_settings.html` | `base.html` |
| `sms_setup.html` | `base.html` |
| `social_complete.html` | `base.html` |
| `staff_auth.html` | `base.html` |
| `staff_base.html` | `base.html` |
| `staff_home.html` | `staff_base.html` |
| `staff_locked.html` | `staff_base.html` |
| `staff_notifications.html` | `staff_base.html` |
| `staff_people.html` | `staff_base.html` |
| `staff_personnel.html` | `staff_base.html` |
| `staff_profile.html` | `staff_base.html` |
| `staff_report_detail.html` | `staff_base.html` |
| `staff_reports.html` | `staff_base.html` |
| `staff_review_queue.html` | `staff_base.html` |
| `staff_tasks.html` | `staff_base.html` |
| `staff_team.html` | `staff_base.html` |
| `student_profile.html` | `base.html` |
| `support_center.html` | `base.html` |
| `teaching_labs.html` | `base.html` |
| `totp_setup.html` | `base.html` |
| `update_page.html` | `base.html` |
| `updates.html` | `base.html` |
| `verify_email.html` | `base.html` |

Shared fragments/base: `account_requests.html`, `admin_attendance.html`, `admin_certificates.html`, `admin_course_links.html`, `admin_dashboard.html`, `admin_filters.html`, `admin_finance.html`, `admin_prices.html`, `admin_results.html`, `admin_student_table.html`, `admin_students.html`, `admin_teaching.html`, `base.html`, `branding_admin.html`, `catalog_admin.html`, `class_links_admin.html`, `dashboard_navigation.html`, `dashboard_topbar.html`, `devops_payment_terms.html`, `enrollment_approvals.html`, `enrollment_payment.html`, `global_pricing_admin.html`, `integration_delivery_control.html`, `journey_header.html`, `lesson_admin.html`, `link_plan_summary.html`, `local_checkout_controls.html`, `local_preferences.html`, `macros.html`, `manual_payments_admin.html`, `money_quote.html`, `payment_methods.html`, `policy_acknowledgment.html`, `portal_course_card.html`, `portal_dashboard.html`, `portal_overview.html`, `profile_menu.html`, `program_settings.html`, `provider_payment_history.html`, `public_content_admin.html`, `public_highlights.html`, `public_language.html`, `session_controls.html`, `share_invitation.html`, `social_buttons.html`, `staff_profile_menu.html`, `staff_stepup.html`, `start_calendar.html`, `student_dashboard.html`, `student_detail_panel.html`, `student_management_actions.html`, `students_console.html`, `support_widget.html`, `update_card.html`, `welcome_story.html`.
