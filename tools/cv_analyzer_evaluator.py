#!/usr/bin/env python3
"""cv_analyzer_evaluator.py — deterministic OpenCV-based camera analyzer.

Operator-proposed alternative to moondream / heuristic evaluators. No model,
no LLM, no random output — measure histogram + LAB colour cast directly,
emit a concrete V4L2 deltaProposal.

Usage as a library:
    delta, diagnostics = analyze_and_propose(image_bytes, current_controls)

Usage as a script (compare across (camera, intent) cells like the moondream
matrix):
    /usr/bin/python3 tools/cv_analyzer_evaluator.py --cam-fid 16 --intent aruco

The analyser is intent-aware via target-mean + target-stddev:
    beam     mean=80   std=40   (dark backdrop, bright spot)
    aruco    mean=125  std=50   (balanced mid-grey)
    yolo     mean=125  std=45
    general  mean=125  std=45
"""
from __future__ import annotations
import argparse, json, sys, time
import urllib.request
from pathlib import Path
import numpy as np
import cv2

ORCH = "http://localhost:8080"
CAMS = {
    12: ("http://192.168.10.235:5000", 0, "tracking"),
    13: ("http://192.168.10.235:5000", 1, "tracking"),
    16: ("http://192.168.10.109:5000", 0, "rpi"),
}

INTENT_TARGETS = {
    "beam":    {"mean": 80,  "std": 40},
    "aruco":   {"mean": 125, "std": 50},
    "yolo":    {"mean": 125, "std": 45},
    "general": {"mean": 125, "std": 45},
}

# How aggressive to step exposure / gain / temp per call. Conservative;
# the loop iterates if needed.
STEP = {
    "exposure_factor_underexposed": 2.5,   # multiply
    "exposure_factor_overexposed": 0.5,    # multiply
    "gain_step_underexposed": 20,          # add
    "gain_step_overexposed": -10,          # add (clamped to >=0)
    "wb_temp_step_warmer": 500,            # add (Kelvin)
    "wb_temp_step_cooler": -500,
}


def analyse(image_bytes, intent="general"):
    """Decode + analyse; return (diagnostics_dict, suggestions_list)."""
    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return {"_error": "decode failed"}, []

    # --- contrast / exposure ---
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    total = gray.size
    contrast_std = float(np.std(gray))
    mean_brightness = float(np.mean(gray))
    clipped_high = float(np.sum(gray >= 245) / total)
    clipped_low = float(np.sum(gray <= 10) / total)

    # --- LAB colour cast ---
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    _l, a, b = cv2.split(lab)
    mean_a = float(np.mean(a))
    mean_b = float(np.mean(b))
    a_cast = mean_a - 128.0   # +ve => magenta, -ve => green
    b_cast = mean_b - 128.0   # +ve => yellow,  -ve => blue

    diag = {
        "mean": round(mean_brightness, 1),
        "std":  round(contrast_std, 1),
        "clippedHighlights": round(clipped_high, 4),
        "clippedShadows": round(clipped_low, 4),
        "labA_cast": round(a_cast, 1),
        "labB_cast": round(b_cast, 1),
    }

    # --- intent-aware suggestions ---
    target = INTENT_TARGETS.get(intent, INTENT_TARGETS["general"])
    target_mean = target["mean"]
    target_std = target["std"]

    suggestions = []
    # Brightness
    if mean_brightness < target_mean - 25:
        suggestions.append(("underexposed",
                             f"mean {mean_brightness:.0f} < target {target_mean - 25}"))
    elif mean_brightness > target_mean + 30:
        suggestions.append(("overexposed",
                             f"mean {mean_brightness:.0f} > target {target_mean + 30}"))
    # Clipping
    if clipped_high > 0.05:
        suggestions.append(("highlights_clipping",
                             f"{clipped_high*100:.1f}% pixels >= 245"))
    if clipped_low > 0.05:
        suggestions.append(("shadows_crushed",
                             f"{clipped_low*100:.1f}% pixels <= 10"))
    # Contrast
    if contrast_std < target_std - 15:
        suggestions.append(("low_contrast",
                             f"std {contrast_std:.0f} < target {target_std - 15}"))
    # Colour cast
    if abs(a_cast) > 5:
        if a_cast > 5:  suggestions.append(("magenta_cast", f"a_cast +{a_cast:.0f}"))
        else:           suggestions.append(("green_cast", f"a_cast {a_cast:.0f}"))
    if abs(b_cast) > 5:
        if b_cast > 5:  suggestions.append(("yellow_cast", f"b_cast +{b_cast:.0f}"))
        else:           suggestions.append(("blue_cast", f"b_cast {b_cast:.0f}"))

    return diag, suggestions


