import cv2
import numpy as np
import glob

img_path = glob.glob('student_photos/*/*')[0]
img = cv2.imread(img_path)

gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()

print("Full Image Laplacian Variance:", laplacian_var)

# Crop face based on previous bbox
bbox = [195, 754, 507, 524] # top, right, bottom, left
top, right, bottom, left = bbox
face = img[int(top):int(bottom), int(left):int(right)]
if face.size > 0:
    face_gray = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
    face_var = cv2.Laplacian(face_gray, cv2.CV_64F).var()
    print("Face Crop Laplacian Variance:", face_var)
