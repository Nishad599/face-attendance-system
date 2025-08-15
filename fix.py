#!/usr/bin/env python3
"""
navbar_fix.py - Fix navbar format and navigation issues
"""

import os
import shutil
from datetime import datetime

def create_backup(filename):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{filename}.backup_{timestamp}"
    shutil.copy2(filename, backup_name)
    print(f"✅ Backup created: {backup_name}")
    return backup_name

def update_attendance_template():
    """Update attendance.html template with proper navbar and navigation"""
    template_file = "templates/attendance.html"
    
    if not os.path.exists(template_file):
        print("❌ attendance.html template not found!")
        return False
    
    backup_file = create_backup(template_file)
    
    try:
        with open(template_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check if the template needs updating
        if "CDAC" not in content or "Centre for Development of Advanced Computing" not in content:
            print("🔧 Updating attendance template with proper navbar...")
            
            # Create new attendance template with proper navbar
            new_template = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Live Attendance Detection - CDAC</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body {
            background: linear-gradient(135deg, #0891b2 0%, #0e7490 50%, #155e75 100%);
            min-height: 100vh;
        }
        .navbar-gradient {
            background: linear-gradient(135deg, #0891b2, #0e7490);
        }
        .detection-card {
            background: white;
            border-radius: 16px;
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.1);
        }
    </style>
</head>
<body>
    <!-- CDAC Header -->
    <nav class="bg-white shadow-sm">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div class="flex justify-between items-center h-16">
                <!-- CDAC Logo and Title -->
                <div class="flex items-center space-x-4">
                    <div class="w-12 h-12 bg-blue-600 rounded-full flex items-center justify-center">
                        <span class="text-white font-bold text-lg">🏛️</span>
                    </div>
                    <div>
                        <h1 class="text-lg font-bold text-gray-800">CDAC</h1>
                        <p class="text-sm text-gray-600">CENTRE FOR DEVELOPMENT OF ADVANCED COMPUTING</p>
                    </div>
                </div>
                
                <!-- Navigation Links -->
                <div class="flex items-center space-x-6">
                    <a href="#" onclick="goHome()" class="text-gray-600 hover:text-blue-600 transition-colors">
                        🏠 HOME
                    </a>
                    <a href="#" class="text-gray-600 hover:text-blue-600 transition-colors">
                        ABOUT US
                    </a>
                    <a href="#" class="text-gray-600 hover:text-blue-600 transition-colors">
                        CONTACT US
                    </a>
                    <button onclick="logout()" class="bg-red-500 hover:bg-red-600 text-white px-4 py-2 rounded-lg transition-colors">
                        🚪 Logout
                    </button>
                </div>
            </div>
        </div>
    </nav>

    <!-- Main Content -->
    <div class="navbar-gradient text-white py-6">
        <div class="max-w-7xl mx-auto px-4 text-center">
            <h1 class="text-3xl font-bold mb-2">📹 Live Attendance Detection</h1>
            <p class="text-lg opacity-90">Automatic face recognition attendance system</p>
        </div>
    </div>

    <!-- Attendance Detection Interface -->
    <div class="max-w-7xl mx-auto px-4 py-8">
        <div class="grid lg:grid-cols-2 gap-8">
            
            <!-- Camera Feed Card -->
            <div class="detection-card p-6">
                <div class="text-center mb-6">
                    <h2 class="text-2xl font-bold text-gray-800 mb-2">📷 Camera Feed</h2>
                    <p class="text-gray-600">Position yourself in front of the camera</p>
                </div>
                
                <div class="relative">
                    <video id="video" autoplay muted class="w-full h-64 bg-gray-900 rounded-lg object-cover"></video>
                    <canvas id="canvas" style="display: none;"></canvas>
                </div>
                
                <div class="mt-6 flex justify-center space-x-4">
                    <button id="startBtn" onclick="startCamera()" class="bg-green-500 hover:bg-green-600 text-white px-6 py-3 rounded-lg font-semibold transition-colors">
                        ▶️ Start Detection
                    </button>
                    <button id="stopBtn" onclick="stopCamera()" class="bg-red-500 hover:bg-red-600 text-white px-6 py-3 rounded-lg font-semibold transition-colors" disabled>
                        ⏹️ Stop Detection
                    </button>
                    <button id="captureBtn" onclick="captureFrame()" class="bg-blue-500 hover:bg-blue-600 text-white px-6 py-3 rounded-lg font-semibold transition-colors" disabled>
                        📸 Manual Capture
                    </button>
                </div>
            </div>
            
            <!-- Detection Results Card -->
            <div class="detection-card p-6">
                <div class="text-center mb-6">
                    <h2 class="text-2xl font-bold text-gray-800 mb-2">📊 Detection Results</h2>
                    <p class="text-gray-600">Real-time attendance marking</p>
                </div>
                
                <div id="detectionResults" class="space-y-4">
                    <div class="text-center text-gray-500 py-8">
                        <p class="text-lg">🔍 Waiting for face detection...</p>
                        <p class="text-sm">Stand in front of the camera to mark attendance</p>
                    </div>
                </div>
                
                <div id="statusMessage" class="mt-4 p-4 rounded-lg bg-gray-100 text-center">
                    <p class="text-gray-600">Ready to detect faces</p>
                </div>
            </div>
        </div>
        
        <!-- User Instructions -->
        <div class="detection-card p-6 mt-8">
            <h3 class="text-xl font-bold text-gray-800 mb-4">📋 Instructions</h3>
            <div class="grid md:grid-cols-3 gap-6 text-center">
                <div class="p-4">
                    <div class="text-3xl mb-2">👤</div>
                    <h4 class="font-semibold text-gray-800 mb-2">Position Yourself</h4>
                    <p class="text-gray-600 text-sm">Stand directly in front of the camera with good lighting</p>
                </div>
                <div class="p-4">
                    <div class="text-3xl mb-2">📷</div>
                    <h4 class="font-semibold text-gray-800 mb-2">Look at Camera</h4>
                    <p class="text-gray-600 text-sm">Face the camera directly for best recognition accuracy</p>
                </div>
                <div class="p-4">
                    <div class="text-3xl mb-2">✅</div>
                    <h4 class="font-semibold text-gray-800 mb-2">Attendance Marked</h4>
                    <p class="text-gray-600 text-sm">System will automatically mark your attendance when recognized</p>
                </div>
            </div>
        </div>
    </div>

    <script>
        let stream = null;
        let detectionInterval = null;
        
        // Navigation functions
        async function goHome() {
            try {
                // Check user type and redirect appropriately
                const response = await fetch('/api/session/status');
                const sessionData = await response.json();
                
                if (sessionData.authenticated) {
                    if (sessionData.user_type === 'admin') {
                        window.location.href = '/dashboard';
                    } else {
                        // User stays on attendance page
                        showMessage('You are already on your home page!', 'info');
                    }
                } else {
                    window.location.href = '/login';
                }
            } catch (error) {
                console.error('Navigation error:', error);
                window.location.href = '/login';
            }
        }
        
        async function logout() {
            try {
                const response = await fetch('/api/logout', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                });
                
                const result = await response.json();
                if (result.success) {
                    window.location.href = '/login';
                } else {
                    alert('Logout failed: ' + result.message);
                }
            } catch (error) {
                console.error('Logout error:', error);
                window.location.href = '/login';
            }
        }
        
        // Camera functions
        async function startCamera() {
            try {
                stream = await navigator.mediaDevices.getUserMedia({ video: true });
                document.getElementById('video').srcObject = stream;
                
                document.getElementById('startBtn').disabled = true;
                document.getElementById('stopBtn').disabled = false;
                document.getElementById('captureBtn').disabled = false;
                
                showMessage('Camera started successfully!', 'success');
                
                // Start automatic detection every 3 seconds
                detectionInterval = setInterval(captureFrame, 3000);
                
            } catch (error) {
                console.error('Camera error:', error);
                showMessage('Camera access failed: ' + error.message, 'error');
            }
        }
        
        function stopCamera() {
            if (stream) {
                stream.getTracks().forEach(track => track.stop());
                stream = null;
            }
            
            if (detectionInterval) {
                clearInterval(detectionInterval);
                detectionInterval = null;
            }
            
            document.getElementById('startBtn').disabled = false;
            document.getElementById('stopBtn').disabled = true;
            document.getElementById('captureBtn').disabled = true;
            
            showMessage('Camera stopped', 'info');
        }
        
        async function captureFrame() {
            const video = document.getElementById('video');
            const canvas = document.getElementById('canvas');
            const context = canvas.getContext('2d');
            
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            context.drawImage(video, 0, 0);
            
            const imageData = canvas.toDataURL('image/jpeg', 0.8);
            
            try {
                showMessage('Processing...', 'info');
                
                const response = await fetch('/api/detect_attendance', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ image_data: imageData })
                });
                
                const result = await response.json();
                displayDetectionResults(result);
                
            } catch (error) {
                console.error('Detection error:', error);
                showMessage('Detection failed: ' + error.message, 'error');
            }
        }
        
        function displayDetectionResults(result) {
            const resultsDiv = document.getElementById('detectionResults');
            
            if (result.success && result.recognized_students.length > 0) {
                let html = '';
                result.recognized_students.forEach(student => {
                    const statusColor = student.status === 'marked' ? 'green' : 'orange';
                    html += `
                        <div class="p-4 border-l-4 border-${statusColor}-500 bg-${statusColor}-50 rounded-lg">
                            <div class="flex items-center justify-between">
                                <div>
                                    <h4 class="font-semibold text-gray-800">${student.name}</h4>
                                    <p class="text-sm text-gray-600">Confidence: ${(student.confidence * 100).toFixed(1)}%</p>
                                </div>
                                <div class="text-right">
                                    <span class="px-3 py-1 rounded-full text-sm font-medium bg-${statusColor}-100 text-${statusColor}-800">
                                        ${student.status === 'marked' ? '✅ Marked' : '⚠️ Already Present'}
                                    </span>
                                </div>
                            </div>
                            <p class="text-sm text-gray-600 mt-2">${student.message}</p>
                        </div>
                    `;
                });
                resultsDiv.innerHTML = html;
                showMessage(result.message, 'success');
            } else {
                resultsDiv.innerHTML = `
                    <div class="text-center text-gray-500 py-8">
                        <p class="text-lg">❌ ${result.message || 'No faces detected'}</p>
                        <p class="text-sm">Please position yourself properly in front of the camera</p>
                    </div>
                `;
                showMessage(result.message || 'No faces detected', 'warning');
            }
        }
        
        function showMessage(message, type) {
            const statusDiv = document.getElementById('statusMessage');
            const colors = {
                success: 'bg-green-100 text-green-800',
                error: 'bg-red-100 text-red-800',
                warning: 'bg-yellow-100 text-yellow-800',
                info: 'bg-blue-100 text-blue-800'
            };
            
            statusDiv.className = `mt-4 p-4 rounded-lg text-center ${colors[type] || colors.info}`;
            statusDiv.innerHTML = `<p>${message}</p>`;
        }
        
        // Initialize page
        document.addEventListener('DOMContentLoaded', function() {
            showMessage('Click "Start Detection" to begin attendance marking', 'info');
        });
        
        // Cleanup on page unload
        window.addEventListener('beforeunload', function() {
            stopCamera();
        });
    </script>
