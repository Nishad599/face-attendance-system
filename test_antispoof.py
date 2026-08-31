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

Picking a threshold (do this on the VM, with the real camera):

    # capture ~10 real faces, one run per person/lighting condition
    python test_antispoof.py --camera --label real

    # capture ~10 spoof attempts: a face on a phone screen, a printout
    python test_antispoof.py --camera --label spoof

    # get a recommended ANTISPOOF_THRESHOLD from the collected samples
    python test_antispoof.py --suggest

    # start over
    python test_antispoof.py --reset

Labelled scores accumulate in .antispoof_scores.json. --suggest picks the
threshold that misclassifies fewest samples, weighting an accepted spoof as
three times worse than a real face being asked to retry.
"""

import os
import sys
import glob
import json
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


SCORES_FILE = ".antispoof_scores.json"


def load_scores():
    if not os.path.exists(SCORES_FILE):
        return {"real": [], "spoof": []}
    try:
        with open(SCORES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {"real": data.get("real", []), "spoof": data.get("spoof", [])}
    except (ValueError, OSError):
        return {"real": [], "spoof": []}


def save_scores(data):
    with open(SCORES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def suggest_threshold():
    """Recommend a threshold from the labelled samples collected so far.

    Doing this by eye is where the previous attempt stalled: the two groups
    overlap in the tails, and picking a number from a printed table is guesswork.
    """
    data = load_scores()
    real = sorted(s["score"] for s in data["real"])
    spoof = sorted(s["score"] for s in data["spoof"])

    print(f"Collected: {len(real)} real, {len(spoof)} spoof "
          f"(from {SCORES_FILE})\n")
    if len(real) < 5 or len(spoof) < 5:
        print("Not enough samples yet. Aim for at least 10 of each, captured on")
        print("the VM with the real camera and real spoof attempts:")
        print("    python test_antispoof.py --camera --label real")
        print("    python test_antispoof.py --camera --label spoof   # phone/printout")
        return

    def pct(values, p):
        if not values:
            return 0.0
        k = (len(values) - 1) * p
        lo, hi = int(k), min(int(k) + 1, len(values) - 1)
        return values[lo] + (values[hi] - values[lo]) * (k - lo)

    print(f"REAL   min={real[0]:.4f}  p05={pct(real, 0.05):.4f}  "
          f"median={pct(real, 0.5):.4f}  max={real[-1]:.4f}")
    print(f"SPOOF  min={spoof[0]:.4f}  p95={pct(spoof, 0.95):.4f}  "
          f"median={pct(spoof, 0.5):.4f}  max={spoof[-1]:.4f}\n")

    # Pick the threshold that misclassifies the fewest samples; break ties by
    # choosing the midpoint of the widest gap so the margin is as large as
    # possible on both sides.
    candidates = sorted(set(real + spoof))
    best, best_err = None, None
    for i in range(len(candidates)):
        t = candidates[i]
        # reject anything scoring BELOW the threshold
        false_reject = sum(1 for r in real if r < t)     # real faces blocked
        false_accept = sum(1 for s in spoof if s >= t)   # spoofs let through
        # A spoof getting in is worse than a real face being asked to retry,
        # so weight false accepts more heavily.
        err = false_reject + 3 * false_accept
        if best_err is None or err < best_err:
            best, best_err = t, err

    false_reject = sum(1 for r in real if r < best)
    false_accept = sum(1 for s in spoof if s >= best)
    gap_lo = max([s for s in spoof if s < best], default=0.0)
    gap_hi = min([r for r in real if r >= best], default=1.0)
    midpoint = round((gap_lo + gap_hi) / 2, 3)

    print(f"Suggested ANTISPOOF_THRESHOLD={midpoint}")
    print(f"  separates {gap_lo:.4f} (highest spoof below) "
          f"from {gap_hi:.4f} (lowest real above)")
    print(f"  at this threshold: {false_reject}/{len(real)} real faces rejected, "
          f"{false_accept}/{len(spoof)} spoofs accepted")
    if false_accept:
        print("\n  WARNING: some spoofs still pass. Collect more samples, or")
        print("  accept a higher threshold and more retries for real users.")
    print("\nAdd to .env:")
    print(f"    ANTISPOOF_THRESHOLD={midpoint}")
    print("    # and REMOVE ANTISPOOF_DISABLED=1 to turn liveness back on")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    use_camera = "--camera" in sys.argv

    if "--reset" in sys.argv:
        if os.path.exists(SCORES_FILE):
            os.remove(SCORES_FILE)
        print(f"Cleared {SCORES_FILE}")
        return

    if "--suggest" in sys.argv:
        suggest_threshold()
        return

    label = None
    if "--label" in sys.argv:
        idx = sys.argv.index("--label")
        if idx + 1 < len(sys.argv):
            label = sys.argv[idx + 1].strip().lower()
        if label not in ("real", "spoof"):
            print("[ERROR] --label must be followed by 'real' or 'spoof'")
            sys.exit(2)
        # the label value is not a path
        args = [a for a in args if a != label]

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
    labelled = []
    for name, img in images:
        bbox = detect_face(img)
        if bbox is None:
            print(f"{os.path.basename(name)[:44]:<45} {'no face':>12} {'no face':>12}")
            continue
        old, fixed = score(session, in_cfg.name, out_cfg.name, size, img, bbox)
        fixed_scores.append(fixed)
        labelled.append({"source": os.path.basename(name), "score": round(float(fixed), 4)})
        print(f"{os.path.basename(name)[:44]:<45} {old:>12.4f} {fixed:>12.4f}")

    if fixed_scores:
        print("-" * 72)
        print(f"FIXED scores: min={min(fixed_scores):.4f} "
              f"max={max(fixed_scores):.4f} mean={sum(fixed_scores)/len(fixed_scores):.4f}")

        if label:
            data = load_scores()
            data[label].extend(labelled)
            save_scores(data)
            print(f"\nRecorded {len(labelled)} sample(s) as '{label}' "
                  f"({len(data['real'])} real / {len(data['spoof'])} spoof so far).")
            print("When you have at least 10 of each, run:")
            print("    python test_antispoof.py --suggest")
        else:
            print("\nIf these are REAL faces, the FIXED score should be high (well above 0.5).")
            print("To pick a threshold mechanically instead of by eye, label your samples:")
            print("    python test_antispoof.py --camera --label real")
            print("    python test_antispoof.py --camera --label spoof")
            print("    python test_antispoof.py --suggest")


if __name__ == "__main__":
    main()
