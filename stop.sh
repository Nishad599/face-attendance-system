#!/bin/bash

# Stop using PID file
if [ -f app.pid ]; then
    kill $(cat app.pid)
    rm app.pid
    echo "🛑 Application stopped."
else
    echo "⚠ No PID file. Stopping all running instances..."
    pkill -f main_with_face_recognition.py
    echo "🛑 Application stopped."
fi
