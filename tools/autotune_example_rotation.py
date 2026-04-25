#!/usr/bin/env python3
"""autotune_example_rotation.py — feed moondream three different example
responses with disagreeing scores + deltas, and see whether its proposed
delta tracks (a) the image content [good] or (b) the example we showed it
[bad — pure mimicry]. Apply each delta + heuristic-score the result so we
have an objective measure.

Three example variants per intent — disagreeing scores + disagreeing deltas:

  A "dim-low":  score 30, delta exposure=100  (suggests further lowering)
  B "mid-bump": score 60, delta exposure=800  (suggests moderate increase)
  C "hi-tiny":  score 90, delta exposure=250  (suggests "fine, tiny tweak")

If moondream is pattern-matching the example, its proposed delta will
copy the example's exposure value. If it's actually reasoning about the
image, all three variants should converge on similar exposure proposals
for the same image (because the right answer is image-dependent, not
example-dependent).

Run on two cameras (cam12 tracking, cam16 rpi) for one intent (aruco) so
we keep the matrix tight.
"""
from __future__ import annotations
import argparse, base64, json, sys, time
import urllib.request
from pathlib import Path

ORCH = "http://localhost:8080"
OLLAMA = "http://localhost:11434"

CAMS = {
    12: ("http://192.168.10.235:5000", 0, "tracking"),
    16: ("http://192.168.10.109:5000", 0, "rpi"),
}

CONTROLS_BRIEF = [
    {"name":"exposure_time_absolute","value":300,"min":1,"max":5000},
    {"name":"gain","value":0,"min":0,"max":100},
    {"name":"white_balance_automatic","value":1,"min":None,"max":None},
    {"name":"white_balance_temperature","value":5000,"min":2300,"max":6500},
    {"name":"auto_exposure","value":1,"min":0,"max":3},
    {"name":"brightness","value":0,"min":-64,"max":64},
    {"name":"contrast","value":57,"min":0,"max":100},
    {"name":"saturation","value":80,"min":0,"max":128},
]

# Three example responses with deliberately disagreeing exposure values.
# All otherwise sensibly shaped.
EXAMPLES = {
    "A_dim_score30": {
        "score": 30,
        "notes": ["scene contrast is poor", "shadows clipping"],
        "deltaProposal": {
            "exposure_time_absolute": 100,
            "gain": 5,
            "white_balance_automatic": 0,
            "white_balance_temperature": 4500,
        },
    },
    "B_mid_score60": {
        "score": 60,
        "notes": ["scene is dim", "exposure could be higher"],
        "deltaProposal": {
            "exposure_time_absolute": 800,
            "gain": 10,
            "white_balance_automatic": 0,
            "white_balance_temperature": 5000,
        },
    },
    "C_hi_score90": {
        "score": 90,
        "notes": ["histogram is balanced", "minor adjustment only"],
        "deltaProposal": {
            "exposure_time_absolute": 250,
            "gain": 0,
            "white_balance_automatic": 0,
            "white_balance_temperature": 5000,
        },
    },
}

PROMPT_TEMPLATE = (
    "You are a machine-vision engineer tuning a USB webcam for computer-"
    "vision detection. Inspect the attached frame and rate it 0-100 for the "
    "stated detection intent. Higher = better.\n\n"
    "Scoring guidance per intent:\n"
    "- beam: dark backdrop with concentrated bright spot is good (60-95). "
    "Blown highlights drop to 0-40.\n"
    "- aruco: balanced mid-grey with crisp markers is good (70-95). "
    "Underexposed (mean<80) or overexposed (mean>180) drops to 30-50.\n"
    "- yolo: balanced exposure, accurate colour. Good 70-95.\n"
    "- general: balanced histogram, no clipping. Good 75-95.\n\n"
    "Reply with STRICT JSON only — no prose, no markdown fences. "
    "Example of a valid response:\n"
    "{example_json}\n"
    "Use the SAME shape. The score must be a whole number 0-100. "
    "Notes must be 1-3 short observations about THIS image — not "
    "placeholder text. Use integer values within min/max from "
    "`Current controls`. Set a key to null only when you don't want "
    "to change it.\n\n"
    "Intent: {intent}\nCurrent controls:\n{controls}"
)


def http(method, url, body=None, timeout=20):
    req = urllib.request.Request(url, method=method,
        headers={"Content-Type":"application/json"})
    data = json.dumps(body).encode() if body is not None else None
    return json.loads(urllib.request.urlopen(req, data=data, timeout=timeout).read())


def snapshot(cam_fid):
    base, idx, _ = CAMS[cam_fid]
    return urllib.request.urlopen(f"{base}/snapshot?cam={idx}", timeout=10).read()


def heuristic_score(cam_fid, intent):
    return http("POST", f"{ORCH}/api/cameras/{cam_fid}/settings/auto-tune",
                 {"intent":intent,"evaluator":"heuristic","maxIterations":1},
                 timeout=15)


