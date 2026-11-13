# Team operations and lesson studio

The Team sidebar now links to Task dashboard and (administrator/instructor) Lesson studio. Navy, yellow, the existing staff shell, 48px controls and persistent Support follow the shared LionTech foundation. Three task lanes collapse to a single column on small screens. Meeting view hides navigation and assignment controls; it is a display mode, not an authentication or authorization change.

## Staff assignments

In Students, select rows and choose **Assign to staff**. The task dashboard opens with those students preselected. Students without an enrollment can be assigned. For payment follow-up, choose Payment follow-up and select enrollment payment records. Choose one active verified staff member, title, instructions, due date and priority. Up to 100 records can be assigned per submission, with one task per record. Assignment emails use the existing encrypted staff outbox; only a generic task title, due date and portal link are emailed.

Staff can open only records attached to their tasks. Supervisors can view records through tasks assigned to staff in their configured supervision scope. The record view exposes contact details and enrollment status; payment tasks additionally show recorded payment references and ledger entries. No payment approval, payment adjustment, enrollment clearance, private HR documents, or bank details are granted by allocation. Existing administrator verification remains authoritative. New allocations, reassignment and opening assigned records are audited. Reassignment immediately removes the former assignee's record access. Existing staff suspension/session policies still apply. Completed assignments remain available for accountability until reassigned; there is no separate record grant.

Staff record outcomes and update status using existing task rules. Click a staff member's name in Team or My team to see submitted reports by date, including status, version count, original report details and review history. Drafts are excluded from this history. Existing report scoping and immutable versioning remain in force.

## Teaching

Administrators grant and revoke course access to active, verified instructors in Lesson studio. Role alone does not grant access to all courses. The studio lists lessons for authorized courses. Uploaded resources attach directly to the selected lesson using existing private storage, chunking, checksums, size limits and Range requests. Administrators retain the existing file library. Instructors cannot upload branding files, manipulate another instructor's incomplete upload, or use administrator-only file management routes.

The lesson presenter provides full screen, previous/next resource navigation and arrow keys. It presents text, images, audio, uploaded video, PDFs and media links. PDF preview uses the browser's native viewer via a same-origin fetch and temporary Blob URL; it never submits private PDFs to an external conversion service. Native PDF support varies by browser; download remains available. PPT/PPTX files can be downloaded; upload a PDF or image version for in-portal slides. This feature does not provide slide conversion, video transcoding, a native video conference, screen broadcast, microphone capture or meeting recording. Share the presenter screen through the existing Zoom meeting when teaching remotely.

Video/audio limits: 250 MB each. Documents: 50 MB. Images: 10 MB. MP4 H.264/AAC or WebM are recommended for browser playback; unsupported formats can be downloaded. Larger recordings should use an external link. YouTube links are validated and canonicalized to youtube-nocookie.com; the iframe loads only after a click. Zoom links open externally. Public/unlisted YouTube and Zoom links retain the original host's sharing permissions, even though the portal hides links until the lesson is accessible.

Lesson publication and paid enrollment gates continue to protect student access to resources and new media links. Revoking a teaching grant takes effect on the next request. CSP allows only the required YouTube frame origin and browser Blob PDF frames, while retaining frame-ancestors none and object-src none.

## Storage and release

Four additive tables: staff_task_records, portal_teaching_grants, portal_teaching_uploads and portal_lesson_media. No existing table alterations, data rewrites or new dependencies. They are created with existing metadata startup initialization. The two-table staff assignment link stores the enrollment where relevant and the student identity for all task records.

Validation covers record isolation, reassignment, unchanged payment authority, students without enrollments, batch atomicity, scoped report history, instructor course grants/revocation, upload ownership, range/video download, unpaid/paid lesson access and unsafe URL rejection. Existing staff, file and branding tests also run. Browser visual review was attempted but the native browser's accessibility provider failed before navigation; presenter JavaScript, native PDF behavior, responsive rendering and YouTube playback have not been visually verified in this environment. No production staff accounts, student records, payments, invitations or notification deliveries are created as release tests.
