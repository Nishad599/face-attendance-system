#!/bin/bash
# healthcheck.sh — verify the app is actually serving, and shout if it is not.
#
# Designed for cron. Silent when healthy, so a quiet mailbox means all is well:
#
#   */5 * * * * cd $HOME/student && ./healthcheck.sh >> logs/health.log 2>&1
#
# On failure it (a) writes to the log, (b) emails ALERT_EMAIL via the app's own
# mailer if SMTP is configured, and (c) optionally restarts the app.
#
#   --restart      attempt a restart when the check fails
#   --quiet        suppress the healthy-case line in the log
#   --url URL      override the URL to probe
#
# Requires only curl. Self-signed certificates are expected (-k).
set -uo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-8000}"
URL="https://127.0.0.1:${PORT}/api/system/status"
TIMEOUT=10
DO_RESTART=0
QUIET=0
# Two consecutive failures before alerting: a single slow response during face
# recognition should not page anyone.
STATE_FILE="logs/.health_failures"
FAIL_THRESHOLD=2

while [ $# -gt 0 ]; do
    case "$1" in
        --restart) DO_RESTART=1 ;;
        --quiet)   QUIET=1 ;;
        --url)     shift; URL="$1" ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1"; exit 2 ;;
    esac
    shift
done

mkdir -p logs
STAMP="$(date '+%Y-%m-%d %H:%M:%S')"

# Load .env so ALERT_EMAIL / SMTP settings are available.
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env 2>/dev/null || true
    set +a
fi

notify() {
    local subject="$1" body="$2"
    local to="${ALERT_EMAIL:-${SMTP_USER:-}}"
    [ -n "$to" ] || return 0
    [ -x ./venv/bin/python ] || return 0
    ./venv/bin/python - "$to" "$subject" "$body" <<'PY' 2>/dev/null || true
import sys
try:
    import mailer
    to, subject, body = sys.argv[1], sys.argv[2], sys.argv[3]
    if mailer.is_configured():
        mailer.send_email(to, subject, f"<pre>{body}</pre>", kind="healthcheck")
except Exception:
    pass
PY
}

CODE="$(curl -sk -o /dev/null -w '%{http_code}' -m "$TIMEOUT" "$URL" 2>/dev/null)"

if [ "$CODE" = "200" ]; then
    echo 0 > "$STATE_FILE"
    [ "$QUIET" -eq 1 ] || echo "[$STAMP] OK (HTTP $CODE)"
    exit 0
fi

FAILURES=$(( $(cat "$STATE_FILE" 2>/dev/null || echo 0) + 1 ))
echo "$FAILURES" > "$STATE_FILE"

if [ "$CODE" = "000" ]; then
    REASON="no response (connection refused or timed out after ${TIMEOUT}s)"
else
    REASON="unexpected HTTP $CODE"
fi
echo "[$STAMP] DOWN - $REASON (failure #$FAILURES)"

if [ "$FAILURES" -lt "$FAIL_THRESHOLD" ]; then
    exit 1
fi

# Gather context worth having in the alert.
DETAIL="Host: $(hostname)
Time: $STAMP
URL : $URL
Fail: $REASON (consecutive failures: $FAILURES)

Disk:
$(df -h . | tail -n +1)

Memory:
$(free -h 2>/dev/null | head -2)

Last 25 log lines:
$(journalctl -u attendance -n 25 --no-pager 2>/dev/null || tail -n 25 app.log 2>/dev/null || echo 'no logs available')"

if [ "$DO_RESTART" -eq 1 ]; then
    echo "[$STAMP] attempting restart..."
    if systemctl is-enabled --quiet attendance 2>/dev/null; then
        sudo -n systemctl restart attendance 2>/dev/null || systemctl restart attendance 2>/dev/null
    else
        ./restart.sh >/dev/null 2>&1
    fi
    sleep 10
    CODE2="$(curl -sk -o /dev/null -w '%{http_code}' -m "$TIMEOUT" "$URL" 2>/dev/null)"
    if [ "$CODE2" = "200" ]; then
        echo "[$STAMP] recovered after restart"
        echo 0 > "$STATE_FILE"
        notify "[attendance] recovered after automatic restart" "$DETAIL

The app was down and an automatic restart brought it back."
        exit 0
    fi
    DETAIL="$DETAIL

Automatic restart was attempted and FAILED (HTTP $CODE2)."
fi

notify "[attendance] SERVICE DOWN on $(hostname)" "$DETAIL"
exit 1
