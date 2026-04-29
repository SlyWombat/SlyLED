package com.slywombat.slyled.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull

@Serializable
data class StatusResponse(
    val role: String = "",
    val hostname: String = "",
    val version: String = ""
)

@Serializable
data class OkResponse(
    val ok: Boolean = false,
    val id: Int? = null,
    val err: String? = null,
    val duplicate: Boolean? = null,
    val added: Int? = null,
    val updated: Int? = null,
    val skipped: Int? = null,
    val warning: String? = null,
    val actions: Int? = null,
    val runners: Int? = null,
    val flights: Int? = null,
    val shows: Int? = null,
    val total: Int? = null,
    val online: Int? = null
)

@Serializable
data class ChildStringConfig(
    val leds: Int = 0,
    @SerialName("mm") val lengthMm: Int = 0,
    val type: Int = 0,
    @SerialName("cdir") val cableDirection: Int = 0,
    @SerialName("cmm") val cableLengthMm: Int = 0,
    @SerialName("sdir") val stripDirection: Int = 0,
    val folded: Boolean = false
)

enum class OnlineStatus {
    OFFLINE, ONLINE;
    companion object {
        fun fromInt(v: Int) = if (v == 1) ONLINE else OFFLINE
    }
}

@Serializable
data class Child(
    val id: Int = -1,
    val ip: String = "",
    val hostname: String = "",
    val name: String = "",
    val desc: String = "",
    val sc: Int = 0,
    val strings: List<ChildStringConfig> = emptyList(),
    val status: Int = 0,
    val seen: Long = 0,
    val type: String = "slyled",
    val boardType: String = "",
    val fwVersion: String? = null,
    val rssi: Int? = null,
    val startupDone: Boolean = true
) {
    val onlineStatus: OnlineStatus get() = OnlineStatus.fromInt(status)
    /** RSSI as dBm (server stores as positive magnitude, e.g. 69 → -69 dBm). */
    val rssiDbm: Int? get() = rssi?.let { if (it > 0) -it else it }
    /** Signal bars 0-4 from RSSI. */
    val signalBars: Int get() {
        val dbm = rssiDbm ?: return 0
        return when {
            dbm >= -50 -> 4
            dbm >= -60 -> 3
            dbm >= -70 -> 2
            dbm >= -80 -> 1
            else -> 0
        }
    }
}

@Serializable
data class Action(
    val id: Int = -1,
    val name: String = "",
    val type: Int = 1,
    val scope: String = "performer",
    val targetIds: List<Int>? = null,
    val canvasEffect: String? = null,
    val r: Int = 0, val g: Int = 0, val b: Int = 0,
    val r2: Int = 0, val g2: Int = 0, val b2: Int = 0,
    val speedMs: Int? = null,
    val periodMs: Int? = null,
    val spawnMs: Int? = null,
    val minBri: Int? = null,
    val spacing: Int? = null,
    val paletteId: Int? = null,
    val cooling: Int? = null,
    val sparking: Int? = null,
    val direction: Int? = null,
    val tailLen: Int? = null,
    val density: Int? = null,
    val decay: Int? = null,
    val fadeSpeed: Int? = null,
    val onMs: Int? = null,
    val offMs: Int? = null,
    val wledFxOverride: Int? = null,
    val wledPalOverride: Int? = null,
    val wledSegId: Int? = null,
    // DMX action params
    val dimmer: Int? = null,
    val pan: Double? = null,
    val tilt: Double? = null,
    val strobe: Int? = null,
    val gobo: Int? = null,
    val colorWheel: Int? = null,
    val prism: Int? = null,
    val focus: Int? = null,
    val zoom: Int? = null,
    val panStart: Double? = null,
    val panEnd: Double? = null,
    val tiltStart: Double? = null,
    val tiltEnd: Double? = null
)

@Serializable
data class LayoutChild(
    val id: Int,
    val x: Int = 0,
    val y: Int = 0,
    val z: Int = 0
)

@Serializable
data class Layout(
    val canvasW: Int = 10000,
    val canvasH: Int = 5000,
    val children: List<LayoutChild> = emptyList(),
    val fixtures: List<Fixture> = emptyList()
)

