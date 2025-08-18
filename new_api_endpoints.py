"""
New API endpoints to add to your main FastAPI application.
Copy these endpoints into your main_with_face_recognition.py file.
"""

from fastapi import HTTPException
from attendance_manager import create_slot_manager_instance
from typing import Optional
import json

# Add these imports to your main file if not already present:
# from attendance_manager import create_slot_manager_instance

# Add these endpoints to your FastAPI app:

@app.get("/api/attendance/live-count")
async def get_live_attendance_count():
    """Get live student count with slot information"""
    try:
        manager = create_slot_manager_instance()
        count_data = manager.get_live_student_count()
        return count_data
    except Exception as e:
        return {
            "success": False,
            "message": str(e),
            "total_students": 0,
            "total_present": 0,
            "total_absent": 0
        }

@app.post("/api/detect_attendance_slots")
async def detect_attendance_with_slots(image_data: DetectionImage):
    """Enhanced detection with slot-based attendance marking"""
    if not FACE_RECOGNITION_AVAILABLE:
        return {"success": False, "message": "Face recognition not available"}
    
    try:
        # Convert base64 to image (same as existing detect_attendance)
        if image_data.image_data.startswith('data:image'):
            image_data_clean = image_data.image_data.split(',')[1]
        else:
            image_data_clean = image_data.image_data
        
        image_bytes = base64.b64decode(image_data_clean)
        image = Image.open(io.BytesIO(image_bytes))
        
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        image_array = np.array(image)
        
        # Use existing face detection
        detected_faces = asian_face_recognizer.detect_faces_optimized(image_array)
        
        if len(detected_faces) == 0:
            return {
                "success": False, 
                "message": "No faces detected",
                "faces_detected": 0
            }
        
        # Initialize slot manager
        manager = create_slot_manager_instance()
        recognized_students = []
        unknown_faces = 0
        
        for face_data in detected_faces:
            face_encoding = face_data['embedding']
            
            # Find best match (same logic as existing)
            if len(attendance_system.known_face_encodings) > 0:
                similarities = []
                for known_encoding in attendance_system.known_face_encodings:
                    face_norm = face_encoding / np.linalg.norm(face_encoding)
                    known_norm = known_encoding / np.linalg.norm(known_encoding)
                    similarity = np.dot(face_norm, known_norm)
                    similarities.append(similarity)
                
                best_match_index = np.argmax(similarities)
                best_similarity = similarities[best_match_index]
                
                RECOGNITION_THRESHOLD = 0.60
                
                if best_similarity > RECOGNITION_THRESHOLD:
                    student_id = attendance_system.known_face_ids[best_match_index]
                    student_name = attendance_system.known_face_names[best_match_index]
                    
                    # Use slot manager for attendance marking
                    attendance_result = manager.mark_attendance_with_slot(
                        student_id=student_id,
                        detection_confidence=best_similarity
                    )
                    
                    face_location = face_data['location']
                    
                    if attendance_result['success']:
                        # Successfully marked
                        recognized_students.append({
                            "student_id": student_id,
                            "name": student_name,
                            "confidence": float(best_similarity),
                            "status": "marked",
                            "message": attendance_result['message'],
                            "slot_name": attendance_result.get('slot_name', ''),
                            "location": {
                                "top": int(face_location[0]),
                                "right": int(face_location[1]),
                                "bottom": int(face_location[2]),
                                "left": int(face_location[3])
                            }
                        })
                    elif attendance_result.get('already_marked'):
                        # Already marked
                        recognized_students.append({
                            "student_id": student_id,
                            "name": student_name,
                            "confidence": float(best_similarity),
                            "status": "already_marked",
                            "message": attendance_result['message'],
                            "slot_name": attendance_result.get('slot_name', ''),
                            "location": {
                                "top": int(face_location[0]),
                                "right": int(face_location[1]),
                                "bottom": int(face_location[2]),
                                "left": int(face_location[3])
                            }
                        })
                    elif attendance_result.get('outside_slot'):
                        # Outside slot hours
                        return {
                            "success": False,
                            "faces_detected": len(detected_faces),
                            "recognized_students": [],
                            "unknown_faces": 0,
                            "outside_slot": True,
                            "face_detected": True,
                            "student_name": student_name,
                            "confidence": float(best_similarity),
                            "message": attendance_result['message'],
                            "next_slot": attendance_result.get('next_slot')
                        }
                else:
                    unknown_faces += 1
            else:
                unknown_faces += 1
        
        success = len(recognized_students) > 0
        message = f"Processed {len(detected_faces)} faces, recognized {len(recognized_students)} students"
        
        return {
            "success": success,
            "faces_detected": len(detected_faces),
            "recognized_students": recognized_students,
            "unknown_faces": unknown_faces,
            "message": message
        }
        
    except Exception as e:
        print(f"[ERROR] Slot detection failed: {str(e)}")
        return {
            "success": False,
            "message": f"Detection failed: {str(e)}",
            "faces_detected": 0
        }

@app.get("/api/attendance/slot-status")
async def get_slot_status():
    """Get current slot status information"""
    try:
        manager = create_slot_manager_instance()
        current_slot = manager.get_current_slot()
        next_slot = manager.get_next_slot()
        
        return {
            "success": True,
            "current_slot": current_slot,
            "next_slot": next_slot,
            "slots_config": manager.attendance_slots
        }
    except Exception as e:
        return {
            "success": False,
            "message": str(e)
        }

@app.get("/api/attendance/slot-details/{date}")
async def get_slot_attendance_details(date: str):
    """Get detailed attendance by slot for a specific date"""
    try:
        manager = create_slot_manager_instance()
        details = manager.get_slot_attendance_details(date)
        return details
    except Exception as e:
        return {
            "success": False,
            "message": str(e)
        }

@app.post("/api/attendance/manual-slot")
async def mark_manual_slot_attendance(data: dict):
    """Mark manual attendance for a specific slot"""
    try:
        student_id = data.get('student_id')
        slot_id = data.get('slot_id')
        
        if not student_id or not slot_id:
            raise HTTPException(status_code=400, detail="student_id and slot_id required")
        
        manager = create_slot_manager_instance()
        result = manager.mark_attendance_with_slot(
            student_id=student_id,
            detection_confidence=0.0,
            force_slot=slot_id
        )
        
        return result
        
    except Exception as e:
        return {
            "success": False,
            "message": str(e)
        }

# Instructions:
# 1. Copy the above endpoints into your main_with_face_recognition.py file
# 2. Make sure to import: from attendance_manager import create_slot_manager_instance
# 3. The DetectionImage model should already exist in your main file
