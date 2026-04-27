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