def restore_sly(cam_fid):
    return http("POST", f"{ORCH}/api/cameras/{cam_fid}/settings/slots/Sly/activate", {})


def apply_delta(cam_fid, delta):
    base, idx, _ = CAMS[cam_fid]
    non_null = {k:v for k,v in delta.items() if v is not None}
    return http("POST", f"{base}/camera/controls", {"cam":idx,"controls":non_null})


def resize_to_max_side(jpeg_bytes, max_side=768, quality=80):
    """Mirror desktop/shared/camera_settings.py::_frame_to_jpeg_b64 — decode,
    downscale so max(h, w) == max_side, re-encode JPEG. Keeps aspect ratio."""
    import cv2, numpy as np
    arr = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        return jpeg_bytes  # fall back to original
    h, w = arr.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    if scale < 1.0:
        arr = cv2.resize(arr, (int(w*scale), int(h*scale)),
                          interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", arr,
                            [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return buf.tobytes() if ok else jpeg_bytes


def ask_moondream(image_bytes, intent, example_json, timeout=90):
    # Match the orchestrator's behaviour at camera_settings.py:294 —
    # always resize to 768 max-side before encoding.
    resized = resize_to_max_side(image_bytes, max_side=768, quality=80)
    prompt = PROMPT_TEMPLATE.format(
        example_json=example_json,
        intent=intent,
        controls=json.dumps(CONTROLS_BRIEF, indent=2))
    body = {"model":"moondream", "prompt":prompt,
            "images":[base64.b64encode(resized).decode()],
            "format":"json", "stream":False,
            "options":{"temperature":0.1, "num_predict":300}}
    req = urllib.request.Request(f"{OLLAMA}/api/generate",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type":"application/json"})
    t0 = time.monotonic()
    r = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    elapsed = time.monotonic() - t0
    raw = r.get("response","").strip()
    return elapsed, json.loads(raw)


def main():
    cams = [12, 16]
    intent = "aruco"

    print(f"=== example-rotation test on intent={intent} ===\n")
    print(f"{'cam':14s} {'example':16s} {'mdr_score':>10s} "
          f"{'mdr_exp':>8s} {'mdr_gain':>9s} {'before':>7s} {'after':>7s} "
          f"{'delta':>7s}  notes")
    print("-" * 130)

    rows = []
    for cam_fid in cams:
        host = CAMS[cam_fid][2]
        # baseline once per camera
        restore_sly(cam_fid); time.sleep(2)
        baseline = heuristic_score(cam_fid, intent)
        before_score = (baseline.get("after") or {}).get("score")
        img = snapshot(cam_fid)
        for ex_name, ex_obj in EXAMPLES.items():
            # restore baseline for each variant
            restore_sly(cam_fid); time.sleep(2)
            ex_json = json.dumps(ex_obj)
            try:
                elapsed, parsed = ask_moondream(img, intent, ex_json, timeout=90)
            except Exception as e:
                print(f"cam{cam_fid:2d}-{host:8s} {ex_name:16s}  MOONDREAM_ERR: {e}")
                continue
            mdr_score = parsed.get("score")
            notes = parsed.get("notes") or []
            delta_raw = parsed.get("deltaProposal")
            delta = delta_raw if isinstance(delta_raw, dict) else {}
            non_null = {k:v for k,v in delta.items() if v is not None}
            mdr_exp = delta.get("exposure_time_absolute")
            mdr_gain = delta.get("gain")
            if non_null:
                apply_delta(cam_fid, delta); time.sleep(2)
                after = heuristic_score(cam_fid, intent)
                after_score = (after.get("after") or {}).get("score")
            else:
                after_score = None
            diff = (after_score - before_score) if (isinstance(after_score,(int,float)) and isinstance(before_score,(int,float))) else None
            diff_str = f"{diff:+.2f}" if diff is not None else "—"
            notes_str = " | ".join(str(n) for n in notes)[:60]
            print(f"cam{cam_fid:2d}-{host:8s} {ex_name:16s} {str(mdr_score)!s:>10s} "
                  f"{str(mdr_exp)!s:>8s} {str(mdr_gain)!s:>9s} "
                  f"{str(before_score)!s:>7s} {str(after_score)!s:>7s} "
                  f"{diff_str:>7s}  {notes_str}")
            rows.append({"cam":cam_fid,"ex":ex_name,"mdr_score":mdr_score,
                         "mdr_exp":mdr_exp,"mdr_gain":mdr_gain,
                         "before":before_score,"after":after_score,
                         "diff":diff,"notes":notes,"delta":delta})
            restore_sly(cam_fid); time.sleep(2)

    # Save NDJSON
    out = Path("/home/sly/slyled2/docs/live-test-sessions/2026-04-25/example-rotation.ndjson")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in rows: fh.write(json.dumps(r) + "\n")
    print(f"\nSaved {len(rows)} cells to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