</body>
</html>'''
            
            # Write the new template
            with open(template_file, 'w', encoding='utf-8') as f:
                f.write(new_template)
            
            print("✅ Updated attendance template with CDAC navbar")
            return True
        else:
            print("ℹ️  Template already has CDAC navbar")
            return True
            
    except Exception as e:
        print(f"❌ Error updating template: {e}")
        if backup_file:
            shutil.copy2(backup_file, template_file)
            print("🔄 Restored backup")
        return False

def add_navigation_route():
    """Add proper navigation handling to backend"""
    main_file = "main_with_face_recognition.py"
    
    if not os.path.exists(main_file):
        print("❌ Main file not found!")
        return False
    
    backup_file = create_backup(main_file)
    
    try:
        with open(main_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Add navigation endpoint if not exists
        navigation_route = '''
@app.get("/api/navigation/home")
async def navigate_home(session: Optional[Dict[str, Any]] = Depends(get_current_session)):
    """Smart home navigation based on user type"""
    from fastapi.responses import RedirectResponse
    
    if not session:
        return {"success": False, "redirect_url": "/login"}
    
    user_type = session.get("user_type", "")
    
    if user_type == "admin":
        return {"success": True, "redirect_url": "/dashboard", "message": "Redirecting to admin dashboard"}
    elif user_type == "user":
        return {"success": True, "redirect_url": "/attendance", "message": "You are already on your home page"}
    else:
        return {"success": False, "redirect_url": "/login", "message": "Invalid session"}
