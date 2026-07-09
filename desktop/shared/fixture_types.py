"""fixture_types.py — server-half fixture-type registry (#899).

`fixtureType` used to be a closed enum hand-checked across
parent_server.py, with the POST/PUT valid-type whitelists and the
per-type field/validation blocks inlined in the fixture routes. This
module makes the type set data-driven: each type is a
:class:`FixtureTypeDescriptor` carrying

  - ``fields``      — per-type keys accepted by the generic
                      ``PUT /api/fixtures/<fid>`` whitelist (the update
                      whitelist is the union of ``COMMON_UPDATE_FIELDS``
                      and every registered type's ``fields``, exactly
                      reproducing the pre-#899 flat literal — any type's
                      fields are accepted on any fixture, as before);
  - ``validate_create(body)`` / ``validate_update(body, fixture)``
                    — return an error string or None (moved verbatim
                      from the route bodies);
  - ``apply_create(f, body)`` — default factory: stamp per-type fields
                      onto a freshly created fixture dict;
  - ``capabilities`` — flags for downstream consumers
                      (``tracks_people``, ``has_dmx``, ``placeable``).

Adding a new sensor type is a registration, not a route edit:

    register_fixture_type(FixtureTypeDescriptor("radar", ...))

The `radar` type (#911) is exactly that: registered at the bottom of
this module with tracks_people=True and its node-binding fields, the
type became creatable/updatable through the existing routes untouched.

Note the *geometry* type (``fixture.type`` ∈ linear/point/surface/group)
is a separate axis from ``fixtureType`` and is not handled here —
"surface" is a geometry, not a fixture type.

The scattered ``fixtureType == "x"`` comparisons elsewhere in
parent_server.py (rendering/behaviour chains) are deliberately out of
scope (#899 covers validation + whitelists; the SPA half owns the
per-type rendering registry).
"""

# ── Q12 fovType whitelist (moved from parent_server.py under #899 so the
# camera descriptor's validators are self-contained; parent_server
# re-exports these under their old names). ──────────────────────────────
FOV_TYPE_WHITELIST = ("horizontal", "vertical", "diagonal")
FOV_TYPE_DEFAULT = "diagonal"


def normalise_fov_type(value, *, default=FOV_TYPE_DEFAULT):
    """Return a whitelist-validated fovType string. Unknown inputs map to
    the default so a malformed fixture record never crashes a ray calc."""
    if isinstance(value, str):
        v = value.strip().lower()
        if v in FOV_TYPE_WHITELIST:
            return v
    return default


# Fields every fixture type accepts on the generic PUT (pre-#899 head of
# the flat whitelist literal, order preserved).
COMMON_UPDATE_FIELDS = (
    "name", "type", "fixtureType", "childId", "childIds", "strings",
    "rotation", "orientation", "mountedInverted", "aoeRadius", "meshFile",
)


class FixtureTypeDescriptor:
    """Descriptor for one fixtureType. All hooks optional."""

    def __init__(self, name, *, fields=(), validate_create=None,
                 validate_update=None, apply_create=None,
                 capabilities=None):
        self.name = name
        self.fields = tuple(fields)
        self._validate_create = validate_create
        self._validate_update = validate_update
        self._apply_create = apply_create
        self.capabilities = dict(capabilities or {})
        self.capabilities.setdefault("tracks_people", False)
        self.capabilities.setdefault("has_dmx", False)
        self.capabilities.setdefault("placeable", True)

    def validate_create(self, body):
        return self._validate_create(body) if self._validate_create else None

    def validate_update(self, body, fixture):
        return (self._validate_update(body, fixture)
                if self._validate_update else None)

    def apply_create(self, f, body):
        if self._apply_create:
            self._apply_create(f, body)


# Registration order matters only for the error-message wording (and the
# update-whitelist ordering), both of which reproduce the pre-#899
# literals: led, dmx, camera, gyro.
FIXTURE_TYPES = {}


