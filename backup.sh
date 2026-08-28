#!/bin/bash

# ============================================================================
# FACE ATTENDANCE SYSTEM - COMPREHENSIVE BACKUP SCRIPT
# Backs up: Database, Student Photos, Code, and Configuration
# ============================================================================

set -e  # Exit on error

BACKUP_DIR="backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_NAME="backup_${TIMESTAMP}"
BACKUP_PATH="${BACKUP_DIR}/${BACKUP_NAME}"

# Ensure backup directory exists
mkdir -p "${BACKUP_DIR}"

echo "📦 Starting backup: ${BACKUP_NAME}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Create backup structure
mkdir -p "${BACKUP_PATH}/database"
mkdir -p "${BACKUP_PATH}/student_photos"
mkdir -p "${BACKUP_PATH}/code"
mkdir -p "${BACKUP_PATH}/config"

# 1. BACKUP DATABASE
echo "📊 Backing up database..."
if [ -f "attendance.db" ]; then
    cp attendance.db "${BACKUP_PATH}/database/attendance.db"
    sqlite3 attendance.db ".backup '${BACKUP_PATH}/database/attendance_backup.sql'" 2>/dev/null || true
    echo "   ✅ Database backed up"
else
    echo "   ⚠️  No database found"
fi

# 2. BACKUP STUDENT PHOTOS
echo "📸 Backing up student photos..."
if [ -d "student_photos" ]; then
    cp -r student_photos "${BACKUP_PATH}/"
    PHOTO_COUNT=$(find student_photos -type f -name "*.jpg" 2>/dev/null | wc -l)
    echo "   ✅ ${PHOTO_COUNT} photos backed up"
else
    echo "   ⚠️  No photos directory found"
fi

# 3. BACKUP KEY PYTHON FILES
echo "🐍 Backing up application code..."
PYTHON_FILES=(
    "main_with_face_recognition.py"
    "asian_face_model.py"
    "camera_manager.py"
    "attendance_manager.py"
    "photo_utils.py"
    "phase1_integration.py"
    "opencv_face_detection.py"
    "new_api_endpoints.py"
)

for file in "${PYTHON_FILES[@]}"; do
    if [ -f "$file" ]; then
        cp "$file" "${BACKUP_PATH}/code/"
    fi
done
echo "   ✅ Application code backed up"

# 4. BACKUP CONFIGURATION FILES
echo "⚙️  Backing up configuration..."
CONFIG_FILES=(
    "requirements.txt"
    ".env"
    ".gitignore"
    "setup_database.py"
)

for file in "${CONFIG_FILES[@]}"; do
    if [ -f "$file" ]; then
        cp "$file" "${BACKUP_PATH}/config/"
    fi
done
echo "   ✅ Configuration backed up"

# 5. BACKUP STATIC FILES & TEMPLATES
echo "🎨 Backing up static files and templates..."
if [ -d "static" ]; then
    cp -r static "${BACKUP_PATH}/"
fi
if [ -d "templates" ]; then
    cp -r templates "${BACKUP_PATH}/"
fi
echo "   ✅ Static files backed up"

# 6. CREATE BACKUP METADATA
echo "📝 Creating backup metadata..."
cat > "${BACKUP_PATH}/BACKUP_INFO.txt" << 'METADATA'
═════════════════════════════════════════════════════════════════════
FACE ATTENDANCE SYSTEM BACKUP
═════════════════════════════════════════════════════════════════════
METADATA

echo "Backup Date: $(date)" >> "${BACKUP_PATH}/BACKUP_INFO.txt"
echo "Backup Time: ${TIMESTAMP}" >> "${BACKUP_PATH}/BACKUP_INFO.txt"
echo "System: $(hostname || echo 'unknown')" >> "${BACKUP_PATH}/BACKUP_INFO.txt"
echo "User: $(whoami)" >> "${BACKUP_PATH}/BACKUP_INFO.txt"
echo "" >> "${BACKUP_PATH}/BACKUP_INFO.txt"
echo "Contents:" >> "${BACKUP_PATH}/BACKUP_INFO.txt"
echo "  - database/: SQLite database and SQL backup" >> "${BACKUP_PATH}/BACKUP_INFO.txt"
echo "  - student_photos/: All student photos" >> "${BACKUP_PATH}/BACKUP_INFO.txt"
echo "  - code/: Python application files" >> "${BACKUP_PATH}/BACKUP_INFO.txt"
echo "  - config/: Configuration and requirements" >> "${BACKUP_PATH}/BACKUP_INFO.txt"
echo "  - templates/: HTML templates" >> "${BACKUP_PATH}/BACKUP_INFO.txt"
echo "  - static/: CSS, JS, and other assets" >> "${BACKUP_PATH}/BACKUP_INFO.txt"
echo "" >> "${BACKUP_PATH}/BACKUP_INFO.txt"
echo "═════════════════════════════════════════════════════════════════════" >> "${BACKUP_PATH}/BACKUP_INFO.txt"

# 7. CALCULATE BACKUP SIZE
BACKUP_SIZE=$(du -sh "${BACKUP_PATH}" | cut -f1)

# 8. DISPLAY SUMMARY
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ BACKUP COMPLETED SUCCESSFULLY"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📁 Backup Location: ${BACKUP_PATH}"
echo "📊 Backup Size: ${BACKUP_SIZE}"
echo ""
echo "To restore from this backup later, use:"
echo "  bash restore.sh ${BACKUP_NAME}"
echo ""
echo "View backup details:"
echo "  cat ${BACKUP_PATH}/BACKUP_INFO.txt"
echo ""

# Optional: Create compressed archive (comment out if not needed)
if command -v tar &> /dev/null; then
    echo "📦 Creating compressed archive..."
    ARCHIVE_NAME="backup_${TIMESTAMP}.tar.gz"
    tar -czf "${BACKUP_DIR}/${ARCHIVE_NAME}" -C "${BACKUP_DIR}" "${BACKUP_NAME}" 2>/dev/null
    ARCHIVE_SIZE=$(du -sh "${BACKUP_DIR}/${ARCHIVE_NAME}" | cut -f1)
    echo "✅ Archive created: ${BACKUP_DIR}/${ARCHIVE_NAME} (${ARCHIVE_SIZE})"
    echo ""
fi

echo "💡 Tip: List all backups with: ls -la backups/"
