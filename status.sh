#!/bin/bash

# ============================================
# Face Recognition Attendance System - STATUS Script
# ============================================

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
PROJECT_NAME="Face Recognition Attendance System"
PYTHON_FILE="main_with_face_recognition.py"
PID_FILE="app.pid"
LOG_FILE="app.log"
PORT=5000
HOST_IP="10.212.13.129"

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}   ${PROJECT_NAME}${NC}"
echo -e "${BLUE}   System Status Check${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# Check process status
STATUS="STOPPED"
PID=""
UPTIME=""
CPU_USAGE=""
MEM_USAGE=""

# Check using PID file
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        STATUS="RUNNING"
        # Get process details
        PROCESS_INFO=$(ps -p "$PID" -o pid,etime,pcpu,pmem,vsz,comm --no-headers)
        UPTIME=$(echo "$PROCESS_INFO" | awk '{print $2}')
        CPU_USAGE=$(echo "$PROCESS_INFO" | awk '{print $3}')
        MEM_USAGE=$(echo "$PROCESS_INFO" | awk '{print $4}')
    else
        STATUS="STALE_PID"
    fi
else
    # Check if running without PID file
    PIDS=$(pgrep -f "$PYTHON_FILE")
    if [ -n "$PIDS" ]; then
        STATUS="RUNNING_NO_PID"
        PID=$(echo "$PIDS" | head -n1)
        PROCESS_INFO=$(ps -p "$PID" -o pid,etime,pcpu,pmem,vsz,comm --no-headers)
        UPTIME=$(echo "$PROCESS_INFO" | awk '{print $2}')
        CPU_USAGE=$(echo "$PROCESS_INFO" | awk '{print $3}')
        MEM_USAGE=$(echo "$PROCESS_INFO" | awk '{print $4}')
    fi
fi

# Display status with appropriate color
echo -e "${CYAN}🔍 Application Status:${NC}"
case $STATUS in
    "RUNNING")
        echo -e "   ${GREEN}● RUNNING${NC}"
        ;;
    "STOPPED")
        echo -e "   ${RED}● STOPPED${NC}"
        ;;
    "STALE_PID")
        echo -e "   ${YELLOW}● STALE PID FILE${NC} (process not running but PID file exists)"
        ;;
    "RUNNING_NO_PID")
        echo -e "   ${YELLOW}● RUNNING${NC} (without PID file)"
        ;;
esac

echo ""

# Show process details if running
if [[ "$STATUS" == "RUNNING" ]] || [[ "$STATUS" == "RUNNING_NO_PID" ]]; then
    echo -e "${CYAN}📊 Process Information:${NC}"
    echo -e "   ${BLUE}PID:${NC}         $PID"
    echo -e "   ${BLUE}Uptime:${NC}      $UPTIME"
    echo -e "   ${BLUE}CPU Usage:${NC}   $CPU_USAGE%"
    echo -e "   ${BLUE}Memory:${NC}      $MEM_USAGE%"
    echo ""
    
    # Check port accessibility
    echo -e "${CYAN}🌐 Network Status:${NC}"
    if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
        echo -e "   ${GREEN}✅ Port $PORT is active${NC}"
        echo -e "   ${BLUE}URLs:${NC}"
        echo -e "      • External: ${GREEN}http://$HOST_IP:$PORT${NC}"
        echo -e "      • Local:    ${GREEN}http://localhost:$PORT${NC}"
        
        # Test if the server responds
        if command -v curl &> /dev/null; then
            echo ""
            echo -e "${CYAN}🔗 Testing server response:${NC}"
            RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" -m 2 http://localhost:$PORT 2>/dev/null)
            if [ "$RESPONSE" == "200" ] || [ "$RESPONSE" == "302" ]; then
                echo -e "   ${GREEN}✅ Server is responding (HTTP $RESPONSE)${NC}"
            elif [ "$RESPONSE" == "000" ]; then
                echo -e "   ${YELLOW}⚠️  Server is not responding${NC}"
            else
                echo -e "   ${YELLOW}⚠️  Server returned HTTP $RESPONSE${NC}"
            fi
        fi
    else
        echo -e "   ${RED}❌ Port $PORT is not active${NC}"
        echo -e "   ${YELLOW}The process may be starting up or crashed${NC}"
    fi
    
    echo ""
    
    # Show recent log entries
    if [ -f "$LOG_FILE" ]; then
        echo -e "${CYAN}📋 Recent Log Entries:${NC}"
        echo "----------------------------------------"
        tail -n 5 "$LOG_FILE" 2>/dev/null || echo "No log entries available"
        echo "----------------------------------------"
    else
        echo -e "${YELLOW}⚠️  Log file not found: $LOG_FILE${NC}"
    fi
    
    # Show all related processes
    echo ""
    echo -e "${CYAN}👥 All Related Processes:${NC}"
    ps aux | grep "$PYTHON_FILE" | grep -v grep | grep -v "status.sh" | while read line; do
        echo "   $line" | cut -c1-120
    done
    
