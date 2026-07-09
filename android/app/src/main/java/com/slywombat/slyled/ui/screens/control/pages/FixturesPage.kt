package com.slywombat.slyled.ui.screens.control.pages

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowForward
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.WarningAmber
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.slywombat.slyled.data.model.Child
import com.slywombat.slyled.data.model.Fixture
import com.slywombat.slyled.ui.theme.GreenOnline
import com.slywombat.slyled.ui.theme.MutedSlate
import com.slywombat.slyled.ui.screens.control.haptics.HapticEvent
import com.slywombat.slyled.ui.screens.control.haptics.rememberHaptics
import com.slywombat.slyled.ui.screens.control.shortcuts.ResolvedShortcut
import com.slywombat.slyled.ui.screens.control.shortcuts.ShortcutUi
import com.slywombat.slyled.ui.screens.control.shortcuts.resolveShortcutsForProfile
import com.slywombat.slyled.ui.screens.control.shortcuts.strobeMomentaryValue
import com.slywombat.slyled.ui.screens.control.shortcuts.strobeOpenValue
import com.slywombat.slyled.ui.theme.DmxPurple
import com.slywombat.slyled.ui.theme.RedError
import com.slywombat.slyled.viewmodel.ControlViewModel
import kotlinx.coroutines.delay
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray

/**
 * Fixtures page — LED children (WS2812B performer strings) and non-mover
 * DMX fixtures (bubble machines, hazers, washes, pars, strobes).
 *
 * LED children get a per-string multi-select with quick controls + a
 * saved-Actions picker that fires an ad-hoc `CMD_ACTION` at the selected
 * strings via `POST /api/children/<id>/action`. DMX fixtures keep the
 * profile-driven shortcut rows resolved by `FixtureShortcuts.kt`; "More
 * controls →" opens FixtureSheet for full capability access.
 * Design doc §5.3 + §6.5. #888.
 */
@Composable
fun FixturesPage(
    onOpenSheet: (Fixture, JsonObject) -> Unit,
    modifier: Modifier = Modifier,
    vm: ControlViewModel = hiltViewModel(),
) {
    val haptic = rememberHaptics()
    val fixtures by vm.fixtures.collectAsState()
    val profiles by vm.profiles.collectAsState()
    val children by vm.children.collectAsState()

    // Filter: DMX fixtures with panRange == 0 (non-movers).
    val nonMovers = remember(fixtures, profiles) {
        val byId = profiles.associateBy { it.id }
        fixtures.filter {
            it.fixtureType == "dmx" && (byId[it.dmxProfileId ?: ""]?.panRange ?: 0) == 0
        }.sortedBy { it.name }
    }

    // LED children = performer nodes with configured strings. DMX bridges
    // (type "dmx") are excluded — they have no addressable LED strings.
    val ledChildren = remember(children) {
        children.filter { it.type != "dmx" && it.sc > 0 }
            .sortedBy { it.name.ifBlank { it.hostname } }
    }

    Column(modifier = modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 12.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(bottom = 12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "FIXTURES",
                style = MaterialTheme.typography.labelMedium,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            OutlinedButton(
                onClick = {
                    haptic(HapticEvent.HEAVY_THUD)
                    vm.stopAllEffects()
                },
                enabled = nonMovers.isNotEmpty(),
                colors = ButtonDefaults.outlinedButtonColors(contentColor = RedError),
            ) {
                Icon(
                    Icons.Filled.WarningAmber,
                    contentDescription = null,
                    modifier = Modifier.size(16.dp),
                )
                Spacer(Modifier.width(6.dp))
                Text("Stop all effects", style = MaterialTheme.typography.labelMedium)
            }
        }

        if (ledChildren.isEmpty() && nonMovers.isEmpty()) {
            Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text(
                    "No LED children or DMX fixtures",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            return@Column
        }

        LazyColumn(
            verticalArrangement = Arrangement.spacedBy(10.dp),
            modifier = Modifier.fillMaxSize(),
        ) {
            if (ledChildren.isNotEmpty()) {
                item(key = "hdr-led") { SectionLabel("LED STRINGS") }
                items(ledChildren, key = { "led-${it.id}" }) { child ->
                    LedChildCard(child)
                }
            }
            if (nonMovers.isNotEmpty()) {
                item(key = "hdr-dmx") { SectionLabel("DMX FIXTURES") }
                items(nonMovers, key = { "dmx-${it.id}" }) { fix ->
                    FixtureCard(fix, onOpenSheet)
                }
            }
        }
    }
}

@Composable
private fun SectionLabel(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.labelSmall,
        fontWeight = FontWeight.Bold,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 4.dp, bottom = 2.dp),
    )
}

