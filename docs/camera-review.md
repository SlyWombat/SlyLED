# SlyLED Code Review
**Model:** gemini-robotics-er-1.6-preview  
**Date:** 2026-04-15 10:43  
**Files reviewed:** 10  
**Groups:** camera

---

## Group: camera — firmware/orangepi/camera_server.py, firmware/orangepi/detector.py, firmware/orangepi/tracker.py, firmware/orangepi/beam_detector.py, firmware/orangepi/depth_estimator.py, desktop/shared/cv_engine.py, desktop/shared/stereo_engine.py, desktop/shared/structured_light.py, desktop/shared/surface_analyzer.py, desktop/shared/space_mapper.py

This review covers the SlyLED lighting control system's camera node firmware and shared orchestrator-side computer vision modules.

---

### 1. firmware/orangepi/camera_server.py

**Bugs & Logic Errors**
*   **Initialization Order:** `_apply_saved_v4l2()` is called inside `_detect_hardware()`, which is called before `log` is initialized at the bottom of the script. This will cause a `NameError` on startup if saved settings exist.
*   **Config Mutation:** In `config_post`, `_config["cameraCfg"]` is updated. If a user sets `preferred: true` for one camera, the code iterates through `cfg_map` to clear others. However, `cfg_map` is a reference to `_config["cameraCfg"]`. If multiple requests hit simultaneously, the state could become inconsistent.
*   **Subprocess usage:** In `snapshot()`, `tools` list uses absolute paths like `/usr/bin/fswebcam`. If these are missing, it continues, but `_cv_capture` is tried first. If `cv2` is present but fails, it falls back to CLI. This is robust, but the `timeout=10` in `subprocess.run` might be too long for a responsive UI.

**Security Issues**
*   **Command Injection:** In `camera_controls_set()`, `f"{k}={v}"` is passed as an argument to `v4l2-ctl`. While `subprocess.run` uses a list (preventing shell injection), `v4l2-ctl` itself might be vulnerable to argument injection if `k` or `v` contains dashes or special control sequences. Input should be strictly validated as alphanumeric/integers.
*   **Unauthenticated Control:** `reboot`, `config/reset`, and `camera/controls` are exposed without any authentication. An attacker on the local network can brick the node or factory reset it.

**Universal Coordinate Correctness**
*   **Flip Logic:** `_cv_capture` applies flips correctly using OpenCV constants. However, `pixel_to_stage` uses a homography `H`. If the image is flipped *after* the homography is calculated (or if the homography was calculated on a non-flipped image), the mapping will be inverted. The system must ensure `H` is recalculated or adjusted if `flip` changes.

---

### 2. firmware/orangepi/detector.py

**Performance**
*   **Redundant Resizing:** The code performs an "optional pre-downscale" and then a "letterbox resize." If `input_size` is 320, it resizes twice. It would be more efficient to calculate the final scale factor once and perform a single `cv2.resize`.

**Robustness**
*   **Model Loading Race:** `_load()` is called inside a `with self._lock` in `detect()`, which is good. However, if `onnxruntime` fails to import, it falls back to `cv2.dnn`. If `cv2.dnn` also fails (e.g., corrupted model file), `self._session` remains `None`, leading to an attribute error in `_infer`.

---

### 3. firmware/orangepi/tracker.py

**Bugs & Logic Errors**
*   **Race Condition:** `self._tracks` is a dictionary modified in `_tick` (running in a background thread) and read in `track_count` and `debug_info` (called by Flask threads). Dictionary iteration in Python during mutation raises `RuntimeError`. Access to `self._tracks` must be wrapped in `self._lock`.
*   **Coordinate Mixup:** In `_tick`, if `_px_to_stage` is `None`, it uses `x: d["x"] + d["w"] // 2` (pixel center). It then sends this to the orchestrator as `pos: [det["x"], det["y"], 0]`. The orchestrator expects stage millimeters. Sending pixel values (e.g., 1920) as millimeters will place the "person" nearly 2 meters from the origin regardless of actual distance.

