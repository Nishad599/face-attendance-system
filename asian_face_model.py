import cv2
import numpy as np
try:
    import insightface
    INSIGHTFACE_AVAILABLE = True
    print("✅ InsightFace available")
except ImportError:
    INSIGHTFACE_AVAILABLE = False
    print("❌ InsightFace not available")

class AsianFaceRecognizer:
    def __init__(self):
        """Initialize buffalo_l w600k model for 512D embeddings"""
        self.use_insightface = False
        self.model_name = "buffalo_l"
        self.embedding_dim = 512  # buffalo_l w600k produces 512D embeddings
        
        if INSIGHTFACE_AVAILABLE:
            try:
                from insightface.app import FaceAnalysis
                # Initialize buffalo_l specifically
                self.insight_app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])
                self.insight_app.prepare(ctx_id=0, det_size=(640, 640))
                self.use_insightface = True
                print(f"🎯 buffalo_l w600k model loaded - {self.embedding_dim}D embeddings")
                
                # Test the model to verify 512D output
                test_frame = np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8)
                test_results = self.insight_app.get(test_frame)
                if len(test_results) > 0:
                    test_embedding = test_results[0].embedding
                    print(f"✅ buffalo_l test embedding shape: {test_embedding.shape}")
                    if len(test_embedding) != 512:
                        print(f"⚠️  Warning: Expected 512D, got {len(test_embedding)}D")
                else:
                    print("ℹ️  No faces in test image (normal)")
                    
            except Exception as e:
                print(f"❌ buffalo_l model failed to load: {e}")
                self.use_insightface = False
        
        if not self.use_insightface:
            raise Exception("❌ buffalo_l w600k model is required!")
    
    def detect_faces_optimized(self, frame):
        """Detect faces and generate 512D embeddings using buffalo_l w600k"""
        faces = []
        
        if not self.use_insightface:
            print("❌ buffalo_l model not available")
            return faces
        
        try:
            # Convert frame format for InsightFace
            if len(frame.shape) == 3:
                if frame.shape[2] == 3:  # RGB
                    bgr_frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                else:  # Already BGR
                    bgr_frame = frame
            else:
                print(f"[ERROR] Invalid frame shape: {frame.shape}")
                return faces
            
            print(f"[DEBUG] buffalo_l processing frame: {frame.shape}")
            
            # Get faces with buffalo_l w600k
            results = self.insight_app.get(bgr_frame)
            
            for i, face in enumerate(results):
                bbox = face.bbox.astype(int)
                # Convert to (top, right, bottom, left) format
                top, right, bottom, left = bbox[1], bbox[2], bbox[3], bbox[0]
                
                # CRITICAL: Verify 512D embedding
                if hasattr(face, 'embedding'):
                    embedding = face.embedding
                    print(f"[DEBUG] buffalo_l face {i+1}: embedding shape {embedding.shape}")
                    
                    if len(embedding) == self.embedding_dim:  # Must be 512D
                        # Validate embedding quality
                        if np.isfinite(embedding).all() and np.linalg.norm(embedding) > 0:
                            faces.append({
                                'location': (top, right, bottom, left),
                                'confidence': float(face.det_score),
                                'embedding': embedding.astype(np.float64),  # Ensure consistent dtype
                                'source': f'buffalo_l_w600k_512D',
                                'embedding_norm': float(np.linalg.norm(embedding))
                            })
                            print(f"[DEBUG] ✅ Valid 512D embedding: norm={np.linalg.norm(embedding):.3f}")
                        else:
                            print(f"[DEBUG] ❌ Invalid embedding values")
                    else:
                        print(f"[DEBUG] ❌ Wrong embedding dimension: {len(embedding)} (expected {self.embedding_dim})")
                else:
                    print(f"[DEBUG] ❌ No embedding found for face")
                    
        except Exception as e:
            print(f"[ERROR] buffalo_l detection error: {e}")
            import traceback
            traceback.print_exc()
        
        print(f"[DEBUG] buffalo_l detected {len(faces)} valid faces with 512D embeddings")
        return faces
    
    def compare_faces_optimized(self, known_embeddings, face_embedding, tolerance=0.5):
        """Compare 512D embeddings using cosine similarity"""
        
        print(f"[DEBUG] buffalo_l comparison: input embedding shape {face_embedding.shape}")
        print(f"[DEBUG] buffalo_l comparison: {len(known_embeddings)} known embeddings")
        
        # Validate face embedding
        if len(face_embedding) != self.embedding_dim:
            print(f"[ERROR] Invalid face embedding: {len(face_embedding)}D (expected {self.embedding_dim}D)")
            return [], []
        
        # Validate known embeddings
        for i, known in enumerate(known_embeddings):
            if len(known) != self.embedding_dim:
                print(f"[ERROR] Invalid known embedding {i}: {len(known)}D (expected {self.embedding_dim}D)")
                return [], []
        
        try:
            similarities = []
            
            # Normalize the input embedding (L2 normalization)
            face_norm = np.linalg.norm(face_embedding)
            if face_norm == 0:
                print("[ERROR] Zero-norm face embedding")
                return [], []
            face_normalized = face_embedding / face_norm
            
            print(f"[DEBUG] buffalo_l face embedding norm: {face_norm:.3f}")
            
            for i, known_embedding in enumerate(known_embeddings):
                # Normalize known embedding
                known_norm = np.linalg.norm(known_embedding)
                if known_norm == 0:
                    print(f"[ERROR] Zero-norm known embedding {i}")
                    similarities.append(0.0)
                    continue
                    
                known_normalized = known_embedding / known_norm
                
                # Calculate cosine similarity
                cosine_sim = np.dot(face_normalized, known_normalized)
                similarities.append(float(cosine_sim))
                
                print(f"[DEBUG] buffalo_l similarity {i}: {cosine_sim:.4f} (known_norm: {known_norm:.3f})")
            
            print(f"[DEBUG] buffalo_l all similarities: {similarities}")
            print(f"[DEBUG] buffalo_l similarity threshold: {tolerance}")
            
            # Apply threshold (similarity must be > tolerance)
            matches = [sim > tolerance for sim in similarities]
            distances = [1.0 - sim for sim in similarities]  # Convert to distances for compatibility
            
            print(f"[DEBUG] buffalo_l matches: {matches}")
            print(f"[DEBUG] buffalo_l distances: {distances}")
            
            return matches, distances
            
        except Exception as e:
            print(f"[ERROR] buffalo_l comparison failed: {e}")
            import traceback
            traceback.print_exc()
            return [], []

# Global buffalo_l w600k face recognizer
print("🚀 Initializing buffalo_l w600k for 512D embeddings...")
asian_face_recognizer = AsianFaceRecognizer()
