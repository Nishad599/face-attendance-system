#!/usr/bin/env bash
#
# migrate_export.sh — package everything needed to move this deployment to a
# NEW server. Run this on the OLD VM.
#
#   ./migrate_export.sh                 # db + photos + config
#   ./migrate_export.sh --with-models    # also bundle the InsightFace models
#                                        # (~300MB; new server has no internet)
#   ./migrate_export.sh --offline        # FULLY self-contained: also bundles the
#                                        # source code and all pip wheels, so the
#                                        # new server needs NO internet at all
#
# Produces:  migration-<host>-<timestamp>.tar.gz  (+ .sha256)
#
# The code itself is NOT included — it comes from git on the new server. This
# bundle carries only what git deliberately ignores: the database, student
# photos, .env and the TLS certs.
#
# The archive contains credentials and biometric data. It is written 0600.
# Move it over a trusted channel and delete it from both machines afterwards.

set -uo pipefail

WITH_MODELS=0
OFFLINE=0
for arg in "$@"; do
    case "$arg" in
        --with-models) WITH_MODELS=1 ;;
        --offline)     WITH_MODELS=1; OFFLINE=1 ;;   # offline implies models
        *) echo "unknown option: $arg"; exit 1 ;;
    esac
done

STAMP="$(date +%Y%m%d-%H%M%S)"
HOSTTAG="$(hostname -s 2>/dev/null || echo host)"
STAGE=".migration-stage-${STAMP}"
ARCHIVE="migration-${HOSTTAG}-${STAMP}.tar.gz"

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
fail() { echo "ERROR: $*" >&2; rm -rf "${STAGE}"; exit 1; }

[ -f "main_with_face_recognition.py" ] || fail "run this from the project root"

log "=== migration export (with-models=${WITH_MODELS}) ==="
mkdir -p "${STAGE}/database" || fail "cannot create staging dir"

# ---------------------------------------------------------------- database
if [ -f "attendance.db" ]; then
    PY=""
    for cand in "./venv/bin/python" "./venv_win/Scripts/python.exe" python3 python; do
        "$cand" -c "import sqlite3" >/dev/null 2>&1 && { PY="$cand"; break; }
    done
    if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 attendance.db ".backup '${STAGE}/database/attendance.db'" || fail "sqlite backup failed"
    elif [ -n "${PY}" ]; then
        "${PY}" - "${STAGE}/database/attendance.db" <<'PYEOF' || fail "python sqlite backup failed"
import sqlite3, sys
src = sqlite3.connect("attendance.db"); out = sqlite3.connect(sys.argv[1])
with out: src.backup(out)
out.close(); src.close()
PYEOF
    else
        fail "no sqlite3 CLI and no working Python — cannot snapshot the database"
    fi
    # verify + record what we captured
    if [ -n "${PY}" ]; then
        "${PY}" - "${STAGE}/database/attendance.db" "${STAGE}/MANIFEST.txt" <<'PYEOF'