def register_fixture_type(descriptor):
    """Register (or replace) a fixture type. New sensor types call this —
    the POST/PUT fixture routes need no edits (#899)."""
    FIXTURE_TYPES[descriptor.name] = descriptor
    return descriptor


def is_valid_type(name):
    return name in FIXTURE_TYPES


def invalid_type_error():
    """The 400 message for an unknown fixtureType. Wording matches the
    pre-#899 literal for the default led/dmx/camera/gyro registry."""
    quoted = [f"'{n}'" for n in FIXTURE_TYPES]
    listing = ", ".join(quoted[:-1]) + f", or {quoted[-1]}" if len(quoted) > 1 else quoted[0]
    return f"Invalid fixtureType - must be {listing}"


def update_field_whitelist():
    """Generic-PUT writable keys: COMMON_UPDATE_FIELDS + the union of all
    registered types' fields (pre-#899 flat-literal semantics preserved:
    membership is what matters; any type's fields apply to any fixture)."""
    seen = list(COMMON_UPDATE_FIELDS)
    for desc in FIXTURE_TYPES.values():
        for k in desc.fields:
            if k not in seen:
                seen.append(k)
    return tuple(seen)


def validate_create(ftype, body):
    desc = FIXTURE_TYPES.get(ftype)
    return desc.validate_create(body) if desc else None


def validate_update(ftype, body, fixture):
    desc = FIXTURE_TYPES.get(ftype)
    return desc.validate_update(body, fixture) if desc else None


def apply_create(ftype, f, body):
    desc = FIXTURE_TYPES.get(ftype)
    if desc:
        desc.apply_create(f, body)


# ── Built-in types. Validation/default blocks moved VERBATIM from the
# pre-#899 POST/PUT route bodies — behaviour-preserving. ────────────────

def _led_capabilities():
    return {"tracks_people": False, "has_dmx": False, "placeable": True}


register_fixture_type(FixtureTypeDescriptor(
    "led",
    capabilities=_led_capabilities(),
))


def _dmx_validate_create(body):
    dmx_uni = body.get("dmxUniverse")
    dmx_addr = body.get("dmxStartAddr")
    dmx_ch = body.get("dmxChannelCount")
    if not isinstance(dmx_uni, int) or dmx_uni < 1:
        return "dmxUniverse must be an integer >= 1"
    if not isinstance(dmx_addr, int) or dmx_addr < 1 or dmx_addr > 512:
        return "dmxStartAddr must be 1-512"
    if not isinstance(dmx_ch, int) or dmx_ch < 1:
        return "dmxChannelCount must be an integer >= 1"
    return None


def _dmx_validate_update(body, fixture):
    addr = body.get("dmxStartAddr", fixture.get("dmxStartAddr"))
    if "dmxStartAddr" in body:
        if not isinstance(addr, int) or addr < 1 or addr > 512:
            return "dmxStartAddr must be 1-512"
    uni = body.get("dmxUniverse", fixture.get("dmxUniverse"))
    if "dmxUniverse" in body:
        if not isinstance(uni, int) or uni < 1:
            return "dmxUniverse must be an integer >= 1"
    ch = body.get("dmxChannelCount", fixture.get("dmxChannelCount"))
    if "dmxChannelCount" in body:
        if not isinstance(ch, int) or ch < 1:
            return "dmxChannelCount must be an integer >= 1"
    return None


def _dmx_apply_create(f, body):
    f["dmxUniverse"] = body["dmxUniverse"]
    f["dmxStartAddr"] = body["dmxStartAddr"]
    f["dmxChannelCount"] = body["dmxChannelCount"]
    f["dmxProfileId"] = body.get("dmxProfileId")