@Serializable
data class Settings(
    val name: String = "SlyLED",
    val units: Int = 0,
    val canvasW: Int = 10000,
    val canvasH: Int = 5000,
    val darkMode: Int = 1,
    val runnerRunning: Boolean = false,
    val activeRunner: Int = -1,
    val activeShow: Int = -1,
    val runnerElapsed: Int = 0,
    val runnerLoop: Boolean = true,
    val globalBrightness: Int? = null,
    val logging: Boolean = false,
    val runnerStartEpoch: Long? = null,
    val activeTimeline: Int? = null
)

@Serializable
data class ChildStatus(
    val ok: Boolean = false,
    val activeAction: Int? = null,
    val runnerActive: Boolean? = null,
    val currentStep: Int? = null,
    val wifiRssi: Int? = null,
    val uptimeS: Long? = null,
    val err: String? = null
)

// Constants
object ActionTypes {
    const val BLACKOUT = 0
    const val SOLID = 1
    const val FADE = 2
    const val BREATHE = 3
    const val CHASE = 4
    const val RAINBOW = 5
    const val FIRE = 6
    const val COMET = 7
    const val TWINKLE = 8
    const val STROBE = 9
    const val WIPE = 10
    const val SCANNER = 11
    const val SPARKLE = 12
    const val GRADIENT = 13
    const val DMX_SCENE = 14
    const val DMX_PT_MOVE = 15
    const val DMX_GOBO = 16
    const val DMX_COLOR_WHEEL = 17

    val names = listOf(
        "Blackout", "Solid", "Fade", "Breathe", "Chase",
        "Rainbow", "Fire", "Comet", "Twinkle", "Strobe",
        "Color Wipe", "Scanner", "Sparkle", "Gradient",
        "DMX Scene", "Pan/Tilt Move", "Gobo Select", "Color Wheel"
    )

    val directionNames = listOf("East", "North", "West", "South")
    val paletteNames = listOf("Classic", "Ocean", "Lava", "Forest", "Party", "Heat", "Cool", "Pastel")
}

@Serializable
data class AddChildRequest(val ip: String)

// Legacy runner/flight/show removed in v8.0 — timeline is the only execution model

// ── Stage (Phase 1) ────────────────────────────────────────────────────
@Serializable
data class Stage(
    val w: Double = 10.0,
    val h: Double = 5.0,
    val d: Double = 10.0,
    val stageBoundsManual: Boolean = false,
    val auto: StageAuto? = null,
)

@Serializable
data class StageAuto(
    val w: Double = 0.0,
    val h: Double = 0.0,
    val d: Double = 0.0,
)

// ── Fixtures (Phase 2) ─────────────────────────────────────────────────
@Serializable
data class Fixture(
    val id: Int = 0,
    val name: String = "",
    val childId: Int? = null,
    val type: String = "linear",  // linear, point, surface, group
    val fixtureType: String = "led",  // "led", "dmx", or "camera"
    val dmxUniverse: Int? = null,
    val dmxStartAddr: Int? = null,
    val dmxChannelCount: Int? = null,
    val dmxProfileId: String? = null,
    val aimPoint: List<Double>? = null,
    val fovDeg: Double? = null,
    val cameraUrl: String? = null,
    val resolutionW: Int? = null,
    val resolutionH: Int? = null,
    val childIds: List<Int> = emptyList(),
    val strings: List<FixtureString> = emptyList(),
    val rotation: List<Double> = listOf(0.0, 0.0, 0.0),
    val aoeRadius: Int = 1000,
    val meshFile: String? = null,
    val x: Int = 0,
    val y: Int = 0,
    val z: Int = 0,
    val positioned: Boolean = false,
    val calibrated: Boolean = false,
    val moverCalibrated: Boolean = false,
    val rangeCalibrated: Boolean = false,
)

@Serializable
data class DmxProfile(
    val id: String = "",
    val name: String = "",
    val manufacturer: String = "Generic",
    val category: String = "par",
    val channelCount: Int = 0,
    val colorMode: String = "rgb",
    val beamWidth: Int = 0,
    val panRange: Int = 0,
    val tiltRange: Int = 0,
    val builtin: Boolean = true
)

