package com.slywombat.slyled.ui.screens.runtime

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
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
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
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
    val previewData by viewModel.previewData.collectAsState()
    val previewSecond by viewModel.previewSecond.collectAsState()
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

        // Show emulator canvas
        if (previewData.isNotEmpty()) {
            item {
                ShowEmulatorCanvas(
                    previewData = previewData,
                    second = previewSecond,
                    durationS = timelineStatus?.durationS ?: 30,
                )
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

    // Preset dialog — fetch presets when opened
    if (showPresetDialog) {
        LaunchedEffect(Unit) { viewModel.loadPresets() }
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
fun ShowEmulatorCanvas(
    previewData: Map<String, List<List<List<Int>>>>,
    second: Int,
    durationS: Int,
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text("Show Preview", fontWeight = FontWeight.Bold, fontSize = 13.sp)
            Spacer(Modifier.height(6.dp))

            Canvas(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(120.dp)
                    .background(Color(0xFF0A0F1A))
            ) {
                val w = size.width
                val h = size.height

                // Stage border
                drawRect(Color(0xFF1E3A5F), style = Stroke(1f))

                // Distribute fixtures evenly across the canvas
                val fixIds = previewData.keys.toList()
                if (fixIds.isEmpty()) return@Canvas

                fixIds.forEachIndexed { idx, fid ->
                    val frames = previewData[fid] ?: return@forEachIndexed
                    val dur = frames.size
                    if (dur == 0) return@forEachIndexed
                    val sec = second % dur
                    val colors = if (sec < frames.size) frames[sec] else emptyList()

                    // Position fixture evenly
                    val cx = (idx + 0.5f) * w / fixIds.size
                    val cy = h / 2

                    // Draw each string as a colored line
                    colors.forEachIndexed { si, rgb ->
                        if (rgb.size < 3) return@forEachIndexed
                        val r = rgb[0]; val g = rgb[1]; val b = rgb[2]
                        if (r + g + b < 5) return@forEachIndexed

                        val angle = if (colors.size > 1) (si.toFloat() / colors.size * Math.PI).toFloat() else 0f
                        val len = 30f
                        val ex = cx + kotlin.math.cos(angle.toDouble()).toFloat() * len
                        val ey = cy - kotlin.math.sin(angle.toDouble()).toFloat() * len

                        drawLine(
                            Color(r, g, b),
                            Offset(cx, cy), Offset(ex, ey),
                            strokeWidth = 8f
                        )
                        // Glow
                        drawCircle(Color(r, g, b, 100), 12f, Offset((cx + ex) / 2, (cy + ey) / 2))
                    }

                    // Node
                    drawCircle(Color(0xFF22CC66), 6f, Offset(cx, cy))
                }
            }

            // Time display
            val m = second / 60
            val s = second % 60
            Text(
                "%02d:%02d / %02d:%02d".format(m, s, durationS / 60, durationS % 60),
                fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 4.dp)
            )
        }
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

            // Clip editor when selected
            if (isSelected) {
                Spacer(Modifier.height(8.dp))
                timeline.tracks.forEachIndexed { ti, track ->
                    val trackName = if (track.allPerformers) "★ Stage" else "Track ${ti + 1}"
                    Text("$trackName: ${track.clips.size} clips", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }

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
fun PresetDialog(presets: List<ShowPreset>?, onDismiss: () -> Unit, onSelect: (String) -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Load Preset Show") },
        text = {
            when {
                presets == null -> {
                    // Loading state
                    Column(
                        modifier = Modifier.fillMaxWidth().padding(16.dp),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        CircularProgressIndicator(modifier = Modifier.size(32.dp))
                        Spacer(Modifier.height(8.dp))
                        Text("Loading presets...", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                presets.isEmpty() -> {
                    // Error/empty state
                    Text("No presets available. Check server connection.",
                        color = MaterialTheme.colorScheme.error, fontSize = 13.sp,
                        modifier = Modifier.padding(16.dp))
                }
                else -> {
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
                }
            }
        },
        confirmButton = {},
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ClipEditorSection(
    timeline: Timeline,
    actions: List<Action>,
    spatialEffects: List<SpatialEffect>,
    onAddClip: (trackIdx: Int, TimelineClip) -> Unit,
    onRemoveClip: (trackIdx: Int, clipIdx: Int) -> Unit,
) {
    var showAddDialog by remember { mutableStateOf(false) }
    var addTrackIdx by remember { mutableIntStateOf(0) }

    Column(modifier = Modifier.fillMaxWidth()) {
        Text("Tracks & Clips", fontWeight = FontWeight.Bold, modifier = Modifier.padding(bottom = 8.dp))

        timeline.tracks.forEachIndexed { ti, track ->
            val trackName = if (track.allPerformers) "★ Stage (All)" else "Track ${ti + 1}"
            Card(
                modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)
            ) {
                Column(modifier = Modifier.padding(12.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(trackName, fontWeight = FontWeight.Bold, fontSize = 13.sp, modifier = Modifier.weight(1f))
                        TextButton(onClick = { addTrackIdx = ti; showAddDialog = true }) {
                            Text("+ Clip", fontSize = 12.sp)
                        }
                    }
                    track.clips.forEachIndexed { ci, clip ->
                        val clipName = when {
                            clip.actionId != null -> actions.find { it.id == clip.actionId }?.name ?: "Action #${clip.actionId}"
                            clip.effectId != null -> spatialEffects.find { it.id == clip.effectId }?.name ?: "Effect #${clip.effectId}"
                            else -> "?"
                        }
                        Row(
                            modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            val isAction = clip.actionId != null
                            val icon = if (isAction) "▶" else "◆"
                            Text("$icon $clipName", fontSize = 12.sp, modifier = Modifier.weight(1f))
                            Text("${clip.startS}s → ${clip.startS + clip.durationS}s", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            IconButton(onClick = { onRemoveClip(ti, ci) }, modifier = Modifier.size(24.dp)) {
                                Icon(Icons.Default.Close, null, modifier = Modifier.size(14.dp), tint = MaterialTheme.colorScheme.error)
                            }
                        }
                    }
                    if (track.clips.isEmpty()) {
                        Text("No clips — tap + Clip to add", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }

        if (timeline.tracks.isEmpty()) {
            Text("No tracks yet. Add a track from the web interface.", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }

    if (showAddDialog) {
        AddClipDialog(
            actions = actions,
            spatialEffects = spatialEffects,
            onDismiss = { showAddDialog = false },
            onAdd = { clip ->
                onAddClip(addTrackIdx, clip)
                showAddDialog = false
            }
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AddClipDialog(
    actions: List<Action>,
    spatialEffects: List<SpatialEffect>,
    onDismiss: () -> Unit,
    onAdd: (TimelineClip) -> Unit,
) {
    var startS by remember { mutableStateOf("0") }
    var durationS by remember { mutableStateOf("5") }
    var selectedType by remember { mutableStateOf("action") }
    var selectedId by remember { mutableIntStateOf(actions.firstOrNull()?.id ?: 0) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Add Clip") },
        text = {
            Column {
                // Type selector
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = selectedType == "action", onClick = { selectedType = "action" }, label = { Text("Action") })
                    FilterChip(selected = selectedType == "effect", onClick = { selectedType = "effect" }, label = { Text("Spatial Effect") })
                }
                Spacer(Modifier.height(8.dp))

                // Item selector
                if (selectedType == "action" && actions.isNotEmpty()) {
                    var expanded by remember { mutableStateOf(false) }
                    ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = { expanded = it }) {
                        OutlinedTextField(
                            value = actions.find { it.id == selectedId }?.name ?: "",
                            onValueChange = {},
                            readOnly = true,
                            label = { Text("Action") },
                            modifier = Modifier.menuAnchor()
                        )
                        ExposedDropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
                            actions.forEach { a ->
                                DropdownMenuItem(text = { Text(a.name) }, onClick = { selectedId = a.id; expanded = false })
                            }
                        }
                    }
                } else if (selectedType == "effect" && spatialEffects.isNotEmpty()) {
                    var expanded by remember { mutableStateOf(false) }
                    ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = { expanded = it }) {
                        OutlinedTextField(
                            value = spatialEffects.find { it.id == selectedId }?.name ?: "",
                            onValueChange = {},
                            readOnly = true,
                            label = { Text("Effect") },
                            modifier = Modifier.menuAnchor()
                        )
                        ExposedDropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
                            spatialEffects.forEach { f ->
                                DropdownMenuItem(text = { Text(f.name) }, onClick = { selectedId = f.id; expanded = false })
                            }
                        }
                    }
                }

                Spacer(Modifier.height(8.dp))
                OutlinedTextField(value = startS, onValueChange = { startS = it }, label = { Text("Start (s)") })
                Spacer(Modifier.height(4.dp))
                OutlinedTextField(value = durationS, onValueChange = { durationS = it }, label = { Text("Duration (s)") })
            }
        },
        confirmButton = {
            TextButton(onClick = {
                val clip = if (selectedType == "action")
                    TimelineClip(actionId = selectedId, startS = startS.toDoubleOrNull() ?: 0.0, durationS = durationS.toDoubleOrNull() ?: 5.0)
                else
                    TimelineClip(effectId = selectedId, startS = startS.toDoubleOrNull() ?: 0.0, durationS = durationS.toDoubleOrNull() ?: 5.0)
                onAdd(clip)
            }) { Text("Add") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}
