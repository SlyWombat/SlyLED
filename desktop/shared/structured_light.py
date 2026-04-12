"""
structured_light.py — Beam-as-structured-light for 3D model refinement (#236).

During mover calibration sweeps, the beam illuminates surfaces. By knowing
the beam's pan/tilt (→ 3D ray from fixture position) and observing which
pixel the beam hits (→ surface contact point), we accumulate definite
surface contact points that refine the 3D environment model.

Each contact point is more accurate than monocular depth because:
- The beam pixel position is precise (bright centroid)
- The ray direction is known (from pan/tilt to 3D via fixture geometry)
- Where the ray intersects a known surface gives exact 3D position
"""

import logging
import math

log = logging.getLogger("slyled")


def accumulate_beam_hits(sweep_samples, fixture_pos, orientation,
                         surfaces, pan_range=540, tilt_range=180):
    """Convert sweep samples to confirmed surface contact points.

    For each sweep sample with stage coordinates:
      1. Convert (pan, tilt) to a 3D ray from the fixture position.
      2. Intersect the ray with known surfaces (floor, walls).
      3. If the intersection point is close to the observed stage position,
         it's a confirmed contact point.

    Args:
        sweep_samples: list of dicts with pan, tilt, stageX, stageY, stageZ
        fixture_pos: (x, y, z) in stage mm
        orientation: dict with panSign, tiltSign, panOffset, tiltOffset
        surfaces: dict with floor, walls, obstacles from surface_analyzer
        pan_range, tilt_range: fixture DMX ranges in degrees

    Returns:
        list of {point: [x,y,z], surface: str, confidence: float}
    """
    from mover_calibrator import pan_tilt_to_ray
    from surface_analyzer import beam_surface_check

    contacts = []
    for s in sweep_samples:
        pan = s.get("pan", 0.5)
        tilt = s.get("tilt", 0.5)
        observed_x = s.get("stageX")
        observed_y = s.get("stageY")
        if observed_x is None or observed_y is None:
            continue

        # Compute ray from fixture
        try:
            ray_dir = pan_tilt_to_ray(pan, tilt, pan_range, tilt_range)
        except Exception:
            continue

        # Intersect with surfaces
        hit = beam_surface_check(surfaces, list(fixture_pos), list(ray_dir))
        if hit is None:
            continue

        # Compare predicted intersection with observed position
        pred = hit.get("point", [0, 0, 0])
        dx = pred[0] - observed_x
        dy = pred[1] - observed_y
        dist = math.sqrt(dx * dx + dy * dy)

        # Confidence: inverse of prediction error (higher = better match)
        if dist < 500:  # within 500mm = likely the same surface
            confidence = max(0.0, 1.0 - dist / 500.0)
            contacts.append({
                "point": [observed_x, observed_y, s.get("stageZ", 0.0)],
                "predicted": pred,
                "surface": hit.get("surface", "unknown"),
                "distance": round(hit.get("distance", 0), 1),
                "confidence": round(confidence, 3),
                "pan": pan,
                "tilt": tilt,
            })

    log.info("Structured light: %d contacts from %d sweep samples",
             len(contacts), len(sweep_samples))
    return contacts


def refine_surface_model(surfaces, beam_contacts, min_contacts=5):
    """Refine the 3D model using beam contact points (#236).

    Beam contacts provide ground-truth surface positions that can:
    - Confirm/correct floor height (Z value).
    - Extend wall boundaries.
    - Detect surfaces not found by RANSAC.

    Args:
        surfaces: dict from surface_analyzer {floor, walls, obstacles}
        beam_contacts: list from accumulate_beam_hits()
        min_contacts: minimum contacts to apply a correction

    Returns:
        dict with updated surfaces and a corrections report
    """
    if not beam_contacts or len(beam_contacts) < min_contacts:
        return {"surfaces": surfaces, "corrections": [], "applied": 0}

    corrections = []

    # Floor height correction
    floor = surfaces.get("floor")
    if floor:
        floor_contacts = [c for c in beam_contacts
                          if c["surface"] == "floor" and c["confidence"] > 0.5]
        if len(floor_contacts) >= min_contacts:
            avg_z = sum(c["point"][2] for c in floor_contacts) / len(floor_contacts)
            old_z = floor.get("z", 0)
            if abs(avg_z - old_z) > 10:  # >10mm difference
                corrections.append({
                    "type": "floor_height",
                    "old_z": old_z,
                    "new_z": round(avg_z, 1),
                    "contacts": len(floor_contacts),
                })
                floor["z"] = round(avg_z, 1)

    # Wall boundary extension
    walls = surfaces.get("walls", [])
    for wall in walls:
        wall_contacts = [c for c in beam_contacts
                         if c["surface"] == "wall" and c["confidence"] > 0.3]
        if wall_contacts:
            xs = [c["point"][0] for c in wall_contacts]
            ys = [c["point"][1] for c in wall_contacts]
            extent = wall.get("extent", {})
            if xs:
                new_xmin = min(xs)
                new_xmax = max(xs)
                if new_xmin < extent.get("xMin", float("inf")):
                    extent["xMin"] = round(new_xmin, 1)
                if new_xmax > extent.get("xMax", float("-inf")):
                    extent["xMax"] = round(new_xmax, 1)
            wall["extent"] = extent

    return {
        "surfaces": surfaces,
        "corrections": corrections,
        "applied": len(corrections),
    }
