package com.slywombat.slyled.ui.screens.status

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import kotlinx.coroutines.flow.collectLatest
import com.slywombat.slyled.data.model.*
import com.slywombat.slyled.ui.theme.*
import com.slywombat.slyled.viewmodel.StatusViewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun StatusScreen(viewModel: StatusViewModel = hiltViewModel()) {
    LaunchedEffect(Unit) { viewModel.load() }

    val children by viewModel.children.collectAsState()
    val cameraFixtures by viewModel.cameraFixtures.collectAsState()
    val trackingState by viewModel.trackingState.collectAsState()
    val cameraOnline by viewModel.cameraOnline.collectAsState()
    val cameraStats by viewModel.cameraStats.collectAsState()
    val dmxStatus by viewModel.dmxStatus.collectAsState()
    val settings by viewModel.settings.collectAsState()
    val isRefreshing by viewModel.isRefreshing.collectAsState()

    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(Unit) {
        viewModel.message.collectLatest { msg ->
            snackbarHostState.showSnackbar(msg)
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) }
    ) { padding ->
    PullToRefreshBox(
        isRefreshing = isRefreshing,
        onRefresh = { viewModel.refreshAll() },
        modifier = Modifier.fillMaxSize().padding(padding)
    ) {
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 16.dp),
            contentPadding = PaddingValues(vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            // System status section
            item {
                SystemStatusCard(settings = settings, dmxStatus = dmxStatus)
            }

            // DMX Status section
            item {
                DmxStatusCard(dmxStatus = dmxStatus)
            }

            // Hardware section header
            item {
                Spacer(Modifier.height(4.dp))
                Text(
                    "Hardware",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold
                )
            }

            if (children.isEmpty()) {
                item {
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 24.dp),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            "No performers registered",
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            style = MaterialTheme.typography.bodyMedium
                        )
                    }
                }
            }

            items(children, key = { it.id }) { child ->
                PerformerCard(child = child)
            }

            // Camera Sensors section
            if (cameraFixtures.isNotEmpty()) {
                item {
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "Camera Sensors",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold
                    )
                }

                items(cameraFixtures, key = { it.id }) { cam ->
                    CameraCard(
                        camera = cam,
                        isOnline = cameraOnline[cam.id] ?: false,
                        isTracking = trackingState[cam.id] ?: false,
                        stats = cameraStats[cam.id],
                        onToggleTracking = { viewModel.toggleTracking(cam.id) }
                    )
                }
            }
        }
    }
    }
}

@Composable
private fun DmxStatusCard(dmxStatus: DmxStatus?) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        Icons.Default.Lightbulb,
                        contentDescription = null,
                        tint = DmxPurple,
                        modifier = Modifier.size(20.dp)
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        "Art-Net / DMX",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold
                    )
                }
                val status = dmxStatus
                if (status != null) {
                    SuggestionChip(
                        onClick = {},
                        label = {
                            Text(
                                if (status.running) "Running" else "Stopped",
                                style = MaterialTheme.typography.labelSmall
                            )
                        },
                        colors = SuggestionChipDefaults.suggestionChipColors(
                            containerColor = if (status.running) GreenOnline.copy(alpha = 0.15f) else MutedSlate.copy(alpha = 0.15f),
                            labelColor = if (status.running) GreenOnline else MutedSlate
                        ),
                        border = null
                    )
                }
            }
            if (dmxStatus != null) {
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                    DetailLabel("Universes", "${dmxStatus.universes}")
                    if (dmxStatus.running) {
                        DetailLabel("FPS", "${dmxStatus.fps}")
                    }
                }
            } else {
                Spacer(Modifier.height(4.dp))
                Text(
                    "Status unavailable",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}

@Composable
private fun SystemStatusCard(settings: Settings, dmxStatus: DmxStatus?) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    Icons.Default.Dashboard,
                    contentDescription = null,
                    tint = CyanSecondary,
                    modifier = Modifier.size(20.dp)
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    "System",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold
                )
            }
            Spacer(Modifier.height(8.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(16.dp)
            ) {
                // Show status
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "Show: ",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Text(
                        if (settings.runnerRunning) "Running" else "Stopped",
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.Medium,
                        color = if (settings.runnerRunning) GreenOnline else MutedSlate
                    )
                }
                // Art-Net status
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "Art-Net: ",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Text(
                        if (dmxStatus?.running == true) "Running" else "Stopped",
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.Medium,
                        color = if (dmxStatus?.running == true) GreenOnline else MutedSlate
                    )
                }
            }
            // Active timeline info
            val activeTl = settings.activeTimeline
            if (settings.runnerRunning && activeTl != null && activeTl >= 0) {
                Spacer(Modifier.height(4.dp))
                Text(
                    "Timeline #$activeTl",
                    style = MaterialTheme.typography.bodySmall,
                    color = CyanSecondary
                )
            }
            // DMX details when running
            if (dmxStatus != null && dmxStatus.running) {
                Spacer(Modifier.height(4.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                    DetailLabel("FPS", "${dmxStatus.fps}")
                    DetailLabel("Universes", "${dmxStatus.universes}")
                    if (dmxStatus.nodes > 0) {
                        DetailLabel("Nodes", "${dmxStatus.nodes}")
                    }
                }
            }
        }
    }
}

