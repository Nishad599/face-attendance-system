# CDAC Attendance — Face Recognition Attendance System

A production-ready, self-hosted **facial-recognition attendance platform** built for classrooms and training batches. Students are marked present simply by looking at a camera — no cards, no roll-calls, no manual registers. Built with **FastAPI**, **InsightFace (buffalo_l)** and a lightweight **SQLite** backend, it runs on a single machine or a small VM with zero external cloud dependencies.

> Originally built for the CDAC Mumbai DBDA batch, but fully configurable for any institute, course, or batch.

---

## ✨ Why this project (USP)

| # | Unique Selling Point | What it means |
|---|----------------------|---------------|
| 🎯 | **Contactless, 1-second attendance** | A student walks in, faces the webcam, and is marked present. No hardware beyond a camera. |
| 🧠 | **High-accuracy 512-D embeddings** | Uses InsightFace `buffalo_l` (w600k) producing 512-dimensional face embeddings — robust across lighting, angles, and diverse (incl. Asian) face types. |
| 🛡️ | **Passive anti-spoofing / liveness** | MiniFASNetV2 (ONNX) liveness detection blocks photo, phone-screen, and printout spoofing — attendance can't be faked with a picture. |
| ⏰ | **Configurable time slots** | Morning / Afternoon (and more) sessions with admin-editable start/end times, stored in the database — not hardcoded. |
| 📊 | **Live attendance analytics** | Real-time present count, class trends, at-risk students, day-of-week heatmaps, and per-student sparklines. |
| 📥 | **Bulk operations** | Bulk-register students from a spreadsheet template and bulk-export attendance to CSV. |
| 🔐 | **Multi-role login** | Admin login, user login, **and face-based login** — sign in with your face. |
| 🌐 | **Self-hosted & offline-first** | Runs fully on-premise over HTTPS with self-signed certs. No student biometric data ever leaves your server. |
| 🕒 | **IST-correct timekeeping** | All slots, dates, and reports are timezone-aware (`Asia/Kolkata`). |

---

## 🧩 Features

### Attendance
- Face-detection based attendance marking (single and slot-aware endpoints)
- Time-slot enforcement (attendance only counts inside the active session window)
- Manual / session override marking for edge cases
- Bulk attendance marking utility
- Holiday calendar support (attendance skipped on holidays / Sundays)

### Students
- Guided face registration (multi-photo capture per student)
- Bulk upload students via downloadable template
- Edit / delete students, per-student photo storage
- Active/inactive student status

### Dashboard & Analytics
- Live present count and today's attendance
- Class-wide attendance trend (configurable window)
- At-risk student detection (low-attendance flagging)
- Attendance heatmap and day-of-week analysis
- Per-student attendance history and sparklines
- CSV export (per-student and bulk)

### Admin
- Configurable session/slot timings per course
- Reload slot configuration without restart
- Clear-all-data and holiday management endpoints
- Multiple UI themes (light, dark, pastel)

---

## 🏗️ Tech Stack

| Layer | Technology |
|-------|-----------|
| Web framework | FastAPI + Uvicorn |
| Templating / UI | Jinja2 + vanilla HTML/CSS/JS |
| Face recognition | InsightFace `buffalo_l` (512-D embeddings) |
| Liveness / anti-spoof | MiniFASNetV2 via ONNX Runtime |
| Computer vision | OpenCV, MediaPipe, dlib, face_recognition |
| Data / ML | NumPy, scikit-learn, SciPy |
| Database | SQLite (`attendance.db`) |
| Timezone | pytz (`Asia/Kolkata` / IST) |

---

## 📂 Project Structure

```
Student_attendance/
├── main_with_face_recognition.py   # FastAPI app — all routes & app entrypoint
├── asian_face_model.py             # InsightFace buffalo_l recognizer (512-D)
├── anti_spoofing.py                # MiniFASNetV2 passive liveness detection
├── attendance_manager.py           # Configurable time-slot & live-count logic
├── analytics_manager.py            # Class analytics, trends, at-risk, heatmaps
├── phase1_integration.py           # Multi-session / working-days calendar
├── camera_manager.py               # Camera capture handling
├── photo_utils.py                  # Student photo directory helpers
├── bulk_mark_attendance.py         # Bulk attendance utility
├── setup_database.py               # Creates the SQLite schema
├── requirements.txt                # Python dependencies
├── templates/                      # Jinja2 HTML (dashboard, login, students…)
├── static/                         # CSS themes, images (CDAC logo)
├── student_photos/                 # Per-student captured photos
├── models/                         # ML model files
├── attendance.db                   # SQLite database
├── start.sh / stop.sh / restart.sh # Process control (Linux)
└── BACKUP_GUIDE.md                 # Backup & restore documentation
```

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

