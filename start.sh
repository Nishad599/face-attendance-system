#!/bin/bash
cd "$(dirname "$0")"

# NOTE: when the systemd unit is installed (deploy/install_service.sh) it owns
# the process and logs to the journal; this script is the fallback path for
# dev machines and servers without systemd.

# Stop any previously running instance
pkill -f main_with_face_recognition.py 2>/dev/null
sleep 2

mkdir -p logs

# Roll the log instead of truncating it. The old `> app.log` threw away the
# history on every restart - i.e. exactly the output you need after a crash.
if [ -f app.log ] && [ -s app.log ]; then
    mv app.log "logs/app_$(date +%Y%m%d-%H%M%S).log"
fi

# Keep the 20 most recent rolled logs; drop the rest so logs/ cannot grow
# without bound on a server that restarts often.
ls -1t logs/app_*.log 2>/dev/null | tail -n +21 | xargs -r rm -f

# Use unbuffered Python output (-u flag). Append, never truncate.
nohup ./venv/bin/python3 -u main_with_face_recognition.py >> app.log 2>&1 &

# Save Process ID
echo $! > app.pid
sleep 3  # Wait for startup

# Check if actually running
if ps -p $(cat app.pid) > /dev/null 2>&1; then
    echo "✅ Application started!"
    echo "🔹 PID: $(cat app.pid)"
    echo "🔹 Logs: tail -f app.log"
    HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"; [ -n "$HOST_IP" ] || HOST_IP="localhost"
    echo "🔹 URL: https://${HOST_IP}:8000"
else
    echo "❌ Application failed to start!"
    echo "📋 Error log:"
    cat app.log
fi