register_fixture_type(FixtureTypeDescriptor(
    "dmx",
    fields=("dmxUniverse", "dmxStartAddr", "dmxChannelCount", "dmxProfileId"),
    validate_create=_dmx_validate_create,
    validate_update=_dmx_validate_update,
    apply_create=_dmx_apply_create,
    capabilities={"tracks_people": False, "has_dmx": True, "placeable": True},
))


def _camera_validate_create(body):
    fov = body.get("fovDeg")
    if fov is not None and (not isinstance(fov, (int, float)) or fov < 1 or fov > 180):
        return "fovDeg must be 1-180"
    # #Q12 — fovType whitelist
    if "fovType" in body and body["fovType"] is not None:
        ft_raw = body["fovType"]
        if not isinstance(ft_raw, str) or ft_raw.strip().lower() not in FOV_TYPE_WHITELIST:
            return f"fovType must be one of {list(FOV_TYPE_WHITELIST)}"
    return None


def _camera_validate_update(body, fixture):
    if "fovDeg" in body:
        fov = body["fovDeg"]
        if not isinstance(fov, (int, float)) or fov < 1 or fov > 180:
            return "fovDeg must be 1-180"
    # #Q12 — fovType whitelist
    if "fovType" in body and body["fovType"] is not None:
        ft_raw = body["fovType"]
        if not isinstance(ft_raw, str) or ft_raw.strip().lower() not in FOV_TYPE_WHITELIST:
            return f"fovType must be one of {list(FOV_TYPE_WHITELIST)}"
    if "trackClasses" in body:
        tc = body["trackClasses"]
        if not isinstance(tc, list) or not tc or not all(isinstance(c, str) for c in tc):
            return "trackClasses must be a non-empty list of strings"
    if "trackFps" in body:
        v = body["trackFps"]
        if not isinstance(v, (int, float)) or v < 0.5 or v > 10:
            return "trackFps must be 0.5-10"
    if "trackThreshold" in body:
        v = body["trackThreshold"]
        if not isinstance(v, (int, float)) or v < 0.1 or v > 0.95:
            return "trackThreshold must be 0.1-0.95"
    # #423 — per-class threshold dict. Each value must be in the
    # same 0.1-0.95 band so an operator can't accidentally set
    # threshold=0 and flood the tracker with noise.
    if "trackClassThresholds" in body:
        ct = body["trackClassThresholds"]
        if not isinstance(ct, dict):
            return "trackClassThresholds must be an object mapping class→threshold"
        for cls, thr in ct.items():
            if not isinstance(cls, str) or not cls:
                return "trackClassThresholds keys must be non-empty class names"
            if not isinstance(thr, (int, float)) or thr < 0.1 or thr > 0.95:
                return f"trackClassThresholds['{cls}'] must be 0.1-0.95"
    if "trackTtl" in body:
        v = body["trackTtl"]
        if not isinstance(v, (int, float)) or v < 1 or v > 60:
            return "trackTtl must be 1-60"
    if "trackReidMm" in body:
        v = body["trackReidMm"]
        if not isinstance(v, (int, float)) or v < 50 or v > 5000:
            return "trackReidMm must be 50-5000"
    return None


def _camera_apply_create(f, body):
    f["fovDeg"] = body.get("fovDeg", 60)
    f["fovType"] = normalise_fov_type(body.get("fovType"))
    f["cameraUrl"] = body.get("cameraUrl", "")
    f["resolutionW"] = body.get("resolutionW", 1920)
    f["resolutionH"] = body.get("resolutionH", 1080)
    f["trackClasses"] = body.get("trackClasses", ["person"])
    f["trackFps"] = body.get("trackFps", 2)
    f["trackThreshold"] = body.get("trackThreshold", 0.4)
    f["trackTtl"] = body.get("trackTtl", 5)
    f["trackReidMm"] = body.get("trackReidMm", 500)
    f["trackInputSize"] = body.get("trackInputSize", 320)


