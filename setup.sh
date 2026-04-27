#!/bin/bash

# ============================================
# Face Recognition Attendance System - SETUP Script
# ============================================
# This script helps with initial setup and deployment

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

# Configuration
PROJECT_NAME="Face Recognition Attendance System"
PYTHON_FILE="main_with_face_recognition.py"
REQUIRED_SCRIPTS=("start.sh" "stop.sh" "status.sh" "restart.sh")

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}   ${PROJECT_NAME}${NC}"
echo -e "${BLUE}   Initial Setup & Configuration${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# Function to check command availability
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to print section headers
print_section() {
    echo ""
    echo -e "${CYAN}=== $1 ===${NC}"
}

# Get current directory
CURRENT_DIR=$(pwd)
echo -e "${BLUE}📁 Current Directory:${NC} $CURRENT_DIR"

# Step 1: Check Python Installation
print_section "Checking Python Installation"

if command_exists python3; then
    PYTHON_CMD="python3"
    PYTHON_VERSION=$($PYTHON_CMD --version 2>&1)
    echo -e "${GREEN}✅ Python3 found: $PYTHON_VERSION${NC}"
elif command_exists python; then
    PYTHON_CMD="python"
    PYTHON_VERSION=$($PYTHON_CMD --version 2>&1)
    echo -e "${GREEN}✅ Python found: $PYTHON_VERSION${NC}"
else
    echo -e "${RED}❌ Python is not installed!${NC}"
    echo -e "${YELLOW}   Please install Python 3.7+ first${NC}"
    echo -e "${YELLOW}   Run: sudo apt-get update && sudo apt-get install python3 python3-pip${NC}"
    exit 1
fi

# Step 2: Check for main Python file
print_section "Checking Application Files"

if [ -f "$PYTHON_FILE" ]; then
    echo -e "${GREEN}✅ Found $PYTHON_FILE${NC}"
else
    echo -e "${RED}❌ $PYTHON_FILE not found!${NC}"
    echo -e "${YELLOW}   Please ensure you're in the project directory${NC}"
    echo -e "${YELLOW}   Looking in: $CURRENT_DIR${NC}"
    exit 1
fi

# Step 3: Check for required Python packages
print_section "Checking Python Dependencies"

echo -e "${BLUE}Checking for required packages...${NC}"

# List of required packages
REQUIRED_PACKAGES=(
    "flask"
    "opencv-python"
    "numpy"
    "face_recognition"
    "pymongo"
    "pytz"
)

MISSING_PACKAGES=()

for package in "${REQUIRED_PACKAGES[@]}"; do
    if $PYTHON_CMD -c "import ${package//-/_}" 2>/dev/null; then
        echo -e "   ${GREEN}✅${NC} $package"
    else
        echo -e "   ${RED}❌${NC} $package (missing)"
        MISSING_PACKAGES+=("$package")
    fi
done

if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
    echo ""
    echo -e "${YELLOW}⚠️  Missing packages detected!${NC}"
    echo -e "${YELLOW}   To install missing packages, run:${NC}"
    echo -e "${CYAN}   pip install ${MISSING_PACKAGES[*]}${NC}"
    
    read -p "Do you want to install them now? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        pip install "${MISSING_PACKAGES[@]}"
    fi
fi

# Step 4: Check and create necessary directories
print_section "Setting Up Directory Structure"

DIRECTORIES=("logs" "static" "templates" "database" "encodings")

for dir in "${DIRECTORIES[@]}"; do
    if [ -d "$dir" ]; then
        echo -e "   ${GREEN}✅${NC} Directory exists: $dir/"
    else
        mkdir -p "$dir"
        echo -e "   ${BLUE}📁${NC} Created directory: $dir/"
    fi
done

# Step 5: Check for management scripts
print_section "Checking Management Scripts"

ALL_SCRIPTS_PRESENT=true
for script in "${REQUIRED_SCRIPTS[@]}"; do
    if [ -f "$script" ]; then
        echo -e "   ${GREEN}✅${NC} $script found"
        chmod +x "$script"
    else
        echo -e "   ${RED}❌${NC} $script missing"
        ALL_SCRIPTS_PRESENT=false
    fi
done

if [ "$ALL_SCRIPTS_PRESENT" = false ]; then
    echo -e "${YELLOW}⚠️  Some management scripts are missing${NC}"
    echo -e "${YELLOW}   Please ensure all scripts are in the project directory${NC}"
fi

# Step 6: Check system dependencies
print_section "Checking System Dependencies"

# Check for required system packages
SYSTEM_PACKAGES=("build-essential" "cmake" "libopencv-dev" "python3-opencv")
MISSING_SYSTEM_PACKAGES=()

