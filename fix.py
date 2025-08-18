#!/usr/bin/env python3
"""
HTML Navbar Standardizer for CDAC Face Recognition Attendance System
This script standardizes the navbar across all HTML pages with improved CDAC branding.
"""

import os
import re
from pathlib import Path

# Improved navbar HTML with better CDAC branding and logo fallback
STANDARD_NAVBAR_HTML = '''<!-- CDAC Header -->
<header class="cdac-header">
    <div class="cdac-container">
        <div class="cdac-logo-section">
            <div class="cdac-logo-wrapper">
                <img src="/static/images/cdac-logo.png" alt="CDAC Logo" class="cdac-logo-img" 
                     onerror="this.style.display='none'; document.getElementById('cdac-fallback-logo').style.display='flex';">
                <div id="cdac-fallback-logo" class="cdac-fallback-logo" style="display: none;">
                    <span class="fallback-icon">🏛️</span>
                </div>
            </div>
            <div class="cdac-text-section">
                <div class="cdac-hindi">भारत संगणन विकास केंद्र</div>
                <div class="cdac-english">CENTRE FOR DEVELOPMENT OF ADVANCED COMPUTING</div>
                <div class="cdac-tagline">Face Recognition Attendance System</div>
            </div>
        </div>
        
        <nav class="cdac-navigation">
            <a href="/dashboard" class="nav-link">
                <span class="nav-icon">🏠</span>
                <span class="nav-text">HOME</span>
            </a>
            <a href="/about" class="nav-link">
                <span class="nav-icon">ℹ️</span>
                <span class="nav-text">ABOUT</span>
            </a>
            <a href="/contact" class="nav-link">
                <span class="nav-icon">📞</span>
                <span class="nav-text">CONTACT</span>
            </a>
        </nav>
        
        <div class="cdac-user-section">
            <button class="logout-button" onclick="performLogout()" title="Logout">
                <span class="logout-icon">🚪</span>
                <span class="logout-text">Logout</span>
            </button>
        </div>
    </div>
</header>'''

# Improved navbar CSS with better styling and CDAC prominence
STANDARD_NAVBAR_CSS = '''/* ===== CDAC NAVBAR STYLES ===== */
.cdac-header {
    background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);
    border-bottom: 4px solid #0066cc;
    box-shadow: 0 4px 12px rgba(0, 102, 204, 0.15);
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    width: 100%;
    z-index: 10000;
    padding: 12px 0;
}

.cdac-container {
    max-width: 1400px;
    margin: 0 auto;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0 24px;
    gap: 20px;
}

/* Logo Section */
.cdac-logo-section {
    display: flex;
    align-items: center;
    gap: 16px;
    flex: 1;
}

.cdac-logo-wrapper {
    background: linear-gradient(135deg, #0066cc 0%, #004499 100%);
    padding: 10px;
    border-radius: 12px;
    box-shadow: 0 4px 8px rgba(0, 102, 204, 0.2);
    display: flex;
    align-items: center;
    justify-content: center;
    min-width: 60px;
    min-height: 60px;
    position: relative;
}

.cdac-logo-img {
    height: 50px;
    width: 50px;
    object-fit: contain;
    display: block;
    /* Remove filter to show original logo colors, add white background if needed */
    background: rgba(255, 255, 255, 0.1);
    border-radius: 8px;
    padding: 4px;
}

.cdac-fallback-logo {
    display: none;
    align-items: center;
    justify-content: center;
    width: 50px;
    height: 50px;
}

.fallback-icon {
    font-size: 32px;
    color: white;
    text-shadow: 0 2px 4px rgba(0, 0, 0, 0.3);
}

.cdac-text-section {
    display: flex;
    flex-direction: column;
    gap: 2px;
}

.cdac-hindi {
    font-size: 16px;
    font-weight: 700;
    color: #0066cc;
    line-height: 1.2;
    letter-spacing: 0.5px;
}

.cdac-english {
    font-size: 13px;
    font-weight: 600;
    color: #004499;
    line-height: 1.2;
    letter-spacing: 0.3px;
}

.cdac-tagline {
    font-size: 11px;
    font-weight: 500;
    color: #666;
    line-height: 1.2;
    font-style: italic;
}

/* Navigation */
.cdac-navigation {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: 0 0 auto;
}

.nav-link {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 10px 16px;
    color: #333;
    text-decoration: none;
    font-size: 14px;
    font-weight: 600;
    border-radius: 8px;
    transition: all 0.3s ease;
    white-space: nowrap;
    border: 2px solid transparent;
}

.nav-link:hover {
    background: linear-gradient(135deg, #e6f3ff 0%, #cce6ff 100%);
    color: #0066cc;
    border-color: #b3d9ff;
    transform: translateY(-2px);
    box-shadow: 0 4px 8px rgba(0, 102, 204, 0.15);
    text-decoration: none;
}

.nav-icon {
    font-size: 16px;
}

.nav-text {
    font-size: 13px;
    letter-spacing: 0.5px;
}

/* User Section */
.cdac-user-section {
    flex: 0 0 auto;
}

.logout-button {
    display: flex;
    align-items: center;
    gap: 8px;
    background: linear-gradient(135deg, #dc3545 0%, #c82333 100%);
    color: white;
    border: none;
    padding: 12px 20px;
    border-radius: 10px;
    cursor: pointer;
    font-weight: 700;
    font-size: 14px;
    box-shadow: 0 4px 12px rgba(220, 53, 69, 0.3);
    transition: all 0.3s ease;
    letter-spacing: 0.3px;
}

.logout-button:hover {
    background: linear-gradient(135deg, #c82333 0%, #a71e2a 100%);
    transform: translateY(-3px);
    box-shadow: 0 6px 16px rgba(220, 53, 69, 0.4);
}

.logout-button:active {
    transform: translateY(-1px);
}

.logout-icon {
    font-size: 16px;
}

.logout-text {
    font-size: 13px;
}

/* Body adjustment */
body {
    margin: 0;
    padding: 0;
    padding-top: 90px !important;
    font-family: 'Inter', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
}

/* Mobile Responsive Design */
@media (max-width: 1024px) {
    .cdac-container {
        padding: 0 16px;
        gap: 12px;
    }
    
    .cdac-logo-img {
        height: 42px;
        width: 42px;
    }
    
    .fallback-icon {
        font-size: 28px;
    }
    
    .cdac-hindi {
        font-size: 14px;
    }
    
    .cdac-english {
        font-size: 12px;
    }
    
    .nav-text {
        font-size: 12px;
    }
}

@media (max-width: 768px) {
    .cdac-header {
        position: relative;
        padding: 16px 0;
    }
    
    body {
        padding-top: 0 !important;
    }
    
    .cdac-container {
        flex-direction: column;
        gap: 16px;
        text-align: center;
        padding: 0 12px;
    }
    
    .cdac-logo-section {
        justify-content: center;
    }
    
    .cdac-navigation {
        order: 3;
        flex-wrap: wrap;
        justify-content: center;
        gap: 6px;
    }
    
    .nav-link {
        padding: 8px 12px;
        font-size: 12px;
    }
    
    .nav-icon {
        font-size: 14px;
    }
    
    .nav-text {
        font-size: 11px;
    }
    
    .cdac-user-section {
        order: 2;
    }
    
    .logout-button {
        padding: 10px 16px;
        font-size: 13px;
    }
    
    .cdac-logo-img {
        height: 36px;
        width: 36px;
    }
    
    .fallback-icon {
        font-size: 24px;
    }
    
    .cdac-hindi {
        font-size: 13px;
    }
    
    .cdac-english {
        font-size: 11px;
    }
    
    .cdac-tagline {
        font-size: 10px;
    }
}

@media (max-width: 480px) {
    .cdac-text-section {
        gap: 1px;
    }
    
    .cdac-hindi {
        font-size: 12px;
    }
    
    .cdac-english {
        font-size: 10px;
    }
    
    .cdac-tagline {
        font-size: 9px;
    }
    
    .nav-link {
        padding: 6px 10px;
    }
    
    .nav-text {
        font-size: 10px;
    }
    
    .logout-button {
        padding: 8px 14px;
        font-size: 12px;
    }
}'''