@Composable
private fun FixtureCard(
    fixture: Fixture,
    onOpenSheet: (Fixture, JsonObject) -> Unit,
    vm: ControlViewModel = hiltViewModel(),
) {
    val haptic = rememberHaptics()
    var profileJson by remember(fixture.dmxProfileId) { mutableStateOf<JsonObject?>(null) }
    var shortcuts by remember(fixture.dmxProfileId) { mutableStateOf<List<ResolvedShortcut>>(emptyList()) }
    var loadError by remember(fixture.dmxProfileId) { mutableStateOf<String?>(null) }

    LaunchedEffect(fixture.dmxProfileId) {
        val pid = fixture.dmxProfileId ?: return@LaunchedEffect
        try {
            val full = vm.fetchProfileFull(pid)
            profileJson = full
            shortcuts = resolveShortcutsForProfile(jsonToMap(full))
        } catch (e: Exception) {
            loadError = e.message ?: "Profile load failed"
        }
    }

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        fixture.name.ifBlank { "Fixture ${fixture.id}" },
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold,
                    )
                    Text(
                        "Universe ${fixture.dmxUniverse ?: 1} / Ch ${fixture.dmxStartAddr ?: 1}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                if (profileJson != null) {
                    TextButton(onClick = {
                        haptic(HapticEvent.SOFT_TICK)
                        onOpenSheet(fixture, profileJson!!)
                    }) {
                        Text("More controls", style = MaterialTheme.typography.labelMedium)
                        Spacer(Modifier.width(2.dp))
                        Icon(
                            Icons.Filled.ArrowForward,
                            contentDescription = null,
                            modifier = Modifier.size(14.dp),
                        )
                    }
                }
            }
            if (loadError != null) {
                Spacer(Modifier.height(6.dp))
                Text(
                    "Profile error — $loadError",
                    style = MaterialTheme.typography.labelSmall,
                    color = RedError,
                )
                return@Card
            }
            if (shortcuts.isEmpty()) return@Card
            Spacer(Modifier.height(10.dp))
            // Render each shortcut. onWrite payload is {offset → byte}.
            shortcuts.forEach { sc ->
                ShortcutRow(fixture, sc) { writes ->
                    vm.channelWrite(fixture.id, writes)
                }
                Spacer(Modifier.height(6.dp))
            }
        }
    }
}

