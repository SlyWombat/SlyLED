package com.slywombat.slyled.ui.screens.runtime

import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.slywombat.slyled.data.model.*
import com.slywombat.slyled.viewmodel.RuntimeViewModel
import kotlinx.coroutines.delay

@Composable
fun RuntimeScreen(viewModel: RuntimeViewModel = hiltViewModel()) {
    val timelines by viewModel.timelines.collectAsState()
    val selectedTimeline by viewModel.selectedTimeline.collectAsState()
    val bakeStatus by viewModel.bakeStatus.collectAsState()
    val syncStatus by viewModel.syncStatus.collectAsState()
    val timelineStatus by viewModel.timelineStatus.collectAsState()
    val message by viewModel.message.collectAsState()
    val presets by viewModel.presets.collectAsState()
    var showNewDialog by remember { mutableStateOf(false) }
    var showPresetDialog by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) { viewModel.load() }

    // Show snackbar messages
    message?.let {
        LaunchedEffect(it) {
            delay(3000)
            viewModel.clearMessage()
        }
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        // Header
        item {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text("Timelines", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.weight(1f))
                FilledTonalButton(onClick = { showPresetDialog = true }) {
                    Icon(Icons.Default.AutoAwesome, null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("Presets")
                }
                FilledTonalButton(onClick = { showNewDialog = true }) {
                    Icon(Icons.Default.Add, null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("New")
                }
            }
        }

        // Active show status
        if (timelineStatus != null && timelineStatus!!.running) {
            item {
                Card(
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(modifier = Modifier.padding(16.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Default.PlayArrow, null, tint = MaterialTheme.colorScheme.primary)
                            Spacer(Modifier.width(8.dp))
                            Text("Now Playing: ${timelineStatus!!.name}", fontWeight = FontWeight.Bold)
                        }
                        Spacer(Modifier.height(8.dp))
                        val elapsed = timelineStatus!!.elapsed
                        val total = timelineStatus!!.durationS
                        val progress = if (total > 0) elapsed.toFloat() / total else 0f
                        LinearProgressIndicator(
                            progress = { progress },
                            modifier = Modifier.fillMaxWidth().height(6.dp).clip(RoundedCornerShape(3.dp))
                        )
                        Row(modifier = Modifier.fillMaxWidth().padding(top = 4.dp)) {
                            Text("${elapsed}s / ${total}s", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Spacer(Modifier.weight(1f))
                            if (timelineStatus!!.loop) Text("Loop", fontSize = 12.sp, color = MaterialTheme.colorScheme.primary)
                        }
                        Spacer(Modifier.height(8.dp))
                        Button(
                            onClick = { viewModel.stopTimeline(timelineStatus!!.id) },
                            colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error)
                        ) { Text("Stop Show") }
                    }
                }
            }
        }

        // Timeline list
        items(timelines) { tl ->
            TimelineCard(
                timeline = tl,
                isSelected = selectedTimeline?.id == tl.id,
                onSelect = { viewModel.selectTimeline(tl.id) },
                onDelete = { viewModel.deleteTimeline(tl.id) },
                onBakeAndStart = { viewModel.bakeAndStart(tl.id) },
                bakeStatus = if (selectedTimeline?.id == tl.id) bakeStatus else null,
                syncStatus = if (selectedTimeline?.id == tl.id) syncStatus else null,
            )
        }

        if (timelines.isEmpty()) {
            item {
                Text(
                    "No timelines yet. Create one or load a preset.",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(32.dp)
                )
            }
        }

        // Message
        message?.let { msg ->
            item {
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.tertiaryContainer)) {
                    Text(msg, modifier = Modifier.padding(12.dp), fontSize = 13.sp)
                }
            }
        }
    }

    // New timeline dialog
    if (showNewDialog) {
        NewTimelineDialog(
            onDismiss = { showNewDialog = false },
            onCreate = { name, dur ->
                viewModel.createTimeline(name, dur)
                showNewDialog = false
            }
        )
    }

    // Preset dialog
    if (showPresetDialog) {
        PresetDialog(
            presets = presets,
            onDismiss = { showPresetDialog = false },
            onSelect = { id ->
                viewModel.loadPreset(id)
                showPresetDialog = false
            }
        )
    }
}