def propose_delta(diag, suggestions, current_controls):
    """Map suggestion list → concrete V4L2 deltaProposal.

    `current_controls` is a dict of {name: value} for at least exposure,
    gain, white_balance_automatic, white_balance_temperature, with
    {name: (min, max)} ranges discoverable in CONTROL_RANGES below.
    """
    # Hardcoded ranges for our basement rig (matches /camera/controls
    # output for both EMEET 4K + the rpi cam used).
    ranges = {
        "exposure_time_absolute": (1, 5000),
        "gain": (0, 100),
        "white_balance_temperature": (2300, 6500),
    }

    delta = {}
    cur_exp = current_controls.get("exposure_time_absolute", 300)
    cur_gain = current_controls.get("gain", 0)
    cur_wbt = current_controls.get("white_balance_temperature", 5000)

    tags = {tag for tag, _ in suggestions}

    # Lock auto WB so manual adjustments stick.
    if current_controls.get("white_balance_automatic") == 1 and (
        "yellow_cast" in tags or "blue_cast" in tags
        or "magenta_cast" in tags or "green_cast" in tags):
        delta["white_balance_automatic"] = 0

    # Exposure / gain — handle underexposed vs overexposed; clipping
    # outranks the simple mean check.
    if "highlights_clipping" in tags:
        new_exp = max(ranges["exposure_time_absolute"][0],
                      int(cur_exp * STEP["exposure_factor_overexposed"]))
        delta["exposure_time_absolute"] = new_exp
        if cur_gain > 0:
            delta["gain"] = max(0, cur_gain + STEP["gain_step_overexposed"])
    elif "shadows_crushed" in tags or "underexposed" in tags:
        new_exp = min(ranges["exposure_time_absolute"][1],
                      int(cur_exp * STEP["exposure_factor_underexposed"]))
        delta["exposure_time_absolute"] = new_exp
        if cur_gain < ranges["gain"][1] - STEP["gain_step_underexposed"]:
            delta["gain"] = min(ranges["gain"][1],
                                 cur_gain + STEP["gain_step_underexposed"])
    elif "overexposed" in tags:
        new_exp = max(ranges["exposure_time_absolute"][0],
                      int(cur_exp * 0.7))
        delta["exposure_time_absolute"] = new_exp

    # White balance — push wb_temp toward neutral.
    new_wbt = cur_wbt
    if "yellow_cast" in tags:
        new_wbt = max(ranges["white_balance_temperature"][0],
                      cur_wbt + STEP["wb_temp_step_cooler"])
    elif "blue_cast" in tags:
        new_wbt = min(ranges["white_balance_temperature"][1],
                      cur_wbt + STEP["wb_temp_step_warmer"])
    if new_wbt != cur_wbt:
        delta["white_balance_temperature"] = new_wbt

    # No-op detection — if the analyser found nothing actionable, return
    # an empty delta so the caller can decide to stop iterating.
    return delta


def http(method, url, body=None, timeout=15):
    req = urllib.request.Request(url, method=method,
        headers={"Content-Type":"application/json"})
    data = json.dumps(body).encode() if body is not None else None
    return json.loads(urllib.request.urlopen(req, data=data, timeout=timeout).read())


def snapshot(cam_fid):
    base, idx, _ = CAMS[cam_fid]
    return urllib.request.urlopen(f"{base}/snapshot?cam={idx}", timeout=10).read()


def get_controls(cam_fid):
    base, idx, _ = CAMS[cam_fid]
    r = http("GET", f"{base}/camera/controls?cam={idx}")
    return {c["name"]: c.get("value") for c in r.get("controls", [])
            if c.get("value") is not None}


def heuristic_score(cam_fid, intent):
    return http("POST", f"{ORCH}/api/cameras/{cam_fid}/settings/auto-tune",
                 {"intent":intent,"evaluator":"heuristic","maxIterations":1})


def restore_sly(cam_fid):
    return http("POST", f"{ORCH}/api/cameras/{cam_fid}/settings/slots/Sly/activate", {})


def apply_delta(cam_fid, delta):
    base, idx, _ = CAMS[cam_fid]
    non_null = {k:v for k,v in delta.items() if v is not None}
    return http("POST", f"{base}/camera/controls", {"cam":idx,"controls":non_null})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam-fid", type=int)
    ap.add_argument("--cams", default="12,16",
                    help="comma-separated camera fixture ids")
    ap.add_argument("--intent", default="aruco",
                    choices=["general","beam","aruco","yolo"])
    ap.add_argument("--no-apply", action="store_true",
                    help="just analyse + propose, don't apply")
    args = ap.parse_args()

    cams = [args.cam_fid] if args.cam_fid else [int(c) for c in args.cams.split(",")]

    print(f"=== CV-analyzer evaluator on intent={args.intent} ===\n")
    print(f"{'cam':14s} {'before':>7s} {'after':>7s} {'delta':>7s}  "
          f"{'mean':>5s} {'std':>5s} {'highC':>6s} {'shadC':>6s} "
          f"{'a_cast':>6s} {'b_cast':>6s}  proposal")
    print("-" * 145)

    for cam_fid in cams:
        host = CAMS[cam_fid][2]
        # baseline
        restore_sly(cam_fid); time.sleep(2)
        baseline_h = heuristic_score(cam_fid, args.intent)
        before = (baseline_h.get("after") or {}).get("score")
        # analyse the baseline frame
        img = snapshot(cam_fid)
        diag, suggestions = analyse(img, args.intent)
        # propose
        cur = get_controls(cam_fid)
        delta = propose_delta(diag, suggestions, cur)
        if not args.no_apply and delta:
            apply_delta(cam_fid, delta); time.sleep(2)
            after_h = heuristic_score(cam_fid, args.intent)
            after = (after_h.get("after") or {}).get("score")
        else:
            after = None
        diff = (after - before) if (isinstance(after,(int,float)) and isinstance(before,(int,float))) else None
        diff_str = f"{diff:+.2f}" if diff is not None else "—"
        proposal_str = ", ".join(f"{k[:3]}={v}" for k,v in delta.items())[:60]
        print(f"cam{cam_fid:2d}-{host:8s} {str(before)!s:>7s} {str(after)!s:>7s} "
              f"{diff_str:>7s}  "
              f"{diag.get('mean'):>5} {diag.get('std'):>5} "
              f"{diag.get('clippedHighlights'):>6} {diag.get('clippedShadows'):>6} "
              f"{diag.get('labA_cast'):>+6.1f} {diag.get('labB_cast'):>+6.1f}  "
              f"{proposal_str}")
        print(f"  suggestions: {[s for s,_ in suggestions]}")
        # restore for next
        restore_sly(cam_fid); time.sleep(2)

    return 0


if __name__ == "__main__":
    sys.exit(main())
