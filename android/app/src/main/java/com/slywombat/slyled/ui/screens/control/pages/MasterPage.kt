package com.slywombat.slyled.ui.screens.control.pages

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.BrightnessHigh
import androidx.compose.material.icons.filled.BrightnessLow
import androidx.compose.material.icons.filled.GraphicEq
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material.icons.filled.Add
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.slywombat.slyled.audio.AudioSourceKind
import com.slywombat.slyled.audio.MicAutoBrightness
import com.slywombat.slyled.ui.screens.control.haptics.HapticEvent
import com.slywombat.slyled.ui.screens.control.haptics.rememberHaptics
import com.slywombat.slyled.ui.theme.CyanSecondary
import com.slywombat.slyled.ui.theme.LuminaBlue
import com.slywombat.slyled.viewmodel.ControlViewModel
import com.slywombat.slyled.viewmodel.SettingsViewModel
import kotlin.math.roundToInt

/**
 * Default Control-tab page on cold start. Master brightness slider with
 * ±5% steppers and bloom-on-drag, plus the Auto Brightness toggle +
 * source picker + live envelope meter (moved from Settings per design
 * doc §5.1). #888.
 */
@Composable
fun MasterPage(
    controlVm: ControlViewModel = hiltViewModel(),
    settingsVm: SettingsViewModel = hiltViewModel(),
    modifier: Modifier = Modifier,
) {
    val haptic = rememberHaptics()
    val settings by controlVm.settings.collectAsState()
    val brightness = settings.globalBrightness ?: 255
    var sliderValue by remember { mutableFloatStateOf(brightness.toFloat()) }
    LaunchedEffect(brightness) { sliderValue = brightness.toFloat() }

    val autoEnabled by settingsVm.autoBrightnessEnabled.collectAsState()
    val envelope by settingsVm.autoBrightnessEnvelope.collectAsState()
    val source by settingsVm.autoBrightnessAudioSourceKindFlow.collectAsState()
    val master by settingsVm.autoBrightnessMaster.collectAsState()

    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        // Brightness section
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp)) {
                Text(
                    "BRIGHTNESS",
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(12.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = "${sliderValue.roundToInt()}",
                        style = MaterialTheme.typography.displaySmall,
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.weight(1f),
                    )
                    val pct = ((sliderValue / 255f) * 100f).roundToInt()
                    Text(
                        text = "${pct}%",
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Spacer(Modifier.height(8.dp))
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Icon(
                        Icons.Default.BrightnessLow,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(20.dp),
                    )
                    Slider(
                        value = sliderValue,
                        onValueChange = { sliderValue = it },
                        onValueChangeFinished = {
                            controlVm.setBrightness(sliderValue.roundToInt())
                            haptic(HapticEvent.LIGHT_TICK)
                        },
                        valueRange = 0f..255f,
                        modifier = Modifier.weight(1f),
                    )
                    Icon(
                        Icons.Default.BrightnessHigh,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(20.dp),
                    )
                }
                Spacer(Modifier.height(8.dp))
                // ±5% (= ±12 of 255) steppers for dim-room finger precision.
                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    OutlinedButton(
                        onClick = {
                            val v = (sliderValue - 13f).coerceIn(0f, 255f)
                            sliderValue = v
                            controlVm.setBrightness(v.roundToInt())
                            haptic(HapticEvent.SOFT_TICK)
                        },
                        modifier = Modifier.weight(1f).height(48.dp),
                    ) {
                        Icon(Icons.Default.Remove, contentDescription = "Brightness -5%")
                        Spacer(Modifier.width(4.dp))
                        Text("−5%")
                    }
                    OutlinedButton(
                        onClick = {
                            val v = (sliderValue + 13f).coerceIn(0f, 255f)
                            sliderValue = v
                            controlVm.setBrightness(v.roundToInt())
                            haptic(HapticEvent.SOFT_TICK)
                        },
                        modifier = Modifier.weight(1f).height(48.dp),
                    ) {
                        Icon(Icons.Default.Add, contentDescription = "Brightness +5%")
                        Spacer(Modifier.width(4.dp))
                        Text("+5%")
                    }
                }
            }
        }

        // Auto Brightness section
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(16.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            "AUTO BRIGHTNESS",
                            style = MaterialTheme.typography.labelMedium,
                            fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Spacer(Modifier.height(2.dp))
                        Text(
                            text = if (autoEnabled) "Active" else "Off",
                            style = MaterialTheme.typography.bodyMedium,
                            color = if (autoEnabled) CyanSecondary
                                    else MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Switch(
                        checked = autoEnabled,
                        onCheckedChange = {
                            haptic(HapticEvent.LIGHT_TICK)
                            settingsVm.setAutoBrightnessEnabled(it)
                        },
                    )
                }
                Spacer(Modifier.height(12.dp))
                Text(
                    "Source",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(4.dp))
                // Source picker — 3 likely-used sources as segmented row;
                // REMOTE_SUBMIX is rare so it's not on the quick-pick row.
                Row(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    SourceChip(
                        label = "Mic",
                        selected = source == AudioSourceKind.MICROPHONE,
                        onClick = {
                            haptic(HapticEvent.SOFT_TICK)
                            settingsVm.configureAutoBrightness(
                                audioSourceKind = AudioSourceKind.MICROPHONE,
                            )
                        },
                        modifier = Modifier.weight(1f),
                    )
                    SourceChip(
                        label = "Playback",
                        selected = source == AudioSourceKind.PLAYBACK_CAPTURE,
                        onClick = {
                            haptic(HapticEvent.SOFT_TICK)
                            settingsVm.configureAutoBrightness(
                                audioSourceKind = AudioSourceKind.PLAYBACK_CAPTURE,
                            )
                        },
                        modifier = Modifier.weight(1f),
                    )
                    SourceChip(
                        label = "USB",
                        selected = source == AudioSourceKind.USB_AUDIO,
                        onClick = {
                            haptic(HapticEvent.SOFT_TICK)
                            settingsVm.configureAutoBrightness(
                                audioSourceKind = AudioSourceKind.USB_AUDIO,
                            )
                        },
                        modifier = Modifier.weight(1f),
                    )
                }
                Spacer(Modifier.height(16.dp))
                // Envelope meter — "is Auto actually doing anything?"
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        Icons.Default.GraphicEq,
                        contentDescription = null,
                        tint = if (autoEnabled) CyanSecondary
                               else MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(18.dp),
                    )
                    Spacer(Modifier.width(8.dp))
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .height(8.dp)
                            .clip(RoundedCornerShape(50))
                            .background(MaterialTheme.colorScheme.outlineVariant),
                    ) {
                        val level = envelope.coerceIn(0f, 1f)
                        Box(
                            modifier = Modifier
                                .fillMaxHeight()
                                .fillMaxWidth(level)
                                .background(if (autoEnabled) CyanSecondary else Color.Transparent),
                        )
                    }
                    Spacer(Modifier.width(8.dp))
                    Text(
                        text = "${(master * 100).roundToInt()}%",
                        style = MaterialTheme.typography.labelSmall,
                        fontFamily = FontFamily.Monospace,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Spacer(Modifier.height(4.dp))
                Text(
                    text = autoBrightnessHint(autoEnabled, settingsVm.autoBrightnessState.collectAsState().value),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun SourceChip(
    label: String,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    FilterChip(
        selected = selected,
        onClick = onClick,
        label = { Text(label, style = MaterialTheme.typography.labelMedium) },
        modifier = modifier.height(36.dp),
        colors = FilterChipDefaults.filterChipColors(
            selectedContainerColor = LuminaBlue.copy(alpha = 0.18f),
            selectedLabelColor = LuminaBlue,
        ),
    )
}

private fun autoBrightnessHint(enabled: Boolean, mode: MicAutoBrightness.Mode): String {
    if (!enabled) return "Tap the switch to follow the room's audio"
    return when (mode) {
        MicAutoBrightness.Mode.Idle -> "Idle"
        MicAutoBrightness.Mode.Listening -> "Live — driving master brightness"
        MicAutoBrightness.Mode.NoMic -> "No microphone detected"
        MicAutoBrightness.Mode.PermissionDenied -> "Microphone permission denied"
        MicAutoBrightness.Mode.Clipping -> "Clipping — lower the input"
        MicAutoBrightness.Mode.NeedsPlaybackConsent -> "Grant playback capture in the system prompt"
        MicAutoBrightness.Mode.NoUsbDevice -> "USB audio device not found"
        MicAutoBrightness.Mode.RemoteSubmixDenied -> "Remote submix not available on this device"
    }
}
