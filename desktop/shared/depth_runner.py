"""depth_runner.py — out-of-process ZoeDepth inference server (#598).

Runs inside a dedicated venv under
%LOCALAPPDATA%\\SlyLED\\runtimes\\depth\\venv so the orchestrator process
never has to import torch / transformers. The orchestrator spawns this
script via the runtime's python.exe and proxies inference over localhost
HTTP.

Wire format:
  GET  /health         → {"ok": true, "model": "...", "loaded": bool}
  POST /infer          body = raw JPEG bytes
                       returns application/octet-stream, raw float32 depth
                       in MILLIMETRES, row-major, with headers:
                         X-Depth-Shape: "H,W"
                         X-Depth-Dtype: "float32"
                         X-Inference-Ms: int
  POST /shutdown       graceful exit

Startup contract:
  - Binds 127.0.0.1 on an ephemeral port.
  - Prints "PORT=<n>" as the very first line of stdout, flushed.
    (Any model-load chatter goes to stderr.)
  - Exits with code 0 on /shutdown or after IDLE_TIMEOUT_S seconds of
    no activity (default 300s).
"""

import os
import sys
import time
import threading
import logging

IDLE_TIMEOUT_S = int(os.environ.get("SLYLED_DEPTH_IDLE_S", "300"))
MODEL_ID = os.environ.get("SLYLED_DEPTH_MODEL", "Intel/zoedepth-nyu-kitti")

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="[depth-runner] %(message)s",
)
log = logging.getLogger("depth_runner")

_last_activity = time.time()
_pipeline = None
_pipeline_lock = threading.Lock()


def _load_pipeline():
    global _pipeline
    with _pipeline_lock:
        if _pipeline is not None:
            return _pipeline
        import torch
        from transformers import AutoImageProcessor, ZoeDepthForDepthEstimation
        log.info("loading %s", MODEL_ID)
        t0 = time.time()
        processor = AutoImageProcessor.from_pretrained(MODEL_ID)
        model = ZoeDepthForDepthEstimation.from_pretrained(MODEL_ID)
        model.eval()
        device = "cuda" if torch.cuda.is_available() else "cpu"
        log.info("loaded in %.1fs on %s", time.time() - t0, device)
        _pipeline = (processor, model, device)
        return _pipeline


def _idle_watchdog():
    while True:
        time.sleep(15)
        if time.time() - _last_activity > IDLE_TIMEOUT_S:
            log.info("idle for %ds, exiting", IDLE_TIMEOUT_S)
            os._exit(0)


def _build_app():
    from flask import Flask, request, Response, jsonify
    app = Flask(__name__)

    @app.get("/health")
    def health():
        global _last_activity
        _last_activity = time.time()
        return jsonify(
            ok=True,
            model=MODEL_ID,
            loaded=_pipeline is not None,
            idleTimeoutS=IDLE_TIMEOUT_S,
        )

    @app.post("/infer")
    def infer():
        global _last_activity
        _last_activity = time.time()
        jpg = request.get_data(cache=False, as_text=False)
        if not jpg:
            return jsonify(err="empty body — expected JPEG bytes"), 400
        try:
            import io
            import traceback
            from PIL import Image
            import numpy as np
            import torch
        except Exception as e:
            return jsonify(err=f"runtime import failed: {e}"), 500
        try:
            processor, model, _device = _load_pipeline()
        except Exception as e:
            log.exception("model load failed")
            return jsonify(err=f"model load failed: {e}",
                           traceback=traceback.format_exc()), 500

        try:
            img = Image.open(io.BytesIO(jpg)).convert("RGB")
        except Exception as e:
            return jsonify(err=f"bad JPEG: {e}"), 400

        # Wrap inference so any backend exception surfaces as structured
        # JSON with the real Python traceback instead of Flask's default
        # HTML 500 page. The orchestrator reads the JSON body and
        # includes the specific error in the user-visible message.
        try:
            t0 = time.time()
            inputs = processor(images=img, return_tensors="pt")
            with torch.no_grad():
                out = model(**inputs)
            pred = torch.nn.functional.interpolate(
                out.predicted_depth.unsqueeze(1),
                size=img.size[::-1],
                mode="bicubic",
                align_corners=False,
            ).squeeze().cpu().numpy()
            depth_mm = (pred * 1000.0).astype(np.float32, copy=False)
            inf_ms = int((time.time() - t0) * 1000)
        except Exception as e:
            log.exception("inference failed")
            return jsonify(err=f"inference failed: {type(e).__name__}: {e}",
                           traceback=traceback.format_exc()), 500

        _last_activity = time.time()
        h, w = depth_mm.shape
        resp = Response(depth_mm.tobytes(), mimetype="application/octet-stream")
        resp.headers["X-Depth-Shape"] = f"{h},{w}"
        resp.headers["X-Depth-Dtype"] = "float32"
        resp.headers["X-Inference-Ms"] = str(inf_ms)
        return resp

    @app.post("/shutdown")
    def shutdown():
        log.info("shutdown requested")
        threading.Thread(target=lambda: (time.sleep(0.1), os._exit(0)), daemon=True).start()
        return jsonify(ok=True)

    return app


def main():
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    sys.stdout.write(f"PORT={port}\n")
    sys.stdout.flush()

    threading.Thread(target=_idle_watchdog, daemon=True).start()

    app = _build_app()
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
