# CDAC Attendance — Face Recognition Attendance System

A production-ready, self-hosted **facial-recognition attendance platform** for classrooms and training batches. Students are marked present simply by looking at a camera — no cards, no roll-calls, no manual registers. Built with **FastAPI**, **InsightFace (buffalo_l)** and a lightweight **SQLite** backend (PostgreSQL optional), it runs on a single machine or a small VM with zero external cloud dependencies.

Around that core it has grown into a full attendance workflow: batches with their own slot timings and academic calendars, teacher portals scoped to assigned batches, a student self-service portal, unattended kiosk terminals, leave and dispute handling, and automated email reporting.

> Originally built for the CDAC Mumbai PGCP batches (BDA and AI), but fully configurable for any institute, course, or batch.

---

## ✨ Why this project (USP)

| # | Unique Selling Point | What it means |
|---|----------------------|---------------|
| 🎯 | **Contactless, 1-second attendance** | A student walks in, faces the camera, and is marked present. No hardware beyond a camera. |
| 🧠 | **High-accuracy 512-D embeddings** | InsightFace `buffalo_l` (w600k) producing 512-dimensional face embeddings — robust across lighting, angles and diverse face types. |
| 🛡️ | **Passive anti-spoofing / liveness** | MiniFASNetV2 (ONNX) liveness detection to block photo, phone-screen and printout spoofing. **Currently disabled pending threshold validation — see [Anti-spoofing status](#-anti-spoofing-status).** |
| 🏫 | **Real multi-batch model** | Every batch has its own slot timings, holidays, academic calendar, terminal PIN and teachers. Nothing is shared by accident. |
| 🖥️ | **Unattended kiosk terminals** | A tablet signs in with a batch PIN and marks attendance all day. A *combined* terminal covers several batches at once — but only if the operator holds every one of their PINs. |
| 👩‍🏫 | **Role-scoped portals** | Admin, teacher, student and terminal each see only what they should. Teachers are hard-scoped to their assigned batches. |
| 📝 | **Requests, not back doors** | Students raise leave requests, attendance disputes, detail changes and face re-registration. A teacher approves; nothing self-applies. |
| 📊 | **Live and historical analytics** | Live present count, class trends, at-risk students, day-of-week and heatmap views, per-subject attendance, per-student sparklines. |
| 📧 | **Automated email reporting** | Monthly student and teacher reports, weekly low-attendance warnings, same-day "you weren't marked" nudges — all deduplicated and logged. |
| 🔐 | **DPDP Act 2023 compliance** | Versioned biometric consent with a record, a withdrawal path and a stated retention period. |
| 🌐 | **Self-hosted, offline-first** | Runs fully on-premise over HTTPS. Installable as a PWA. No student biometric data ever leaves your server. |
| 🕒 | **IST-correct timekeeping** | All slots, dates and reports are timezone-aware (`Asia/Kolkata`). |

---

## 🏢 Institutes (multi-tenancy)

One deployment can serve several institutes without their data touching. The
`institutes` table is the tenant; `courses.institute_id` is the anchor, and
because nearly every table already carries a `course_id`, a row's institute is
reachable from it.

**Tenancy is inert while there is one institute.** Every scoping rule in
[tenancy.py](tenancy.py) falls back to unscoped behaviour when
`institute_count() <= 1`, so a single-institute deployment behaves exactly as it
did before this existed. The rules start applying the moment a second institute
is created.

What is scoped, and why each one matters:

| Scoped | What it would otherwise do with two institutes |
|---|---|
| **Face galleries** (`AttendanceSystem.gallery_for`) | one flat gallery of every student meant a student at institute A could be recognised and marked at B's terminal |
| **`GET /api/terminal/batches`** | unauthenticated by design, so it would hand anyone opening the login page every customer's batch names. Now needs `?institute=<slug>` once there are several |
| **Holidays** (`working_days.holiday_dates`) | `course_id IS NULL` means "every batch" — across institutes that silently wiped a working day a customer never declared |
| **Slot timings** (`attendance_manager`) | the all-batches fallback let one institute's hours decide whether another's student was inside their slot |
| **Face login** | no session exists yet, so it takes an `institute` slug; without one on a multi-institute deployment it refuses rather than searching everyone |
| **Combined terminals** | holding two PINs no longer joins two institutes onto one kiosk |

Isolation is covered by [tests/test_tenant_isolation.py](tests/test_tenant_isolation.py)
(28 tests), written adversarially: two institutes, colliding holiday dates,
colliding slot timings.

> ⚠️ SQLite is one writer at a time. It is fine for a single institute and gets
> risky with several punching in at once — move to PostgreSQL via `DATABASE_URL`
> before onboarding a second tenant, not after.

---

## 👥 Roles

Sessions are **database-backed** (`sessions` table), so a restart or a deploy no longer signs everyone out.

| Role | Signs in with | Gets |
|------|---------------|------|
| **superadmin** | username + password | Platform level: every institute. `users.institute_id` is NULL |

> No superadmin account is created by the migration — a privileged login
> appearing by itself, with a password nobody can account for, is worse than
> running one command. Make one with `python create_superadmin.py`; it prompts
> for the password rather than taking it as an argument. The owner signs in at
> `/login` like anyone else and lands on `/superadmin`.

| **admin** | username + password | Everything **inside their own institute**: batches, teachers, students, slot config, reports, audit log |
| **teacher** | username + password | The **Teacher Portal**, scoped to assigned batches only (`teacher_batches`) |
| **student** | roll number + password (DOB by default, must change on first login) | The **Student Portal** — own attendance, calendar, timetable, requests |
| **terminal** | a batch's numeric PIN | The live attendance screen for that batch (or several, with every PIN) |
| ~~user~~ | — | Legacy role, still accepted by `require_user_or_admin_access` |

There is deliberately **no face login**. `POST /api/face-login` existed — it
took a base64 image and returned a signed-in student session — and was removed:
an HTTP endpoint cannot tell a live face from a photograph of one, nothing
attests the bytes came from a camera, and no page in the app ever called it. A
student's photo lifted off social media was their account.

The attendance terminal matches faces and that is fine, because the difference
is what a match buys you. There a face is an attendance signal — a staff-set
kiosk, a per-batch PIN, in the room, liveness checked, and a wrong mark is
correctable. A credential is not.

---

## 🧩 Features

### Attendance capture
- Face-recognition marking, slot-aware (`/api/detect_attendance_slots`) and plain (`/api/detect_attendance`)
- **Slot enforcement per batch** — a student is judged against *their own* batch's timings
- Late marking with a configurable grace period (`LATE_GRACE_MINUTES`)
- **Working-day enforcement on every path** — no marking on Sundays or batch holidays, by camera, by teacher, or as a side effect of approving a dispute ([working_days.py](working_days.py))
- Teacher bulk-mark and manual session override
- Half-day counting, **opt-in per batch** — morning slots worth 0.5, afternoon 0.5
- Subject attribution recorded **at mark time**, so a later timetable change never rewrites history

### Kiosk terminals
- Per-batch PIN (`courses.terminal_pin_hash`), throttled per batch
- Combined terminals require every covered batch's PIN
- Tablet-fitted layout; manual fallback that files a teacher-approved request

### Students (self-service portal)
- Own attendance %, calendar, weekly timetable, per-subject breakdown
- **Leave requests** (planned absence, capped by `LEAVE_MAX_DAYS`)
- **Grievances** — dispute a day or session you were marked absent for
- **Profile-change requests** — propose edits to your own details
- **Face re-registration requests** — capture a new face, teacher approves
- Biometric consent: grant, review, withdraw

### Teachers (portal, per batch)
Tabs: Mark Attendance · Analytics · Face Requests · Detail Requests · Grievances · Leave · Modules · Holidays · Students
- Bulk-mark a batch for a date and session
- Approve/reject every request type, with a note
- Batch analytics, at-risk list, per-subject analytics
- Add holidays and academic-calendar entries for their batch
- CSV export for their batch

### Admin
- Batches and teachers (`/admin/batches`), teacher-to-batch assignment
- Students: add, bulk-upload from a template, edit, delete, reset password, send welcome email
- Slot timings per batch, live reload without a restart
- Institute-wide monthly report and export
- Consent status report, audit log, email delivery status and test send
- Clear-all-data, holiday management

### Academic calendar
- Modules (`subjects`) with faculty, hours, exam and mid-quiz dates, sequence, minimum attendance
- Non-attendance events (`academic_events`) — induction, revision, exams, picnics, convocation
- Weekly `timetable` grid mapping (batch, weekday, slot) → subject
- Bulk import from CSV via [import_calendar.py](import_calendar.py)

### Analytics & reporting
- Live present count, today's attendance, dashboard stats
- Class trend, heatmap, day-of-week, at-risk, per-student sparklines
- Monthly student / teacher / institute reports ([reports.py](reports.py))
- CSV export: per student, per batch, bulk, institute

### Platform
- PWA: installable, service worker, offline notice. **API responses are never cached** — a stale attendance figure is worse than none
- Password reset by emailed OTP
- Durable login throttling (counters in the database, so a restart doesn't reset them)
- `audit_log` for privileged actions, `email_log` for every send attempt
- Three UI themes (light, dark, pastel)

---

## 🏗️ Tech Stack

| Layer | Technology |
|-------|-----------|
| Web framework | FastAPI + Uvicorn |
| Templating / UI | Jinja2 + vanilla HTML/CSS/JS (no build step) |
| Face recognition | InsightFace `buffalo_l` (512-D embeddings) |
| Liveness / anti-spoof | MiniFASNetV2 via ONNX Runtime |
| Computer vision | OpenCV, MediaPipe |
| Data / ML | NumPy, scikit-learn, SciPy |
| Database | SQLite (`attendance.db`), WAL mode — or PostgreSQL via `DATABASE_URL` |
| Auth | PBKDF2-SHA256 (200k iterations), stdlib only |
| Email | SMTP (Gmail App Password friendly) |
| Timezone | pytz (`Asia/Kolkata` / IST) |
| Tests | pytest (341 tests) |

---

## 📂 Project Structure

```
Student_attendance/
├── main_with_face_recognition.py   # FastAPI app — all 126 routes, guards, app entrypoint
├── db.py                           # SQLite/PostgreSQL abstraction, WAL + busy-timeout tuning
├── auth_utils.py                   # PBKDF2 password hashing, default student password
├── login_throttle.py               # Durable (DB-backed) login lockout
│
├── asian_face_model.py             # InsightFace buffalo_l recognizer (512-D)
├── anti_spoofing.py                # MiniFASNetV2 passive liveness detection
├── camera_manager.py               # Camera capture handling
├── photo_utils.py                  # Student photo directory helpers
│
├── attendance_manager.py           # Per-batch slot timings, slot marking, live count
├── working_days.py                 # Is this a working day for this batch? (single source of truth)
├── timetable.py                    # Subjects, weekly grid, subject-wise attendance, calendar
├── analytics_manager.py            # Trends, heatmap, day-of-week, at-risk, sparklines
├── reports.py                      # Monthly/cumulative/institute report data + email HTML
├── mailer.py                       # SMTP sending with a DB send-log
│
├── tenancy.py                      # Tenant scoping — the one place institute rules live
├── setup_database.py               # Base schema (migrations add the rest)
├── migrate_phase1.py … _phase10.py # Idempotent schema migrations, re-run on every deploy
├── check_db.py                     # Verifies every table, column and index the app needs
├── import_calendar.py              # Load a batch's academic calendar from CSV
│
├── send_reports.py                 # Monthly reports        (cron)
├── send_alerts.py                  # Weekly low-attendance warnings (cron)
├── send_daily_nudge.py             # Same-day "you weren't marked" nudge (cron)
│
├── templates/                      # Jinja2 pages (see Pages below)
├── static/                         # CSS themes, JS, PWA manifest + service worker, images
├── tests/                          # pytest suite
├── deploy/                         # systemd unit, installer, logrotate, DEPLOYMENT.md
├── data/                           # Batch calendar CSVs
├── models/anti_spoof/              # MiniFASNetV2.onnx
├── student_photos/                 # Per-student captured photos (gitignored)
├── attendance.db                   # SQLite database (gitignored)
└── *.sh                            # start/stop/restart/status/healthcheck/backup
```

### Pages

| Path | Role | Page |
|------|------|------|
| `/` `/login` | public | Login (staff, student, face, terminal) |
| `/dashboard` | admin | Live stats, trends, at-risk |
| `/attendance` | staff + terminal | Live attendance detection / kiosk screen |
| `/attendance-management` | admin | Attendance reports and calendar editing |
| `/students` | admin | Manage students |
| `/register` | teacher, admin | Guided face registration |
| `/admin` | admin | System administration |
| `/admin/batches` | admin | Batches & teachers |
| `/teacher` | teacher, admin | Teacher portal (9 tabs) |
| `/student` | student | Student portal (5 tabs) |
| `/change-password` `/forgot-password` | any / public | Password management |
| `/about` `/contact` `/privacy` | public | Static pages |

---

## 🚀 Getting Started

### Prerequisites
- Python **3.10+**
- A working **webcam**
- (Optional) OpenSSL — for HTTPS self-signed certificates
- ~2 GB disk for models and dependencies

### 1. Clone & create a virtual environment

```bash
git clone <your-repo-url>
cd Student_attendance

# Linux / macOS
python3 -m venv venv
source venv/bin/activate

# Windows (PowerShell)
python -m venv venv_win
venv_win\Scripts\Activate.ps1
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

> ⚠️ `insightface` and `onnxruntime` may need build tools (CMake / Visual C++ Build Tools on Windows). On first run, InsightFace downloads the `buffalo_l` model automatically.

### 3. Configure

```bash
cp .env.example .env
```

Everything is optional — the app runs with an empty `.env` — but email, liveness and security settings all live there. See [Configuration](#️-configuration).

### 4. Initialize the database

```bash
python setup_database.py
for i in 1 2 3 4 5 6 7 8 9 10; do python migrate_phase$i.py; done
python check_db.py
```

> Phase 10 creates your first institute and moves everything into it. Set
> `DEFAULT_INSTITUTE_NAME` in `.env` before that first run if you want something
> other than the default — renaming later is just an `UPDATE`.

`setup_database.py` creates only the base tables (`students`, `attendance`, `face_encodings`, `registration_sessions`). Everything else — `users`, `sessions`, `courses`, `grievances`, `subjects`, `timetable`, `leave_requests`, `consent_records`, `academic_events` and the rest — is added by the phase migrations, so **both steps are required** on a fresh install.

Every migration is idempotent, which is why the deploy workflow re-runs all of them on each push. `check_db.py` verifies the result and exits non-zero if anything is missing. If an `attendance.db` already exists it is backed up first.

### 5. Run the app

```bash
python main_with_face_recognition.py
```

- On startup the app generates a self-signed SSL cert (`cert.pem` / `key.pem`) if missing and serves over **HTTPS**.
- Default URL: **https://&lt;host&gt;:8000/**
- On the first HTTPS visit, accept the browser security warning (self-signed cert).

> The server binds to `0.0.0.0` and auto-detects your machine's IP for the displayed URL. Override the port with `PORT` (default `8000`).

#### Linux process helpers
```bash
./start.sh     # start in background (logs → app.log)
./status.sh    # check status
./stop.sh      # stop
./restart.sh   # restart (hands over to systemd when the unit is installed)
```

For production — systemd, cron jobs, log rotation, backups — see **[deploy/DEPLOYMENT.md](deploy/DEPLOYMENT.md)**.

### 6. Run the tests

```bash
pytest
```

341 tests, all against throwaway databases in a temp directory — safe to run on the production machine.

---

## 🖥️ Usage

1. **Log in** as admin.
2. **Create a batch** in *Batches & Teachers*, set its date range, assign teachers, and set a terminal PIN if you want a kiosk.
3. **Configure slot timings** for that batch in *Admin*.
4. **Import the academic calendar** (optional) — see [import_calendar.py](import_calendar.py) and the runbook.
5. **Add students** — individually or by bulk upload from the downloadable template. Welcome emails carry their roll number and default password.
6. **Register faces** in *Register* — capture a few photos per student.
7. **Mark attendance** — either staff open `/attendance`, or a tablet signs in at the login screen with the batch PIN and runs unattended.
8. **Monitor** — the dashboard and teacher portal show live counts, trends and at-risk students.
9. **Handle requests** — leave, grievances, detail changes and face re-registrations all queue in the teacher portal.
10. **Export / report** — CSV from any portal; email reports go out on cron.

---

## 🔌 API

126 routes. The handlers are grouped by area in [main_with_face_recognition.py](main_with_face_recognition.py), and every one carries an explicit auth dependency — a test ([tests/test_endpoint_auth.py](tests/test_endpoint_auth.py)) fails the build if a new endpoint appears without one, or if any route is declared outside the app file where that check cannot see it.

A representative selection:

| Area | Examples |
|------|----------|
| **Auth** | `POST /api/admin-login` · `/api/user-login` · `/api/terminal-login` · `GET /api/terminal/batches` · `POST /api/logout` · `/api/change-password` · `/api/forgot-password` · `/api/reset-password-otp` |
| **Capture** | `POST /api/detect_attendance` · `/api/detect_attendance_slots` · `/api/attendance/manual/session` · `/api/attendance/bulk-mark` |
| **Registration** | `POST /api/start_registration` · `/api/upload_face_photo` · `/api/complete_registration` |
| **Students (admin)** | `GET /api/students/list` · `POST /api/students/add` · `/api/students/bulk-upload` · `GET /api/students/bulk-upload/template` · `PUT/DELETE /api/students/{id}` · `POST /api/students/{id}/reset-password` · `/api/students/{id}/send-welcome` |
| **Batches & teachers** | `GET/POST /api/courses` · `PUT/DELETE /api/courses/{id}` · `GET/POST /api/teachers` · `PUT/DELETE /api/teachers/{id}` |
| **Student portal** | `GET /api/student/me` · `/api/student/profile` · `/api/student/calendar` · `/api/student/timetable` · `/api/student/schedule` · `/api/student/subjects` · `/api/student/leaves` · `/api/student/grievances` · `/api/student/consent` |
| **Student requests** | `POST /api/student/leave` · `/api/student/grievance` · `/api/student/profile-request` · `/api/student/register-face/start` · `/api/student/register-face/complete` |
| **Teacher review** | `GET /api/teacher/leaves` · `/api/teacher/grievances` · `/api/teacher/face-requests` · `/api/teacher/profile-requests` and a matching `POST …/action` for each |
| **Teacher batch** | `GET /api/teacher/batch/{id}/students` · `…/analytics` · `…/subject-analytics` · `…/export` |
| **Subjects & timetable** | `GET/POST /api/courses/{id}/subjects` · `PUT/DELETE /api/subjects/{id}` · `GET/PUT /api/courses/{id}/timetable` · `GET /api/courses/{id}/schedule` · `POST /api/courses/{id}/events` |
| **Live & analytics** | `GET /api/attendance/live-count` · `/api/attendance/today` · `/api/dashboard/stats` · `/api/analytics/heatmap` · `/api/analytics/at-risk` · `/api/analytics/day-of-week` · `/api/analytics/student/{id}/sparkline` |
| **Export** | `GET /api/attendance/bulk-export` · `/api/attendance/export/{id}` · `/api/admin/institute-report` · `/api/admin/institute-export` |
| **Slots & holidays** | `GET/PUT /api/admin/session-config` · `GET /api/admin/current-slots` · `POST /api/admin/reload-slot-config` · `GET/POST/DELETE /api/holidays` |
| **Admin ops** | `GET /api/admin/email-status` · `/api/admin/audit-log` · `/api/admin/consent-status` · `POST /api/admin/email-test` · `/api/admin/clear_all_data` |
| **Health** | `GET /api/system/status` (public — used by `healthcheck.sh`; also reports whether liveness is enforcing) |

---

## ⚙️ Configuration

All runtime configuration is environment variables, read from `.env` (gitignored). Copy [.env.example](.env.example) and fill in what you need.

| Key | Default | What it does |
|-----|---------|--------------|
| `DATABASE_URL` | *(blank)* | Blank = SQLite (`attendance.db`). A `postgresql://…` URL switches backend. **Blank is not the same as a commented placeholder** — delete the value. |
| `PORT` | `8000` | HTTPS listen port |
| `APP_BASE_URL` | — | Public URL used in email links. Wrong value = dead links. |
| `SMTP_HOST` / `SMTP_PORT` | `smtp.gmail.com` / `587` | Mail server |
| `SMTP_USER` / `SMTP_PASSWORD` | *(blank)* | Gmail needs a 16-char **App Password**. Blank disables email; the app still runs. |
| `SMTP_FROM_NAME` | `CDAC Attendance` | Sender display name |
| `ALERT_EMAIL` | `SMTP_USER` | Where `healthcheck.sh` sends downtime alerts |
| `ANTISPOOF_DISABLED` | *(unset)* | `1` turns liveness off everywhere. See below. |
| `ANTISPOOF_THRESHOLD` | `0.45` | Minimum "real" score to pass liveness. Lower = more lenient. |
| `ANTISPOOF_ON_REGISTRATION` | *(off)* | `1` enforces liveness during enrolment too (off by default — enrolment is staff-supervised) |
| `FACE_MATCH_THRESHOLD` | `0.60` | Cosine similarity needed to call a face a known student. Lower = more students recognised *and* more mix-ups. Shown on Settings so you can confirm it took. |
| `LATE_GRACE_MINUTES` | `10` | Minutes past a slot's start before a mark counts as late |
| `COOKIE_SECURE` | `1` | Session cookies are Secure. Only set `0` for deliberate plain HTTP. |
| `LOGIN_MAX_ATTEMPTS` | `8` | Failed logins before lockout — staff, students and terminal PINs alike |
| `LOGIN_LOCKOUT_MINUTES` | `10` | Lockout window |
| `LEAVE_MAX_DAYS` | `30` | Longest planned absence in one request |
| `CONSENT_POLICY_VERSION` | `1.0` | Bump when the privacy notice changes materially — students are asked again |
| `BIOMETRIC_RETENTION_DAYS` | `90` | Days after a course ends before face data is deleted (shown in the notice) |
| `SQLITE_BUSY_TIMEOUT_MS` | `15000` | Lock wait before a statement fails. The app runs WAL, so this covers writer-vs-writer contention during a slot-start burst. |

Other settings:

| What | Where |
|------|-------|
| Session/slot timings | Admin UI → `session_configs` table, per batch |
| Half-day counting | Per batch — `courses.half_day_enabled` |
| Subject minimum attendance | Per subject — `subjects.min_attendance` |
| Face model | [asian_face_model.py](asian_face_model.py) (`buffalo_l`, 512-D) |
| Recognition threshold | `RECOGNITION_THRESHOLD = 0.60`, hardcoded in each of the three detection handlers in [main_with_face_recognition.py](main_with_face_recognition.py) — change all three together |
| Timezone | `Asia/Kolkata` (IST) across all modules |

---

## 🛡️ Anti-spoofing status

**Liveness is currently not enforcing in production** (`ANTISPOOF_DISABLED=1`). While it is off, a printed photo or a face on a phone screen will mark attendance.

`GET /api/system/status` reports this explicitly (`liveness_active`, plus a `liveness_warning`), so it is visible rather than silently off.

The crop bug that caused over-rejection is fixed; the threshold has simply never been validated against the real camera. That has to happen **on the VM** — a sandbox falls back to a Haar cascade because `buffalo_l` isn't installed there, so its bounding boxes and therefore its scores are not the ones production will see.

```bash
# ~10 real faces: different people, different lighting
./venv/bin/python test_antispoof.py --camera --label real
# ~10 spoof attempts: a face on a phone screen, a printout
./venv/bin/python test_antispoof.py --camera --label spoof
# recommends a threshold from what you collected
./venv/bin/python test_antispoof.py --suggest
```

Then set `ANTISPOOF_THRESHOLD` in `.env`, **remove** `ANTISPOOF_DISABLED`, restart, and have a few people mark attendance before walking away from it. Full procedure in [deploy/DEPLOYMENT.md](deploy/DEPLOYMENT.md).

---

## 💾 Backup & Restore

The SQLite database and student photos are the source of truth. See **[BACKUP_GUIDE.md](BACKUP_GUIDE.md)** and `backup.sh` / `backup_cron.sh`.

---

## 🔒 Privacy & Security Notes

- All face embeddings and photos are stored **locally** — nothing is sent to any cloud service.
- **Biometric consent** is modelled for the India DPDP Act 2023: versioned policy, a consent record per student, a withdrawal path and a stated retention period. The notice is at `/privacy` and is readable before login.
- Passwords are PBKDF2-SHA256 with 200,000 iterations and a per-password salt.
- Serves over HTTPS (self-signed by default; use a real certificate in production).
- Login throttling is **durable** — counters live in the database, so a restart no longer hands an attacker a fresh budget of attempts.
- Teachers are hard-scoped to their assigned batches (`assert_course_allowed`); a combined kiosk needs every batch's PIN.
- Privileged actions are written to `audit_log`; every email attempt to `email_log`.
- Treat `attendance.db`, `student_photos/`, `.env` and `key.pem` as sensitive — all are gitignored.
- ⚠️ Liveness is off right now; see [Anti-spoofing status](#️-anti-spoofing-status).

---

## 📜 License

Add your license here (e.g. MIT). No license file is currently included.

---

*Built with FastAPI + InsightFace — contactless attendance, done right.*