@Composable
private fun ShortcutRow(
    fixture: Fixture,
    sc: ResolvedShortcut,
    onWrite: (Map<Int, Int>) -> Unit,
) {
    val haptic = rememberHaptics()

    when (sc.ui) {
        ShortcutUi.TOGGLE -> {
            val off = sc.channelOffset ?: return
            var on by remember(fixture.id, sc.id) { mutableStateOf(false) }
            Row(
                modifier = Modifier.fillMaxWidth().height(48.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("${sc.icon}  ${sc.label}", modifier = Modifier.weight(1f))
                Switch(
                    checked = on,
                    onCheckedChange = {
                        on = it
                        haptic(HapticEvent.LIGHT_TICK)
                        onWrite(mapOf(off to if (on) sc.onValue else sc.offValue))
                    },
                )
            }
        }
        ShortcutUi.SEGMENTED -> {
            val off = sc.channelOffset ?: return
            var selectedIdx by remember(fixture.id, sc.id) { mutableIntStateOf(0) }
            Column(modifier = Modifier.fillMaxWidth()) {
                Text("${sc.icon}  ${sc.label}", style = MaterialTheme.typography.bodyMedium)
                Spacer(Modifier.height(4.dp))
                SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
                    sc.segments.forEachIndexed { i, seg ->
                        SegmentedButton(
                            selected = i == selectedIdx,
                            onClick = {
                                selectedIdx = i
                                haptic(HapticEvent.LIGHT_TICK)
                                onWrite(mapOf(off to seg.value))
                            },
                            shape = SegmentedButtonDefaults.itemShape(i, sc.segments.size),
                        ) { Text(seg.label, style = MaterialTheme.typography.labelSmall) }
                    }
                }
            }
        }
        ShortcutUi.COLOR_SWATCH -> {
            val offs = sc.channelOffsets ?: return
            val redOff = offs["red"] ?: return
            val greenOff = offs["green"] ?: return
            val blueOff = offs["blue"] ?: return
            Column(modifier = Modifier.fillMaxWidth()) {
                Text("${sc.icon}  ${sc.label}", style = MaterialTheme.typography.bodyMedium)
                Spacer(Modifier.height(6.dp))
                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    sc.swatches.forEach { sw ->
                        Box(
                            modifier = Modifier
                                .weight(1f)
                                .height(36.dp)
                                .clip(RoundedCornerShape(6.dp))
                                .background(
                                    Color(sw.rgb.first, sw.rgb.second, sw.rgb.third),
                                )
                                .border(
                                    width = 1.dp,
                                    color = Color.Black.copy(alpha = 0.3f),
                                    shape = RoundedCornerShape(6.dp),
                                )
                                .clickable {
                                    haptic(HapticEvent.LIGHT_TICK)
                                    onWrite(
                                        mapOf(
                                            redOff   to sw.rgb.first,
                                            greenOff to sw.rgb.second,
                                            blueOff  to sw.rgb.third,
                                        )
                                    )
                                },
                        )
                    }
                }
            }
        }
        ShortcutUi.MOMENTARY -> {
            val off = sc.channelOffset ?: return
            val pressed = remember(fixture.id, sc.id) { mutableStateOf(false) }
            val onValue = strobeMomentaryValue(sc) ?: 128
            val offValue = strobeOpenValue(sc)
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(
                        if (pressed.value) DmxPurple.copy(alpha = 0.35f)
                        else MaterialTheme.colorScheme.surface,
                    )
                    .border(
                        width = 1.dp,
                        color = DmxPurple.copy(alpha = if (pressed.value) 0.8f else 0.3f),
                        shape = RoundedCornerShape(8.dp),
                    )
                    .pointerInput(fixture.id, sc.id) {
                        // Use detectTapGestures-like press/release semantics
                        // via awaitPointerEventScope for momentary action.
                        awaitPointerEventScope {
                            while (true) {
                                val event = awaitPointerEvent()
                                val anyDown = event.changes.any { it.pressed }
                                if (anyDown != pressed.value) {
                                    pressed.value = anyDown
                                    if (anyDown) {
                                        haptic(HapticEvent.LOW_RUMBLE)
                                        onWrite(mapOf(off to onValue))
                                    } else {
                                        onWrite(mapOf(off to offValue))
                                    }
                                }
                            }
                        }
                    },
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    "${sc.icon}  ${sc.label}  (hold)",
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
        ShortcutUi.LONG_PRESS -> {
            // Long-press clean mode — fires after `confirmHoldMs`.
            val off = sc.channelOffset ?: return
            val pressed = remember(fixture.id, sc.id) { mutableStateOf(false) }
            val firedRef = remember(fixture.id, sc.id) { mutableStateOf(false) }
            LaunchedEffect(pressed.value) {
                firedRef.value = false
                if (!pressed.value) return@LaunchedEffect
                delay(sc.confirmHoldMs)
                if (pressed.value) {
                    firedRef.value = true
                    haptic(HapticEvent.HEAVY_THUD)
                    onWrite(mapOf(off to sc.onValue))
                }
            }
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(MaterialTheme.colorScheme.surface)
                    .border(
                        width = 1.dp,
                        color = MaterialTheme.colorScheme.outline.copy(alpha = 0.4f),
                        shape = RoundedCornerShape(8.dp),
                    )
                    .pointerInput(fixture.id, sc.id) {
                        awaitPointerEventScope {
                            while (true) {
                                val event = awaitPointerEvent()
                                val anyDown = event.changes.any { it.pressed }
                                if (anyDown != pressed.value) {
                                    pressed.value = anyDown
                                    if (!anyDown && firedRef.value) {
                                        onWrite(mapOf(off to sc.offValue))
                                    }
                                }
                            }
                        }
                    },
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    if (firedRef.value) "${sc.icon}  ${sc.label} — RUNNING (release to stop)"
                    else "${sc.icon}  ${sc.label}  (hold ${sc.confirmHoldMs / 1000}s)",
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
    }
}

// ── LED children ──────────────────────────────────────────────────────

/** Action-type names, index = wire `type`. Mirrors `_typeNames` in
 *  `desktop/shared/spa/js/actions.js`. Only LED types (0-13) are listed;
 *  the DMX/Track types (14-18) never appear in the LED action picker.
 *  #906 — canonical spelling is "Color ..." (matches docs/USER_MANUAL.md
 *  and parent_server.py _ACTION_NAMES); gated by
 *  tests/test_parity_action_names.py. */
private val LED_ACTION_TYPE_NAMES = arrayOf(
    "Blackout", "Solid", "Fade", "Breathe", "Chase", "Rainbow", "Fire",
    "Comet", "Twinkle", "Strobe", "Color Wipe", "Scanner", "Sparkle",
    "Gradient",
)

/** Quick solid-colour swatches as (r, g, b). */
private val LED_SWATCHES = listOf(
    Triple(255, 0, 0),
    Triple(0, 255, 0),
    Triple(0, 60, 255),
    Triple(255, 255, 255),
    Triple(255, 120, 0),
    Triple(255, 0, 140),
)

/**
 * One LED child — header, per-string multi-select, quick controls, and
 * a saved-Actions picker. Every fire targets only the ticked strings
 * (or all of them, sent as `allStrings`, when the whole set is ticked).
 */
@Composable
private fun LedChildCard(
    child: Child,
    vm: ControlViewModel = hiltViewModel(),
) {
    val haptic = rememberHaptics()
    val actions by vm.actions.collectAsState()
    val ledActions = remember(actions) {
        actions.filter { it.type in 0..13 }.sortedBy { it.name }
    }
    val stringCount = minOf(child.sc, child.strings.size)

    var selected by remember(child.id, stringCount) {
        mutableStateOf((0 until stringCount).toSet())
    }
    var showActions by remember(child.id) { mutableStateOf(false) }

    // null = every string (server sends allStrings); else the ticked subset.
    fun targets(): List<Int>? =
        if (selected.size == stringCount) null else selected.sorted()
    val enabled = selected.isNotEmpty()
    val online = child.onlineStatus == com.slywombat.slyled.data.model.OnlineStatus.ONLINE

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            // Header — online dot + name + string count.
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
                Box(
                    modifier = Modifier
                        .size(9.dp)
                        .clip(CircleShape)
                        .background(if (online) GreenOnline else MutedSlate),
                )
                Spacer(Modifier.width(8.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        child.name.ifBlank { child.hostname.ifBlank { "Child ${child.id}" } },
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold,
                    )
                    Text(
                        "$stringCount string${if (stringCount == 1) "" else "s"}" +
                            (child.boardType.takeIf { it.isNotBlank() }?.let { "  ·  $it" } ?: ""),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            Spacer(Modifier.height(10.dp))

            // Per-string multi-select.
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "TARGET STRINGS",
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Row {
                    TextButton(onClick = {
                        selected = (0 until stringCount).toSet()
                        haptic(HapticEvent.SOFT_TICK)
                    }) { Text("All", style = MaterialTheme.typography.labelSmall) }
                    TextButton(onClick = {
                        selected = emptySet()
                        haptic(HapticEvent.SOFT_TICK)
                    }) { Text("None", style = MaterialTheme.typography.labelSmall) }
                }
            }
            for (idx in 0 until stringCount) {
                val cfg = child.strings.getOrNull(idx)
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(40.dp)
                        .clickable {
                            selected = if (idx in selected) selected - idx else selected + idx
                            haptic(HapticEvent.LIGHT_TICK)
                        },
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Checkbox(
                        checked = idx in selected,
                        onCheckedChange = {
                            selected = if (idx in selected) selected - idx else selected + idx
                            haptic(HapticEvent.LIGHT_TICK)
                        },
                    )
                    Text(
                        "String ${idx + 1}  —  ${cfg?.leds ?: 0} LEDs",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }

            Spacer(Modifier.height(8.dp))
            HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.3f))
            Spacer(Modifier.height(10.dp))

            // Quick controls — solid swatches + effect buttons.
            Text(
                "QUICK CONTROLS",
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(6.dp))
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                LED_SWATCHES.forEach { (r, g, b) ->
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .height(34.dp)
                            .clip(RoundedCornerShape(6.dp))
                            .background(Color(r, g, b))
                            .border(1.dp, Color.Black.copy(alpha = 0.3f), RoundedCornerShape(6.dp))
                            .clickable(enabled = enabled) {
                                haptic(HapticEvent.LIGHT_TICK)
                                vm.fireLedAction(
                                    child.id, inlineType = 1,
                                    r = r, g = g, b = b, strings = targets(),
                                )
                            },
                    )
                }
            }
            Spacer(Modifier.height(8.dp))
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                FilledTonalButton(
                    onClick = {
                        haptic(HapticEvent.LIGHT_TICK)
                        // Fire: orange base tint, cooling p8a, sparking p8b.
                        vm.fireLedAction(
                            child.id, inlineType = 6,
                            r = 255, g = 100, b = 0,
                            p16a = 30, p8a = 55, p8b = 120,
                            strings = targets(),
                        )
                    },
                    enabled = enabled,
                    contentPadding = PaddingValues(horizontal = 10.dp),
                    modifier = Modifier.weight(1f),
                ) { Text("Fire", style = MaterialTheme.typography.labelMedium) }
                FilledTonalButton(
                    onClick = {
                        haptic(HapticEvent.LIGHT_TICK)
                        vm.fireLedAction(
                            child.id, inlineType = 5, p16a = 20, strings = targets(),
                        )
                    },
                    enabled = enabled,
                    contentPadding = PaddingValues(horizontal = 10.dp),
                    modifier = Modifier.weight(1f),
                ) { Text("Rainbow", style = MaterialTheme.typography.labelMedium) }
                OutlinedButton(
                    onClick = {
                        haptic(HapticEvent.HEAVY_THUD)
                        vm.fireLedAction(child.id, inlineType = 0, strings = targets())
                    },
                    enabled = enabled,
                    contentPadding = PaddingValues(horizontal = 10.dp),
                    modifier = Modifier.weight(1f),
                ) { Text("Blackout", style = MaterialTheme.typography.labelMedium) }
                OutlinedButton(
                    onClick = {
                        haptic(HapticEvent.HEAVY_THUD)
                        vm.stopLedAction(child.id)
                    },
                    contentPadding = PaddingValues(horizontal = 10.dp),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = RedError),
                    modifier = Modifier.weight(1f),
                ) { Text("Stop", style = MaterialTheme.typography.labelMedium) }
            }

            // Saved Actions picker.
            if (ledActions.isNotEmpty()) {
                Spacer(Modifier.height(6.dp))
                TextButton(onClick = { showActions = !showActions }) {
                    Text(
                        "Saved actions (${ledActions.size})",
                        style = MaterialTheme.typography.labelMedium,
                    )
                    Spacer(Modifier.width(2.dp))
                    Icon(
                        if (showActions) Icons.Filled.ExpandLess else Icons.Filled.ExpandMore,
                        contentDescription = null,
                        modifier = Modifier.size(16.dp),
                    )
                }
                if (showActions) {
                    ledActions.forEach { act ->
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(44.dp)
                                .clickable(enabled = enabled) {
                                    haptic(HapticEvent.LIGHT_TICK)
                                    vm.fireLedAction(
                                        child.id, actionId = act.id, strings = targets(),
                                    )
                                },
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                act.name.ifBlank { "Action ${act.id}" },
                                style = MaterialTheme.typography.bodyMedium,
                                modifier = Modifier.weight(1f),
                            )
                            Text(
                                LED_ACTION_TYPE_NAMES.getOrElse(act.type) { "?" },
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
        }
    }
}

/** Convert kotlinx JsonObject to the plain Map<String, Any?> shape that
 *  FixtureShortcuts.kt's resolver expects. Lossy but enough for the
 *  fields the resolver reads (type, name, capabilities, shortcut,
 *  offset, bits, channels[]). */
fun jsonToMap(obj: JsonObject): Map<String, Any?> {
    val out = mutableMapOf<String, Any?>()
    for ((k, v) in obj) out[k] = decode(v)
    return out
}

private fun decode(el: JsonElement): Any? = when (el) {
    is JsonNull -> null
    is JsonPrimitive -> when {
        // Quoted strings stay as-is so "True White" doesn't get
        // tokenised into a boolean.
        el.isString -> el.content
        el.booleanOrNull != null -> el.boolean
        el.intOrNull != null -> el.intOrNull
        el.doubleOrNull != null -> el.doubleOrNull
        else -> el.content
    }
    is JsonObject -> jsonToMap(el)
    else -> el.jsonArray.map(::decode)
}
