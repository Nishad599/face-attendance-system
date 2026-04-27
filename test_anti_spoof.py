import cv2
import numpy as np
import os
import glob
from asian_face_model import asian_face_recognizer
from anti_spoofing import anti_spoof_checker

# Get a test image
folders = glob.glob('student_photos/*')
if not folders:
    print("No folders found")
    exit()

img_files = glob.glob(os.path.join(folders[0], '*'))
if not img_files:
    print("No images found in", folders[0])
    exit()

img_path = img_files[0]
print("Testing with image:", img_path)

img = cv2.imread(img_path)
if img is None:
    print("Failed to read image")
    exit()

img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
faces = asian_face_recognizer.detect_faces_optimized(img_rgb)
print(f"Detected {len(faces)} faces.")

if faces:
    face = faces[0]
    bbox = face['location']
    print("Bbox:", bbox)
    res = anti_spoof_checker.check(img_rgb, bbox)
    print("Anti spoof result:", res)
else:
    print("No faces detected by InsightFace.")