@Serializable
data class FixtureString(
    val leds: Int = 0,
    val mm: Int = 1000,
    val sdir: Int = 0,
    val type: Int = 0,
    val cdir: Int = 0,
    val cmm: Int = 0,
    val folded: Boolean = false,
    val points: List<List<Double>>? = null,
)

// ── Stage Objects (Phase 2) ────────────────────────────────────────────
@Serializable
data class StageObject(
    val id: Int = 0,
    val name: String = "",
    val objectType: String = "custom",
    val mobility: String = "static",
    val color: String = "#334155",
    val opacity: Int = 30,
    val transform: ObjectTransform = ObjectTransform(),
    val stageLocked: Boolean = false,
    @SerialName("_temporal")
    val temporal: Boolean = false,
    val ttl: Int = 0,
    val patrol: PatrolConfig? = null,
)

@Serializable
data class PatrolConfig(
    val enabled: Boolean = false,
    val axis: String = "x",
    val speedPreset: String = "medium",
    val cycleS: Double = 10.0,
    val startPct: Int = 10,
    val endPct: Int = 90,
    val easing: String = "sine",
)

@Serializable
data class ObjectTransform(
    val pos: List<Double> = listOf(0.0, 0.0, 0.0),
    val rot: List<Double> = listOf(0.0, 0.0, 0.0),
    val scale: List<Double> = listOf(2000.0, 1500.0, 100.0),
)

// ── Spatial Effects (Phase 3) ──────────────────────────────────────────
@Serializable
data class SpatialEffect(
    val id: Int = 0,
    val name: String = "",
    val category: String = "spatial-field",  // spatial-field or fixture-local
    val shape: String = "sphere",
    val r: Int = 255, val g: Int = 255, val b: Int = 255,
    val r2: Int = 0, val g2: Int = 0, val b2: Int = 0,
    val size: Map<String, Double> = emptyMap(),
    val motion: SpatialMotion = SpatialMotion(),
    val blend: String = "replace",
    val fixtureIds: List<Int> = emptyList(),
    val actionType: Int? = null,
)

@Serializable
data class SpatialMotion(
    val startPos: List<Double> = listOf(0.0, 0.0, 0.0),
    val endPos: List<Double> = listOf(5000.0, 0.0, 0.0),
    val durationS: Double = 5.0,
    val easing: String = "linear",
)

// ── Timelines (Phase 4) ────────────────────────────────────────────────
@Serializable
data class Timeline(
    val id: Int = 0,
    val name: String = "",
    val durationS: Int = 60,
    val tracks: List<TimelineTrack> = emptyList(),
    val loop: Boolean = false,
)

@Serializable
data class TimelineTrack(
    val fixtureId: Int? = null,
    val allPerformers: Boolean = false,
    val clips: List<TimelineClip> = emptyList(),
)

@Serializable
data class TimelineClip(
    val effectId: Int? = null,
    val actionId: Int? = null,
    val startS: Double = 0.0,
    val durationS: Double = 5.0,
)

// ── Bake/Sync (Phase 5-6) ──────────────────────────────────────────────
@Serializable
data class BakeStatus(
    val running: Boolean = false,
    val done: Boolean = false,
    val status: String = "",
    val frame: Int = 0,
    val totalFrames: Int = 0,
    val progress: Double = 0.0,
    val error: String? = null,
    val segments: Map<String, Int> = emptyMap(),
)

@Serializable
data class SyncStatus(
    val done: Boolean = false,
    val allReady: Boolean = false,
    val readyCount: Int = 0,
    val totalPerformers: Int = 0,
    val performers: Map<String, SyncPerformerStatus> = emptyMap(),
)

@Serializable
data class SyncPerformerStatus(
    val name: String = "",
    val ip: String = "",
    val status: String = "pending",
    val stepsLoaded: Int = 0,
    val totalSteps: Int = 0,
    val retries: Int = 0,
    val verified: Boolean = false,
    val error: String? = null,
)

