package com.slywombat.slyled.ui.screens.control.shortcuts

// Profile-driven UI shortcut resolver for non-mover DMX fixtures
// (bubble machines, hazers, RGB pars, etc.) on the Fixtures page.
// #888.
//
// Byte-for-byte twin of desktop/shared/spa/js/fixture_shortcuts.js.
// Three-pass resolver:
//   1. explicit `channel.shortcut` field (profile-author opt-in)
//   2. capability-type heuristic (strobe → strobe-momentary, etc.)
//   3. name-match fallback (channel name contains "bubble" → bubble-toggle)

enum class ShortcutId(val tag: String) {
    BUBBLE_TOGGLE("bubble-toggle"),
    HAZE_SEGMENTED("haze-segmented"),
    FAN_SEGMENTED("fan-segmented"),
    COLOR_SWATCH("color-swatch"),
    UV_TOGGLE("uv-toggle"),
    STROBE_MOMENTARY("strobe-momentary"),
    CLEAN_MODE("clean-mode");

    companion object {
        fun fromTag(tag: String?): ShortcutId? =
            entries.firstOrNull { it.tag == tag }
    }
}

enum class ShortcutUi { TOGGLE, SEGMENTED, COLOR_SWATCH, MOMENTARY, LONG_PRESS }

data class SegmentedOption(val label: String, val value: Int)
data class ColorSwatchOption(val label: String, val rgb: Triple<Int, Int, Int>)

/**
 * One rendered shortcut. The composable that hosts a Fixtures-page card
 * iterates a `List<ResolvedShortcut>` and renders each by `ui` type.
 *
 * For TOGGLE: writes `onValue` to `channelOffset` when active, else
 * `offValue`.
 *
 * For SEGMENTED: writes the selected segment's `value` to
 * `channelOffset`.
 *
 * For COLOR_SWATCH: writes the chosen (r, g, b) to the channels at
 * `channelOffsets.red/green/blue` (uv left untouched).
 *
 * For MOMENTARY (strobe): writes the midpoint of the ShutterStrobe
 * Strobe capability range to `channelOffset` while held, and the Open
 * value on release. Capability ranges live in `capabilities`.
 *
 * For LONG_PRESS (clean): writes `onValue` after a hold of
 * `confirmHoldMs`, then `offValue` on release.
 */
data class ResolvedShortcut(
    val id: ShortcutId,
    val ui: ShortcutUi,
    val icon: String,
    val label: String,
    // Filled for everything except COLOR_SWATCH.
    val channelOffset: Int? = null,
    val channelName: String? = null,
    // Filled for COLOR_SWATCH.
    val anchorOffset: Int? = null,
    val channelOffsets: Map<String, Int>? = null,
    // Toggle/long-press values.
    val onValue: Int = 255,
    val offValue: Int = 0,
    val confirmHoldMs: Long = 0L,
    // Segmented options.
    val segments: List<SegmentedOption> = emptyList(),
    // Color swatch options.
    val swatches: List<ColorSwatchOption> = emptyList(),
    // Capability list for MOMENTARY range lookups.
    val capabilities: List<Map<String, Any?>> = emptyList(),
)

