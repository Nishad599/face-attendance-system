import cv2
import numpy as np
import pickle
import os
from datetime import datetime
import urllib.request

class OpenCVFaceSystem:
    def __init__(self):
        # Load OpenCV's face detectors
        cascade_paths = [
                    '/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml',
                    '/usr/local/share/opencv4/haarcascades/haarcascade_frontalface_default.xml', 
                    'haarcascade_frontalface_default.xml'   
                ]
        
        self.face_cascade = None
        for path in cascade_paths:
            if os.path.exists(path):
                self.face_cascade = cv2.CascadeClassifier(path)
                break
        
        if self.face_cascade is None or self.face_cascade.empty():
            # Download the cascade file
            import urllib.request
            cascade_url = 'https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/haarcascade_frontalface_default.xml'
            urllib.request.urlretrieve(cascade_url, 'haarcascade_frontalface_default.xml')
            self.face_cascade = cv2.CascadeClassifier('haarcascade_frontalface_default.xml')
        
        
        # Try to create LBPH recognizer (if available)
        try:
            self.recognizer = cv2.face.LBPHFaceRecognizer_create()
            self.has_recognizer = True
        except:
            self.has_recognizer = False
            print("⚠️  LBPH recognizer not available, using basic detection")
        
        self.known_faces = {}
        self.load_known_faces()
        print("✅ OpenCV face detection system initialized")
    
    def face_locations(self, image, model="hog"):
        """Detect faces using OpenCV"""
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        else:
            gray = image
            
        faces = self.face_cascade.detectMultiScale(
            gray, 
            scaleFactor=1.1, 
            minNeighbors=5, 
            minSize=(30, 30),
            flags=cv2.CASCADE_SCALE_IMAGE
        )
        
        # Convert to face_recognition format (top, right, bottom, left)
        locations = []
        for (x, y, w, h) in faces:
            top = y
            right = x + w
            bottom = y + h
            left = x
            locations.append((top, right, bottom, left))
        
        return locations
    
    def face_encodings(self, image, face_locations=None):
        """Generate face features using OpenCV"""
        if face_locations is None:
            face_locations = self.face_locations(image)
        
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        else:
            gray = image
            
        encodings = []
        
        for (top, right, bottom, left) in face_locations:
            # Extract face region
            face_img = gray[top:bottom, left:right]
            
            if face_img.size > 0:
                # Resize to standard size
                face_img = cv2.resize(face_img, (100, 100))
                
                # Create simple feature vector
                features = self.extract_features(face_img)
                encodings.append(features)
        
        return encodings
    
    def extract_features(self, face_img):
        """Extract simple features from face image"""
        features = []
        
        # Method 1: Pixel intensity histogram
        hist = cv2.calcHist([face_img], [0], None, [32], [0, 256])
        features.extend(hist.flatten())
        
        # Method 2: Local Binary Pattern
        # Divide into 4x4 grid and compute LBP-like features
        h, w = face_img.shape
        for i in range(4):
            for j in range(4):
                start_h = i * h // 4
                end_h = (i + 1) * h // 4
                start_w = j * w // 4
                end_w = (j + 1) * w // 4
                
                region = face_img[start_h:end_h, start_w:end_w]
                if region.size > 0:
                    # Calculate mean and std of region
                    features.append(np.mean(region))
                    features.append(np.std(region))
        
        # Method 3: Edge features
        edges = cv2.Canny(face_img, 50, 150)
        edge_hist = cv2.calcHist([edges], [0], None, [16], [0, 256])
        features.extend(edge_hist.flatten())
        
        return np.array(features, dtype=np.float32)
    
    def compare_faces(self, known_encodings, face_encoding, tolerance=0.6):
        """Compare faces using distance metrics"""
        if len(known_encodings) == 0:
            return []
        
        distances = self.face_distance(known_encodings, face_encoding)
        
        # Adaptive threshold based on data distribution
        if len(distances) > 0:
            mean_dist = np.mean(distances)
            std_dist = np.std(distances)
            threshold = mean_dist - (tolerance * std_dist)
            threshold = max(threshold, mean_dist * 0.7)  # Don't go too low
        else:
            threshold = tolerance
        
        matches = [dist < threshold for dist in distances]
        return matches
    
    def face_distance(self, face_encodings, face_to_compare):
        """Calculate distances between face encodings"""
        if len(face_encodings) == 0:
            return np.array([])
        
        distances = []
        for encoding in face_encodings:
            # Normalize encodings for better comparison
            norm1 = encoding / (np.linalg.norm(encoding) + 1e-8)
            norm2 = face_to_compare / (np.linalg.norm(face_to_compare) + 1e-8)
            
            # Use cosine distance
            dist = 1 - np.dot(norm1, norm2)
            distances.append(dist)
        
        return np.array(distances)
    
    def save_face_encoding(self, student_id, student_name, face_encoding):
        """Save face encoding to file"""
        face_data = {
            'student_id': student_id,
            'student_name': student_name,
            'encoding': face_encoding,
            'timestamp': datetime.now().isoformat()
        }
        
        filename = f"face_data_{student_id}.pkl"
        filepath = os.path.join('face_encodings', filename)
        
        # Create directory if it doesn't exist
        os.makedirs('face_encodings', exist_ok=True)
        
        with open(filepath, 'wb') as f:
            pickle.dump(face_data, f)
        
        self.known_faces[student_id] = face_data
        print(f"💾 Saved face encoding for {student_name}")
    
    def load_known_faces(self):
        """Load all known face encodings"""
        if not os.path.exists('face_encodings'):
            return
        
        loaded_count = 0
        for filename in os.listdir('face_encodings'):
            if filename.endswith('.pkl'):
                filepath = os.path.join('face_encodings', filename)
                try:
                    with open(filepath, 'rb') as f:
                        face_data = pickle.load(f)
                    student_id = face_data['student_id']
                    self.known_faces[student_id] = face_data
                    loaded_count += 1
                except Exception as e:
                    print(f"⚠️  Could not load {filename}: {e}")
        
        if loaded_count > 0:
            print(f"📚 Loaded {loaded_count} known faces")
    
    def get_known_encodings(self):
        """Get all known face encodings as arrays"""
        encodings = []
        names = []
        ids = []
        
        for student_id, face_data in self.known_faces.items():
            encodings.append(face_data['encoding'])
            names.append(face_data['student_name'])
            ids.append(student_id)
        
        return encodings, names, ids

# Global instance
opencv_face_system = OpenCVFaceSystem()
