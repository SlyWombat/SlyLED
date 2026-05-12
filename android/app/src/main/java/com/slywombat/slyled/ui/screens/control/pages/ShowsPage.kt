package com.slywombat.slyled.ui.screens.control.pages

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.StarBorder
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.slywombat.slyled.data.model.Timeline
import com.slywombat.slyled.ui.screens.control.haptics.HapticEvent
import com.slywombat.slyled.ui.screens.control.haptics.rememberHaptics
import com.slywombat.slyled.ui.theme.CyanSecondary
import com.slywombat.slyled.viewmodel.ControlViewModel

/**
 * Shows page — ranked timeline list: starred first, then recently
 * played, then the rest. One-tap launch. Playlist controls live in a
 * collapsible footer. Design doc §5.4. #888.
 */
@Composable
fun ShowsPage(
    modifier: Modifier = Modifier,
    vm: ControlViewModel = hiltViewModel(),
) {
    val haptic = rememberHaptics()
    val timelines by vm.timelines.collectAsState()
    val starred by vm.starredTimelines.collectAsState()
    val lastPlayed by vm.lastPlayedAt.collectAsState()
    val playlist by vm.playlist.collectAsState()
    val showStatus by vm.showStatus.collectAsState()
    val settings by vm.settings.collectAsState()

    val now = System.currentTimeMillis()
    val sevenDaysMs = 7 * 24 * 60 * 60 * 1000L

    val starredShows = remember(timelines, starred) {
        timelines.filter { it.id in starred }.sortedBy { it.name }
    }
    val recentShows = remember(timelines, starred, lastPlayed, now) {
        timelines.filter {
            it.id !in starred &&
                ((lastPlayed[it.id] ?: 0) > now - sevenDaysMs)
        }.sortedByDescending { lastPlayed[it.id] ?: 0 }
    }
    val rest = remember(timelines, starred, recentShows) {
        val taken = (starred + recentShows.map { it.id }.toSet())
        timelines.filter { it.id !in taken }.sortedBy { it.name }
    }

    Column(modifier = modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 12.dp)) {
        Text(
            "SHOWS",
            style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(bottom = 12.dp),
        )
        if (timelines.isEmpty()) {
            Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text(
                    "No timelines yet — build one in the desktop app",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            return@Column
        }

        LazyColumn(
            verticalArrangement = Arrangement.spacedBy(2.dp),
            modifier = Modifier.fillMaxSize(),
        ) {
            if (starredShows.isNotEmpty()) {
                item {
                    SectionLabel("Favourites")
                }
                items(starredShows, key = { "s-${it.id}" }) { tl ->
                    ShowRow(
                        timeline = tl,
                        starred = true,
                        lastPlayedAt = lastPlayed[tl.id],
                        isPlaying = settings.runnerRunning && settings.activeTimeline == tl.id,
                        onStart = {
                            haptic(HapticEvent.SUCCESS_TICK)
                            vm.startTimeline(tl.id)
                        },
                        onToggleStar = {
                            haptic(HapticEvent.LIGHT_TICK)
                            vm.toggleStarredTimeline(tl.id)
                        },
                    )
                }
            }
            if (recentShows.isNotEmpty()) {
                item { SectionLabel("Recent") }
                items(recentShows, key = { "r-${it.id}" }) { tl ->
                    ShowRow(
                        timeline = tl,
                        starred = false,
                        lastPlayedAt = lastPlayed[tl.id],
                        isPlaying = settings.runnerRunning && settings.activeTimeline == tl.id,
                        onStart = {
                            haptic(HapticEvent.SUCCESS_TICK)
                            vm.startTimeline(tl.id)
                        },
                        onToggleStar = {
                            haptic(HapticEvent.LIGHT_TICK)
                            vm.toggleStarredTimeline(tl.id)
                        },
                    )
                }
            }
            if (rest.isNotEmpty()) {
                item { SectionLabel("All shows") }
                items(rest, key = { "a-${it.id}" }) { tl ->
                    ShowRow(
                        timeline = tl,
                        starred = false,
                        lastPlayedAt = lastPlayed[tl.id],
                        isPlaying = settings.runnerRunning && settings.activeTimeline == tl.id,
                        onStart = {
                            haptic(HapticEvent.SUCCESS_TICK)
                            vm.startTimeline(tl.id)
                        },
                        onToggleStar = {
                            haptic(HapticEvent.LIGHT_TICK)
                            vm.toggleStarredTimeline(tl.id)
                        },
                    )
                }
            }
            // Playlist footer
            val pl = playlist
            if (pl != null && pl.order.isNotEmpty()) {
                item { SectionLabel("Playlist") }
                item {
                    Column(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                        pl.order.forEachIndexed { idx, tlId ->
                            val tl = timelines.find { it.id == tlId }
                            val isCurrent = showStatus?.running == true && showStatus?.currentIndex == idx
                            Text(
                                "${idx + 1}. ${tl?.name ?: "Timeline #$tlId"}${if (isCurrent) "  ▶" else ""}",
                                style = MaterialTheme.typography.bodySmall,
                                color = if (isCurrent) CyanSecondary
                                        else MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(vertical = 2.dp),
                            )
                        }
                        Spacer(Modifier.height(6.dp))
                        FilledTonalButton(
                            onClick = {
                                haptic(HapticEvent.SUCCESS_TICK)
                                vm.startShow()
                            },
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Icon(Icons.Filled.PlayArrow, contentDescription = null,
                                 modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("Start playlist")
                        }
                    }
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
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 12.dp, bottom = 4.dp),
    )
}

@Composable
private fun ShowRow(
    timeline: Timeline,
    starred: Boolean,
    lastPlayedAt: Long?,
    isPlaying: Boolean,
    onStart: () -> Unit,
    onToggleStar: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .background(
                if (isPlaying) CyanSecondary.copy(alpha = 0.10f)
                else MaterialTheme.colorScheme.surface,
            )
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        IconButton(onClick = onToggleStar, modifier = Modifier.size(32.dp)) {
            Icon(
                if (starred) Icons.Filled.Star else Icons.Filled.StarBorder,
                contentDescription = "Star",
                tint = if (starred) CyanSecondary
                       else MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Column(modifier = Modifier.weight(1f)) {
            Text(
                timeline.name.ifBlank { "Timeline ${timeline.id}" },
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = if (isPlaying) FontWeight.Bold else FontWeight.Normal,
                color = if (isPlaying) CyanSecondary else MaterialTheme.colorScheme.onSurface,
            )
            Text(
                lastPlayedHint(lastPlayedAt, timeline.durationS),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (isPlaying) {
            SuggestionChip(
                onClick = {},
                label = { Text("Playing", style = MaterialTheme.typography.labelSmall) },
                colors = SuggestionChipDefaults.suggestionChipColors(
                    containerColor = CyanSecondary.copy(alpha = 0.15f),
                    labelColor = CyanSecondary,
                ),
                border = null,
            )
        } else {
            IconButton(
                onClick = onStart,
                modifier = Modifier.size(40.dp),
                colors = IconButtonDefaults.iconButtonColors(
                    contentColor = MaterialTheme.colorScheme.primary,
                ),
            ) {
                Icon(Icons.Filled.PlayArrow, contentDescription = "Start")
            }
        }
    }
}

private fun lastPlayedHint(at: Long?, durationS: Int): String {
    val dur = "%02d:%02d".format(durationS / 60, durationS % 60)
    if (at == null || at == 0L) return "$dur · never"
    val ageMin = ((System.currentTimeMillis() - at) / 60_000L)
    val age = when {
        ageMin < 1 -> "just now"
        ageMin < 60 -> "${ageMin} min ago"
        ageMin < 24 * 60 -> "${ageMin / 60} hr ago"
        else -> "${ageMin / (24 * 60)} d ago"
    }
    return "$dur · $age"
}
