package com.slywombat.slyled.ui.screens.control.overlays

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.slywombat.slyled.data.model.Fixture
import com.slywombat.slyled.ui.screens.control.haptics.HapticEvent
import com.slywombat.slyled.ui.screens.control.haptics.rememberHaptics
import com.slywombat.slyled.ui.screens.control.pages.jsonToMap
import com.slywombat.slyled.ui.theme.DmxPurple
import kotlinx.serialization.json.JsonObject

/**
 * Full-profile "More controls →" sheet. Lists every channel of the
 * fixture's profile grouped by name, each with a slider whose range
 * comes from the channel's capabilities. Profile-driven; no raw byte
 * values exposed unless the operator drags into a capability range
 * that doesn't have a label.
 *
 * Design doc §5.3.3. #888.
 */
@Composable
fun FixtureSheet(
    fixture: Fixture,
    profile: JsonObject,
    onDismiss: () -> Unit,
    onWrite: (offset: Int, byte: Int) -> Unit,
) {
    val haptic = rememberHaptics()
    val asMap = remember(profile) { jsonToMap(profile) }
    @Suppress("UNCHECKED_CAST")
    val channels = (asMap["channels"] as? List<Map<String, Any?>>) ?: emptyList()
    val profileName = (asMap["name"] as? String) ?: fixture.dmxProfileId ?: "Unknown profile"

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Surface(
            modifier = Modifier
                .fillMaxSize()
                .padding(8.dp),
            shape = RoundedCornerShape(16.dp),
            color = MaterialTheme.colorScheme.surface,
        ) {
            Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            fixture.name.ifBlank { "Fixture ${fixture.id}" },
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                        )
                        Text(
                            profileName,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    IconButton(onClick = {
                        haptic(HapticEvent.SOFT_TICK)
                        onDismiss()
                    }) {
                        Icon(Icons.Filled.Close, contentDescription = "Close")
                    }
                }
                Spacer(Modifier.height(12.dp))
                Column(modifier = Modifier.verticalScroll(rememberScrollState()).weight(1f)) {
                    channels.forEach { ch ->
                        ChannelControl(ch, onWrite)
                        Spacer(Modifier.height(12.dp))
                    }
                }
            }
        }
    }
}

@Composable
private fun ChannelControl(
    ch: Map<String, Any?>,
    onWrite: (offset: Int, byte: Int) -> Unit,
) {
    val haptic = rememberHaptics()
    val name = (ch["name"] as? String) ?: "Channel"
    val type = (ch["type"] as? String) ?: ""
    val offset = (ch["offset"] as? Number)?.toInt() ?: return
    var sliderValue by remember(offset) { mutableFloatStateOf(0f) }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .background(MaterialTheme.colorScheme.surfaceContainer)
            .padding(12.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    name,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                )
                Text(
                    "Ch ${offset + 1} · $type",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Text(
                "${sliderValue.toInt()}",
                style = MaterialTheme.typography.bodyLarge,
                fontFamily = FontFamily.Monospace,
                color = DmxPurple,
            )
        }
        Slider(
            value = sliderValue,
            onValueChange = { sliderValue = it },
            onValueChangeFinished = {
                haptic(HapticEvent.SOFT_TICK)
                onWrite(offset, sliderValue.toInt())
            },
            valueRange = 0f..255f,
        )
        // Show capability ranges as a horizontal mini-legend so the operator
        // can see what each region of the slider does.
        @Suppress("UNCHECKED_CAST")
        val caps = (ch["capabilities"] as? List<Map<String, Any?>>) ?: emptyList()
        if (caps.isNotEmpty()) {
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                caps.forEach { cap ->
                    val range = cap["range"] as? List<*>
                    val lo = (range?.getOrNull(0) as? Number)?.toInt() ?: 0
                    val hi = (range?.getOrNull(1) as? Number)?.toInt() ?: 0
                    val label = (cap["label"] as? String) ?: (cap["type"] as? String) ?: ""
                    val inRange = sliderValue.toInt() in lo..hi
                    Text(
                        "  $lo–$hi  $label",
                        style = MaterialTheme.typography.labelSmall,
                        color = if (inRange) DmxPurple
                                else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}