/** Spec table for each shortcut id. Mirrors `SHORTCUT_SPECS` in the JS. */
private fun specFor(id: ShortcutId): ResolvedShortcut = when (id) {
    ShortcutId.BUBBLE_TOGGLE -> ResolvedShortcut(
        id = id, ui = ShortcutUi.TOGGLE, icon = "🫧", label = "BUBBLES",
        onValue = 255, offValue = 0)
    ShortcutId.HAZE_SEGMENTED -> ResolvedShortcut(
        id = id, ui = ShortcutUi.SEGMENTED, icon = "💨", label = "HAZE",
        segments = listOf(
            SegmentedOption("OFF",  0),
            SegmentedOption("LOW",  64),
            SegmentedOption("MED",  128),
            SegmentedOption("HIGH", 220),
        ))
    ShortcutId.FAN_SEGMENTED -> ResolvedShortcut(
        id = id, ui = ShortcutUi.SEGMENTED, icon = "🌀", label = "FAN",
        segments = listOf(
            SegmentedOption("SLOW", 64),
            SegmentedOption("MED",  160),
            SegmentedOption("FAST", 255),
        ))
    ShortcutId.COLOR_SWATCH -> ResolvedShortcut(
        id = id, ui = ShortcutUi.COLOR_SWATCH, icon = "💡", label = "COLOUR",
        swatches = listOf(
            ColorSwatchOption("Red",     Triple(255,   0,   0)),
            ColorSwatchOption("Orange",  Triple(255, 128,   0)),
            ColorSwatchOption("Yellow",  Triple(255, 255,   0)),
            ColorSwatchOption("Green",   Triple(  0, 255,   0)),
            ColorSwatchOption("Cyan",    Triple(  0, 255, 255)),
            ColorSwatchOption("Blue",    Triple(  0,   0, 255)),
            ColorSwatchOption("Magenta", Triple(255,   0, 255)),
            ColorSwatchOption("White",   Triple(255, 255, 255)),
            ColorSwatchOption("Off",     Triple(  0,   0,   0)),
        ))
    ShortcutId.UV_TOGGLE -> ResolvedShortcut(
        id = id, ui = ShortcutUi.TOGGLE, icon = "🟣", label = "UV",
        onValue = 255, offValue = 0)
    ShortcutId.STROBE_MOMENTARY -> ResolvedShortcut(
        id = id, ui = ShortcutUi.MOMENTARY, icon = "⚡", label = "STROBE")
    ShortcutId.CLEAN_MODE -> ResolvedShortcut(
        id = id, ui = ShortcutUi.LONG_PRESS, icon = "🧼", label = "CLEAN",
        onValue = 255, offValue = 0, confirmHoldMs = 1500L)
}

// Name-match heuristic table. Order matters (most specific first).
private val NAME_HEURISTIC: List<Pair<String, ShortcutId>> = listOf(
    "bubble"  to ShortcutId.BUBBLE_TOGGLE,
    "haze"    to ShortcutId.HAZE_SEGMENTED,
    "fog"     to ShortcutId.HAZE_SEGMENTED,
    "fan"     to ShortcutId.FAN_SEGMENTED,
    "blower"  to ShortcutId.FAN_SEGMENTED,
    "clean"   to ShortcutId.CLEAN_MODE,
)

private val ELIGIBLE_NAME_TYPES = setOf("dimmer", "intensity", "speed", "reset")

/**
 * Resolve a single channel to a shortcut id, or null.
 */
fun resolveChannelShortcut(channel: Map<String, Any?>): ShortcutId? {
    // Pass 1 — explicit annotation.
    val explicit = ShortcutId.fromTag(channel["shortcut"] as? String)
    if (explicit != null) return explicit

    val type = channel["type"] as? String
    val caps = channel["capabilities"] as? List<*> ?: emptyList<Any?>()

    // Pass 2 — capability-type heuristic.
    if (type == "strobe") {
        val hasStrobeRange = caps.any { c ->
            val m = c as? Map<*, *> ?: return@any false
            m["type"] == "ShutterStrobe" && m["shutterEffect"] == "Strobe"
        }
        if (hasStrobeRange) return ShortcutId.STROBE_MOMENTARY
    }
    if (type == "uv") return ShortcutId.UV_TOGGLE

    // Pass 3 — name-match fallback for dimmer-class/speed/reset only.
    if (type in ELIGIBLE_NAME_TYPES) {
        val lname = ((channel["name"] as? String) ?: "").lowercase()
        for ((needle, sid) in NAME_HEURISTIC) {
            if (lname.contains(needle)) return sid
        }
    }
    return null
}

/**
 * Walk a profile and return the deduped, ordered shortcuts.
 *
 * Color-swatch is special: only one entry per fixture, anchored at the
 * first eligible channel (red). The renderer reads green/blue/uv
 * offsets from `channelOffsets`.
 *
 * @param profile profile dict with `channels` list and `channel_map`.
 *   Pass `channel_map` already-resolved (see `Models.kt` ProfileInfo).
 */
