# 📦 Backup & Restore Guide

## Quick Start

### Create a Full Backup
```bash
bash backup.sh
```
This creates a timestamped backup with everything: database, photos, code, and configuration.

**Example output:**
```
📦 Starting backup: backup_20260326_120000
✅ BACKUP COMPLETED SUCCESSFULLY
📁 Backup Location: backups/backup_20260326_120000
📊 Backup Size: 2.5GB
📦 Archive created: backups/backup_20260326_120000.tar.gz (845MB)
```

---

## What Gets Backed Up

### 1. **Database** (`database/`)
- `attendance.db` - SQLite database with all student records and attendance data
- `attendance_backup.sql` - SQL backup for human-readable format

### 2. **Student Photos** (`student_photos/`)
- All student registration photos organized by student ID and name
- Maintains directory structure exactly as it is

### 3. **Application Code** (`code/`)
- `main_with_face_recognition.py` - Main API
- `asian_face_model.py` - Face recognition model
- `camera_manager.py` - Camera handling
- `attendance_manager.py` - Attendance logic
- And other Python modules

### 4. **Configuration** (`config/`)
- `requirements.txt` - Python dependencies
- `.env` - Environment variables
- `setup_database.py` - Database schema

### 5. **Static Files & Templates**
- HTML templates
- CSS and JavaScript
- Images and assets

---

## Backup Structure

```
backups/
├── backup_20260326_120000/           ← Timestamped backup directory
│   ├── BACKUP_INFO.txt               ← Metadata about the backup
│   ├── database/
│   │   ├── attendance.db
│   │   └── attendance_backup.sql
│   ├── student_photos/
│   │   ├── 250840325001_Aashi_chahal/
│   │   ├── 250840325002_Abhinav_Dattatray_Thakare/
│   │   └── ...
│   ├── code/
│   │   ├── main_with_face_recognition.py
│   │   ├── asian_face_model.py
│   │   └── ...
│   ├── config/
│   ├── templates/
│   └── static/
└── backup_20260326_120000.tar.gz     ← Compressed archive (optional)
```

---

## Restore from Backup

### View Available Backups
```bash
ls backups/
```

### Restore Specific Backup
```bash
bash restore.sh backup_20260326_120000
```

### Restore Steps
1. ✅ Automatically stops the application
2. ✅ Backs up your current database and photos (prefixed with `backup_` and `.before_restore`)
3. ✅ Restores database, photos, code, and configuration
4. ✅ Creates safety copies of previous versions

### After Restore
```bash
# Review restored files (optional)
cat backups/backup_20260326_120000/BACKUP_INFO.txt

# Start the application
bash start.sh

# Check logs
tail -f app.log
```

---

## Use Cases

### 1. **Production Backup (Before Major Update)**
```bash
# Before updating code
bash backup.sh

# Make your changes
# If issues occur, restore
bash restore.sh backup_<timestamp>
```

### 2. **Daily Backup Schedule**
Add to crontab:
```bash
# Backup every day at 2 AM
0 2 * * * cd /home/user1/face-attendance-system && bash backup.sh >> backup.log 2>&1
```

### 3. **Archive Old Backups**
```bash
# Keep recent backups, compress old ones
tar -czf backup_archive_$(date +%Y%m).tar.gz backups/backup_202603*/
```

### 4. **Copy Backup to External Drive/Cloud**
```bash
# Copy to external location
cp -r backups/backup_20260326_120000 /mnt/backup_drive/

# Or upload to cloud
aws s3 cp backups/backup_20260326_120000.tar.gz s3://my-backups/
```

---

## Safety Features

### Automatic Backups Created
- When restoring, current files are automatically backed up:
  - `attendance.db.before_restore`
  - Current photos moved to `student_photos.backup_<timestamp>`
  - Code files saved as `.py.before_restore`

### Recovery from Mistakes
```bash
# If restore went wrong, revert to pre-restore state
mv student_photos.backup_* student_photos
cp attendance.db.before_restore attendance.db
```

---

## Backup Size & Storage

| Component | Typical Size |
|-----------|-------------|
| Database | 50-500 MB |
| Student Photos | 1-5 GB |
| Code + Config | 50 MB |
| **Total** | **1-6 GB** |
| Compressed (.tar.gz) | 30-50% of original |

**Recommendation:** Keep at least 3-5 recent backups on the system, archive older ones to external storage.

---

## Manual Database-Only Backup

If you only need to backup the database:
```bash
# Create SQL dump
sqlite3 attendance.db ".backup '/path/to/backup.db'"

# Or create SQL text file
sqlite3 attendance.db ".dump" > database_backup.sql
```

---

## Troubleshooting

### Backup fails with "Permission denied"
```bash
sudo bash backup.sh
```

### Restore is too slow (large photos)
Skip photo restore and do it separately:
```bash
# Edit restore.sh and comment out photo restoration
# Then manually restore photos
cp -r backups/backup_20260326_120000/student_photos .
```

### Want to backup only specific parts
Edit `backup.sh` and comment out the sections you don't need.

---

## Best Practices

✅ **Do:**
- Backup before major changes
- Test restore procedures occasionally
- Keep backups in multiple locations
- Archive old backups to external storage
- Document what each backup contains

❌ **Don't:**
- Delete backups immediately
- Store backups only on the same disk
- Skip backup before updates
- Ignore backup logs and errors

---

## Questions?

Check the backup execution status:
```bash
cat backups/backup_*/BACKUP_INFO.txt
```

View script details:
```bash
head -20 backup.sh
head -20 restore.sh
```