@Serializable
data class TimelineStatus(
    val id: Int = 0,
    val name: String = "",
    val running: Boolean = false,
    val elapsed: Int = 0,
    val durationS: Int = 0,
    val loop: Boolean = false,
    val activeTimeline: Int = -1,
)

// ── Presets (Phase 7) ──────────────────────────────────────────────────
@Serializable
data class ShowPreset(
    val id: String = "",
    val name: String = "",
    val desc: String = "",
)

// ── Camera Status ─────────────────────────────────────────────────────
@Serializable
data class CameraStatus(
    val id: Int = -1,
    val name: String = "",
    val cameraIp: String = "",
    val online: Boolean = false,
    val fwVersion: String = "",
    val hostname: String = "",
    val tracking: Boolean = false,
    val trackClasses: List<String> = listOf("person"),
)

// ── Show Status / Playlist ────────────────────────────────────────────
@Serializable
data class ShowStatus(
    val running: Boolean = false,
    val currentTimeline: Int? = null,
    val currentIndex: Int = 0,
    val totalTimelines: Int = 0,
    val elapsed: Double = 0.0,
    val duration: Double = 0.0,
    val loop: Boolean = false,
)

@Serializable
data class ShowPlaylist(
    val order: List<Int> = emptyList(),
    val loop: Boolean = false,
)

// ── DMX Status (parsed from nested {artnet:{...}, sacn:{...}} response) ──
@Serializable
data class DmxStatus(
    val running: Boolean = false,
    val universes: Int = 0,
    val fps: Int = 0,
    val protocol: String = "",
    val nodes: Int = 0,
) {
    companion object {
        /** Parse the nested /api/dmx/status response into a flat DmxStatus.
         *  #648 — use kotlinx booleanOrNull / intOrNull / contentOrNull
         *  helpers so JSON booleans/numbers parse without false-negative
         *  string compares (the previous `content == "true"` check failed
         *  in some cases, leaving Status reading Stopped while the engine
         *  was running). */
        fun fromJson(json: kotlinx.serialization.json.JsonObject): DmxStatus {
            val artnet = json["artnet"] as? kotlinx.serialization.json.JsonObject
            val sacn = json["sacn"] as? kotlinx.serialization.json.JsonObject
            fun running(o: kotlinx.serialization.json.JsonObject?): Boolean =
                (o?.get("running") as? kotlinx.serialization.json.JsonPrimitive)?.booleanOrNull == true
            val engine = if (running(artnet)) artnet else if (running(sacn)) sacn else artnet ?: sacn
            val isRunning = running(engine)
            val uniArr = engine?.get("universes")
            val universes = if (uniArr is kotlinx.serialization.json.JsonArray) uniArr.size else 0
            val fps = (engine?.get("frameRate") as? kotlinx.serialization.json.JsonPrimitive)
                ?.intOrNull ?: 0
            val protocol = (engine?.get("protocol") as? kotlinx.serialization.json.JsonPrimitive)
                ?.contentOrNull ?: ""
            val nodes = (engine?.get("discoveredNodes") as? kotlinx.serialization.json.JsonPrimitive)
                ?.intOrNull ?: 0
            return DmxStatus(isRunning, universes, fps, protocol, nodes)
        }
    }
}

// #479 — Mover-Control live status (polled while a fixture is claimed).
@Serializable
data class MoverControlClaim(
    val moverId: Int = -1,
    val deviceId: String = "",
    val deviceName: String = "",
    val deviceType: String = "",
    val state: String = "",          // "claimed" | "streaming" | "calibrating"
    val lastWriteAge: Float = 0f,    // seconds since last DMX write
    val calibrated: Boolean = false,
    val panNorm: Float = 0.5f,
    val tiltNorm: Float = 0.5f,
)

@Serializable
data class MoverControlEngineHealth(
    val running: Boolean = false,
    val engineType: String? = null,
    val droppedWrites: Int = 0,
)

@Serializable
data class MoverControlStatus(
    val claims: List<MoverControlClaim> = emptyList(),
    val engine: MoverControlEngineHealth = MoverControlEngineHealth(),
)
