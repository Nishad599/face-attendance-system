#!/usr/bin/env bash
#
# migrate_restore.sh — restore a migration bundle onto a NEW server.
# Run this on the NEW VM, from inside the freshly-cloned project directory.
#
#   git clone <repo> student && cd student
#   ./migrate_restore.sh ~/migration-oldhost-20260828-1200.tar.gz
#
# It restores the database, student photos and .env, regenerates the TLS cert
# for THIS host's IP, builds the venv, runs the migrations and verifies.
#
# Safe to inspect first:  ./migrate_restore.sh <archive> --dry-run

set -uo pipefail

ARCHIVE="${1:-}"
DRY=0
[ "${2:-}" = "--dry-run" ] && DRY=1

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

[ -n "${ARCHIVE}" ] || fail "usage: ./migrate_restore.sh <migration-archive.tar.gz> [--dry-run]"
[ -f "${ARCHIVE}" ] || fail "archive not found: ${ARCHIVE}"
# Normally run from a fresh git clone. If the app isn't here but the bundle
# carries source.tar.gz (--offline export), unpack that instead — no git needed.
if [ ! -f "main_with_face_recognition.py" ]; then
    # Pure-bash substring match: NO pipe. `grep -q` exits early, which
    # SIGPIPEs whatever feeds it; with `set -o pipefail` the pipeline then
    # reports failure (exit 141) even though the match succeeded.
    ARCHIVE_LIST="$(tar -tzf "${ARCHIVE}" 2>/dev/null)"
    if [ "${ARCHIVE_LIST#*source.tar.gz}" != "${ARCHIVE_LIST}" ]; then
        echo "[bootstrap] no source here — extracting the bundled code…"
        tar -xzf "${ARCHIVE}" -O ./source.tar.gz | tar -xz ||             fail "could not unpack the bundled source"
        [ -f "main_with_face_recognition.py" ] || fail "bundled source looks incomplete"
        chmod +x ./*.sh 2>/dev/null
        echo "[bootstrap] source unpacked"
    else
        fail "run this from the project root (after git clone), or use an --offline bundle"
    fi
fi

log "=== migration restore (dry-run=${DRY}) ==="

# ---------------------------------------------------------------- checksum
if [ -f "${ARCHIVE}.sha256" ]; then
    # Compare the hash VALUES rather than using `sha256sum -c`, which resolves
    # the filename inside the .sha256 relative to the current directory and so
    # fails whenever the archive lives somewhere else (e.g. ~/migration-*.tar.gz).
    EXPECTED="$(awk '{print $1}' "${ARCHIVE}.sha256" | head -1)"
    ACTUAL=""
    if command -v sha256sum >/dev/null 2>&1; then
        ACTUAL="$(sha256sum "${ARCHIVE}" | awk '{print $1}')"
    elif command -v shasum >/dev/null 2>&1; then
        ACTUAL="$(shasum -a 256 "${ARCHIVE}" | awk '{print $1}')"
    fi
    if [ -z "${ACTUAL}" ]; then
        log "no sha256 tool available — skipping integrity check"
    elif [ "${EXPECTED}" = "${ACTUAL}" ]; then
        log "checksum OK"
    else
        echo "  expected: ${EXPECTED}" >&2
        echo "  actual  : ${ACTUAL}" >&2
        fail "CHECKSUM MISMATCH — the archive is corrupt or was truncated in transfer"
    fi
else
    log "no .sha256 alongside the archive — skipping integrity check"
fi

tar -tzf "${ARCHIVE}" >/dev/null 2>&1 || fail "archive is unreadable"

STAGE=".migration-restore-$$"
rm -rf "${STAGE}"; mkdir -p "${STAGE}"
tar -xzf "${ARCHIVE}" -C "${STAGE}" || fail "extraction failed"

echo ""
if [ -f "${STAGE}/MANIFEST.txt" ]; then
    echo "----- BUNDLE CONTENTS -----"
    cat "${STAGE}/MANIFEST.txt"
    echo "---------------------------"
    echo ""
fi

if [ "${DRY}" -eq 1 ]; then
    log "dry run — nothing was written. Contents listed above."
    rm -rf "${STAGE}"
    exit 0
fi

# ---------------------------------------------------------------- guard
if [ -f "attendance.db" ]; then
    echo "WARNING: attendance.db already exists here and will be REPLACED."
    cp attendance.db "attendance.db.pre-restore-$(date +%Y%m%d-%H%M%S)" \
        && log "existing database backed up first"
    printf "Continue? [y/N] "
    read -r ans
    case "${ans}" in [yY]*) ;; *) rm -rf "${STAGE}"; fail "aborted by user";; esac
fi

# ---------------------------------------------------------------- restore
[ -f "${STAGE}/database/attendance.db" ] \
    && { cp "${STAGE}/database/attendance.db" attendance.db; log "restored attendance.db"; } \
    || log "WARNING: no database in the bundle"

if [ -d "${STAGE}/student_photos" ]; then
    mkdir -p student_photos
    cp -r "${STAGE}/student_photos/." student_photos/ 2>/dev/null
    log "restored $(find student_photos -type f | wc -l) student photo(s)"
fi

if [ -f "${STAGE}/env.backup" ]; then
    cp "${STAGE}/env.backup" .env
    chmod 600 .env
    log "restored .env (0600)"
fi

if [ -d "${STAGE}/insightface_models" ]; then
    mkdir -p "$HOME/.insightface"
    cp -r "${STAGE}/insightface_models/." "$HOME/.insightface/" 2>/dev/null
    log "restored InsightFace models to ~/.insightface"
fi

[ -f "${STAGE}/crontab.txt" ] && cp "${STAGE}/crontab.txt" crontab.from-old-host.txt \
    && log "old crontab saved to crontab.from-old-host.txt (not installed)"

rm -rf "${STAGE}"

# ---------------------------------------------------------------- new identity
NEWIP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "${NEWIP}" ] || NEWIP="localhost"
log "this host's IP: ${NEWIP}"

# The old certs are bound to the old IP — always regenerate.
rm -f cert.pem key.pem
if command -v openssl >/dev/null 2>&1; then
    openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes \
        -subj "/C=IN/ST=Maharashtra/L=Mumbai/O=CDAC/CN=${NEWIP}" >/dev/null 2>&1 \
        && { chmod 600 key.pem; log "generated a fresh TLS cert for ${NEWIP}"; } \
        || log "WARNING: cert generation failed — the app will regenerate on first start"
else
    log "openssl not installed — the app will generate certs on first start"
fi

# Point emails at the new host
if [ -f ".env" ]; then
    if grep -q '^APP_BASE_URL=' .env; then
        sed -i "s#^APP_BASE_URL=.*#APP_BASE_URL=https://${NEWIP}:8000#" .env
    else
        printf '\nAPP_BASE_URL=https://%s:8000\n' "${NEWIP}" >> .env
    fi
    log "APP_BASE_URL set to https://${NEWIP}:8000"
fi

# ---------------------------------------------------------------- venv
if [ ! -d "venv" ]; then
    log "creating virtualenv…"
    python3 -m venv venv || fail "could not create venv (need python3-venv installed)"
fi
log "installing dependencies (this takes a few minutes)…"
if [ -d "${WHEELS_DIR:-}" ] && [ -n "$(ls -A "${WHEELS_DIR}" 2>/dev/null)" ]; then
    log "  using the ${WHEELS_DIR} bundled with the archive (no internet needed)"
    ./venv/bin/pip install --quiet --no-index --find-links "${WHEELS_DIR}"         -r requirements.txt || fail "offline pip install failed"
else
    ./venv/bin/pip install --quiet --upgrade pip >/dev/null 2>&1
    ./venv/bin/pip install --quiet -r requirements.txt         || fail "pip install failed (no internet? re-export with --offline)"
fi
log "dependencies installed"

# ---------------------------------------------------------------- migrate + verify
log "applying migrations…"
for m in migrate_phase1.py migrate_phase2.py migrate_phase3.py; do
    [ -f "$m" ] && ./venv/bin/python "$m" >/dev/null 2>&1 && log "  ran $m"
done

echo ""
log "verifying schema…"
./venv/bin/python check_db.py || log "WARNING: check_db reported problems (see above)"

chmod +x ./*.sh 2>/dev/null
[ -n "${WHEELS_DIR:-}" ] && rm -rf "${WHEELS_DIR}" && log "removed temporary wheel cache"

echo ""
log "=== restore complete ==="
cat <<EOF

Next steps on this host:

  1. Review .env  (SMTP creds carried over; APP_BASE_URL now points here)
       nano .env

  2. Start the app
       ./restart.sh && tail -n 20 app.log

  3. Open  https://${NEWIP}:8000   (accept the self-signed cert warning)

  4. Reinstate scheduled jobs if you want them (see crontab.from-old-host.txt)
       crontab -e

  5. If this host uses GitHub Actions deploys, register a NEW self-hosted
     runner here — the old runner is bound to the old machine.

  6. Once verified, delete the migration archive from both machines.
EOF
