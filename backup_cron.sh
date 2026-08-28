#!/usr/bin/env bash
#
# backup_cron.sh — scheduled backup of the attendance data.
#
# Backs up the database and student photos into one compressed, timestamped
# archive, verifies it, and deletes archives older than the retention window.
#
#   ./backup_cron.sh                 # normal run
#   ./backup_cron.sh --dry-run       # show what it would do
#   RETAIN_DAYS=30 ./backup_cron.sh  # override retention (default 14)
#
# Cron — every night at 01:30:
#   30 1 * * * cd /home/user1/face-attendance-system && ./backup_cron.sh >> logs/backup.log 2>&1
#
# Why not plain `cp attendance.db`: copying a live SQLite file can capture a
# half-written transaction. `sqlite3 .backup` takes a consistent snapshot while
# the app keeps running. For PostgreSQL it uses pg_dump.

set -uo pipefail

RETAIN_DAYS="${RETAIN_DAYS:-14}"
BACKUP_DIR="${BACKUP_DIR:-backups}"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

STAMP="$(date +%Y%m%d-%H%M%S)"
STAGE="${BACKUP_DIR}/.staging-${STAMP}"
ARCHIVE="${BACKUP_DIR}/attendance-backup-${STAMP}.tar.gz"

log()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
fail() { log "ERROR: $*"; rm -rf "${STAGE}" 2>/dev/null; exit 1; }

# Must run from the project root
[ -f "main_with_face_recognition.py" ] || fail "run this from the project root"

log "=== backup start (retain ${RETAIN_DAYS} days, dry-run=${DRY_RUN}) ==="

if [ "${DRY_RUN}" -eq 1 ]; then
    log "would create : ${ARCHIVE}"
    log "would include: database + student_photos/ + .env + models/anti_spoof"
    log "would delete : archives older than ${RETAIN_DAYS} days:"
    find "${BACKUP_DIR}" -maxdepth 1 -name 'attendance-backup-*.tar.gz' \
         -mtime +"${RETAIN_DAYS}" -print 2>/dev/null | sed 's/^/               /' || true
    exit 0
fi

mkdir -p "${STAGE}/database" || fail "cannot create staging dir"

# ---------------------------------------------------------------- database
# Read DATABASE_URL from .env if present (without exporting the whole file)
DB_URL=""
if [ -f ".env" ]; then
    DB_URL="$(grep -E '^[[:space:]]*DATABASE_URL=' .env | tail -1 | cut -d= -f2- | tr -d '"'"'"'' | xargs || true)"
fi

if [ -n "${DB_URL}" ] && echo "${DB_URL}" | grep -qi '^postgres'; then
    log "database: PostgreSQL (pg_dump)"
    command -v pg_dump >/dev/null 2>&1 || fail "pg_dump not found but DATABASE_URL is postgres"
    pg_dump "${DB_URL}" > "${STAGE}/database/attendance.sql" \
        || fail "pg_dump failed"
    log "  dumped $(du -h "${STAGE}/database/attendance.sql" | cut -f1)"
elif [ -f "attendance.db" ]; then
    log "database: SQLite (consistent .backup snapshot)"
    if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 attendance.db ".backup '${STAGE}/database/attendance.db'" \
            || fail "sqlite3 .backup failed"
        # Verify the snapshot actually opens and has the core table
        sqlite3 "${STAGE}/database/attendance.db" \
            "SELECT COUNT(*) FROM students;" >/dev/null 2>&1 \
            || fail "backup verification failed — snapshot is unreadable"
        log "  snapshot verified ($(sqlite3 "${STAGE}/database/attendance.db" 'SELECT COUNT(*) FROM students;') students)"
    else
        # No sqlite3 CLI — use Python's backup API, which is also a consistent
        # snapshot of a live database (a plain file copy is not).
        log "  sqlite3 CLI missing; using Python's sqlite3 backup API"
        # Pick an interpreter that actually RUNS (on Windows `python3` may be a
        # Microsoft Store stub that exists but fails), preferring the venv.
        PY=""
        for cand in "./venv/bin/python" "./venv_win/Scripts/python.exe" python3 python; do
            if "$cand" -c "import sqlite3" >/dev/null 2>&1; then PY="$cand"; break; fi
        done
        [ -n "${PY}" ] || fail "no working Python interpreter found for the sqlite backup"
        "${PY}" - "$STAGE/database/attendance.db" <<'PYEOF' || fail "python sqlite backup failed"
import sqlite3, sys
dest = sys.argv[1]
src = sqlite3.connect("attendance.db")
out = sqlite3.connect(dest)
with out:
    src.backup(out)
n = out.execute("SELECT COUNT(*) FROM students").fetchone()[0]
out.close(); src.close()
print(f"  snapshot verified ({n} students)")
PYEOF
    fi
else
    log "WARNING: no database found to back up"
fi

# ---------------------------------------------------------------- photos
if [ -d "student_photos" ]; then
    cp -r student_photos "${STAGE}/" 2>/dev/null || log "WARNING: some photos could not be copied"
    log "photos: $(find "${STAGE}/student_photos" -type f 2>/dev/null | wc -l) file(s)"
else
    log "photos: none"
fi

# ---------------------------------------------------------------- config
# .env holds SMTP + DB credentials — keep it, but the archive must stay private.
[ -f ".env" ]      && cp .env      "${STAGE}/env.backup"
[ -f "cert.pem" ]  && cp cert.pem  "${STAGE}/" 2>/dev/null
[ -f "key.pem" ]   && cp key.pem   "${STAGE}/" 2>/dev/null

# ---------------------------------------------------------------- archive
tar -czf "${ARCHIVE}" -C "${STAGE}" . || fail "archive creation failed"
tar -tzf "${ARCHIVE}" >/dev/null 2>&1 || fail "archive is corrupt"
chmod 600 "${ARCHIVE}"                      # contains credentials
rm -rf "${STAGE}"

log "created ${ARCHIVE} ($(du -h "${ARCHIVE}" | cut -f1))"

# ---------------------------------------------------------------- rotate
OLD=$(find "${BACKUP_DIR}" -maxdepth 1 -name 'attendance-backup-*.tar.gz' \
      -mtime +"${RETAIN_DAYS}" 2>/dev/null | wc -l)
if [ "${OLD}" -gt 0 ]; then
    find "${BACKUP_DIR}" -maxdepth 1 -name 'attendance-backup-*.tar.gz' \
         -mtime +"${RETAIN_DAYS}" -delete 2>/dev/null
    log "rotated out ${OLD} archive(s) older than ${RETAIN_DAYS} days"
fi

TOTAL=$(find "${BACKUP_DIR}" -maxdepth 1 -name 'attendance-backup-*.tar.gz' 2>/dev/null | wc -l)
log "=== backup complete — ${TOTAL} archive(s) retained ==="

# Warn if the disk is getting full (backups are the first thing to suffer)
USE=$(df -P . | awk 'NR==2 {print $5}' | tr -d '%')
[ "${USE}" -ge 90 ] && log "WARNING: disk ${USE}% full — free space or reduce RETAIN_DAYS"

exit 0