else
    # Application is not running
    echo -e "${CYAN}📊 Application is not running${NC}"
    
    if [ "$STATUS" == "STALE_PID" ]; then
        echo ""
        echo -e "${YELLOW}⚠️  Found stale PID file: $PID_FILE${NC}"
        echo -e "${YELLOW}   Run './stop.sh' to clean up${NC}"
    fi
    
    # Check if port is being used by something else
    if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
        OTHER_PID=$(lsof -ti:$PORT)
        echo ""
        echo -e "${YELLOW}⚠️  Port $PORT is being used by another process (PID: $OTHER_PID)${NC}"
        echo -e "${YELLOW}   You may need to stop this process before starting the application${NC}"
    fi
    
    # Show last log entries if available
    if [ -f "$LOG_FILE" ]; then
        echo ""
        echo -e "${CYAN}📋 Last Log Entries (before shutdown):${NC}"
        echo "----------------------------------------"
        tail -n 5 "$LOG_FILE" 2>/dev/null || echo "No log entries available"
        echo "----------------------------------------"
    fi
fi

# System resource information
echo ""
echo -e "${CYAN}💻 System Resources:${NC}"
echo -e "   ${BLUE}CPU Load:${NC}    $(uptime | awk -F'load average:' '{print $2}')"
echo -e "   ${BLUE}Memory:${NC}      $(free -h | awk '/^Mem:/ {printf "%s / %s (%.1f%%)", $3, $2, ($3/$2)*100}')"
echo -e "   ${BLUE}Disk:${NC}        $(df -h . | awk 'NR==2 {printf "%s / %s (%s)", $3, $2, $5}')"

# Log file information
echo ""
echo -e "${CYAN}📁 Log Files:${NC}"
if [ -f "$LOG_FILE" ]; then
    LOG_SIZE=$(ls -lh "$LOG_FILE" | awk '{print $5}')
    LOG_LINES=$(wc -l < "$LOG_FILE")
    LOG_MODIFIED=$(stat -c %y "$LOG_FILE" | cut -d'.' -f1)
    echo -e "   ${BLUE}Current log:${NC} $LOG_FILE"
    echo -e "   ${BLUE}Size:${NC}        $LOG_SIZE"
    echo -e "   ${BLUE}Lines:${NC}       $LOG_LINES"
    echo -e "   ${BLUE}Modified:${NC}    $LOG_MODIFIED"
else
    echo -e "   ${YELLOW}No active log file${NC}"
fi

if [ -d "logs" ]; then
    BACKUP_COUNT=$(find logs -name "app_*.log" -type f 2>/dev/null | wc -l)
    if [ "$BACKUP_COUNT" -gt 0 ]; then
        echo -e "   ${BLUE}Backup logs:${NC} $BACKUP_COUNT files in logs/"
    fi
fi

# Available commands
echo ""
echo -e "${BLUE}============================================${NC}"
echo -e "${CYAN}📌 Available Commands:${NC}"
echo -e "${BLUE}============================================${NC}"

if [[ "$STATUS" == "RUNNING" ]] || [[ "$STATUS" == "RUNNING_NO_PID" ]]; then
    echo -e "   ${GREEN}•${NC} View live logs:    ${CYAN}tail -f $LOG_FILE${NC}"
    echo -e "   ${GREEN}•${NC} Stop application:  ${CYAN}./stop.sh${NC}"
    echo -e "   ${GREEN}•${NC} Restart:           ${CYAN}./restart.sh${NC}"
    echo -e "   ${GREEN}•${NC} Process details:   ${CYAN}ps -fp $PID${NC}"
else
    echo -e "   ${GREEN}•${NC} Start application: ${CYAN}./start.sh${NC}"
    echo -e "   ${GREEN}•${NC} View old logs:     ${CYAN}cat $LOG_FILE${NC}"
    echo -e "   ${GREEN}•${NC} Clean up:          ${CYAN}./stop.sh${NC}"
fi

echo ""
echo -e "${GREEN}✅ Status check complete!${NC}"