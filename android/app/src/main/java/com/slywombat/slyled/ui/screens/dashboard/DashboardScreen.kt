package com.slywombat.slyled.ui.screens.dashboard

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.slywombat.slyled.data.model.*
import com.slywombat.slyled.ui.screens.runtime.ShowEmulatorCanvas
import com.slywombat.slyled.ui.theme.CyanSecondary
import com.slywombat.slyled.ui.theme.GreenOnline
import com.slywombat.slyled.ui.theme.OrangeWled
import com.slywombat.slyled.ui.theme.RedError
import com.slywombat.slyled.viewmodel.DashboardViewModel
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DashboardScreen(viewModel: DashboardViewModel = hiltViewModel()) {
    val children by viewModel.children.collectAsState()
    val settings by viewModel.settings.collectAsState()
    val networkError by viewModel.networkError.collectAsState()
    val isRefreshing by viewModel.isRefreshing.collectAsState()

    PullToRefreshBox(
        isRefreshing = isRefreshing,
        onRefresh = { viewModel.refreshAll() },
        modifier = Modifier.fillMaxSize()
    ) {
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(horizontal = 16.dp),
            contentPadding = PaddingValues(vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            // Network error banner
            if (networkError) {
                item {
                    Card(
                        modifier = Modifier.fillMaxWidth(),
                        colors = CardDefaults.cardColors(
                            containerColor = MaterialTheme.colorScheme.errorContainer
                        )
                    ) {
                        Text(
                            "Unable to reach server",
                            modifier = Modifier.padding(12.dp),
                            color = MaterialTheme.colorScheme.onErrorContainer,
                            style = MaterialTheme.typography.labelLarge
                        )
                    }
                }
            }

            // Header row
            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    val online = children.count { it.onlineStatus == OnlineStatus.ONLINE }
                    Text(
                        "$online / ${children.size} devices online",
                        style = MaterialTheme.typography.titleMedium
                    )
                    FilledTonalButton(onClick = { viewModel.refreshAll() }) {
                        Icon(
                            Icons.Default.Refresh,
                            contentDescription = "Refresh",
                            modifier = Modifier.size(18.dp)
                        )
                        Spacer(Modifier.width(4.dp))
                        Text("Refresh All")
                    }
                }
            }

            // Active show card + stage preview
            if (settings.runnerRunning) {
                item {
                    val tlId = settings.activeTimeline
                    if (tlId != null && tlId >= 0) {
                        // Timeline-based show
                        Card(modifier = Modifier.fillMaxWidth()) {
                            Column(modifier = Modifier.padding(12.dp)) {
                                Text("Show Running", fontWeight = FontWeight.Bold,
                                    color = GreenOnline, style = MaterialTheme.typography.titleSmall)
                                Spacer(Modifier.height(4.dp))
                                val elapsed = settings.runnerElapsed
                                val m = elapsed / 60; val s = elapsed % 60
                                Text("%02d:%02d".format(m, s), style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                                Spacer(Modifier.height(8.dp))
                                Button(
                                    onClick = { viewModel.stopTimeline(tlId) },
                                    colors = ButtonDefaults.buttonColors(containerColor = RedError)
                                ) { Text("Stop Show") }
                            }
                        }
                    } else {
                        ActiveRunnerCard(
                            activeRunnerId = settings.activeRunner,
                            elapsed = settings.runnerElapsed,
                            loop = settings.runnerLoop,
                            onStop = { viewModel.stopAll() }
                        )
                    }
                }
                // Stage preview
                item {
                    LaunchedEffect(Unit) { viewModel.loadStageData() }
                    val layout by viewModel.layout.collectAsState()
                    val surfaces by viewModel.surfaces.collectAsState()
                    ShowEmulatorCanvas(
                        previewData = emptyMap(),
                        second = settings.runnerElapsed,
                        durationS = 0,
                        layout = layout,
                        surfaces = surfaces
                    )
                }
            }

            // Empty state
            if (children.isEmpty()) {
                item {
                    Box(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 48.dp),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            "No devices registered",
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            style = MaterialTheme.typography.bodyLarge
                        )
                    }
                }
            }

            // Performer cards
            items(children, key = { it.id }) { child ->
                PerformerCard(child = child)
            }
        }
    }
}

