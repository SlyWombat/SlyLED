package com.slywombat.slyled.ui.screens.control.pages

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowForward
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
import com.slywombat.slyled.data.model.Fixture
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
 * Fixtures page — non-mover DMX fixtures (bubble machines, hazers,
 * washes, pars, strobes) with profile-driven shortcut rows. Each card
 * surfaces the shortcuts resolved by `FixtureShortcuts.kt`; "More
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

    // Filter: DMX fixtures with panRange == 0 (non-movers).
    val nonMovers = remember(fixtures, profiles) {
        val byId = profiles.associateBy { it.id }
        fixtures.filter {
            it.fixtureType == "dmx" && (byId[it.dmxProfileId ?: ""]?.panRange ?: 0) == 0
        }.sortedBy { it.name }
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

        if (nonMovers.isEmpty()) {
            Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text(
                    "No DMX fixtures in the layout",
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
            items(nonMovers, key = { it.id }) { fix ->
                FixtureCard(fix, onOpenSheet)
            }
        }
    }
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