**Robustness**
*   **Persistent Capture:** If `cap.read()` fails once, `frame` becomes `None` and it falls back to `_cv_capture`. `_cv_capture` attempts to open the same device again. This will fail because `cap` (the persistent one) still holds the file descriptor. The persistent `cap` should be released before fallback or handled more gracefully.

---

### 4. firmware/orangepi/beam_detector.py

**Bugs & Logic Errors**
*   **Dark Frame Resizing:** In `detect()`, `dark_resized` is created every frame if the shapes don't match. This is expensive. The dark frame should be resized once when `set_dark_frame` is called.
*   **Median X Logic:** In `detect_center()`, `components.sort(key=lambda c: c["cx"])` then `len(components) // 2` assumes the beams are arranged horizontally. If the fixture is rotated 90 degrees (vertical multi-beam), this logic fails to identify the "center" beam correctly based on fixture index.

---

### 5. firmware/orangepi/depth_estimator.py

**Universal Coordinate Correctness**
*   **Z-Scaling:** `z = rel_depth * max_depth_mm`. This assumes a linear relationship between disparity and distance, which is incorrect for monocular depth (usually inverse). While `Depth-Anything-V2` outputs relative disparity, simply multiplying by `max_depth` is a heuristic that will cause non-linear errors in point cloud "flatness."

---

### 6. desktop/shared/cv_engine.py

**Security Issues**
*   **SSRF / Input Validation:** `fetch_snapshot` takes `camera_ip`. If this comes from a user-provided layout file without validation, it can be used to scan internal ports on the orchestrator's network.

**Code Quality**
*   **Monkey-patching:** The engine overrides `_de_mod.MODEL_PATH` at runtime. This is clever but fragile. It would be better to pass the path into the constructor of the Estimator/Detector.

---

### 7. desktop/shared/stereo_engine.py

**Universal Coordinate Correctness**
*   **Default Rotation:** `R = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]])`. This correctly maps Camera Z (forward) to Stage Y (depth) and Camera -Y (up) to Stage Z (height).
*   **Parallel Rays:** `abs(denom) < 1e-10` is a good check, but `triangulate` for N>2 uses `np.linalg.solve`. If rays are nearly parallel, this will throw a `LinAlgError`. The `try-except` is present, but it should return a specific error indicating "poor geometry."

---

### 8. desktop/shared/space_mapper.py

**Bugs & Logic Errors**
*   **Floor Normalization:** `floor.get("z", floor.get("y", 0))`. In `surface_analyzer.py`, the floor detector specifically returns `"z"`. The fallback to `"y"` suggests a confusion between coordinate systems (Y-up vs Z-up) that exists elsewhere in the code.

**Universal Coordinate Correctness**
*   **Frame Swap:** `sx = pt[0]`, `sy = pt[2]`, `sz = -pt[1]`. This is the correct conversion from Pinhole (Y-down) to Stage (Z-up).

---

### Summary of Priorities

#### P1 - Must Fix (Stability & Security)
1.  **Tracker Race Condition:** Add locking to `self._tracks` in `tracker.py` to prevent Flask thread crashes.
2.  **Initialization Crash:** Move `_detect_hardware()` after `log` initialization in `camera_server.py`.
3.  **Command Injection:** Sanitize `k` and `v` in `camera_controls_set` (firmware).
4.  **Coordinate Scaling:** Fix `tracker.py` logic to not send raw pixel coordinates as stage millimeters when `_px_to_stage` is missing.

#### P2 - Should Fix (Robustness & Performance)
1.  **V4L2 Contention:** Fix `tracker.py` to release the persistent camera handle before attempting fallback `_cv_capture`.
2.  **Auth:** Add a simple API key or token requirement for `POST` actions on the camera node.
3.  **Redundant Resize:** Optimize `detector.py` to combine resize operations.

#### P3 - Nice to Have (Code Quality)
1.  **Linearity:** Improve depth-to-mm conversion in `depth_estimator.py` using an inverse-depth model rather than linear scaling.
2.  **SSRF:** Validate `camera_ip` in `cv_engine.py`.