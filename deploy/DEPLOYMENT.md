# Production setup

What the deploy workflow does for you, and the handful of things it cannot.

`.github/workflows/deploy.yml` runs on every push to `main` and handles code
sync, the virtualenv, **all database migrations**, the restart, and a
post-deploy health check. You never need to run a migration by hand.

Everything below is a **one-time** setup on the server. Once done, deploys are
fully automatic.

Paths assume the app lives at `$HOME/student` (the `APP_DIR` in the workflow).

---

## 1. `.env` — required

The deploy deliberately excludes `.env` so a push can never overwrite your
credentials. It must exist before the app will start correctly.

```bash
cd $HOME/student
cp .env.example .env
nano .env
```

Values that matter most:

| Key | Why |
|---|---|
| `APP_BASE_URL` | Links inside every email. Wrong value = dead links. |
| `SMTP_USER` / `SMTP_PASSWORD` | Gmail needs an **App Password** (16 chars), not the account password. Blank disables email entirely; the app still runs. |
| `ALERT_EMAIL` | Where `healthcheck.sh` sends downtime alerts. |
| `COOKIE_SECURE` | Leave at `1`. Only set `0` if you deliberately serve plain HTTP. |
| `DATABASE_URL` | **Leave completely blank** for SQLite. |

> A leftover placeholder (`DATABASE_URL=postgresql://user:pass@localhost/your_db`)
> is not the same as blank — `load_dotenv` will hand that literal string to the
> app and the connection will fail. Delete the value, don't comment it out.

Also required on the server, and never in git: `cert.pem` and `key.pem`.

---

## 2. systemd — strongly recommended

Without this the app runs under `nohup` and **does not survive a reboot** or an
OOM kill. Nothing restarts it; attendance is silently down until someone
notices.

```bash
cd $HOME/student && sudo ./deploy/install_service.sh
```

The installer detects the app directory and owning user, stops any running
`nohup` instance, installs the unit, enables it at boot and starts it.

```bash
systemctl status attendance
journalctl -u attendance -f
sudo systemctl restart attendance
```

