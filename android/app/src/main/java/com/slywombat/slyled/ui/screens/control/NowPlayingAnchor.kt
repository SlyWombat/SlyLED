package com.slywombat.slyled.ui.screens.control

import androidx.compose.foundation.clickable
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MusicOff
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.slywombat.slyled.data.model.ShowStatus
import com.slywombat.slyled.data.model.Timeline
import com.slywombat.slyled.data.model.TimelineStatus
import com.slywombat.slyled.ui.screens.control.haptics.HapticEvent
import com.slywombat.slyled.ui.screens.control.haptics.rememberHaptics
import com.slywombat.slyled.ui.theme.CyanSecondary
import com.slywombat.slyled.ui.theme.GreenOnline
import com.slywombat.slyled.ui.theme.RedError

/**
 * Persistent anchor above the Master/Grab/Fixtures/Shows pager.
 * Idle = collapsed one-line "No show running".
 * Playing = name + loop chip + elapsed/total + progress + STOP + Next.
 * Design doc §5.1 + §4.3.
 */
@Composable
fun NowPlayingAnchor(
    isRunning: Boolean,
    timelineStatus: TimelineStatus?,
    showStatus: ShowStatus?,
    timelines: List<Timeline>,
    modifier: Modifier = Modifier,
    onStop: () -> Unit,
    onNext: () -> Unit,
    onResumeSchedule: () -> Unit = {},
    onJumpToShows: () -> Unit = {},
) {
    val schedule = showStatus?.schedule
    val haptic = rememberHaptics()
    if (!isRunning || timelineStatus == null) {
        // Idle: tight one-liner. Reserves the slot so the pager doesn't
        // jump up when a show starts/stops. Tap jumps to Shows page
        // (design doc §4.3).
        Surface(
            modifier = modifier
                .fillMaxWidth()
                .clickable {
                    haptic(HapticEvent.SOFT_TICK)
                    onJumpToShows()
                },
            color = MaterialTheme.colorScheme.surfaceVariant,
        ) {
          Column {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Icon(
                    Icons.Filled.MusicOff,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(16.dp),
                )
                Text(
                    text = "No show running",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            ScheduleLine(schedule, onResumeSchedule,
                Modifier.padding(start = 16.dp, end = 16.dp, bottom = 6.dp))
          }
        }
        return
    }

    // Playing — pulse the border in CyanSecondary at 0.5 Hz.
    val transition = rememberInfiniteTransition(label = "anchor-pulse")
    val pulse by transition.animateFloat(
        initialValue = 0.4f,
        targetValue = 0.85f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 2000),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "anchor-pulse-alpha",
    )

    Surface(
        modifier = modifier.fillMaxWidth(),
        color = GreenOnline.copy(alpha = 0.08f),
        border = androidx.compose.foundation.BorderStroke(
            width = 1.dp, color = CyanSecondary.copy(alpha = pulse),
        ),
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        "NOW PLAYING",
                        style = MaterialTheme.typography.labelSmall,
                        color = GreenOnline,
                        fontWeight = FontWeight.Bold,
                    )
                    Spacer(Modifier.height(2.dp))
                    val name = timelineStatus.name.ifBlank {
                        timelines.find { it.id == timelineStatus.id }?.name
                            ?: "Timeline #${timelineStatus.id}"
                    }
                    Text(
                        text = name,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                    )
                }
                if (timelineStatus.loop) {
                    SuggestionChip(
                        onClick = {},
                        label = {
                            Text("Loop", style = MaterialTheme.typography.labelSmall)
                        },
                        colors = SuggestionChipDefaults.suggestionChipColors(
                            containerColor = CyanSecondary.copy(alpha = 0.15f),
                            labelColor = CyanSecondary,
                        ),
                        border = null,
                    )
                }
            }
            Spacer(Modifier.height(8.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    text = formatTime(timelineStatus.elapsed),
                    style = MaterialTheme.typography.bodySmall,
                )
                Text(
                    text = formatTime(timelineStatus.durationS),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.height(4.dp))
            if (timelineStatus.durationS > 0) {
                LinearProgressIndicator(
                    progress = {
                        (timelineStatus.elapsed.toFloat() / timelineStatus.durationS)
                            .coerceIn(0f, 1f)
                    },
                    modifier = Modifier.fillMaxWidth(),
                    color = CyanSecondary,
                    trackColor = MaterialTheme.colorScheme.outlineVariant,
                )
            }
            // #954 — scheduler line: "Scheduled · until 23:00" / "Manual — Resume".
            ScheduleLine(schedule, onResumeSchedule, Modifier.padding(top = 6.dp))
            // Playlist progress hint when applicable.
            if (showStatus != null && showStatus.running && showStatus.totalTimelines > 1) {
                Spacer(Modifier.height(6.dp))
                Text(
                    text = "Playlist: ${showStatus.currentIndex + 1} of ${showStatus.totalTimelines}",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.height(10.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Button(
                    onClick = {
                        haptic(HapticEvent.HEAVY_THUD)
                        onStop()
                    },
                    modifier = Modifier
                        .weight(1f)
                        .height(56.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = RedError),
                ) {
                    Icon(Icons.Filled.Stop, contentDescription = null, modifier = Modifier.size(20.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("STOP", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                }
                // Next only shows when we're in a playlist of >1.
                val hasNext = (showStatus?.running == true && showStatus.totalTimelines > 1)
                if (hasNext) {
                    OutlinedButton(
                        onClick = {
                            haptic(HapticEvent.SUCCESS_TICK)
                            onNext()
                        },
                        modifier = Modifier
                            .weight(0.6f)
                            .height(56.dp),
                    ) {
                        Icon(
                            Icons.Filled.SkipNext,
                            contentDescription = null,
                            modifier = Modifier.size(20.dp),
                        )
                        Spacer(Modifier.width(4.dp))
                        Text("Next")
                    }
                }
            }
        }
    }
}

private fun formatTime(seconds: Int): String {
    val m = seconds / 60
    val s = seconds % 60
    return "%02d:%02d".format(m, s)
}


/**
 * #954 — one line under Now Playing when the show scheduler is on:
 * "Scheduled · Evening (Christmas) · until 23:00 (sunset −15 m)" or
 * "Manual — schedule paused" with a Resume button. Stop/Next still work as
 * before; the server turns them into the manual override.
 */
@Composable
fun ScheduleLine(
    schedule: com.slywombat.slyled.data.model.ScheduleSummary?,
    onResume: () -> Unit,
    modifier: Modifier = Modifier,
) {
    if (schedule == null || !schedule.enabled) return
    val haptic = rememberHaptics()
    val paused = schedule.override?.active == true
    Row(
        modifier = modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        val text = when {
            paused -> "Manual — schedule paused"
            schedule.error != null -> "Schedule: ${schedule.error}"
            else -> {
                val now = schedule.now
                val what = now?.what ?: "idle"
                val until = scheduleLocalTime(now?.until)
                buildString {
                    append("Scheduled · ").append(what)
                    now?.schedule?.let { append(" (").append(it).append(")") }
                    if (until != null) {
                        append(" · until ").append(until)
                        val desc = now?.untilDesc
                        if (desc != null && desc != until) append(" (").append(desc).append(")")
                    } else {
                        val nx = schedule.next
                        val at = scheduleLocalTime(nx?.at)
                        if (nx?.what != null && at != null) append(" · next ").append(nx.what).append(" at ").append(at)
                    }
                }
            }
        }
        Text(
            text = text,
            style = MaterialTheme.typography.labelSmall,
            color = if (paused) RedError else CyanSecondary,
            modifier = Modifier.weight(1f),
        )
        if (paused) {
            TextButton(onClick = {
                haptic(HapticEvent.SOFT_TICK)
                onResume()
            }) { Text("Resume") }
        }
    }
}

/** ISO-8601 UTC ("2026-12-01T04:00:00Z") → local "HH:mm", or null. */
fun scheduleLocalTime(iso: String?): String? {
    if (iso.isNullOrBlank()) return null
    return try {
        java.time.OffsetDateTime.parse(iso)
            .atZoneSameInstant(java.time.ZoneId.systemDefault())
            .format(java.time.format.DateTimeFormatter.ofPattern("HH:mm"))
    } catch (_: Exception) {
        null
    }
}
