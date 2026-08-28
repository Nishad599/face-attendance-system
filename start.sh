#!/bin/bash
cd "$(dirname "$0")"

# Stop any previously running instance
pkill -f main_with_face_recognition.py 2>/dev/null
sleep 2

# Use unbuffered Python output (-u flag)
nohup ./venv/bin/python3 -u main_with_face_recognition.py > app.log 2>&1 &

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
