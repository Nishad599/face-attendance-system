import cv2
import threading
import time
from contextlib import contextmanager

class CameraManager:
    def __init__(self):
        self.camera = None
        self.is_locked = False
        self.lock = threading.Lock()
        self.last_used = 0
        self.timeout = 30  # 30 seconds timeout
    
    @contextmanager
    def get_camera(self):
        """Get camera with automatic resource management"""
        with self.lock:
            try:
                if self.camera is None or not self.camera.isOpened():
                    self._open_camera()
                
                self.is_locked = True
                self.last_used = time.time()
                yield self.camera
                
            finally:
                self.is_locked = False
                # Don't close immediately, keep for a short time
                threading.Timer(10.0, self._maybe_close_camera).start()
    
    def _open_camera(self):
        """Open camera with multiple fallback methods"""
        # Try different camera backends
        backends = [
            cv2.CAP_V4L2,
            cv2.CAP_GSTREAMER, 
            cv2.CAP_FFMPEG,
            cv2.CAP_ANY
        ]
        
        for backend in backends:
            try:
                self.camera = cv2.VideoCapture(0, backend)
                if self.camera.isOpened():
                    # Set camera properties for better performance
                    self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    self.camera.set(cv2.CAP_PROP_FPS, 30)
                    self.camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    
                    print(f"✅ Camera opened with backend: {backend}")
                    return
            except Exception as e:
                print(f"❌ Backend {backend} failed: {e}")
                continue
        
        raise Exception("Could not open camera with any backend")
    
    def _maybe_close_camera(self):
        """Close camera if not used recently"""
        with self.lock:
            if (not self.is_locked and 
                self.camera is not None and 
                time.time() - self.last_used > self.timeout):
                
                self.camera.release()
                self.camera = None
                print("📹 Camera auto-closed due to inactivity")
    
    def force_close(self):
        """Force close camera"""
        with self.lock:
            if self.camera is not None:
                self.camera.release()
                self.camera = None
                print("📹 Camera force closed")
    
    def is_available(self):
        """Check if camera is available"""
        return not self.is_locked

# Global camera manager
camera_manager = CameraManager()
