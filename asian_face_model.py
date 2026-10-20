import logging

import cv2
import numpy as np

# Startup messages stay as print(): once per process, and the rest of the
# project reports boot state that way. Everything inside detect_faces_optimized
# runs per frame -- and the per-face lines run per face within that -- so they
# are logger calls. On systemd each print is a synchronous journald write, which
# at a frame every three seconds across several terminals was real latency
# spent telling the log what it already knew.
logger = logging.getLogger(__name__)
try:
    import insightface
    INSIGHTFACE_AVAILABLE = True
    print("[OK] InsightFace available")
except ImportError:
    INSIGHTFACE_AVAILABLE = False
    print("[ERROR] InsightFace not available")

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
                self.insight_app = FaceAnalysis(name='buffalo_l')
                self.insight_app.prepare(ctx_id=0, det_size=(640, 640))
                self.use_insightface = True
                print(f"[MODEL] buffalo_l w600k model loaded - {self.embedding_dim}D embeddings")
                
                # Test the model to verify 512D output
                test_frame = np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8)
                test_results = self.insight_app.get(test_frame)
                if len(test_results) > 0:
                    test_embedding = test_results[0].embedding
                    print(f"[OK] buffalo_l test embedding shape: {test_embedding.shape}")
                    if len(test_embedding) != 512:
                        print(f"[WARN] Warning: Expected 512D, got {len(test_embedding)}D")
                else:
                    print("[INFO] No faces in test image (normal)")
                    
            except Exception as e:
                print(f"[ERROR] buffalo_l model failed to load: {e}")
                self.use_insightface = False
        
        if not self.use_insightface:
            print("[WARN] [FATAL] buffalo_l w600k model is required, but failed to load. The app will start, but face recognition will not function.")
    
    def detect_faces_optimized(self, frame):
        """Detect faces and generate 512D embeddings using buffalo_l w600k"""
        faces = []
        
        if not self.use_insightface:
            logger.error("buffalo_l model not available")
            return faces
        
        try:
            # Convert frame format for InsightFace
            if len(frame.shape) == 3:
                if frame.shape[2] == 3:  # RGB
                    bgr_frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                else:  # Already BGR
                    bgr_frame = frame
            else:
                logger.error("Invalid frame shape: %s", frame.shape)
                return faces
            
            logger.debug("processing frame: %s", frame.shape)
            
            # Get faces with buffalo_l w600k
            results = self.insight_app.get(bgr_frame)
            
            for i, face in enumerate(results):
                bbox = face.bbox.astype(int)
                # Convert to (top, right, bottom, left) format
                top, right, bottom, left = bbox[1], bbox[2], bbox[3], bbox[0]
                
                # CRITICAL: Verify 512D embedding
                if hasattr(face, 'embedding'):
                    embedding = face.embedding
                    logger.debug("face %d: embedding shape %s", i + 1, embedding.shape)
                    
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
                            logger.debug("valid 512D embedding")
                        else:
                            logger.warning("embedding has non-finite or zero values; face skipped")
                    else:
                        logger.warning("wrong embedding dimension: %d (expected %d)",
                                       len(embedding), self.embedding_dim)
                else:
                    logger.warning("no embedding on detected face; skipped")
                    
        except Exception as e:
            logger.error("detection failed: %s", e)
            logger.debug("detection traceback", exc_info=True)
        
        logger.debug("detected %d valid face(s)", len(faces))
        return faces
    
    # There was a compare_faces_optimized() here. Nothing called it -- the
    # detection handlers always did their own matching inline -- and it printed
    # a [DEBUG] line for every entry in the gallery, for every face, plus the
    # whole similarity list. At 500 students that is 500-odd synchronous
    # journald writes per face. The real matcher is best_match() in
    # main_with_face_recognition.py, which is one pre-normalised matmul.


# Global buffalo_l w600k face recognizer
print("[INIT] Initializing buffalo_l w600k for 512D embeddings...")
asian_face_recognizer = AsianFaceRecognizer()
