"""
Anti-Spoofing Module - MiniFASNetV2 (ONNX)
Passive liveness detection to prevent photo/screen-based spoofing.
Cross-platform: works on both Windows (local) and Linux (production VM).
"""

import os
import numpy as np

try:
    import onnxruntime as ort
    ONNX_AVAILABLE = True
    print("[OK] ONNXRuntime available for anti-spoofing")
except ImportError:
    ONNX_AVAILABLE = False
    print("[WARN] ONNXRuntime not available - anti-spoofing disabled")

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False


class AntiSpoofChecker:
    """MiniFASNetV2-based passive liveness detection."""

    def __init__(self, model_path=None, threshold=0.45, scale=2.7):
        """
        Args:
            model_path: Path to MiniFASNetV2.onnx. Auto-detected if None.
            threshold: Minimum "Real" score to pass liveness (0-1).
            scale: Bounding box expansion factor (2.7 for MiniFASNetV2).
        """
        self.threshold = threshold
        self.scale = scale
        self.ready = False

        if not ONNX_AVAILABLE or not CV2_AVAILABLE:
            print("[WARN] Anti-spoofing disabled: missing onnxruntime or cv2")
            return

        # Auto-detect model path
        if model_path is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            model_path = os.path.join(base_dir, 'models', 'anti_spoof', 'MiniFASNetV2.onnx')

        if not os.path.exists(model_path):
            print(f"[WARN] Anti-spoof model not found at: {model_path}")
            return

        try:
            self.session = ort.InferenceSession(
                model_path,
                providers=["CPUExecutionProvider"]  # CPU-only for cross-platform compatibility
            )

            input_cfg = self.session.get_inputs()[0]
            self.input_name = input_cfg.name
            self.input_size = tuple(input_cfg.shape[2:])  # (80, 80)

            output_cfg = self.session.get_outputs()[0]
            self.output_name = output_cfg.name

            self.ready = True
            print(f"[SHIELD] Anti-spoofing loaded: MiniFASNetV2 (input={self.input_size}, threshold={self.threshold})")

        except Exception as e:
            print(f"[ERROR] Failed to load anti-spoof model: {e}")
            self.ready = False

    def _crop_face(self, image, bbox_xywh):
        """Crop face with expanded bounding box, padding with black if necessary so phone edges are seen."""
        src_h, src_w = image.shape[:2]
        x, y, box_w, box_h = bbox_xywh

        # Use full scale so phone borders are included in the crop
        new_w = box_w * self.scale
        new_h = box_h * self.scale

        center_x = x + box_w / 2
        center_y = y + box_h / 2

        x1 = int(center_x - new_w / 2)
        y1 = int(center_y - new_h / 2)
        x2 = int(center_x + new_w / 2)
        y2 = int(center_y + new_h / 2)

        # How much we fall out of bounds
        pad_top = max(0, -y1)
        pad_bottom = max(0, y2 - src_h + 1)
        pad_left = max(0, -x1)
        pad_right = max(0, x2 - src_w + 1)

        # The valid region inside the image
        y1_clamped = max(0, y1)
        y2_clamped = min(src_h - 1, y2)
        x1_clamped = max(0, x1)
        x2_clamped = min(src_w - 1, x2)

        cropped_valid = image[y1_clamped:y2_clamped + 1, x1_clamped:x2_clamped + 1]

        if pad_top > 0 or pad_bottom > 0 or pad_left > 0 or pad_right > 0:
            cropped = cv2.copyMakeBorder(
                cropped_valid, 
                pad_top, pad_bottom, pad_left, pad_right, 
                cv2.BORDER_CONSTANT, value=[0, 0, 0]
            )
        else:
            cropped = cropped_valid

        return cv2.resize(cropped, (self.input_size[1], self.input_size[0]))

    def _preprocess(self, image, bbox_xywh):
        """Crop, resize to 80x80, convert to NCHW float32."""
        face = self._crop_face(image, bbox_xywh)
        face = face.astype(np.float32)
        face = np.transpose(face, (2, 0, 1))  # HWC -> CHW
        face = np.expand_dims(face, axis=0)    # Add batch dim -> NCHW
        return face

    def _softmax(self, x):
        """Numerically stable softmax."""
        e_x = np.exp(x - np.max(x, axis=1, keepdims=True))
        return e_x / e_x.sum(axis=1, keepdims=True)

    def check(self, image, face_location):
        """
        Run liveness check on a detected face.

        Args:
            image: Original full frame as numpy array (RGB format from PIL).
            face_location: Tuple (top, right, bottom, left) from InsightFace detection.

        Returns:
            dict with keys:
                - is_real (bool): Whether the face passes liveness.
                - score (float): The "Real" confidence score (0-1).
                - label (str): "Real" or "Spoof".
        """
        if not self.ready:
            # Fail-open: if model not loaded, allow through
            return {"is_real": True, "score": 1.0, "label": "Real (unchecked)"}

        try:
            # Convert RGB to BGR for the model (it expects BGR)
            if len(image.shape) == 3 and image.shape[2] == 3:
                bgr_image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            else:
                bgr_image = image

            # Convert InsightFace location (top, right, bottom, left) -> (x, y, w, h)
            top, right, bottom, left = face_location
            bbox_xywh = [int(left), int(top), int(right - left), int(bottom - top)]

            # Preprocess and run inference
            input_tensor = self._preprocess(bgr_image, bbox_xywh)
            outputs = self.session.run([self.output_name], {self.input_name: input_tensor})

            # Softmax on 3-class output: [Spoof, Real, Spoof]
            logits = outputs[0]
            probs = self._softmax(logits)

            # "Real" score is at index 1
            real_score = float(probs[0, 1])
            is_real = real_score >= self.threshold

            label = "Real" if is_real else "Spoof"
            print(f"[SHIELD] Liveness: {label} (score={real_score:.3f}, threshold={self.threshold})")

            return {
                "is_real": is_real,
                "score": real_score,
                "label": label
            }

        except Exception as e:
            print(f"[ERROR] Anti-spoof check error: {e}")
            # Fail-closed on error: reject the face
            return {"is_real": False, "score": 0.0, "label": "Error"}


# Global instance
print("[INIT] Initializing Anti-Spoofing (MiniFASNetV2)...")
anti_spoof_checker = AntiSpoofChecker()