def clean_existing_navbar_styles(content):
    """Remove existing CDAC header styles and elements"""
    # Remove existing CDAC header CSS blocks
    content = re.sub(r'/\*\s*CDAC Header Styles.*?\*/.*?(?=<\/style>|/\*|\s*</head>)', '', content, flags=re.DOTALL)
    content = re.sub(r'\.cdac-header\s*{.*?}', '', content, flags=re.DOTALL)
    content = re.sub(r'\.cdac-container\s*{.*?}', '', content, flags=re.DOTALL)
    content = re.sub(r'\.cdac-logo.*?{.*?}', '', content, flags=re.DOTALL)
    content = re.sub(r'\.logo-.*?{.*?}', '', content, flags=re.DOTALL)
    content = re.sub(r'\.hindi-text.*?{.*?}', '', content, flags=re.DOTALL)
    content = re.sub(r'\.english-text.*?{.*?}', '', content, flags=re.DOTALL)
    content = re.sub(r'\.cdac-nav.*?{.*?}', '', content, flags=re.DOTALL)
    content = re.sub(r'\.nav-item.*?{.*?}', '', content, flags=re.DOTALL)
    content = re.sub(r'\.logout-btn.*?{.*?}', '', content, flags=re.DOTALL)
    
    # Remove existing body padding styles that conflict
    content = re.sub(r'body\s*{[^}]*padding-top[^}]*}', '', content)
    
    # Clean up multiple empty lines
    content = re.sub(r'\n\s*\n\s*\n', '\n\n', content)
    
    return content

def remove_existing_navbar_html(content):
    """Remove existing CDAC header HTML"""
    # Remove existing CDAC header divs
    content = re.sub(r'<!--\s*CDAC Header\s*-->.*?</div>\s*</div>', '', content, flags=re.DOTALL)
    content = re.sub(r'<div class="cdac-header">.*?</div>\s*</div>', '', content, flags=re.DOTALL)
    content = re.sub(r'<header class="cdac-header">.*?</header>', '', content, flags=re.DOTALL)
    
    # Remove standalone logout buttons
    content = re.sub(r'<!--\s*Logout Button\s*-->.*?</button>', '', content, flags=re.DOTALL)
    content = re.sub(r'<button[^>]*class="logout-btn"[^>]*>.*?</button>', '', content, flags=re.DOTALL)
    
    return content

def update_html_file(file_path):
    """Update a single HTML file with the standardized navbar"""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            content = file.read()
        
        # Clean existing navbar content
        content = clean_existing_navbar_styles(content)
        content = remove_existing_navbar_html(content)
        
        # Add new CSS styles before </style> or create style section
        if '</style>' in content:
            content = content.replace('</style>', f'\n{STANDARD_NAVBAR_CSS}\n</style>')
        elif '</head>' in content:
            style_section = f'<style>\n{STANDARD_NAVBAR_CSS}\n</style>'
            content = content.replace('</head>', f'{style_section}\n</head>')
        
        # Add navbar HTML after <body> tag
        if '<body>' in content:
            content = content.replace('<body>', f'<body>\n{STANDARD_NAVBAR_HTML}\n')
        elif '<body' in content and '>' in content:
            # Handle body tags with attributes
            body_match = re.search(r'<body[^>]*>', content)
            if body_match:
                body_tag = body_match.group(0)
                content = content.replace(body_tag, f'{body_tag}\n{STANDARD_NAVBAR_HTML}\n')
        
        # Write updated content
        with open(file_path, 'w', encoding='utf-8') as file:
            file.write(content)
        
        return True, "Successfully updated"
        
    except Exception as e:
        return False, f"Error: {str(e)}"

