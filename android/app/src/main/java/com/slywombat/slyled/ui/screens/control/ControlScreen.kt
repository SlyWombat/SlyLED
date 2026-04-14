package com.slywombat.slyled.ui.screens.control

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.slywombat.slyled.data.model.*
import com.slywombat.slyled.ui.theme.*
import com.slywombat.slyled.viewmodel.ControlViewModel

@Composable
fun ControlScreen(viewModel: ControlViewModel = hiltViewModel()) {
    LaunchedEffect(Unit) { viewModel.load() }

    val timelines by viewModel.timelines.collectAsState()
    val settings by viewModel.settings.collectAsState()
    val timelineStatus by viewModel.timelineStatus.collectAsState()
    val playlist by viewModel.playlist.collectAsState()
    val showStatus by viewModel.showStatus.collectAsState()
    val message by viewModel.message.collectAsState()

    val fixtures by viewModel.fixtures.collectAsState()
    val controllerFixtureId by viewModel.controllerFixtureId.collectAsState()
    val controllerReady by viewModel.controllerReady.collectAsState()
    val controllerConnected by viewModel.controllerConnected.collectAsState()
    val controllerPanRange by viewModel.controllerPanRange.collectAsState()
    val controllerTiltRange by viewModel.controllerTiltRange.collectAsState()
    val controllerPanSign by viewModel.controllerPanSign.collectAsState()
    val controllerTiltSign by viewModel.controllerTiltSign.collectAsState()
    val controllerInitialPan by viewModel.controllerInitialPan.collectAsState()
    val controllerInitialTilt by viewModel.controllerInitialTilt.collectAsState()
    val dmxFixtures = fixtures.filter { it.fixtureType == "dmx" }

    val isRunning = settings.runnerRunning
    val brightness = settings.globalBrightness ?: 255
    var brightnessSlider by remember { mutableFloatStateOf(brightness.toFloat()) }

    LaunchedEffect(brightness) {
        brightnessSlider = brightness.toFloat()
    }

    // Snackbar
    val snackbarHostState = remember { SnackbarHostState() }
    LaunchedEffect(message) {
        message?.let {
            snackbarHostState.showSnackbar(it)
            viewModel.clearMessage()
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) }
    ) { innerPadding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .padding(horizontal = 16.dp),
            contentPadding = PaddingValues(vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            // Now Playing card
            item {
                NowPlayingCard(
                    isRunning = isRunning,
                    timelineStatus = timelineStatus,
                    showStatus = showStatus,
                    timelines = timelines,
                    onStop = { viewModel.stopShow() }
                )
            }

            // Large Stop button when running
            if (isRunning) {
                item {
                    Button(
                        onClick = { viewModel.stopShow() },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(56.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = RedError)
                    ) {
                        Icon(Icons.Default.Stop, contentDescription = null, modifier = Modifier.size(24.dp))
                        Spacer(Modifier.width(8.dp))
                        Text("STOP SHOW", style = MaterialTheme.typography.titleMedium)
                    }
                }
            }

            // Global brightness
            item {
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(modifier = Modifier.padding(16.dp)) {
                        Text(
                            "Global Brightness",
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.Bold
                        )
                        Spacer(Modifier.height(8.dp))
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Icon(
                                Icons.Default.BrightnessLow,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.size(20.dp)
                            )
                            Slider(
                                value = brightnessSlider,
                                onValueChange = { brightnessSlider = it },
                                onValueChangeFinished = {
                                    viewModel.setBrightness(brightnessSlider.toInt())
                                },
                                valueRange = 0f..255f,
                                modifier = Modifier.weight(1f)
                            )
                            Icon(
                                Icons.Default.BrightnessHigh,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.size(20.dp)
                            )
                            Spacer(Modifier.width(8.dp))
                            Text(
                                "${brightnessSlider.toInt()}",
                                style = MaterialTheme.typography.labelLarge,
                                modifier = Modifier.width(36.dp)
                            )
                        }
                    }
                }
            }

            // Playlist section
            val pl = playlist
            if (pl != null && pl.order.isNotEmpty()) {
                item {
                    PlaylistSection(
                        playlist = pl,
                        timelines = timelines,
                        showStatus = showStatus,
                        onStartShow = { viewModel.startShow() },
                        onToggleLoop = { viewModel.setLoop(it) }
                    )
                }
            }

            // Controller mode — DMX moving head fixture picker
            if (dmxFixtures.isNotEmpty()) {
                item {
                    Card(modifier = Modifier.fillMaxWidth()) {
                        Column(modifier = Modifier.padding(16.dp)) {
                            Text(
                                "Controller Mode",
                                style = MaterialTheme.typography.titleSmall,
                                fontWeight = FontWeight.Bold
                            )
                            Text(
                                "Use your phone to control moving head fixtures",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                            Spacer(Modifier.height(8.dp))
                            dmxFixtures.forEach { f ->
                                OutlinedButton(
                                    onClick = { viewModel.enterControllerMode(f.id) },
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .padding(vertical = 2.dp)
                                ) {
                                    Icon(
                                        Icons.Default.ControlCamera,
                                        contentDescription = null,
                                        tint = DmxPurple,
                                        modifier = Modifier.size(18.dp)
                                    )
                                    Spacer(Modifier.width(8.dp))
                                    Text(f.name ?: "Fixture ${f.id}")
                                }
                            }
                        }
                    }
                }
            }

            // Timelines header
            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        "Timelines",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold
                    )
                    IconButton(onClick = { viewModel.refreshTimelines() }) {
                        Icon(Icons.Default.Refresh, contentDescription = "Refresh")
                    }
                }
            }

            if (timelines.isEmpty()) {
                item {
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 32.dp),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            "No timelines created yet",
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            style = MaterialTheme.typography.bodyLarge
                        )
                    }
                }
            }

            // Timeline cards
            items(timelines, key = { it.id }) { timeline ->
                TimelineCard(
                    timeline = timeline,
                    isActive = settings.activeTimeline == timeline.id && isRunning,
                    timelineStatus = if (timelineStatus?.id == timeline.id) timelineStatus else null,
                    onStart = { viewModel.startTimeline(timeline.id) }
                )
            }
        }
    }

    // Full-screen controller overlay
    val controllerFix = controllerFixtureId?.let { id -> fixtures.find { it.id == id } }
    if (controllerFix != null && controllerReady) {
        ControllerModeOverlay(
            fixtureName = controllerFix.name ?: "Fixture ${controllerFix.id}",
            panRangeDeg = controllerPanRange,
            tiltRangeDeg = controllerTiltRange,
            initialPanNorm = controllerInitialPan,
            initialTiltNorm = controllerInitialTilt,
            panSign = controllerPanSign,
            tiltSign = controllerTiltSign,
            connected = controllerConnected,
            onAim = { panNorm, tiltNorm ->
                viewModel.aimFixture(controllerFix.id, panNorm, tiltNorm)
            },
            onChannelChange = { dimmer, red, green, blue, white, strobe ->
                viewModel.setFixtureChannels(controllerFix.id, dimmer, red, green, blue, white, strobe)
            },
            onDismiss = { viewModel.exitControllerMode() }
        )
    }
}