for package in "${SYSTEM_PACKAGES[@]}"; do
    if dpkg -l | grep -q "^ii.*$package"; then
        echo -e "   ${GREEN}✅${NC} $package"
    else
        echo -e "   ${YELLOW}⚠️${NC} $package (may be missing)"
        MISSING_SYSTEM_PACKAGES+=("$package")
    fi
done

if [ ${#MISSING_SYSTEM_PACKAGES[@]} -gt 0 ]; then
    echo ""
    echo -e "${YELLOW}Some system packages may be missing.${NC}"
    echo -e "${YELLOW}If you encounter issues, install them with:${NC}"
    echo -e "${CYAN}   sudo apt-get update && sudo apt-get install ${MISSING_SYSTEM_PACKAGES[*]}${NC}"
fi

# Step 7: Check network configuration
print_section "Network Configuration"

# Get IP addresses
echo -e "${BLUE}Available network interfaces:${NC}"
ip -4 addr show | grep -E "inet " | awk '{print "   • " $NF ": " $2}' | cut -d/ -f1

# Check if port 5000 is available
if lsof -Pi :5000 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  Port 5000 is currently in use${NC}"
    PID=$(lsof -ti:5000)
    echo -e "${YELLOW}   Process $PID is using the port${NC}"
else
    echo -e "${GREEN}✅ Port 5000 is available${NC}"
fi

# Step 8: Create configuration file
print_section "Creating Configuration File"

CONFIG_FILE="app_config.sh"
if [ ! -f "$CONFIG_FILE" ]; then
    cat > "$CONFIG_FILE" << 'EOF'
#!/bin/bash
# Configuration file for Face Recognition Attendance System

# Application settings
export APP_HOST="0.0.0.0"
export APP_PORT="5000"
export APP_DEBUG="False"

# MongoDB settings (update if using MongoDB)
export MONGO_URI="mongodb://localhost:27017/"
export MONGO_DB="attendance_db"

# Timezone setting
export TZ="Asia/Kolkata"

# Camera settings
export CAMERA_INDEX="0"

# Recognition threshold
export RECOGNITION_THRESHOLD="0.6"

echo "Configuration loaded successfully!"
EOF
    chmod +x "$CONFIG_FILE"
    echo -e "${GREEN}✅ Created $CONFIG_FILE${NC}"
    echo -e "${YELLOW}   Edit this file to customize your settings${NC}"
else
    echo -e "${GREEN}✅ Configuration file already exists${NC}"
fi

# Step 9: Create systemd service file (optional)
print_section "Systemd Service Configuration"

SERVICE_FILE="/etc/systemd/system/face-attendance.service"
echo -e "${BLUE}Systemd service setup (for auto-start on boot)${NC}"

if [ -f "$SERVICE_FILE" ]; then
    echo -e "${GREEN}✅ Systemd service already configured${NC}"
else
    echo -e "${YELLOW}To enable auto-start on boot, create a systemd service:${NC}"
    echo ""
    echo -e "${CYAN}sudo nano $SERVICE_FILE${NC}"
    echo ""
    echo "Add the following content (update paths as needed):"
    echo "----------------------------------------"
    cat << EOF
[Unit]
Description=Face Recognition Attendance System
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$CURRENT_DIR
Environment="PATH=/usr/bin:/usr/local/bin"
ExecStart=/usr/bin/python3 $CURRENT_DIR/$PYTHON_FILE
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
    echo "----------------------------------------"
fi

# Step 10: Quick start guide
print_section "Quick Start Guide"

echo -e "${GREEN}Setup complete! Here's how to use the application:${NC}"
echo ""
echo -e "${BLUE}1. Start the application:${NC}"
echo -e "   ${CYAN}./start.sh${NC}"
echo ""
echo -e "${BLUE}2. Check status:${NC}"
echo -e "   ${CYAN}./status.sh${NC}"
echo ""
echo -e "${BLUE}3. View logs:${NC}"
echo -e "   ${CYAN}tail -f app.log${NC}"
echo ""
echo -e "${BLUE}4. Stop the application:${NC}"
echo -e "   ${CYAN}./stop.sh${NC}"
echo ""
echo -e "${BLUE}5. Restart the application:${NC}"
echo -e "   ${CYAN}./restart.sh${NC}"

# Step 11: Test run option
echo ""
print_section "Test Run"

read -p "Do you want to start the application now? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${BLUE}Starting the application...${NC}"
    ./start.sh
else
    echo -e "${YELLOW}You can start the application later with: ./start.sh${NC}"
fi

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}   Setup completed successfully!${NC}"
echo -e "${GREEN}============================================${NC}"