def setup_static_directory():
    """Create static directory structure for logo"""
    try:
        static_dir = Path.cwd() / 'static' / 'images'
        static_dir.mkdir(parents=True, exist_ok=True)
        print(f"✅ Created directory: {static_dir}")
        
        # Check if logo already exists
        logo_path = static_dir / 'cdac-logo.png'
        if logo_path.exists():
            print(f"🖼️  CDAC logo found: {logo_path}")
            print("   Logo will be displayed in navbar!")
        else:
            print(f"⚠️  CDAC logo not found at: {logo_path}")
            print("   Fallback icon (🏛️) will be shown until logo is added")
        
        # Create a placeholder file with instructions
        readme_path = static_dir / 'README_LOGO.txt'
        with open(readme_path, 'w') as f:
            f.write("""CDAC LOGO SETUP INSTRUCTIONS
================================

1. Place your official CDAC logo in this directory as: cdac-logo.png
2. Recommended specifications:
   - Size: 256x256px minimum (square format works best)
   - Format: PNG (for transparency) or JPG
   - Quality: High resolution for crisp display

3. Logo will be automatically displayed in the navbar
4. If logo file is missing, a fallback icon (🏛️) will be shown

5. To test: Open any HTML page and check the top-left navbar area

Need the official CDAC logo? Contact your system administrator.
""")
        print(f"📝 Created setup guide: {readme_path}")
        return True
    except Exception as e:
        print(f"❌ Failed to create static directory: {e}")
        return False