**Let the deploy restart it without a password.** The GitHub runner is not
interactive, so `sudo systemctl restart` would hang. Grant just that one
command (replace `nishadk` with the runner's user):

```bash
echo 'nishadk ALL=(root) NOPASSWD: /bin/systemctl restart attendance, /bin/systemctl status attendance' | sudo tee /etc/sudoers.d/attendance
sudo chmod 440 /etc/sudoers.d/attendance
```

`restart.sh` detects the unit and hands over to `systemctl` automatically, so
the deploy workflow needs no changes. Without the sudoers line it falls back to
the old `nohup` path and prints a warning.

---

## 3. Scheduled jobs

None of these are installed by the deploy. Add them once with `crontab -e`:

```cron
# Health check every 5 minutes; emails ALERT_EMAIL after 2 consecutive failures
*/5 * * * * cd $HOME/student && ./healthcheck.sh --quiet --restart >> logs/health.log 2>&1

# Nightly backup at 01:30, 14-day rotation
30 1 * * * cd $HOME/student && ./backup_cron.sh >> logs/backup.log 2>&1

# Weekly low-attendance alerts, Monday 07:30
30 7 * * 1 cd $HOME/student && ./venv/bin/python send_alerts.py --admin-digest >> logs/alerts.log 2>&1

# Monthly reports, 1st of the month at 07:00 (covers the month just ended)
0 7 1 * * cd $HOME/student && ./venv/bin/python send_reports.py >> logs/reports.log 2>&1

# Same-day "you weren't marked" nudge, Mon-Sat after the last slot
30 17 * * 1-6 cd $HOME/student && ./venv/bin/python send_daily_nudge.py >> logs/nudge.log 2>&1

# Delete face data past its retention window, Sunday 02:15 (after the backup)
15 2 * * 0 cd $HOME/student && ./venv/bin/python purge_biometrics.py >> logs/purge.log 2>&1
```

**The purge job is not optional.** `/privacy` tells every student "your face
data is deleted within N days of your course ending", and until this script
existed nothing deleted anything — `BIOMETRIC_RETENTION_DAYS` was only ever
read in order to print that sentence. Without the cron entry the deployment
makes a promise it does not keep, which under the DPDP Act is a
storage-limitation failure and not merely a missing feature.

Check what it would do before trusting it, and after any change to course end
dates:

```bash
./venv/bin/python purge_biometrics.py --dry-run
```

It clears `students.face_encoding`, resets `photo_count` and removes the
enrolment JPEGs under `student_photos/`. It deliberately keeps attendance
records, consent records and the student's own details, because the same
notice says those are kept. The clock runs from the **course end date**, so a
running course or one with a blank `end_date` is never touched. Re-running is
a no-op, and each institute gets its own `audit_log` entry.

`cron` does not expand `$HOME` in every implementation — if a job silently does
nothing, replace `$HOME` with the absolute path (`/home/nishadk/student`).

**Test each one before trusting it:**

```bash
cd $HOME/student
./healthcheck.sh                                   # prints OK when healthy
./backup_cron.sh --dry-run
./venv/bin/python send_alerts.py --dry-run
./venv/bin/python send_reports.py --dry-run
```

`--dry-run` never sends mail. Both mailers write every attempt to the
`email_log` table, and the alert sender additionally records `alert_log` so a
cron that fires twice cannot double-mail a student.

---

## 4. Log rotation

Under systemd the journal rotates on its own — nothing to do. Cap its size if
disk is tight:

```bash
sudo journalctl --vacuum-size=500M
```

On the `nohup` path, `start.sh` now rolls `app.log` into `logs/` on each restart
and keeps the 20 most recent. For time-based rotation as well:

```bash
sudo cp deploy/logrotate.conf /etc/logrotate.d/attendance
sudo sed -i "s|__APPDIR__|$HOME/student|g; s|__USER__|$USER|g" /etc/logrotate.d/attendance
sudo logrotate -d /etc/logrotate.d/attendance      # dry run
```

---

## 5. Anti-spoofing (liveness) — still pending

Liveness is currently **off** (`ANTISPOOF_DISABLED=1`), which means a printed
photo or a face on a phone screen can mark attendance. The crop bug that caused
the over-rejection is fixed, but the threshold has never been validated against
real hardware.

Check at any time without reading `.env` on the server — `/api/system/status`
reports it:

```bash
curl -sk https://127.0.0.1:8000/api/system/status | python3 -m json.tool
```

`"liveness_active": false` comes with a `liveness_warning` spelling out what is
and is not being enforced. Once a validated threshold is in place the field
flips to `true` and reports the threshold in use.

This has to happen on the VM: the sandbox falls back to a Haar cascade because
`buffalo_l` isn't installed there, so its bounding boxes — and therefore its
scores — are not the ones production will see.

```bash
cd $HOME/student

# ~10 real faces: different people, different lighting, one run each
./venv/bin/python test_antispoof.py --camera --label real

# ~10 spoof attempts: a face on a phone screen, a printout
./venv/bin/python test_antispoof.py --camera --label spoof

# recommends a threshold from what you collected
./venv/bin/python test_antispoof.py --suggest
```

`--suggest` picks the value that misclassifies the fewest samples, weighting an
accepted spoof as three times worse than a real face being asked to retry. It
prints the error rates at that threshold — if any spoofs still pass, collect
more samples rather than shipping the number.

Then in `.env`:

```
ANTISPOOF_THRESHOLD=<the suggested value>
```

and **remove** `ANTISPOOF_DISABLED=1`. Restart, then have a few people mark
attendance normally before you walk away from it.

Registration stays exempt by default (`ANTISPOOF_ON_REGISTRATION` off) because
enrolment is staff-supervised.

---

## 6. Verifying a deploy

The workflow now fails if the app does not return HTTP 200 within 60 seconds of
the restart, so a red run means something genuinely broke. To check by hand:

```bash
cd $HOME/student && ./status.sh && ./venv/bin/python check_db.py && journalctl -u attendance -n 30 --no-pager
```

`check_db.py` verifies every table, column **and index** the app expects. It is
also run non-fatally at the end of each deploy.

One of those indexes carries correctness, not just speed:
`idx_attendance_unique_slot` (added by `migrate_phase9.py`) is what makes the
`INSERT OR IGNORE` in the two camera paths actually refuse a duplicate. If
`check_db.py` reports it missing, attendance can be double-counted — run the
migration rather than ignoring it:

```bash
./venv/bin/python migrate_phase9.py --dry-run   # report duplicates, change nothing
./venv/bin/python migrate_phase9.py             # dedupe, then add the index
```

The dry run lists every duplicated (student, date, slot) group it found. The
real run keeps the **earliest** mark in each group — the one reflecting when the
student actually arrived — and deletes the rest, after backing up the database.

---

## Quick reference

| Task | Command |
|---|---|
| Restart | `sudo systemctl restart attendance` |
| Live logs | `journalctl -u attendance -f` |
| Health | `./healthcheck.sh` |
| Schema check | `./venv/bin/python check_db.py` |
| Backup now | `./backup_cron.sh` |
| Preview alerts | `./venv/bin/python send_alerts.py --dry-run` |
| Preview reports | `./venv/bin/python send_reports.py --dry-run` |
| Email config check | Admin dashboard → email card, or `/api/admin/email-status` |

---

## Importing a batch's academic calendar

The published schedule (modules, exams, events, holidays) loads from CSV:

```bash
cd $HOME/student
# PGCP-BDA
./venv/bin/python import_calendar.py --batch 2 --file data/calendar_pgcp_bda_aug2026.csv --dry-run
./venv/bin/python import_calendar.py --batch 2 --file data/calendar_pgcp_bda_aug2026.csv

# PGCP-AI
./venv/bin/python import_calendar.py --batch 3 --file data/calendar_pgcp_ai_feb2026.csv --dry-run
./venv/bin/python import_calendar.py --batch 3 --file data/calendar_pgcp_ai_feb2026.csv
```

Each calendar is scoped to its batch: students only ever see their own, and
holidays declared for one batch do not affect the other. Teachers can add
further entries themselves from the **Modules** tab of the teacher portal.

Check `--batch` against the real id first (`SELECT id, name FROM courses`).

Re-running updates rather than duplicating, so a corrected calendar can simply
be re-imported. The importer also widens the batch's start/end dates to cover
the calendar, and warns about dates that contradict each other — an exam
falling on a holiday or a Sunday.

Only `module` rows become subjects and can carry attendance. Events (picnics,
revision days, exams) stay separate; holidays go to the `holidays` table and
are automatically excluded from working days.
