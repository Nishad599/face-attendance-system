import cv2
import numpy as np
import glob
from asian_face_model import asian_face_recognizer
from anti_spoofing import anti_spoof_checker

img_path = glob.glob('student_photos/*/*')[0]
img = cv2.imread(img_path)
img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
faces = asian_face_recognizer.detect_faces_optimized(img_rgb)
if faces:
    face = faces[0]
    bbox = face['location']
    
    top, right, bottom, left = bbox
    bbox_xywh = [int(left), int(top), int(right - left), int(bottom - top)]
    
    scale = anti_spoof_checker.scale
    src_h, src_w = img_rgb.shape[:2]
    x, y, box_w, box_h = bbox_xywh

    # Expand bbox by scale factor, clamped to image bounds
    actual_scale = min((src_h - 1) / box_h, (src_w - 1) / box_w, scale)
    new_w = box_w * actual_scale
    new_h = box_h * actual_scale

    center_x = x + box_w / 2
    center_y = y + box_h / 2

    x1 = max(0, int(center_x - new_w / 2))
    y1 = max(0, int(center_y - new_h / 2))
    x2 = min(src_w - 1, int(center_x + new_w / 2))
    y2 = min(src_h - 1, int(center_y + new_h / 2))

    print(f"Original Box: {box_w}x{box_h}")
    print(f"Cropped Box: {x2-x1}x{y2-y1} expanded by scale {actual_scale:.2f}")

    cropped = anti_spoof_checker._crop_face(img_rgb, bbox_xywh)
    print("Crop successful, shape:", cropped.shape)