fun resolveShortcutsForProfile(profile: Map<String, Any?>): List<ResolvedShortcut> {
    @Suppress("UNCHECKED_CAST")
    val channels = profile["channels"] as? List<Map<String, Any?>> ?: return emptyList()

    // Rebuild channel_map from the `channels` list when caller didn't
    // supply it. The server's /api/dmx-profiles/<id> endpoint omits
    // channel_map from the JSON response; without this fallback the
    // color-swatch shortcut (which gates on red/green/blue presence)
    // was silently dropped on Android while the SPA — which rebuilds
    // channel_map at the call site in fixtures.js — saw it correctly.
    @Suppress("UNCHECKED_CAST")
    val provided = profile["channel_map"] as? Map<String, Any?>
    val channelMap: Map<String, Int> = if (provided != null) {
        provided.mapValues { (it.value as? Number)?.toInt() ?: 0 }
    } else {
        val cm = mutableMapOf<String, Int>()
        for (ch in channels) {
            val t = ch["type"] as? String ?: continue
            if (t !in cm) cm[t] = (ch["offset"] as? Number)?.toInt() ?: 0
        }
        cm
    }

    val out = mutableListOf<ResolvedShortcut>()
    var swatchAdded = false

    for (ch in channels) {
        val sid = resolveChannelShortcut(ch) ?: continue
        val base = specFor(sid)

        if (sid == ShortcutId.COLOR_SWATCH) {
            if (swatchAdded) continue
            if (!(channelMap.containsKey("red")
                  && channelMap.containsKey("green")
                  && channelMap.containsKey("blue"))) continue
            swatchAdded = true
            val offsets = mutableMapOf(
                "red"   to channelMap["red"]!!,
                "green" to channelMap["green"]!!,
                "blue"  to channelMap["blue"]!!,
            )
            channelMap["uv"]?.let { offsets["uv"] = it }
            out += base.copy(
                anchorOffset = (ch["offset"] as? Number)?.toInt() ?: 0,
                channelOffsets = offsets,
            )
            continue
        }

        @Suppress("UNCHECKED_CAST")
        val caps = (ch["capabilities"] as? List<Map<String, Any?>>) ?: emptyList()
        out += base.copy(
            channelOffset = (ch["offset"] as? Number)?.toInt() ?: 0,
            channelName = ch["name"] as? String,
            capabilities = caps,
        )
    }

    val order = listOf(
        ShortcutId.BUBBLE_TOGGLE, ShortcutId.HAZE_SEGMENTED, ShortcutId.FAN_SEGMENTED,
        ShortcutId.COLOR_SWATCH,  ShortcutId.UV_TOGGLE,      ShortcutId.STROBE_MOMENTARY,
        ShortcutId.CLEAN_MODE,
    )
    return out.sortedBy { order.indexOf(it.id) }
}

/**
 * Compute the DMX byte to write for STROBE_MOMENTARY when pressed —
 * midpoint of the first ShutterStrobe range with shutterEffect=Strobe.
 * Returns null if the profile doesn't declare one (renderer should hide
 * the shortcut in that case).
 */
fun strobeMomentaryValue(shortcut: ResolvedShortcut): Int? {
    for (cap in shortcut.capabilities) {
        if (cap["type"] == "ShutterStrobe" && cap["shutterEffect"] == "Strobe") {
            val rng = cap["range"] as? List<*> ?: continue
            if (rng.size < 2) continue
            val lo = (rng[0] as? Number)?.toInt() ?: continue
            val hi = (rng[1] as? Number)?.toInt() ?: continue
            return (lo + hi) / 2
        }
    }
    return null
}

/** Open value for the strobe channel (release of momentary). */
fun strobeOpenValue(shortcut: ResolvedShortcut): Int {
    for (cap in shortcut.capabilities) {
        if (cap["type"] == "ShutterStrobe" && cap["shutterEffect"] == "Open") {
            val rng = cap["range"] as? List<*> ?: continue
            if (rng.size < 2) continue
            val lo = (rng[0] as? Number)?.toInt() ?: continue
            val hi = (rng[1] as? Number)?.toInt() ?: continue
            return (lo + hi) / 2
        }
    }
    return 0
}