def create_new_pages():
    """Create About Us and Contact Us pages"""
    try:
        current_dir = Path.cwd()
        
        # About Us page content
        about_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>About Us - CDAC Face Recognition Attendance System</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        /* ===== CDAC NAVBAR STYLES ===== */
        .cdac-header {
            background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);
            border-bottom: 4px solid #0066cc;
            box-shadow: 0 4px 12px rgba(0, 102, 204, 0.15);
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            width: 100%;
            z-index: 10000;
            padding: 12px 0;
        }

        .cdac-container {
            max-width: 1400px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0 24px;
            gap: 20px;
        }

        /* Logo Section */
        .cdac-logo-section {
            display: flex;
            align-items: center;
            gap: 16px;
            flex: 1;
        }

        .cdac-logo-wrapper {
            background: linear-gradient(135deg, #0066cc 0%, #004499 100%);
            padding: 10px;
            border-radius: 12px;
            box-shadow: 0 4px 8px rgba(0, 102, 204, 0.2);
            display: flex;
            align-items: center;
            justify-content: center;
            min-width: 60px;
            min-height: 60px;
            position: relative;
        }

        .cdac-logo-img {
            height: 50px;
            width: 50px;
            object-fit: contain;
            display: block;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 8px;
            padding: 4px;
        }

        .cdac-fallback-logo {
            display: none;
            align-items: center;
            justify-content: center;
            width: 50px;
            height: 50px;
        }

        .fallback-icon {
            font-size: 32px;
            color: white;
            text-shadow: 0 2px 4px rgba(0, 0, 0, 0.3);
        }

        .cdac-text-section {
            display: flex;
            flex-direction: column;
            gap: 2px;
        }

        .cdac-hindi {
            font-size: 16px;
            font-weight: 700;
            color: #0066cc;
            line-height: 1.2;
            letter-spacing: 0.5px;
        }

        .cdac-english {
            font-size: 13px;
            font-weight: 600;
            color: #004499;
            line-height: 1.2;
            letter-spacing: 0.3px;
        }

        .cdac-tagline {
            font-size: 11px;
            font-weight: 500;
            color: #666;
            line-height: 1.2;
            font-style: italic;
        }

        /* Navigation */
        .cdac-navigation {
            display: flex;
            align-items: center;
            gap: 8px;
            flex: 0 0 auto;
        }

        .nav-link {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 10px 16px;
            color: #333;
            text-decoration: none;
            font-size: 14px;
            font-weight: 600;
            border-radius: 8px;
            transition: all 0.3s ease;
            white-space: nowrap;
            border: 2px solid transparent;
        }

        .nav-link:hover {
            background: linear-gradient(135deg, #e6f3ff 0%, #cce6ff 100%);
            color: #0066cc;
            border-color: #b3d9ff;
            transform: translateY(-2px);
            box-shadow: 0 4px 8px rgba(0, 102, 204, 0.15);
            text-decoration: none;
        }

        .nav-link.active {
            background: linear-gradient(135deg, #0066cc 0%, #004499 100%);
            color: white;
            border-color: #0066cc;
        }

        .nav-icon {
            font-size: 16px;
        }

        .nav-text {
            font-size: 13px;
            letter-spacing: 0.5px;
        }

        /* User Section */
        .cdac-user-section {
            flex: 0 0 auto;
        }

        .logout-button {
            display: flex;
            align-items: center;
            gap: 8px;
            background: linear-gradient(135deg, #dc3545 0%, #c82333 100%);
            color: white;
            border: none;
            padding: 12px 20px;
            border-radius: 10px;
            cursor: pointer;
            font-weight: 700;
            font-size: 14px;
            box-shadow: 0 4px 12px rgba(220, 53, 69, 0.3);
            transition: all 0.3s ease;
            letter-spacing: 0.3px;
        }

        .logout-button:hover {
            background: linear-gradient(135deg, #c82333 0%, #a71e2a 100%);
            transform: translateY(-3px);
            box-shadow: 0 6px 16px rgba(220, 53, 69, 0.4);
        }

        .logout-icon {
            font-size: 16px;
        }

        .logout-text {
            font-size: 13px;
        }

        /* Body adjustment */
        body {
            margin: 0;
            padding: 0;
            padding-top: 90px !important;
            font-family: 'Inter', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%);
            min-height: 100vh;
        }

        /* Page Content Styles */
        .page-hero {
            background: linear-gradient(135deg, #0066cc 0%, #004499 50%, #003366 100%);
            color: white;
            padding: 60px 0;
            text-align: center;
            position: relative;
            overflow: hidden;
        }

        .hero-content {
            position: relative;
            z-index: 2;
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 20px;
        }

        .hero-title {
            font-size: 3.5rem;
            font-weight: 800;
            margin-bottom: 1rem;
            text-shadow: 0 4px 8px rgba(0, 0, 0, 0.3);
        }

        .hero-subtitle {
            font-size: 1.5rem;
            opacity: 0.9;
            margin-bottom: 2rem;
            font-weight: 300;
        }

        .main-content {
            max-width: 1200px;
            margin: 0 auto;
            padding: 60px 20px;
        }

        .content-section {
            background: white;
            border-radius: 20px;
            padding: 40px;
            margin-bottom: 40px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1);
            border: 1px solid #e2e8f0;
        }

        .section-title {
            font-size: 2.5rem;
            color: #0066cc;
            margin-bottom: 1.5rem;
            font-weight: 700;
            position: relative;
            padding-bottom: 15px;
        }

        .section-title::after {
            content: '';
            position: absolute;
            bottom: 0;
            left: 0;
            width: 60px;
            height: 4px;
            background: linear-gradient(135deg, #0066cc, #004499);
            border-radius: 2px;
        }

        .back-button {
            background: linear-gradient(135deg, #0066cc 0%, #004499 100%);
            color: white;
            padding: 15px 30px;
            border: none;
            border-radius: 10px;
            font-size: 1.1rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 30px;
        }

        .back-button:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(0, 102, 204, 0.3);
            text-decoration: none;
            color: white;
        }

        /* Mobile Responsive */
        @media (max-width: 768px) {
            .cdac-header { position: relative; padding: 16px 0; }
            body { padding-top: 0 !important; }
            .cdac-container { flex-direction: column; gap: 16px; text-align: center; padding: 0 12px; }
            .hero-title { font-size: 2.5rem; }
            .hero-subtitle { font-size: 1.2rem; }
            .content-section { padding: 25px; margin-bottom: 25px; }
            .section-title { font-size: 2rem; }
        }
    </style>
</head>
<body>
    <!-- CDAC Header -->
    <header class="cdac-header">
        <div class="cdac-container">
            <div class="cdac-logo-section">
                <div class="cdac-logo-wrapper">
                    <img src="/static/images/cdac-logo.png" alt="CDAC Logo" class="cdac-logo-img" 
                         onerror="this.style.display='none'; document.getElementById('cdac-fallback-logo').style.display='flex';">
                    <div id="cdac-fallback-logo" class="cdac-fallback-logo" style="display: none;">
                        <span class="fallback-icon">🏛️</span>
                    </div>
                </div>
                <div class="cdac-text-section">
                    <div class="cdac-hindi">भारत संगणन विकास केंद्र</div>
                    <div class="cdac-english">CENTRE FOR DEVELOPMENT OF ADVANCED COMPUTING</div>
                    <div class="cdac-tagline">Face Recognition Attendance System</div>
                </div>
            </div>
            
            <nav class="cdac-navigation">
                <a href="/dashboard" class="nav-link">
                    <span class="nav-icon">🏠</span>
                    <span class="nav-text">HOME</span>
                </a>
                <a href="/about" class="nav-link active">
                    <span class="nav-icon">ℹ️</span>
                    <span class="nav-text">ABOUT</span>
                </a>
                <a href="/contact" class="nav-link">
                    <span class="nav-icon">📞</span>
                    <span class="nav-text">CONTACT</span>
                </a>
            </nav>
            
            <div class="cdac-user-section">
                <button class="logout-button" onclick="performLogout()" title="Logout">
                    <span class="logout-icon">🚪</span>
                    <span class="logout-text">Logout</span>
                </button>
            </div>
        </div>
    </header>

    <!-- Hero Section -->
    <section class="page-hero">
        <div class="hero-content">
            <h1 class="hero-title">About CDAC</h1>
            <p class="hero-subtitle">Pioneering Advanced Computing Solutions Since 1988</p>
        </div>
    </section>

    <!-- Main Content -->
    <div class="main-content">
        <a href="/dashboard" class="back-button">
            ← Back to Dashboard
        </a>

        <section class="content-section">
            <h2 class="section-title">🏛️ About CDAC</h2>
            <p style="font-size: 1.2rem; line-height: 1.8; color: #4a5568; margin-bottom: 2rem;">
                The Centre for Development of Advanced Computing (CDAC) is India's premier Research & Development organization in Information Technology and Electronics. Established in 1988, CDAC has been at the forefront of indigenous technology development and has significantly contributed to the growth of India's IT sector.
            </p>
            
            <h3 style="color: #0066cc; font-size: 1.8rem; margin: 2rem 0 1rem 0;">🎯 Our Mission</h3>
            <p style="font-size: 1.1rem; line-height: 1.8; color: #4a5568; margin-bottom: 2rem;">
                To develop cutting-edge computing technologies and solutions that empower India's digital transformation while fostering innovation in research, education, and industry collaboration.
            </p>

            <h3 style="color: #0066cc; font-size: 1.8rem; margin: 2rem 0 1rem 0;">🎓 PG Diploma in Big Data Analytics (PG DBDA)</h3>
            <p style="font-size: 1.1rem; line-height: 1.8; color: #4a5568; margin-bottom: 1rem;">
                Our flagship Post Graduate Diploma in Big Data Analytics is designed to create industry-ready professionals capable of handling the massive data challenges of today's digital world.
            </p>
            <ul style="font-size: 1.1rem; line-height: 1.8; color: #4a5568; margin-bottom: 2rem;">
                <li>Python & R Programming</li>
                <li>Statistical Analysis & Machine Learning</li>
                <li>Deep Learning & Neural Networks</li>
                <li>Data Visualization & Analytics</li>
                <li>Apache Spark & Hadoop</li>
                <li>Cloud Platforms (AWS, Azure)</li>
            </ul>

            <h3 style="color: #0066cc; font-size: 1.8rem; margin: 2rem 0 1rem 0;">👁️ Face Recognition Attendance System</h3>
            <p style="font-size: 1.1rem; line-height: 1.8; color: #4a5568; margin-bottom: 1rem;">
                This innovative attendance management system represents the fusion of CDAC's expertise in artificial intelligence and practical application development. Built using advanced computer vision and machine learning algorithms, it demonstrates our commitment to creating technology solutions that solve real-world problems.
            </p>
            <ul style="font-size: 1.1rem; line-height: 1.8; color: #4a5568; margin-bottom: 2rem;">
                <li>Real-time face detection and recognition</li>
                <li>Automated attendance tracking with time slots</li>
                <li>Comprehensive reporting and analytics</li>
                <li>Secure data management</li>
                <li>Mobile-responsive web interface</li>
            </ul>

            <h3 style="color: #0066cc; font-size: 1.8rem; margin: 2rem 0 1rem 0;">🏆 Excellence & Achievements</h3>
            <ul style="font-size: 1.1rem; line-height: 1.8; color: #4a5568; margin-bottom: 2rem;">
                <li><strong>PARAM Supercomputers:</strong> Designed India's first indigenous supercomputer series</li>
                <li><strong>Technology Innovation:</strong> 150+ technology products and solutions developed</li>
                <li><strong>Education Excellence:</strong> 50,000+ students trained with 90%+ placement rates</li>
                <li><strong>Industry Impact:</strong> 35+ years of excellence in advanced computing</li>
            </ul>
        </section>
    </div>

    <script>
        async function performLogout() {
            if (confirm('Are you sure you want to logout?')) {
                try {
                    const response = await fetch('/api/logout', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        credentials: 'include'
                    });
                    const result = await response.json();
                    if (result.success) {
                        localStorage.clear();
                        sessionStorage.clear();
                        window.location.href = '/login';
                    } else {
                        alert('Logout failed: ' + result.message);
                    }
                } catch (error) {
                    console.error('Logout error:', error);
                    window.location.href = '/login';
                }
            }
        }

        async function checkSession() {
            try {
                const response = await fetch('/api/session/status', { credentials: 'include' });
                const result = await response.json();
                if (!result.authenticated) {
                    window.location.href = '/login';
                }
            } catch (error) {
                console.log('Session check failed:', error);
            }
        }

        setInterval(checkSession, 5 * 60 * 1000);
        document.addEventListener('visibilitychange', function() {
            if (!document.hidden) {
                checkSession();
            }
        });
    </script>
</body>
</html>"""

        # Contact Us page content  
        contact_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Contact Us - CDAC Kharghar</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        /* ===== CDAC NAVBAR STYLES ===== */
        .cdac-header {
            background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);
            border-bottom: 4px solid #0066cc;
            box-shadow: 0 4px 12px rgba(0, 102, 204, 0.15);
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            width: 100%;
            z-index: 10000;
            padding: 12px 0;
        }

        .cdac-container {
            max-width: 1400px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0 24px;
            gap: 20px;
        }

        /* Logo Section */
        .cdac-logo-section {
            display: flex;
            align-items: center;
            gap: 16px;
            flex: 1;
        }

        .cdac-logo-wrapper {
            background: linear-gradient(135deg, #0066cc 0%, #004499 100%);
            padding: 10px;
            border-radius: 12px;
            box-shadow: 0 4px 8px rgba(0, 102, 204, 0.2);
            display: flex;
            align-items: center;
            justify-content: center;
            min-width: 60px;
            min-height: 60px;
            position: relative;
        }

        .cdac-logo-img {
            height: 50px;
            width: 50px;
            object-fit: contain;
            display: block;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 8px;
            padding: 4px;
        }

        .cdac-fallback-logo {
            display: none;
            align-items: center;
            justify-content: center;
            width: 50px;
            height: 50px;
        }

        .fallback-icon {
            font-size: 32px;
            color: white;
            text-shadow: 0 2px 4px rgba(0, 0, 0, 0.3);
        }

        .cdac-text-section {
            display: flex;
            flex-direction: column;
            gap: 2px;
        }

        .cdac-hindi {
            font-size: 16px;
            font-weight: 700;
            color: #0066cc;
            line-height: 1.2;
            letter-spacing: 0.5px;
        }

        .cdac-english {
            font-size: 13px;
            font-weight: 600;
            color: #004499;
            line-height: 1.2;
            letter-spacing: 0.3px;
        }

        .cdac-tagline {
            font-size: 11px;
            font-weight: 500;
            color: #666;
            line-height: 1.2;
            font-style: italic;
        }

        /* Navigation */
        .cdac-navigation {
            display: flex;
            align-items: center;
            gap: 8px;
            flex: 0 0 auto;
        }

        .nav-link {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 10px 16px;
            color: #333;
            text-decoration: none;
            font-size: 14px;
            font-weight: 600;
            border-radius: 8px;
            transition: all 0.3s ease;
            white-space: nowrap;
            border: 2px solid transparent;
        }

        .nav-link:hover {
            background: linear-gradient(135deg, #e6f3ff 0%, #cce6ff 100%);
            color: #0066cc;
            border-color: #b3d9ff;
            transform: translateY(-2px);
            box-shadow: 0 4px 8px rgba(0, 102, 204, 0.15);
            text-decoration: none;
        }

        .nav-link.active {
            background: linear-gradient(135deg, #0066cc 0%, #004499 100%);
            color: white;
            border-color: #0066cc;
        }

        .nav-icon {
            font-size: 16px;
        }

        .nav-text {
            font-size: 13px;
            letter-spacing: 0.5px;
        }

        /* User Section */
        .cdac-user-section {
            flex: 0 0 auto;
        }

        .logout-button {
            display: flex;
            align-items: center;
            gap: 8px;
            background: linear-gradient(135deg, #dc3545 0%, #c82333 100%);
            color: white;
            border: none;
            padding: 12px 20px;
            border-radius: 10px;
            cursor: pointer;
            font-weight: 700;
            font-size: 14px;
            box-shadow: 0 4px 12px rgba(220, 53, 69, 0.3);
            transition: all 0.3s ease;
            letter-spacing: 0.3px;
        }

        .logout-button:hover {
            background: linear-gradient(135deg, #c82333 0%, #a71e2a 100%);
            transform: translateY(-3px);
            box-shadow: 0 6px 16px rgba(220, 53, 69, 0.4);
        }

        .logout-icon {
            font-size: 16px;
        }

        .logout-text {
            font-size: 13px;
        }

        /* Body adjustment */
        body {
            margin: 0;
            padding: 0;
            padding-top: 90px !important;
            font-family: 'Inter', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%);
            min-height: 100vh;
        }

        /* Page Content Styles */
        .page-hero {
            background: linear-gradient(135deg, #0066cc 0%, #004499 50%, #003366 100%);
            color: white;
            padding: 60px 0;
            text-align: center;
            position: relative;
            overflow: hidden;
        }

        .hero-content {
            position: relative;
            z-index: 2;
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 20px;
        }

        .hero-title {
            font-size: 3.5rem;
            font-weight: 800;
            margin-bottom: 1rem;
            text-shadow: 0 4px 8px rgba(0, 0, 0, 0.3);
        }

        .hero-subtitle {
            font-size: 1.5rem;
            opacity: 0.9;
            margin-bottom: 2rem;
            font-weight: 300;
        }

        .main-content {
            max-width: 1200px;
            margin: 0 auto;
            padding: 60px 20px;
        }

        .content-section {
            background: white;
            border-radius: 20px;
            padding: 40px;
            margin-bottom: 40px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1);
            border: 1px solid #e2e8f0;
        }

        .section-title {
            font-size: 2.5rem;
            color: #0066cc;
            margin-bottom: 1.5rem;
            font-weight: 700;
            position: relative;
            padding-bottom: 15px;
        }

        .section-title::after {
            content: '';
            position: absolute;
            bottom: 0;
            left: 0;
            width: 60px;
            height: 4px;
            background: linear-gradient(135deg, #0066cc, #004499);
            border-radius: 2px;
        }

        .contact-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 30px;
            margin: 40px 0;
        }

        .contact-card {
            background: white;
            border-radius: 15px;
            padding: 30px;
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
            border: 1px solid #e2e8f0;
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }

        .contact-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 4px;
            background: linear-gradient(135deg, #0066cc, #004499);
        }

        .contact-card:hover {
            transform: translateY(-10px);
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.15);
        }

        .contact-icon {
            font-size: 3rem;
            margin-bottom: 20px;
            display: block;
            color: #0066cc;
        }

        .contact-title {
            font-size: 1.5rem;
            font-weight: 700;
            color: #0066cc;
            margin-bottom: 15px;
        }

        .contact-details {
            color: #666;
            line-height: 1.8;
        }

        .developer-section {
            background: linear-gradient(135deg, #0066cc 0%, #004499 100%);
            color: white;
            border-radius: 20px;
            padding: 50px;
            text-align: center;
            margin: 50px 0;
        }

        .developer-name {
            font-size: 2.5rem;
            font-weight: 800;
            margin-bottom: 10px;
        }

        .developer-title {
            font-size: 1.3rem;
            opacity: 0.9;
            margin-bottom: 20px;
            font-weight: 300;
        }

        .back-button {
            background: linear-gradient(135deg, #0066cc 0%, #004499 100%);
            color: white;
            padding: 15px 30px;
            border: none;
            border-radius: 10px;
            font-size: 1.1rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 30px;
        }

        .back-button:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(0, 102, 204, 0.3);
            text-decoration: none;
            color: white;
        }

        /* Mobile Responsive */
        @media (max-width: 768px) {
            .cdac-header { position: relative; padding: 16px 0; }
            body { padding-top: 0 !important; }
            .cdac-container { flex-direction: column; gap: 16px; text-align: center; padding: 0 12px; }
            .hero-title { font-size: 2.5rem; }
            .hero-subtitle { font-size: 1.2rem; }
            .content-section { padding: 25px; margin-bottom: 25px; }
            .section-title { font-size: 2rem; }
            .contact-grid { grid-template-columns: 1fr; gap: 20px; }
            .developer-section { padding: 30px 20px; }
            .developer-name { font-size: 2rem; }
        }
    </style>
</head>
<body>
    <!-- CDAC Header -->
    <header class="cdac-header">
        <div class="cdac-container">
            <div class="cdac-logo-section">
                <div class="cdac-logo-wrapper">
                    <img src="/static/images/cdac-logo.png" alt="CDAC Logo" class="cdac-logo-img" 
                         onerror="this.style.display='none'; document.getElementById('cdac-fallback-logo').style.display='flex';">
                    <div id="cdac-fallback-logo" class="cdac-fallback-logo" style="display: none;">
                        <span class="fallback-icon">🏛️</span>
                    </div>
                </div>
                <div class="cdac-text-section">
                    <div class="cdac-hindi">भारत संगणन विकास केंद्र</div>
                    <div class="cdac-english">CENTRE FOR DEVELOPMENT OF ADVANCED COMPUTING</div>
                    <div class="cdac-tagline">Face Recognition Attendance System</div>
                </div>
            </div>
            
            <nav class="cdac-navigation">
                <a href="/dashboard" class="nav-link">
                    <span class="nav-icon">🏠</span>
                    <span class="nav-text">HOME</span>
                </a>
                <a href="/about" class="nav-link">
                    <span class="nav-icon">ℹ️</span>
                    <span class="nav-text">ABOUT</span>
                </a>
                <a href="/contact" class="nav-link active">
                    <span class="nav-icon">📞</span>
                    <span class="nav-text">CONTACT</span>
                </a>
            </nav>
            
            <div class="cdac-user-section">
                <button class="logout-button" onclick="performLogout()" title="Logout">
                    <span class="logout-icon">🚪</span>
                    <span class="logout-text">Logout</span>
                </button>
            </div>
        </div>
    </header>

    <!-- Hero Section -->
    <section class="page-hero">
        <div class="hero-content">
            <h1 class="hero-title">Contact Us</h1>
            <p class="hero-subtitle">Connect with CDAC Kharghar - Your Gateway to Advanced Computing</p>
        </div>
    </section>

    <!-- Main Content -->
    <div class="main-content">
        <a href="/dashboard" class="back-button">
            ← Back to Dashboard
        </a>

        <!-- Contact Information -->
        <section class="content-section">
            <h2 class="section-title">�� CDAC Kharghar</h2>
            <div class="contact-grid">
                <div class="contact-card">
                    <span class="contact-icon">🏢</span>
                    <h3 class="contact-title">Office Address</h3>
                    <div class="contact-details">
                        <p><strong>CDAC Knowledge Park</strong><br>
                        Plot No. 14A, Sector 7, CBD Belapur<br>
                        Navi Mumbai, Maharashtra 400614</p>
                        <p><strong>Nearest Station:</strong> CBD Belapur Railway Station<br>
                        <em>5 minutes walk from station</em></p>
                    </div>
                </div>

                <div class="contact-card">
                    <span class="contact-icon">📞</span>
                    <h3 class="contact-title">Contact Numbers</h3>
                    <div class="contact-details">
                        <p><strong>Main Office:</strong><br>
                        +91-22-2734-7111 / 7222</p>
                        <p><strong>Admissions:</strong><br>
                        +91-22-2734-7100</p>
                        <p><strong>Student Support:</strong><br>
                        +91-22-2734-7200</p>
                    </div>
                </div>

                <div class="contact-card">
                    <span class="contact-icon">✉️</span>
                    <h3 class="contact-title">Email Contacts</h3>
                    <div class="contact-details">
                        <p><strong>General Inquiry:</strong><br>
                        info@cdac.in</p>
                        <p><strong>Admissions:</strong><br>
                        admissions@cdacmumbai.in</p>
                        <p><strong>Training Programs:</strong><br>
                        training@cdacmumbai.in</p>
                    </div>
                </div>

                <div class="contact-card">
                    <span class="contact-icon">🕐</span>
                    <h3 class="contact-title">Office Hours</h3>
                    <div class="contact-details">
                        <p><strong>Monday - Friday:</strong><br>
                        9:00 AM - 6:00 PM</p>
                        <p><strong>Saturday:</strong><br>
                        9:00 AM - 1:00 PM</p>
                        <p><strong>Sunday:</strong> Closed</p>
                    </div>
                </div>
            </div>
        </section>

        <!-- Developer Section -->
        <section class="developer-section">
            <div style="font-size: 4rem; margin-bottom: 20px;">👨‍💻</div>
            <h2 class="developer-name">Nishad Kharote</h2>
            <p class="developer-title">Lead Developer & AI Systems Engineer</p>
            
            <p style="font-size: 1.1rem; line-height: 1.6; opacity: 0.9; max-width: 800px; margin: 0 auto 30px;">
                Passionate software engineer specializing in AI/ML solutions and modern web development. 
                Architect of the Face Recognition Attendance System, combining cutting-edge computer vision 
                with intuitive user experience design.
            </p>

            <div class="contact-grid">
                <div class="contact-card" style="background: rgba(255, 255, 255, 0.1); border: 1px solid rgba(255, 255, 255, 0.2);">
                    <span class="contact-icon" style="color: white;">💼</span>
                    <h3 class="contact-title" style="color: white;">Professional Contact</h3>
                    <div class="contact-details" style="color: rgba(255, 255, 255, 0.9);">
                        <p>📧 nishad.kharote@cdac.in</p>
                        <p>🔗 Senior Software Engineer</p>
                        <p>🎯 AI/ML Specialist</p>
                    </div>
                </div>

                <div class="contact-card" style="background: rgba(255, 255, 255, 0.1); border: 1px solid rgba(255, 255, 255, 0.2);">
                    <span class="contact-icon" style="color: white;">🚀</span>
                    <h3 class="contact-title" style="color: white;">Expertise</h3>
                    <div class="contact-details" style="color: rgba(255, 255, 255, 0.9);">
                        <p>🧠 AI/ML Algorithm Development</p>
                        <p>👁️ Computer Vision Systems</p>
                        <p>🎨 Full-Stack Web Development</p>
                    </div>
                </div>
            </div>

            <div style="margin-top: 40px; padding: 25px; background: rgba(255, 255, 255, 0.1); border-radius: 15px;">
                <h3 style="margin-bottom: 15px;">🎯 Project Highlights</h3>
                <ul style="text-align: left; max-width: 600px; margin: 0 auto; line-height: 1.8;">
                    <li>Built real-time face recognition system with 95%+ accuracy</li>
                    <li>Implemented secure attendance tracking with time-slot management</li>
                    <li>Designed responsive web interface for multi-device compatibility</li>
                    <li>Integrated advanced analytics and reporting features</li>
                    <li>Optimized system performance for institutional-scale deployment</li>
                </ul>
            </div>
        </section>

        <!-- Academic Programs -->
        <section class="content-section">
            <h2 class="section-title">🎓 Academic Programs</h2>
            <div class="contact-grid">
                <div class="contact-card">
                    <span class="contact-icon">📊</span>
                    <h3 class="contact-title">PG DBDA</h3>
                    <div class="contact-details">
                        <p><strong>Post Graduate Diploma in Big Data Analytics</strong></p>
                        <p>Duration: 6 Months</p>
                        <p>Admissions: +91-22-2734-7100</p>
                    </div>
                </div>

                <div class="contact-card">
                    <span class="contact-icon">💻</span>
                    <h3 class="contact-title">DAC</h3>
                    <div class="contact-details">
                        <p><strong>Diploma in Advanced Computing</strong></p>
                        <p>Duration: 6 Months</p>
                        <p>Info: +91-22-2734-7100</p>
                    </div>
                </div>

                <div class="contact-card">
                    <span class="contact-icon">🤖</span>
                    <h3 class="contact-title">AI/ML Programs</h3>
                    <div class="contact-details">
                        <p><strong>Specialized courses in Artificial Intelligence</strong></p>
                        <p>Various Durations Available</p>
                        <p>Training: +91-22-2734-7200</p>
                    </div>
                </div>
            </div>
        </section>
    </div>

    <script>
        async function performLogout() {
            if (confirm('Are you sure you want to logout?')) {
                try {
                    const response = await fetch('/api/logout', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        credentials: 'include'
                    });
                    const result = await response.json();
                    if (result.success) {
                        localStorage.clear();
                        sessionStorage.clear();
                        window.location.href = '/login';
                    } else {
                        alert('Logout failed: ' + result.message);
                    }
                } catch (error) {
                    console.error('Logout error:', error);
                    window.location.href = '/login';
                }
            }
        }

        async function checkSession() {
            try {
                const response = await fetch('/api/session/status', { credentials: 'include' });
                const result = await response.json();
                if (!result.authenticated) {
                    window.location.href = '/login';
                }
            } catch (error) {
                console.log('Session check failed:', error);
            }
        }

        setInterval(checkSession, 5 * 60 * 1000);
        document.addEventListener('visibilitychange', function() {
            if (!document.hidden) {
                checkSession();
            }
        });
    </script>
</body>
</html>"""

        # Write the files
        about_path = current_dir / 'about.html'
        contact_path = current_dir / 'contact.html'
        
        with open(about_path, 'w', encoding='utf-8') as f:
            f.write(about_content)
        print(f"✅ Created: about.html")
        
        with open(contact_path, 'w', encoding='utf-8') as f:
            f.write(contact_content)
        print(f"✅ Created: contact.html")
        
        return True
        
    except Exception as e:
        print(f"❌ Failed to create new pages: {e}")
        return False
    """Create static directory structure for logo"""
    try:
        static_dir = Path.cwd() / 'static' / 'images'
        static_dir.mkdir(parents=True, exist_ok=True)
        print(f"✅ Created directory: {static_dir}")
        
        # Check if logo already exists
        logo_path = static_dir / 'cdac-logo.png'
        if logo_path.exists():
            print(f"🖼️  CDAC logo found: {logo_path}")
            print("   Logo will be displayed in navbar!")
        else:
            print(f"⚠️  CDAC logo not found at: {logo_path}")
            print("   Fallback icon (🏛️) will be shown until logo is added")
        
        # Create a placeholder file with instructions
        readme_path = static_dir / 'README_LOGO.txt'
        with open(readme_path, 'w') as f:
            f.write("""CDAC LOGO SETUP INSTRUCTIONS
================================

1. Place your official CDAC logo in this directory as: cdac-logo.png
2. Recommended specifications:
   - Size: 256x256px minimum (square format works best)
   - Format: PNG (for transparency) or JPG
   - Quality: High resolution for crisp display

3. Logo will be automatically displayed in the navbar
4. If logo file is missing, a fallback icon (🏛️) will be shown

5. To test: Open any HTML page and check the top-left navbar area

Need the official CDAC logo? Contact your system administrator.
""")
        print(f"📝 Created setup guide: {readme_path}")
        return True
    except Exception as e:
        print(f"❌ Failed to create static directory: {e}")
        return False

def main():
    """Main function to process all HTML files"""
    # Get current directory
    current_dir = Path.cwd()
    
    print("🚀 CDAC Navbar Standardizer")
    print("=" * 50)
    
    # Create static directory first
    print("📁 Setting up static directory structure...")
    setup_static_directory()
    print()
    
    # Create new pages
    print("🆕 Creating new pages...")
    create_new_pages()
    print()
    
    # Define HTML files to update
    html_files = [
        'admin.html',
        'attendance.html', 
        'attendance_management.html',
        'dashboard.html',
        'register.html',
        'simple_login.html',
        'students.html'
    ]
    
    print("Updating HTML files with improved CDAC navbar...")
    print()
    
    updated_files = []
    failed_files = []
    
    for html_file in html_files:
        file_path = current_dir / html_file
        
        if file_path.exists():
            print(f"📝 Processing: {html_file}")
            success, message = update_html_file(file_path)
            
            if success:
                updated_files.append(html_file)
                print(f"   ✅ {message}")
            else:
                failed_files.append((html_file, message))
                print(f"   ❌ {message}")
        else:
            print(f"⚠️  File not found: {html_file}")
            failed_files.append((html_file, "File not found"))
        
        print()
    
    # Summary
    print("=" * 50)
    print("📊 SUMMARY")
    print("=" * 50)
    print(f"✅ Successfully updated: {len(updated_files)} files")
    for file in updated_files:
        print(f"   • {file}")
    
    if failed_files:
        print(f"\n❌ Failed to update: {len(failed_files)} files")
        for file, error in failed_files:
            print(f"   • {file}: {error}")
    
    print(f"\n🎉 Navbar standardization complete!")
    print("\nKey improvements:")
    print("• 🏢 Enhanced CDAC branding with logo wrapper")
    print("• 🖼️  Logo with fallback system (🏛️ icon if image fails)")
    print("• 📐 Consistent sizing across all pages")
    print("• 📱 Improved responsive design")
    print("• 🎨 Modern gradient styling")
    print("• 🔄 Smooth hover animations")
    print("• �� Better user experience")
    print("\n📁 LOGO SETUP:")
    print("• Place your CDAC logo at: /static/images/cdac-logo.png")
    print("• Recommended size: 256x256px or higher")
    print("• Supported formats: PNG, JPG, SVG")
    print("• Fallback icon (🏛️) will show if logo file is missing")
    print("\n🔧 NEXT STEPS:")
    print("1. Ensure /static/images/ directory exists")
    print("2. Add cdac-logo.png to /static/images/")
    print("3. Test pages to verify logo displays correctly")
    print("4. If logo doesn't appear, check browser console for errors")

if __name__ == "__main__":
    main()
