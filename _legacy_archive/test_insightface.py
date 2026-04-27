import onnxruntime as ort
import insightface
from insightface.app import FaceAnalysis

print("ONNX Runtime version:", ort.__version__)
print("InsightFace version:", insightface.__version__)

try:
    print("Trying to create FaceAnalysis...")
    app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])
    print("FaceAnalysis created successfully.")
except Exception as e:
    print("Failed with providers kwarg:", e)
    
try:
    print("Trying without providers kwarg...")
    app = FaceAnalysis(name='buffalo_l')
    app.prepare(ctx_id=0, det_size=(640, 640))
    print("FaceAnalysis created and prepared successfully.")
except Exception as e:
    print("Failed without providers kwarg:", e)
    import traceback
    traceback.print_exc()
