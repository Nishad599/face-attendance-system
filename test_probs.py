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
    
    bgr_image = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)
    input_tensor = anti_spoof_checker._preprocess(bgr_image, bbox_xywh) / 255.0
    outputs = anti_spoof_checker.session.run([anti_spoof_checker.output_name], {anti_spoof_checker.input_name: input_tensor})
    logits = outputs[0]
    probs = anti_spoof_checker._softmax(logits)
    print("Raw Logits:", logits)
    print("Raw Probs:", probs)
    print("Real score at index 1:", probs[0, 1])
