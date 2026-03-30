package com.slywombat.slyled.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

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
    val fwVersion: String? = null
) {
    val onlineStatus: OnlineStatus get() = OnlineStatus.fromInt(status)
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
    val wledSegId: Int? = null
)

@Serializable
data class RunnerStep(
    val actionId: Int? = null,
    val durationS: Int = 5,
    val speedMs: Int? = null,
    val direction: Int? = null,
    val brightness: Int? = null,
    val r: Int? = null, val g: Int? = null, val b: Int? = null,
    val scope: String? = null
)

@Serializable
data class RunnerSummary(
    val id: Int,
    val name: String = "",
    val steps: Int = 0,
    val totalDurationS: Int = 0,
    val computed: Boolean = false
)

@Serializable
data class Runner(
    val id: Int = -1,
    val name: String = "",
    val computed: Boolean = false,
    val steps: List<RunnerStep> = emptyList()
)

@Serializable
data class Flight(
    val id: Int = -1,
    val name: String = "",
    val performerIds: List<Int> = emptyList(),
    val runnerId: Int? = null,
    val priority: Int = 1
)

@Serializable
data class Show(
    val id: Int = -1,
    val name: String = "",
    val flightIds: List<Int> = emptyList(),
    val loop: Boolean = true
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
    val children: List<LayoutChild> = emptyList()
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
data class LiveEvent(
    val ip: String = "",
    val actionType: Int? = null,
    val stepIndex: Int? = null,
    val totalSteps: Int? = null,
    val event: Int? = null,
    val age: Double? = null
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

    val names = listOf(
        "Blackout", "Solid", "Fade", "Breathe", "Chase",
        "Rainbow", "Fire", "Comet", "Twinkle", "Strobe",
        "Color Wipe", "Scanner", "Sparkle", "Gradient"
    )

    val directionNames = listOf("East", "North", "West", "South")
    val paletteNames = listOf("Classic", "Ocean", "Lava", "Forest", "Party", "Heat", "Cool", "Pastel")
}

@Serializable
data class AddChildRequest(val ip: String)

@Serializable
data class CreateRunnerRequest(val name: String)

// ── Stage (Phase 1) ────────────────────────────────────────────────────
@Serializable
data class Stage(
    val w: Double = 10.0,
    val h: Double = 5.0,
    val d: Double = 10.0,
)

// ── Fixtures (Phase 2) ─────────────────────────────────────────────────
@Serializable
data class Fixture(
    val id: Int = 0,
    val name: String = "",
    val childId: Int? = null,
    val type: String = "linear",  // linear, point, surface, group
    val childIds: List<Int> = emptyList(),
    val strings: List<FixtureString> = emptyList(),
    val rotation: List<Double> = listOf(0.0, 0.0, 0.0),
    val aoeRadius: Int = 1000,
    val meshFile: String? = null,
)

@Serializable
data class FixtureString(
    val leds: Int = 0,
    val mm: Int = 1000,
    val sdir: Int = 0,
    val points: List<List<Double>>? = null,
)

// ── Surfaces (Phase 2) ─────────────────────────────────────────────────
@Serializable
data class Surface(
    val id: Int = 0,
    val name: String = "",
    val surfaceType: String = "custom",
    val color: String = "#334155",
    val opacity: Int = 30,
    val transform: SurfaceTransform = SurfaceTransform(),
)

@Serializable
data class SurfaceTransform(
    val pos: List<Int> = listOf(0, 0, 0),
    val rot: List<Int> = listOf(0, 0, 0),
    val scale: List<Int> = listOf(2000, 1500, 100),
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