import sqlite3, sys
db, manifest = sys.argv[1], sys.argv[2]
c = sqlite3.connect(db)
assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "integrity check FAILED"
rows = []
for t in ("students", "courses", "users", "attendance", "teacher_batches",
          "grievances", "holidays", "session_configs"):
    try:
        rows.append(f"  {t:24} {c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
    except sqlite3.Error:
        rows.append(f"  {t:24} (missing)")
open(manifest, "w").write("DATABASE CONTENTS\n" + "\n".join(rows) + "\n")
print("\n".join(rows))
c.close()
PYEOF
        [ $? -eq 0 ] || fail "database verification failed"
    fi
    log "database captured and verified"
else
    fail "attendance.db not found — nothing to migrate"
fi

# ---------------------------------------------------------------- photos
if [ -d "student_photos" ]; then
    cp -r student_photos "${STAGE}/" 2>/dev/null
    N=$(find "${STAGE}/student_photos" -type f 2>/dev/null | wc -l)
    log "student photos: ${N} file(s)"
    echo "  student_photos           ${N} files" >> "${STAGE}/MANIFEST.txt"
else
    log "student photos: none"
fi

# ---------------------------------------------------------------- config
[ -f ".env" ]     && { cp .env     "${STAGE}/env.backup";  log "captured .env (contains secrets)"; }
[ -f "cert.pem" ] && cp cert.pem "${STAGE}/" 2>/dev/null
[ -f "key.pem" ]  && cp key.pem  "${STAGE}/" 2>/dev/null
log "TLS certs captured (regenerate on the new host — they are bound to the old IP)"

# crontab, so the new host can reinstate the schedules
crontab -l > "${STAGE}/crontab.txt" 2>/dev/null && log "captured crontab" || log "no crontab to capture"

# ---------------------------------------------------------------- models
if [ "${WITH_MODELS}" -eq 1 ]; then
    if [ -d "$HOME/.insightface" ]; then
        log "bundling InsightFace models (this takes a moment)…"
        cp -r "$HOME/.insightface" "${STAGE}/insightface_models"
        log "  $(du -sh "${STAGE}/insightface_models" | cut -f1)"
    else
        log "WARNING: ~/.insightface not found — the new host will download them on first run"
    fi
fi

# ---------------------------------------------------------------- offline extras
if [ "${OFFLINE}" -eq 1 ]; then
    # (a) the source code itself, so the new host needs no git access
    if git rev-parse --git-dir >/dev/null 2>&1; then
        git archive --format=tar HEAD | gzip > "${STAGE}/source.tar.gz"             && log "bundled source code ($(du -h "${STAGE}/source.tar.gz" | cut -f1))"             || log "WARNING: could not archive source"
    fi
    # (b) pip wheels for every dependency, so the new host needs no PyPI
    if [ -f "requirements.txt" ]; then
        log "downloading pip wheels (needs internet HERE, once)…"
        PIP="./venv/bin/pip"; [ -x "$PIP" ] || PIP="pip3"
        mkdir -p "${STAGE}/wheels"
        if "$PIP" download -r requirements.txt -d "${STAGE}/wheels" >/dev/null 2>&1; then
            log "  bundled $(ls -1 "${STAGE}/wheels" | wc -l) wheel(s), $(du -sh "${STAGE}/wheels" | cut -f1)"
        else
            log "  WARNING: wheel download failed — the new host will need PyPI access"
            rmdir "${STAGE}/wheels" 2>/dev/null
        fi
    fi
fi

# record the commit so the new host checks out the same code
{
    echo ""
    echo "SOURCE HOST : $(hostname 2>/dev/null)"
    echo "EXPORTED    : $(date '+%Y-%m-%d %H:%M:%S')"
    echo "GIT COMMIT  : $(git rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "GIT BRANCH  : $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
    echo "MODELS      : $([ "${WITH_MODELS}" -eq 1 ] && echo included || echo 'not included')"
    echo "OFFLINE     : $([ "${OFFLINE}" -eq 1 ] && echo 'yes - source + wheels included' || echo 'no - new host needs git + PyPI')"
} >> "${STAGE}/MANIFEST.txt"

# ---------------------------------------------------------------- archive
tar -czf "${ARCHIVE}" -C "${STAGE}" . || fail "archive creation failed"
tar -tzf "${ARCHIVE}" >/dev/null 2>&1  || fail "archive is corrupt"
chmod 600 "${ARCHIVE}"
sha256sum "${ARCHIVE}" > "${ARCHIVE}.sha256" 2>/dev/null || \
    shasum -a 256 "${ARCHIVE}" > "${ARCHIVE}.sha256" 2>/dev/null || true
rm -rf "${STAGE}"

echo ""
log "=== export complete ==="
echo "  archive : ${ARCHIVE}  ($(du -h "${ARCHIVE}" | cut -f1))"
[ -f "${ARCHIVE}.sha256" ] && echo "  checksum: $(cat "${ARCHIVE}.sha256")"
echo ""
echo "Next:"
echo "  1. Copy ${ARCHIVE} (and the .sha256) to the NEW server."
echo "  2. On the new server: git clone the repo, then run"
echo "         ./migrate_restore.sh ${ARCHIVE}"
echo ""
echo "  This archive holds password hashes, SMTP credentials and student face"
echo "  photos. Delete it from both machines once the migration is verified."