register_fixture_type(FixtureTypeDescriptor(
    "camera",
    # NOTE: trackInputSize is create-stamped but deliberately NOT in the
    # generic-PUT whitelist — pre-#899 behaviour preserved.
    fields=("fovDeg", "fovType", "cameraUrl", "cameraIp", "cameraIdx",
            "resolutionW", "resolutionH",
            "trackClasses", "trackClassThresholds",
            "trackFps", "trackThreshold", "trackTtl", "trackReidMm"),
    validate_create=_camera_validate_create,
    validate_update=_camera_validate_update,
    apply_create=_camera_apply_create,
    capabilities={"tracks_people": True, "has_dmx": False, "placeable": True},
))


def _gyro_apply_create(f, body):
    f["gyroChildId"] = body.get("gyroChildId")          # child record ID of the gyro board
    f["assignedMoverId"] = body.get("assignedMoverId")  # fixture ID of the DMX mover to control
    f["gyroEnabled"] = body.get("gyroEnabled", False)
    # `smoothing` removed in #877 — orchestrator no longer transforms
    # the aim vector. Stale persisted values are ignored on load; the
    # generic PUT no longer accepts the field.


register_fixture_type(FixtureTypeDescriptor(
    "gyro",
    fields=("gyroChildId", "assignedMoverId", "gyroEnabled"),
    apply_create=_gyro_apply_create,
    capabilities={"tracks_people": False, "has_dmx": False, "placeable": True},
))


# ── radar (#911) — MMwave radar node (ESP32-C61 + Rd-03D, design doc
# docs/design/mmwave_tracking.md §4.4). A placeable people-tracking
# sensor: pose (layout position + `rotation`, stage Z-up mm, read only
# via camera_math.rotation_from_layout) plus node binding & coverage:
#
#   radarNode    — hostname the reporting node announces in its PONG
#                  (e.g. "MMW-A1B2"); optional at create — the #910
#                  ingest falls back to a single-radar heuristic until
#                  the operator binds it.
#   rangeMm      — detection range, mm (Rd-03D ≈ 8 m).
#   fovDeg       — azimuth field of view, degrees (Rd-03D ≈ ±60°).
#   radarEnabled — ingest gate; a disabled radar's packets are ignored.

def _radar_validate(body, fixture=None):
    """Shared create/update validation — every radar field is optional,
    so create and update enforce the same per-field constraints."""
    if "radarNode" in body and body["radarNode"] is not None:
        v = body["radarNode"]
        if not isinstance(v, str) or not v.strip():
            return "radarNode must be a non-empty string (node hostname, e.g. 'MMW-A1B2')"
    if "rangeMm" in body and body["rangeMm"] is not None:
        v = body["rangeMm"]
        if not isinstance(v, int) or isinstance(v, bool) or v < 100 or v > 20000:
            return "rangeMm must be an integer 100-20000"
    if "fovDeg" in body and body["fovDeg"] is not None:
        v = body["fovDeg"]
        if not isinstance(v, int) or isinstance(v, bool) or v < 1 or v > 360:
            return "fovDeg must be an integer 1-360 for radar fixtures"
    if "radarEnabled" in body and not isinstance(body["radarEnabled"], bool):
        return "radarEnabled must be a boolean"
    return None


def _radar_apply_create(f, body):
    f["radarNode"] = body.get("radarNode")      # PONG hostname of the node
    f["rangeMm"] = body.get("rangeMm", 8000)    # Rd-03D detection range
    f["fovDeg"] = body.get("fovDeg", 120)       # ±60° azimuth wedge
    f["radarEnabled"] = body.get("radarEnabled", True)


register_fixture_type(FixtureTypeDescriptor(
    "radar",
    fields=("radarNode", "rangeMm", "fovDeg", "radarEnabled"),
    validate_create=_radar_validate,
    validate_update=lambda body, fixture: _radar_validate(body, fixture),
    apply_create=_radar_apply_create,
    capabilities={"tracks_people": True, "has_dmx": False, "placeable": True},
))