@Composable
private fun NowPlayingCard(
    isRunning: Boolean,
    timelineStatus: TimelineStatus?,
    showStatus: ShowStatus?,
    timelines: List<Timeline>,
    onStop: () -> Unit
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = if (isRunning)
                GreenOnline.copy(alpha = 0.1f)
            else
                MaterialTheme.colorScheme.surfaceVariant
        )
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            if (isRunning && timelineStatus != null) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            "Now Playing",
                            style = MaterialTheme.typography.labelMedium,
                            color = GreenOnline
                        )
                        Spacer(Modifier.height(2.dp))
                        val name = timelineStatus.name.ifBlank {
                            timelines.find { it.id == timelineStatus.id }?.name ?: "Timeline #${timelineStatus.id}"
                        }
                        Text(
                            name,
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold
                        )
                    }
                    if (timelineStatus.loop) {
                        SuggestionChip(
                            onClick = {},
                            label = { Text("Loop", style = MaterialTheme.typography.labelSmall) },
                            colors = SuggestionChipDefaults.suggestionChipColors(
                                containerColor = CyanSecondary.copy(alpha = 0.15f),
                                labelColor = CyanSecondary
                            ),
                            border = null
                        )
                    }
                }
                Spacer(Modifier.height(8.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text(
                        formatTime(timelineStatus.elapsed),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurface
                    )
                    Text(
                        formatTime(timelineStatus.durationS),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                Spacer(Modifier.height(4.dp))
                if (timelineStatus.durationS > 0) {
                    LinearProgressIndicator(
                        progress = { (timelineStatus.elapsed.toFloat() / timelineStatus.durationS).coerceIn(0f, 1f) },
                        modifier = Modifier.fillMaxWidth(),
                        color = CyanSecondary,
                        trackColor = MaterialTheme.colorScheme.outlineVariant,
                    )
                }

                // Show status (playlist progress)
                if (showStatus != null && showStatus.running && showStatus.totalTimelines > 1) {
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Playlist: ${showStatus.currentIndex + 1} of ${showStatus.totalTimelines}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            } else {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Icon(
                        Icons.Default.MusicOff,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(24.dp)
                    )
                    Spacer(Modifier.width(12.dp))
                    Text(
                        "No show running",
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
        }
    }
}

@Composable
private fun PlaylistSection(
    playlist: ShowPlaylist,
    timelines: List<Timeline>,
    showStatus: ShowStatus?,
    onStartShow: () -> Unit,
    onToggleLoop: (Boolean) -> Unit
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    "Playlist",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "Loop",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Spacer(Modifier.width(4.dp))
                    Switch(
                        checked = playlist.loop,
                        onCheckedChange = { onToggleLoop(it) }
                    )
                }
            }
            Spacer(Modifier.height(8.dp))
            playlist.order.forEachIndexed { idx, tlId ->
                val tl = timelines.find { it.id == tlId }
                val isCurrentInPlaylist = showStatus?.running == true && showStatus.currentIndex == idx
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        "${idx + 1}.",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.width(24.dp)
                    )
                    Text(
                        tl?.name ?: "Timeline #$tlId",
                        style = MaterialTheme.typography.bodyMedium,
                        fontWeight = if (isCurrentInPlaylist) FontWeight.Bold else FontWeight.Normal,
                        color = if (isCurrentInPlaylist) CyanSecondary else MaterialTheme.colorScheme.onSurface
                    )
                    if (isCurrentInPlaylist) {
                        Spacer(Modifier.width(8.dp))
                        Icon(
                            Icons.Default.PlayArrow,
                            contentDescription = "Playing",
                            tint = CyanSecondary,
                            modifier = Modifier.size(16.dp)
                        )
                    }
                }
            }
            Spacer(Modifier.height(8.dp))
            FilledTonalButton(
                onClick = onStartShow,
                modifier = Modifier.fillMaxWidth()
            ) {
                Icon(Icons.Default.PlayArrow, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(4.dp))
                Text("Start Playlist")
            }
        }
    }
}