@Composable
fun TimelineCard(
    timeline: Timeline,
    isSelected: Boolean,
    onSelect: () -> Unit,
    onDelete: () -> Unit,
    onBakeAndStart: () -> Unit,
    bakeStatus: BakeStatus?,
    syncStatus: SyncStatus?,
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onSelect() }
            .then(if (isSelected) Modifier.border(2.dp, MaterialTheme.colorScheme.primary, RoundedCornerShape(12.dp)) else Modifier),
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(timeline.name, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                Text("${timeline.durationS}s", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (timeline.loop) {
                    Spacer(Modifier.width(6.dp))
                    Icon(Icons.Default.Loop, null, modifier = Modifier.size(16.dp), tint = MaterialTheme.colorScheme.primary)
                }
            }

            val trackCount = timeline.tracks.size
            val clipCount = timeline.tracks.sumOf { it.clips.size }
            val hasStage = timeline.tracks.any { it.allPerformers }
            Text(
                "$trackCount tracks, $clipCount clips${if (hasStage) " (stage)" else ""}",
                fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            // Bake/sync progress (when selected)
            if (isSelected && bakeStatus != null && bakeStatus.running) {
                Spacer(Modifier.height(8.dp))
                Text("Baking: ${bakeStatus.status}", fontSize = 12.sp)
                LinearProgressIndicator(
                    progress = { (bakeStatus.progress.toFloat() / 100f).coerceIn(0f, 1f) },
                    modifier = Modifier.fillMaxWidth().padding(top = 4.dp)
                )
            }

            if (isSelected && syncStatus != null && !syncStatus.done) {
                Spacer(Modifier.height(8.dp))
                Text("Syncing: ${syncStatus.readyCount}/${syncStatus.totalPerformers} ready", fontSize = 12.sp)
                syncStatus.performers.forEach { (_, p) ->
                    Row(modifier = Modifier.padding(start = 8.dp, top = 2.dp)) {
                        val icon = when (p.status) {
                            "ready" -> "\u2705"
                            "syncing" -> "\u25b6"
                            "verifying" -> "\ud83d\udd0d"
                            "failed" -> "\u274c"
                            else -> "\u23f3"
                        }
                        Text("$icon ${p.name}: ${p.status}", fontSize = 11.sp)
                    }
                }
            }

            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = onBakeAndStart, modifier = Modifier.weight(1f)) {
                    Icon(Icons.Default.PlayArrow, null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("Sync & Start")
                }
                IconButton(onClick = onDelete) {
                    Icon(Icons.Default.Delete, null, tint = MaterialTheme.colorScheme.error)
                }
            }
        }
    }
}

@Composable
fun NewTimelineDialog(onDismiss: () -> Unit, onCreate: (String, Int) -> Unit) {
    var name by remember { mutableStateOf("") }
    var duration by remember { mutableStateOf("30") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("New Timeline") },
        text = {
            Column {
                OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text("Name") })
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(value = duration, onValueChange = { duration = it }, label = { Text("Duration (s)") })
            }
        },
        confirmButton = {
            TextButton(onClick = { onCreate(name, duration.toIntOrNull() ?: 30) }) { Text("Create") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}

@Composable
fun PresetDialog(presets: List<ShowPreset>, onDismiss: () -> Unit, onSelect: (String) -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Load Preset Show") },
        text = {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                items(presets) { p ->
                    Card(
                        modifier = Modifier.fillMaxWidth().clickable { onSelect(p.id) },
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)
                    ) {
                        Column(modifier = Modifier.padding(12.dp)) {
                            Text(p.name, fontWeight = FontWeight.Bold)
                            Text(p.desc, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        },
        confirmButton = {},
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}
