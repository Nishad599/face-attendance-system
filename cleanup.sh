#!/usr/bin/env bash
#
# cleanup.sh — safely remove dev/runtime cruft from the deployment.
#
#   ./cleanup.sh          # remove clearly-safe junk (backs up the DB first)
#   ./cleanup.sh --deep   # ALSO remove stale DB/photo backups (deliberate)
#   ./cleanup.sh --dry    # show what WOULD be removed, delete nothing
#
# NEVER touches the live data/app: attendance.db, student_photos/, cert.pem,
# key.pem, venv/, models/, actions-runner/, templates/, static/, backups/,
# the real *.py, the migrate_*.py scripts, or requirements.txt.

set -u

DEEP=0
DRY=0
for arg in "$@"; do
  case "$arg" in
    --deep) DEEP=1 ;;
    --dry|--dry-run) DRY=1 ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done

# Safety: only run from the project root (avoids nuking the wrong directory)
if [ ! -f "main_with_face_recognition.py" ]; then
  echo "[ABORT] Run this from the project root (main_with_face_recognition.py not found)."
  exit 1
fi

# rm helper that respects --dry
scrub() {
  for target in "$@"; do
    # Expand globs; skip if nothing matches
    for path in $target; do
      [ -e "$path" ] || continue
      if [ "$DRY" -eq 1 ]; then
        echo "  would remove: $path"
      else
        rm -rf -- "$path" && echo "  removed: $path"
      fi
    done
  done
}

echo "=== cleanup.sh (deep=$DEEP dry=$DRY) ==="

# 1) Back up the live DB first (skip on --dry)
if [ "$DRY" -eq 0 ] && [ -f "attendance.db" ]; then
  mkdir -p backups
  stamp="$(date +%Y%m%d-%H%M%S)"
  cp attendance.db "backups/attendance.db.cleanup-$stamp"
  echo "[OK] backed up DB -> backups/attendance.db.cleanup-$stamp"
fi

# 2) Clearly-safe junk (dev scripts, temp files, restore copies, legacy docs)
echo "[*] removing safe junk…"
scrub "*.before_restore"
scrub "temp_encodings_*.npy"
scrub "test_anti_spoof.py" "test_crop.py" "test_laplacian.py" "test_probs.py" \
      "test_scale.py" "test_insightface.py"
scrub "fix.py" "new_api_endpoints.py" "opencv_face_detection.py" \
      "migrate_attendance_to_4_slots.py"
scrub "4_SLOT_SYSTEM_CHANGES.md" "BULK_UPLOAD_GUIDE.md" \
      "ENHANCEMENT_SUMMARY.md" "INTEGRATION_GUIDE.md"
scrub "_legacy_archive" "scratch" "__pycache__"
# stray editor/OS files
scrub "*.pyc" "*.pyo" ".DS_Store"

# 3) Deep: stale DB / photo backups (only with --deep)
if [ "$DEEP" -eq 1 ]; then
  echo "[*] --deep: removing stale backups…"
  scrub "attendance.db.backup.safe" "attendance.db.before_restore_"*
  scrub "student_photos.backup_"*
else
  echo "[i] skipping stale backups (run with --deep to remove them)"
fi

echo "=== done ==="
echo "Protected (never touched): attendance.db, student_photos/, cert.pem, key.pem,"
echo "  venv/, models/, actions-runner/, templates/, static/, backups/, migrate_phase*.py"