> ⚠️ `dlib`, `insightface`, and `onnxruntime` may need build tools (CMake / Visual C++ Build Tools on Windows). On first run, InsightFace downloads the `buffalo_l` model automatically.

### 3. Initialize the database

```bash
python setup_database.py
```

This creates `attendance.db` with the `students`, `attendance`, `face_encodings`, `slot_attendance`, `session_configs`, and related tables. If a database already exists it is safely backed up first.

### 4. Run the app

```bash
python main_with_face_recognition.py
```

- On startup the app generates a self-signed SSL cert (`cert.pem` / `key.pem`) if missing and serves over **HTTPS**.
- Default URL: **https://<host>:8000/**
- On the first HTTPS visit, accept the browser security warning (self-signed cert).

> **Note:** The server binds to `0.0.0.0` (all interfaces) and auto-detects your machine's IP for the displayed URL. Override the port with the `PORT` environment variable (default `8000`).

#### Linux process helpers
```bash
./start.sh     # start in background (logs → app.log)
./status.sh    # check status
./stop.sh      # stop
./restart.sh   # restart
```

---

## 🖥️ Usage

1. **Log in** — open the app and sign in as admin, as a user, or **with your face** (`/api/face-login`).
2. **Register students** — go to *Register*, capture a few face photos per student, or **bulk-upload** via the downloadable template.
3. **Mark attendance** — open the *Attendance* page; students face the camera and are marked present within the active time slot. Anti-spoofing rejects photos/screens.
4. **Monitor** — the *Dashboard* shows the live present count, trends, and at-risk students.
5. **Configure slots** — in *Admin*, set Morning/Afternoon session start & end times.
6. **Export** — download per-student or full attendance as CSV.

---

## 🔌 Key API Endpoints (selection)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/admin-login`, `/api/user-login`, `/api/face-login` | Authentication |
| `POST` | `/api/detect_attendance` / `/api/detect_attendance_slots` | Face-based attendance |
| `POST` | `/api/start_registration`, `/api/upload_face_photo`, `/api/complete_registration` | Student registration |
| `GET`  | `/api/students/list`, `POST /api/students/bulk-upload` | Student management |
| `GET`  | `/api/dashboard/stats`, `/api/attendance/today`, `/api/attendance/live-count` | Live dashboard |
| `GET`  | `/api/analytics/heatmap`, `/api/analytics/at-risk`, `/api/analytics/day-of-week` | Analytics |
| `GET`  | `/api/attendance/bulk-export`, `/api/attendance/export/{student_id}` | CSV export |
| `GET/PUT` | `/api/admin/session-config`, `/api/admin/current-slots` | Slot configuration |
| `GET/POST/DELETE` | `/api/holidays` | Holiday calendar |

---

## 💾 Backup & Restore

The SQLite database and student photos are the source of truth. See **[BACKUP_GUIDE.md](BACKUP_GUIDE.md)** and `backup.sh` for automated backup/restore procedures.

---

## 🔒 Privacy & Security Notes

- All face embeddings and photos are stored **locally** — nothing is sent to any cloud service.
- Serves over HTTPS (self-signed by default; use a real certificate in production).
- Anti-spoofing is enabled to prevent fraudulent (photo/screen) check-ins.
- Treat `attendance.db`, `student_photos/`, and `key.pem` as sensitive; keep them out of version control (see `.gitignore`).

---

## 🛠️ Configuration Cheatsheet

| What | Where |
|------|-------|
| Server host / port / SSL | `main_with_face_recognition.py` (`__main__` block) |
| Session/slot timings | Admin UI → stored in `session_configs` table |
| Timezone | `Asia/Kolkata` (IST) across modules |
| Anti-spoof threshold | `anti_spoofing.py` (`AntiSpoofChecker(threshold=…)`) |
| Face model | `asian_face_model.py` (`buffalo_l`, 512-D) |

---

## 📜 License

Add your license here (e.g. MIT). No license file is currently included.

---

*Built with FastAPI + InsightFace — contactless attendance, done right.*