@Composable
private fun TimelineCard(
    timeline: Timeline,
    isActive: Boolean,
    timelineStatus: TimelineStatus?,
    onStart: () -> Unit
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = if (isActive)
                CyanSecondary.copy(alpha = 0.08f)
            else
                MaterialTheme.colorScheme.surface
        )
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        timeline.name.ifBlank { "Timeline #${timeline.id}" },
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold
                    )
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            formatTime(timeline.durationS),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                        Text(
                            "${timeline.tracks.size} track${if (timeline.tracks.size != 1) "s" else ""}",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                        if (timeline.loop) {
                            Text(
                                "Loop",
                                style = MaterialTheme.typography.labelSmall,
                                color = CyanSecondary
                            )
                        }
                    }
                }
                if (isActive) {
                    SuggestionChip(
                        onClick = {},
                        label = { Text("Playing", style = MaterialTheme.typography.labelSmall) },
                        colors = SuggestionChipDefaults.suggestionChipColors(
                            containerColor = GreenOnline.copy(alpha = 0.15f),
                            labelColor = GreenOnline
                        ),
                        border = null
                    )
                } else {
                    FilledTonalButton(onClick = onStart) {
                        Icon(Icons.Default.PlayArrow, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(4.dp))
                        Text("Start")
                    }
                }
            }
            // Progress bar for active timeline
            if (isActive && timelineStatus != null && timelineStatus.durationS > 0) {
                Spacer(Modifier.height(8.dp))
                LinearProgressIndicator(
                    progress = { (timelineStatus.elapsed.toFloat() / timelineStatus.durationS).coerceIn(0f, 1f) },
                    modifier = Modifier.fillMaxWidth(),
                    color = CyanSecondary,
                    trackColor = MaterialTheme.colorScheme.outlineVariant,
                )
            }
        }
    }
}

private fun formatTime(seconds: Int): String {
    val m = seconds / 60
    val s = seconds % 60
    return "%02d:%02d".format(m, s)
}