@Composable
private fun ActiveRunnerCard(
    activeRunnerId: Int,
    elapsed: Int,
    loop: Boolean,
    onStop: () -> Unit
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.primaryContainer
        )
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column {
                    Text(
                        "Runner Active",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onPrimaryContainer
                    )
                    Text(
                        "Runner #$activeRunnerId${if (loop) " (looping)" else ""}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onPrimaryContainer.copy(alpha = 0.7f)
                    )
                }
                FilledTonalButton(
                    onClick = onStop,
                    colors = ButtonDefaults.filledTonalButtonColors(
                        containerColor = RedError.copy(alpha = 0.2f),
                        contentColor = RedError
                    )
                ) {
                    Icon(
                        Icons.Default.Stop,
                        contentDescription = "Stop",
                        modifier = Modifier.size(18.dp)
                    )
                    Spacer(Modifier.width(4.dp))
                    Text("Stop")
                }
            }
            Spacer(Modifier.height(8.dp))
            Text(
                "Elapsed: ${formatDuration(elapsed)}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onPrimaryContainer.copy(alpha = 0.7f)
            )
            Spacer(Modifier.height(4.dp))
            LinearProgressIndicator(
                modifier = Modifier.fillMaxWidth(),
                color = MaterialTheme.colorScheme.primary,
                trackColor = MaterialTheme.colorScheme.onPrimaryContainer.copy(alpha = 0.12f)
            )
        }
    }
}

@Composable
private fun PerformerCard(child: Child) {
    val isOnline = child.onlineStatus == OnlineStatus.ONLINE
    val totalLeds = child.strings.sumOf { it.leds }

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    val displayName = if (child.name.isNotBlank() && child.name != child.hostname) child.name else child.hostname
                    Text(
                        displayName,
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold
                    )
                    if (child.name.isNotBlank() && child.name != child.hostname) {
                        Text(
                            child.hostname,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
                Row(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    BoardBadge(type = child.type)
                    if (!child.startupDone) {
                        SuggestionChip(
                            onClick = {},
                            label = { Text("Checking\u2026", style = MaterialTheme.typography.labelSmall) },
                            colors = SuggestionChipDefaults.suggestionChipColors(
                                containerColor = Color(0xFF3B82F6).copy(alpha = 0.15f),
                                labelColor = Color(0xFF3B82F6)
                            ),
                            border = null
                        )
                    } else {
                        StatusBadge(online = isOnline)
                    }
                }
            }
            Spacer(Modifier.height(8.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column {
                    DetailLabel("IP", child.ip)
                    if (child.fwVersion != null) {
                        DetailLabel("Firmware", "v${child.fwVersion}")
                    }
                }
                Column(horizontalAlignment = Alignment.End) {
                    if (totalLeds > 0) {
                        DetailLabel("LEDs", "$totalLeds (${child.sc} string${if (child.sc != 1) "s" else ""})")
                    }
                    if (child.rssi != null && child.rssi > 0) {
                        val bars = child.signalBars
                        val barStr = "\u2582".repeat(bars.coerceAtLeast(1)) + "\u2582".repeat((4 - bars).coerceAtLeast(0)).let { dim -> "" }
                        val barChars = (1..4).map { if (it <= bars) "\u2588" else "\u2581" }.joinToString("")
                        DetailLabel("RSSI", "${child.rssiDbm} dBm $barChars")
                    }
                    if (child.seen > 0) {
                        DetailLabel("Last seen", formatTimestamp(child.seen))
                    }
                }
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
        label = {
            Text(label, style = MaterialTheme.typography.labelSmall)
        },
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

private fun formatDuration(seconds: Int): String {
    val m = seconds / 60
    val s = seconds % 60
    return if (m > 0) "${m}m ${s}s" else "${s}s"
}

private fun formatTimestamp(epoch: Long): String {
    if (epoch <= 0) return "Never"
    val date = Date(epoch * 1000)
    val fmt = SimpleDateFormat("HH:mm:ss", Locale.getDefault())
    return fmt.format(date)
}
