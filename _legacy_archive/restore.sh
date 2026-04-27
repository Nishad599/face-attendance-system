#!/bin/bash

# ============================================================================
# FACE ATTENDANCE SYSTEM - RESTORE BACKUP SCRIPT
# Restores database, photos, and code from a backup
# ============================================================================

set -e  # Exit on error

if [ -z "$1" ]; then
    echo "Usage: $0 <backup_name>"
    echo ""
    echo "Available backups:"
    ls -d backups/backup_* 2>/dev/null | sed 's|backups/||' | head -10 || echo "No backups found"
    echo ""
    echo "Example: $0 backup_20260326_120000"
    exit 1
fi

BACKUP_NAME="$1"
BACKUP_PATH="backups/${BACKUP_NAME}"

if [ ! -d "${BACKUP_PATH}" ]; then
    echo "❌ Backup not found: ${BACKUP_PATH}"
    exit 1
fi

echo "🔄 FACE ATTENDANCE SYSTEM - RESTORE BACKUP"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📁 Restoring from: ${BACKUP_PATH}"
echo ""

# Display backup info
if [ -f "${BACKUP_PATH}/BACKUP_INFO.txt" ]; then
    cat "${BACKUP_PATH}/BACKUP_INFO.txt"
    echo ""
fi

# Confirmation
read -p "⚠️  This will OVERWRITE current files. Continue? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "❌ Restore cancelled"
    exit 0
fi

# Stop running application
echo "🛑 Stopping application..."
if [ -f "app.pid" ]; then
    pkill -f main_with_face_recognition.py 2>/dev/null || true
    sleep 2
fi

# 1. RESTORE DATABASE
echo "📊 Restoring database..."
if [ -f "${BACKUP_PATH}/database/attendance.db" ]; then
    # Create backup of current database
    if [ -f "attendance.db" ]; then
        cp attendance.db "attendance.db.before_restore_$(date +%s)"
        echo "   📋 Current database backed up"
    fi
    cp "${BACKUP_PATH}/database/attendance.db" attendance.db
    echo "   ✅ Database restored"
else
    echo "   ⚠️  No database found in backup"
fi

# 2. RESTORE STUDENT PHOTOS
echo "📸 Restoring student photos..."
if [ -d "${BACKUP_PATH}/student_photos" ]; then
    # Create backup of current photos
    if [ -d "student_photos" ]; then
        rm -rf "student_photos.backup_$(date +%s)" || true
        mv student_photos "student_photos.backup_$(date +%s)"
        echo "   📋 Current photos backed up"
    fi
    cp -r "${BACKUP_PATH}/student_photos" .
    PHOTO_COUNT=$(find student_photos -type f -name "*.jpg" 2>/dev/null | wc -l)
    echo "   ✅ ${PHOTO_COUNT} photos restored"
else
    echo "   ⚠️  No photos in backup"
fi

# 3. RESTORE CODE
echo "🐍 Restoring application code..."
if [ -d "${BACKUP_PATH}/code" ]; then
    for file in "${BACKUP_PATH}/code"/*.py; do
        if [ -f "$file" ]; then
            filename=$(basename "$file")
            # Create backup of current code
            if [ -f "$filename" ]; then
                cp "$filename" "${filename}.before_restore"
            fi
            cp "$file" .
        fi
    done
    echo "   ✅ Application code restored (previous versions saved as *.py.before_restore)"
else
    echo "   ⚠️  No code found in backup"
fi

# 4. RESTORE CONFIGURATION
echo "⚙️  Restoring configuration..."
if [ -d "${BACKUP_PATH}/config" ]; then
    for file in "${BACKUP_PATH}/config"/*; do
        if [ -f "$file" ]; then
            filename=$(basename "$file")
            if [ -f "$filename" ]; then
                cp "$filename" "${filename}.before_restore"
            fi
            cp "$file" .
        fi
    done
    echo "   ✅ Configuration restored"
else
    echo "   ⚠️  No configuration in backup"
fi

# 5. RESTORE STATIC FILES & TEMPLATES
echo "🎨 Restoring static files and templates..."
if [ -d "${BACKUP_PATH}/static" ]; then
    cp -r "${BACKUP_PATH}/static" .
fi
if [ -d "${BACKUP_PATH}/templates" ]; then
    cp -r "${BACKUP_PATH}/templates" .
fi
echo "   ✅ Static files restored"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ RESTORE COMPLETED"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "⚠️  If anything went wrong, previous files are saved with .before_restore extension"
echo ""
echo "Next steps:"
echo "  1. Review the restored files"
echo "  2. Start the application: bash start.sh"
echo "  3. Check logs: tail -f app.log"
echo ""
