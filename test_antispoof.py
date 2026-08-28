"""
test_antispoof.py — check the liveness model against real photos.

The old crop padded out-of-bounds regions with BLACK, putting large flat areas
into the 80x80 input that never occur in training data and dragging the score
down. The fixed crop clamps the scale and shifts the box back inside the frame.

(Normalisation was verified empirically: this ONNX export wants RAW 0-255 —
scaling to 0-1 makes the model stop discriminating entirely.)

This script scores the SAME face with both crops so you can confirm the fix on
real images, and helps you pick a threshold.

Usage:
    # score one or more images
    python test_antispoof.py face1.jpg face2.jpg

    # score every image in a folder (e.g. a student's captured photos)
    python test_antispoof.py student_photos/000_Nishad/

    # grab a frame from the webcam and score it
    python test_antispoof.py --camera

Interpreting the output: for a REAL face you want a high score with the FIXED
column. Take a few real photos and a few spoof attempts (a face on a phone
screen or a printout) and pick a threshold that separates them, then set it in
.env as ANTISPOOF_THRESHOLD.
"""

import os
import sys
import glob
import numpy as np

try:
    import cv2
except ImportError:
    print("[ERROR] opencv (cv2) is required: pip install opencv-python")
    sys.exit(1)

try:
    import onnxruntime as ort
except ImportError:
    print("[ERROR] onnxruntime is required: pip install onnxruntime")
    sys.exit(1)

MODEL = os.path.join("models", "anti_spoof", "MiniFASNetV2.onnx")
SCALE = 2.7


def softmax(x):
    e = np.exp(x - np.max(x, axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def crop_fixed(image, bbox, size):
    """The corrected crop: clamp the scale, shift in-bounds, no black padding."""
    src_h, src_w = image.shape[:2]
    x, y, box_w, box_h = bbox
    box_w, box_h = max(1, int(box_w)), max(1, int(box_h))
    scale = min((src_h - 1) / box_h, (src_w - 1) / box_w, SCALE)
    new_w, new_h = box_w * scale, box_h * scale
    cx, cy = box_w / 2 + x, box_h / 2 + y
    lx, ly = cx - new_w / 2, cy - new_h / 2
    rx, ry = cx + new_w / 2, cy + new_h / 2
    if lx < 0:
        rx -= lx; lx = 0
    if ly < 0:
        ry -= ly; ly = 0
    if rx > src_w - 1:
        lx -= rx - src_w + 1; rx = src_w - 1
    if ry > src_h - 1:
        ly -= ry - src_h + 1; ry = src_h - 1
    x1, y1 = int(max(0, lx)), int(max(0, ly))
    x2, y2 = int(min(src_w - 1, rx)), int(min(src_h - 1, ry))
    out = image[y1:y2 + 1, x1:x2 + 1]
    if out.size == 0:
        out = image
    return cv2.resize(out, size)


def crop_old(image, bbox, size):
    """The previous crop: full scale with black padding when out of bounds."""
    src_h, src_w = image.shape[:2]
    x, y, box_w, box_h = bbox
    new_w, new_h = box_w * SCALE, box_h * SCALE
    cx, cy = x + box_w / 2, y + box_h / 2
    x1, y1 = int(cx - new_w / 2), int(cy - new_h / 2)
    x2, y2 = int(cx + new_w / 2), int(cy + new_h / 2)
    pt, pb = max(0, -y1), max(0, y2 - src_h + 1)
    pl, pr = max(0, -x1), max(0, x2 - src_w + 1)
    valid = image[max(0, y1):min(src_h - 1, y2) + 1, max(0, x1):min(src_w - 1, x2) + 1]
    if valid.size == 0:
        valid = image
    if pt or pb or pl or pr:
        valid = cv2.copyMakeBorder(valid, pt, pb, pl, pr, cv2.BORDER_CONSTANT, value=[0, 0, 0])
    return cv2.resize(valid, size)


def detect_face(bgr):
    """Find the largest face. Uses InsightFace if available, else Haar cascade."""
    try:
        from asian_face_model import asian_face_recognizer
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        faces = asian_face_recognizer.detect_faces_optimized(rgb)
        if faces:
            top, right, bottom, left = faces[0]["location"]
            return [int(left), int(top), int(right - left), int(bottom - top)]
    except Exception:
        pass
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    found = cascade.detectMultiScale(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), 1.1, 5)
    if len(found) == 0:
        return None
    x, y, w, h = max(found, key=lambda f: f[2] * f[3])
    return [int(x), int(y), int(w), int(h)]


def score(session, in_name, out_name, size, bgr, bbox):
    """Return (old_score, fixed_score) for the 'real' class."""
    results = []
    for crop_fn in (crop_old, crop_fixed):
        # RAW 0-255 — this export has normalisation baked in (see anti_spoofing.py)
        face = crop_fn(bgr, bbox, (size[1], size[0])).astype(np.float32)
        tensor = np.expand_dims(np.transpose(face, (2, 0, 1)), axis=0)
        probs = softmax(session.run([out_name], {in_name: tensor})[0])
        results.append(float(probs[0, 1]))       # index 1 = "real"
    return results[0], results[1]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    use_camera = "--camera" in sys.argv

    if not os.path.exists(MODEL):
        print(f"[ERROR] model not found: {MODEL}")
        sys.exit(1)

    session = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
    in_cfg, out_cfg = session.get_inputs()[0], session.get_outputs()[0]
    size = tuple(in_cfg.shape[2:])
    print(f"Model: {MODEL}  input={size}\n")

    images = []
    if use_camera:
        cap = cv2.VideoCapture(0)
        print("Capturing from camera… look at the lens.")
        for _ in range(10):
            cap.read()
        ok, frame = cap.read()
        cap.release()
        if not ok:
            print("[ERROR] could not read from camera")
            sys.exit(1)
        images.append(("<camera>", frame))

    for path in args:
        if os.path.isdir(path):
            files = sorted(sum([glob.glob(os.path.join(path, e))
                                for e in ("*.jpg", "*.jpeg", "*.png")], []))
        else:
            files = [path]
        for f in files:
            img = cv2.imread(f)
            if img is None:
                print(f"[skip] unreadable: {f}")
                continue
            images.append((f, img))

    if not images:
        print(__doc__)
        sys.exit(0)

    print(f"{'image':<45} {'OLD crop':>12} {'FIXED crop':>12}")
    print("-" * 72)
    fixed_scores = []
    for name, img in images:
        bbox = detect_face(img)
        if bbox is None:
            print(f"{os.path.basename(name)[:44]:<45} {'no face':>12} {'no face':>12}")
            continue
        old, fixed = score(session, in_cfg.name, out_cfg.name, size, img, bbox)
        fixed_scores.append(fixed)
        print(f"{os.path.basename(name)[:44]:<45} {old:>12.4f} {fixed:>12.4f}")

    if fixed_scores:
        print("-" * 72)
        print(f"FIXED scores: min={min(fixed_scores):.4f} "
              f"max={max(fixed_scores):.4f} mean={sum(fixed_scores)/len(fixed_scores):.4f}")
        print("\nIf these are REAL faces, the FIXED score should be high (well above 0.5).")
        print("Now run the same test on spoof attempts (a face shown on a phone/printout)")
        print("and set ANTISPOOF_THRESHOLD in .env between the two groups.")


if __name__ == "__main__":
    main()
