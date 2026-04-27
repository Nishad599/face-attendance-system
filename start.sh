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
    echo "🔹 URL: https://10.212.13.129:8000"
else
    echo "❌ Application failed to start!"
    echo "📋 Error log:"
    cat app.log
fi