@Composable
private fun PerformerCard(child: Child) {
    val isOnline = child.onlineStatus == OnlineStatus.ONLINE

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    val displayName = if (child.name.isNotBlank() && child.name != child.hostname)
                        child.name else child.hostname
                    Text(
                        displayName,
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold
                    )
                    Text(
                        child.ip,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                Row(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    BoardBadge(type = child.type)
                    StatusBadge(online = isOnline)
                }
            }
            Spacer(Modifier.height(8.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column {
                    if (child.fwVersion != null) {
                        DetailLabel("Firmware", "v${child.fwVersion}")
                    }
                    if (child.boardType.isNotBlank()) {
                        DetailLabel("Board", child.boardType)
                    }
                }
                Column(horizontalAlignment = Alignment.End) {
                    if (child.rssi != null && child.rssi > 0) {
                        val bars = child.signalBars
                        val barChars = (1..4).map { if (it <= bars) "\u2588" else "\u2581" }.joinToString("")
                        DetailLabel("RSSI", "${child.rssiDbm} dBm $barChars")
                    }
                    val totalLeds = child.strings.sumOf { it.leds }
                    if (totalLeds > 0) {
                        DetailLabel("LEDs", "$totalLeds")
                    }
                }
            }
        }
    }
}

@Composable
private fun CameraCard(
    camera: Fixture,
    isOnline: Boolean = false,
    isTracking: Boolean,
    stats: CameraStatus? = null,
    onToggleTracking: () -> Unit
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        Icons.Default.Videocam,
                        contentDescription = null,
                        tint = CyanSecondary,
                        modifier = Modifier.size(20.dp)
                    )
                    Spacer(Modifier.width(8.dp))
                    Column {
                        Text(
                            camera.name.ifBlank { "Camera #${camera.id}" },
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.Bold
                        )
                        val url = camera.cameraUrl
                        if (!url.isNullOrBlank()) {
                            Text(
                                url,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }
                StatusBadge(online = isOnline)
            }
            Spacer(Modifier.height(8.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column {
                    if (camera.resolutionW != null && camera.resolutionH != null) {
                        DetailLabel("Resolution", "${camera.resolutionW}x${camera.resolutionH}")
                    }
                    if (camera.fovDeg != null) {
                        DetailLabel("FOV", "${camera.fovDeg.toInt()}\u00B0")
                    }
                }
                // Tracking toggle
                FilledTonalButton(
                    onClick = onToggleTracking,
                    colors = ButtonDefaults.filledTonalButtonColors(
                        containerColor = if (isTracking) GreenOnline.copy(alpha = 0.2f) else MaterialTheme.colorScheme.surfaceVariant,
                        contentColor = if (isTracking) GreenOnline else MaterialTheme.colorScheme.onSurfaceVariant
                    )
                ) {
                    Icon(
                        if (isTracking) Icons.Default.PersonSearch else Icons.Default.PersonOff,
                        contentDescription = null,
                        modifier = Modifier.size(18.dp)
                    )
                    Spacer(Modifier.width(4.dp))
                    Text(if (isTracking) "Tracking" else "Track")
                }
            }
            // Tracking classes from camera stats
            if (stats != null && stats.trackClasses.isNotEmpty() && isTracking) {
                Spacer(Modifier.height(6.dp))
                Text(
                    "Tracking: ${stats.trackClasses.joinToString(", ")}",
                    style = MaterialTheme.typography.bodySmall,
                    color = GreenOnline
                )
            }
            // Firmware version if available
            if (stats != null && stats.fwVersion.isNotBlank()) {
                Spacer(Modifier.height(2.dp))
                DetailLabel("Firmware", "v${stats.fwVersion}")
            }
        }
    }
}

@Composable
private fun BoardBadge(type: String) {
    val (label, color) = when (type.lowercase()) {
        "esp32" -> "ESP32" to CyanSecondary
        "d1mini", "d1_mini" -> "D1 Mini" to OrangeWled
        "giga" -> "Giga" to Color(0xFFa78bfa)
        "wled" -> "WLED" to OrangeWled
        else -> "SlyLED" to MaterialTheme.colorScheme.primary
    }
    SuggestionChip(
        onClick = {},
        label = { Text(label, style = MaterialTheme.typography.labelSmall) },
        colors = SuggestionChipDefaults.suggestionChipColors(
            containerColor = color.copy(alpha = 0.15f),
            labelColor = color
        ),
        border = null
    )
}

@Composable
private fun StatusBadge(online: Boolean) {
    SuggestionChip(
        onClick = {},
        label = {
            Text(
                if (online) "Online" else "Offline",
                style = MaterialTheme.typography.labelSmall
            )
        },
        colors = SuggestionChipDefaults.suggestionChipColors(
            containerColor = if (online) GreenOnline.copy(alpha = 0.15f) else RedError.copy(alpha = 0.15f),
            labelColor = if (online) GreenOnline else RedError
        ),
        border = null
    )
}

@Composable
private fun DetailLabel(label: String, value: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(
            "$label: ",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        Text(
            value,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Medium
        )
    }
}
