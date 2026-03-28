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
    val y: Int = 0
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
    val runnerStartEpoch: Long? = null
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
