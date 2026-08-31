#!/bin/bash
# Install (or refresh) the systemd unit for the attendance app.
#
#   sudo ./deploy/install_service.sh
#
# Safe to re-run: it rewrites the unit, reloads systemd and restarts the
# service. Detects the app directory and the owning user automatically, so
# nothing has to be hand-edited when the deployment path changes.
set -euo pipefail

SERVICE_NAME="attendance"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"

if [ "$EUID" -ne 0 ]; then
    echo "This script needs root to write to /etc/systemd/system."
    echo "Re-run it as:  sudo $0"
    exit 1
fi

# The app must run as the user that owns the files, NOT as root — it writes
# attendance.db and student_photos/, and root-owned files there would break
# the next deploy (which runs as the unprivileged runner user).
APP_USER="$(stat -c '%U' "$APP_DIR/main_with_face_recognition.py")"
if [ "$APP_USER" = "root" ]; then
    echo "Refusing to install: $APP_DIR is owned by root."
    echo "The app should be owned by the deploy user (e.g. chown -R user1 $APP_DIR)."
    exit 1
fi

if [ ! -x "$APP_DIR/venv/bin/python" ]; then
    echo "No virtualenv found at $APP_DIR/venv — run the deploy first."
    exit 1
fi

echo "App directory : $APP_DIR"
echo "Running as    : $APP_USER"

UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
sed -e "s|__APPDIR__|${APP_DIR}|g" \
    -e "s|__USER__|${APP_USER}|g" \
    "$SCRIPT_DIR/attendance.service" > "$UNIT"
chmod 644 "$UNIT"
echo "Wrote $UNIT"

# The old nohup process holds port 8000 and would fight the service for it.
if pgrep -f main_with_face_recognition.py >/dev/null 2>&1; then
    echo "Stopping the existing nohup instance..."
    pkill -f main_with_face_recognition.py || true
    rm -f "$APP_DIR/app.pid"
    sleep 2
fi

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

sleep 3
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo
    echo "OK — ${SERVICE_NAME} is running and enabled at boot."
    echo
    echo "  status : systemctl status ${SERVICE_NAME}"
    echo "  logs   : journalctl -u ${SERVICE_NAME} -f"
    echo "  restart: sudo systemctl restart ${SERVICE_NAME}"
else
    echo
    echo "FAILED to start. Recent log:"
    journalctl -u "$SERVICE_NAME" -n 40 --no-pager
    exit 1
fi