'''
        
        if "@app.get(\"/api/navigation/home\")" not in content:
            # Insert before the bulk export route
            insert_point = "@app.post(\"/api/attendance/bulk-export\")"
            if insert_point in content:
                content = content.replace(insert_point, navigation_route + "\n" + insert_point)
                print("✅ Added navigation route")
        
        # Write updated content
        with open(main_file, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return True
        
    except Exception as e:
        print(f"❌ Error adding navigation: {e}")
        if backup_file:
            shutil.copy2(backup_file, main_file)
        return False

def main():
    print("🔧 Navbar & Navigation Fix")
    print("="*40)
    
    success = True
    
    # Update attendance template
    if update_attendance_template():
        print("✅ Template updated successfully")
    else:
        print("❌ Template update failed")
        success = False
    
    # Add navigation handling
    if add_navigation_route():
        print("✅ Navigation route added")
    else:
        print("❌ Navigation route failed")
        success = False
    
    print("\n" + "="*40)
    if success:
        print("✅ SUCCESS! Navbar and navigation fixed")
        print("\n🔧 Changes made:")
        print("   • Updated attendance page with proper CDAC navbar")
        print("   • Fixed home/dashboard navigation for users")
        print("   • Added proper logout functionality")
        print("   • Improved camera interface")
        
        print("\n🚀 Restart your server:")
        print("   python main_with_face_recognition.py")
        
        print("\n🎯 Navigation behavior:")
        print("   • Admin clicking 'Home' → Dashboard")
        print("   • User clicking 'Home' → Stays on attendance page")
        print("   • Proper logout for both user types")
    else:
        print("❌ Some fixes failed. Check the errors above.")

if __name__ == "__main__":
    main()
