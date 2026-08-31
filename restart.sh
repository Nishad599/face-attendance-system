#!/bin/bash

# ============================================
# Face Recognition Attendance System - RESTART Script
# ============================================

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
PROJECT_NAME="Face Recognition Attendance System"
WAIT_TIME=3

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# If the systemd unit is installed, it owns the process lifecycle. Restarting
# via start.sh here would leave systemd's copy running and fight for port 8000,
# so hand over instead. Keeps ./restart.sh working either way, including from
# the deploy workflow.
if systemctl list-unit-files attendance.service >/dev/null 2>&1 && \
   systemctl is-enabled --quiet attendance 2>/dev/null; then
    echo -e "${CYAN}systemd unit detected - restarting via systemctl${NC}"
    if sudo -n systemctl restart attendance 2>/dev/null || systemctl restart attendance 2>/dev/null; then
        sleep 3
        if systemctl is-active --quiet attendance; then
            echo -e "${GREEN}✅ attendance.service restarted${NC}"
            systemctl status attendance --no-pager -n 8 || true
            exit 0
        fi
        echo -e "${RED}❌ attendance.service failed to come back${NC}"
        journalctl -u attendance -n 30 --no-pager || true
        exit 1
    fi
    # Do NOT fall back to start.sh here. The unit is enabled, so systemd owns
    # the process: stop.sh would pkill it, systemd would immediately restart it
    # (Restart=always), and start.sh's nohup copy would race it for port 8000.
    # Failing loudly is the safe outcome — the app keeps running under systemd.
    echo -e "${RED}❌ attendance.service is enabled but could not be restarted.${NC}"
    echo -e "${YELLOW}   The deploy user needs passwordless sudo for this one command:${NC}"
    echo -e "${YELLOW}     echo '$(whoami) ALL=(root) NOPASSWD: /bin/systemctl restart attendance' \\${NC}"
    echo -e "${YELLOW}       | sudo tee /etc/sudoers.d/attendance && sudo chmod 440 /etc/sudoers.d/attendance${NC}"
    echo -e "${YELLOW}   See deploy/DEPLOYMENT.md. The old code is still running.${NC}"
    exit 1
fi

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}   Restarting ${PROJECT_NAME}${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# Check if stop.sh exists
if [ ! -f "./stop.sh" ]; then
    echo -e "${RED}❌ Error: stop.sh not found!${NC}"
    echo -e "${YELLOW}   Please ensure all scripts are in the same directory${NC}"
    exit 1
fi

# Check if start.sh exists
if [ ! -f "./start.sh" ]; then
    echo -e "${RED}❌ Error: start.sh not found!${NC}"
    echo -e "${YELLOW}   Please ensure all scripts are in the same directory${NC}"
    exit 1
fi

# Make scripts executable if they aren't
chmod +x ./stop.sh ./start.sh 2>/dev/null

# Step 1: Stop the application
echo -e "${CYAN}Step 1: Stopping the application...${NC}"
echo "----------------------------------------"
./stop.sh
STOP_STATUS=$?
echo "----------------------------------------"

if [ $STOP_STATUS -ne 0 ]; then
    echo -e "${YELLOW}⚠️  Stop script returned non-zero status, but continuing...${NC}"
fi

# Step 2: Wait before restarting
echo ""
echo -e "${CYAN}Step 2: Waiting ${WAIT_TIME} seconds before restart...${NC}"
for i in $(seq $WAIT_TIME -1 1); do
    echo -e "${YELLOW}   Starting in $i seconds...${NC}"
    sleep 1
done

# Step 3: Start the application
echo ""
echo -e "${CYAN}Step 3: Starting the application...${NC}"
echo "----------------------------------------"
./start.sh
START_STATUS=$?
echo "----------------------------------------"

# Final status
echo ""
echo -e "${BLUE}============================================${NC}"
if [ $START_STATUS -eq 0 ]; then
    echo -e "${GREEN}✅ Restart completed successfully!${NC}"
else
    echo -e "${RED}❌ Restart completed with errors${NC}"
    echo -e "${YELLOW}   Please check the status with: ./status.sh${NC}"
fi
echo -e "${BLUE}============================================${NC}"

exit $START_STATUS