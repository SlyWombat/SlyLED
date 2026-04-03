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
import androidx.compose.ui.graphics.Path
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
    val actions by viewModel.actions.collectAsState()
    val spatialEffects by viewModel.spatialEffects.collectAsState()
    var showNewDialog by remember { mutableStateOf(false) }
    var showPresetDialog by remember { mutableStateOf(false) }
    var brightnessValue by remember { mutableStateOf(255f) }

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

        // Global brightness slider
        item {
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Default.WbSunny, null, modifier = Modifier.size(20.dp), tint = MaterialTheme.colorScheme.primary)
                        Spacer(Modifier.width(8.dp))
                        Text("Brightness", fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                        Text("${brightnessValue.toInt()}", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Slider(
                        value = brightnessValue,
                        onValueChange = { brightnessValue = it },
                        onValueChangeFinished = { viewModel.setBrightness(brightnessValue.toInt()) },
                        valueRange = 0f..255f,
                        steps = 0,
                        modifier = Modifier.fillMaxWidth()
                    )
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

        // Stage preview canvas — always visible, gray pixels when idle
        item {
            ShowEmulatorCanvas(
                previewData = previewData,
                second = previewSecond,
                durationS = timelineStatus?.durationS ?: 0,
                children = viewModel.stageChildren.collectAsState().value,
                layout = viewModel.stageLayout.collectAsState().value,
                fixtures = viewModel.stageFixtures.collectAsState().value,
                surfaces = viewModel.stageSurfaces.collectAsState().value,
            )
        }

        // Timeline list
        items(timelines) { tl ->
            TimelineCard(
                timeline = tl,
                isSelected = selectedTimeline?.id == tl.id,
                selectedTimeline = if (selectedTimeline?.id == tl.id) selectedTimeline else null,
                onSelect = { viewModel.selectTimeline(tl.id) },
                onDelete = { viewModel.deleteTimeline(tl.id) },
                onBakeAndStart = { viewModel.bakeAndStart(tl.id) },
                onUpdateTimeline = { name, dur, loop -> viewModel.updateTimeline(tl.id, name, dur, loop) },
                onAddTrack = { viewModel.addTrackToTimeline(tl.id) },
                onAddClip = { trackIdx, clip -> viewModel.addClipToTimeline(tl.id, trackIdx, clip) },
                onRemoveClip = { trackIdx, clipIdx -> viewModel.removeClipFromTimeline(tl.id, trackIdx, clipIdx) },
                actions = actions,
                spatialEffects = spatialEffects,
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
    children: List<Child> = emptyList(),
    layout: Layout? = null,
    fixtures: List<Fixture> = emptyList(),
    surfaces: List<Surface> = emptyList(),
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text("Stage Preview", fontWeight = FontWeight.Bold, fontSize = 13.sp)
            Spacer(Modifier.height(6.dp))

            Canvas(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(150.dp)
                    .background(Color(0xFF060A12))
            ) {
                val w = size.width
                val h = size.height
                val cw = (layout?.canvasW ?: 10000).toFloat()
                val ch = (layout?.canvasH ?: 5000).toFloat()

                // Stage border + grid
                drawRect(Color(0xFF1E3A5F), style = Stroke(1f))
                for (gx in 1..9) drawLine(Color(0xFF0C1222), Offset(gx * w / 10, 0f), Offset(gx * w / 10, h), 0.5f)
                for (gy in 1..4) drawLine(Color(0xFF0C1222), Offset(0f, gy * h / 5), Offset(w, gy * h / 5), 0.5f)

                // Surfaces (draw first so fixtures render on top)
                surfaces.forEach { s ->
                    val t = s.transform
                    val sx = (t.pos[0].toFloat() * w / cw)
                    val sy = (h - t.pos[1].toFloat() * h / ch)
                    val sw = (t.scale[0].toFloat() * w / cw)
                    val sh = (t.scale[1].toFloat() * h / ch)
                    val col = try { Color(android.graphics.Color.parseColor(s.color)) } catch (_: Exception) { Color(0xFF334155) }
                    drawRect(col.copy(alpha = s.opacity / 100f), Offset(sx, sy - sh), androidx.compose.ui.geometry.Size(sw, sh))
                }

                // Use fixtures from layout response (positioned fixtures have x,y,z)
                val placedFixtures = layout?.fixtures?.filter { it.positioned } ?: emptyList()
                if (placedFixtures.isEmpty()) return@Canvas

                val dirDx = floatArrayOf(1f, 0f, -1f, 0f)
                val dirDy = floatArrayOf(0f, -1f, 0f, 1f)

                // Render each fixture (LED and DMX)
                placedFixtures.forEach { fixture ->
                    val cx = (fixture.x * w / cw).coerceIn(12f, w - 12f)
                    val cy = (h - fixture.y * h / ch).coerceIn(12f, h - 12f)

                    // Get preview colors for this fixture
                    var previewColors: List<List<Int>>? = null
                    if (previewData.isNotEmpty()) {
                        val frames = previewData[fixture.id.toString()]
                        if (frames != null && frames.isNotEmpty()) {
                            val dur = frames.size
                            val sec = if (dur > 0) second % dur else 0
                            if (sec < frames.size) previewColors = frames[sec]
                        }
                    }

                    if (fixture.fixtureType == "dmx") {
                        // DMX fixture: draw beam cone — aimPoint[0]=X, aimPoint[1]=Y (height)
                        val aimX = fixture.aimPoint?.getOrNull(0)?.toFloat()
                        val aimY = fixture.aimPoint?.getOrNull(1)?.toFloat()
                        if (aimX != null && aimY != null) {
                            val ax = (aimX * w / cw).coerceIn(0f, w)
                            val ay = (h - aimY * h / ch).coerceIn(0f, h)
                            val bLen = kotlin.math.sqrt((ax - cx) * (ax - cx) + (ay - cy) * (ay - cy))
                            if (bLen > 5f) {
                                val bwRad = 15f * Math.PI.toFloat() / 180f
                                val halfW = kotlin.math.tan(bwRad / 2) * bLen
                                val angle = kotlin.math.atan2(ay - cy, ax - cx)
                                val perpX = -kotlin.math.sin(angle)
                                val perpY = kotlin.math.cos(angle)

                                // Beam color from preview or default purple
                                var br = 124; var bg = 58; var bb = 237; var dimmer = 0.12f
                                if (previewColors != null && previewColors!!.isNotEmpty()) {
                                    val pc = previewColors!![0]
                                    if (pc.size >= 3) { br = pc[0]; bg = pc[1]; bb = pc[2] }
                                    if (pc.size >= 4 && pc[3] > 0) dimmer = (pc[3] / 255f) * 0.3f
                                    else if (br + bg + bb > 10) dimmer = 0.2f
                                }

                                val path = Path()
                                path.moveTo(cx, cy)
                                path.lineTo(ax + perpX * halfW, ay + perpY * halfW)
                                path.lineTo(ax - perpX * halfW, ay - perpY * halfW)
                                path.close()
                                drawPath(path, Color(br, bg, bb, (dimmer * 255).toInt().coerceIn(0, 255)))

                                // Aim dot
                                drawCircle(Color(0xFFFF4444), 3f, Offset(ax, ay))
                            }
                        }
                        // DMX node dot (purple)
                        drawCircle(Color(0xFF9966FF), 5f, Offset(cx, cy))
                        drawCircle(Color(0xFF9966FF).copy(alpha = 0.4f), 7f, Offset(cx, cy), style = Stroke(1.5f))
                    } else {
                        // LED fixture: draw string dots
                        fixture.strings.forEachIndexed { si, s ->
                            if (s.leds <= 0) return@forEachIndexed
                            val sdir = s.sdir.coerceIn(0, 3)
                            val dx = dirDx[sdir]
                            val dy = dirDy[sdir]
                            val lenMm = if (s.mm < 500) (s.leds * 16).coerceAtLeast(500) else s.mm
                            val pxLen = (if (dx != 0f) lenMm * w / cw else lenMm * h / ch).coerceAtLeast(20f)

                            var r = 40; var g = 40; var b = 45; var lit = false
                            if (previewColors != null && si < previewColors!!.size) {
                                val pc = previewColors!![si]
                                if (pc.size >= 3 && pc[0] + pc[1] + pc[2] > 3) {
                                    r = pc[0]; g = pc[1]; b = pc[2]; lit = true
                                }
                            }

                            val dotCount = s.leds.coerceAtMost((pxLen / 3).toInt()).coerceAtLeast(5)
                            for (di in 0 until dotCount) {
                                val t = (di + 0.5f) / dotCount
                                val dpx = cx + dx * pxLen * t
                                val dpy = cy + dy * pxLen * t
                                val dotR = if (lit) 3f else 1.8f
                                drawCircle(Color(r, g, b), dotR, Offset(dpx, dpy))
                                if (lit) drawCircle(Color(r, g, b, 30), 5f, Offset(dpx, dpy))
                            }
                        }
                        // LED node dot (green)
                        drawCircle(Color(0xFF22CC66), 5f, Offset(cx, cy))
                    }
                }
            }

            if (durationS > 0) {
                val m = second / 60; val s = second % 60
                Text(
                    "%02d:%02d / %02d:%02d".format(m, s, durationS / 60, durationS % 60),
                    fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 4.dp)
                )
            }
        }
    }
}

@Composable
fun TimelineCard(
    timeline: Timeline,
    isSelected: Boolean,
    selectedTimeline: Timeline?,
    onSelect: () -> Unit,
    onDelete: () -> Unit,
    onBakeAndStart: () -> Unit,
    onUpdateTimeline: (name: String, durationS: Int, loop: Boolean) -> Unit,
    onAddTrack: () -> Unit,
    onAddClip: (trackIdx: Int, TimelineClip) -> Unit,
    onRemoveClip: (trackIdx: Int, clipIdx: Int) -> Unit,
    actions: List<Action>,
    spatialEffects: List<SpatialEffect>,
    bakeStatus: BakeStatus?,
    syncStatus: SyncStatus?,
) {
    var showEditDialog by remember { mutableStateOf(false) }

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
                if (isSelected) {
                    Spacer(Modifier.width(4.dp))
                    IconButton(onClick = { showEditDialog = true }, modifier = Modifier.size(28.dp)) {
                        Icon(Icons.Default.Edit, null, modifier = Modifier.size(18.dp), tint = MaterialTheme.colorScheme.primary)
                    }
                }
            }

            val trackCount = timeline.tracks.size
            val clipCount = timeline.tracks.sumOf { it.clips.size }
            val hasStage = timeline.tracks.any { it.allPerformers }
            Text(
                "$trackCount tracks, $clipCount clips${if (hasStage) " (stage)" else ""}",
                fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            // Clip editor when selected — use the full detail from selectedTimeline
            if (isSelected) {
                val displayTimeline = selectedTimeline ?: timeline
                Spacer(Modifier.height(8.dp))
                ClipEditorSection(
                    timeline = displayTimeline,
                    actions = actions,
                    spatialEffects = spatialEffects,
                    onAddClip = onAddClip,
                    onRemoveClip = onRemoveClip,
                )
                Spacer(Modifier.height(4.dp))
                TextButton(onClick = onAddTrack) {
                    Icon(Icons.Default.Add, null, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("Add Track")
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

    // Edit timeline dialog
    if (showEditDialog) {
        EditTimelineDialog(
            timeline = timeline,
            onDismiss = { showEditDialog = false },
            onSave = { name, dur, loop ->
                onUpdateTimeline(name, dur, loop)
                showEditDialog = false
            }
        )
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
fun EditTimelineDialog(timeline: Timeline, onDismiss: () -> Unit, onSave: (String, Int, Boolean) -> Unit) {
    var name by remember { mutableStateOf(timeline.name) }
    var duration by remember { mutableStateOf(timeline.durationS.toString()) }
    var loop by remember { mutableStateOf(timeline.loop) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Edit Timeline") },
        text = {
            Column {
                OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text("Name") })
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(value = duration, onValueChange = { duration = it }, label = { Text("Duration (s)") })
                Spacer(Modifier.height(8.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Loop", modifier = Modifier.weight(1f))
                    Switch(checked = loop, onCheckedChange = { loop = it })
                }
            }
        },
        confirmButton = {
            TextButton(onClick = { onSave(name, duration.toIntOrNull() ?: timeline.durationS, loop) }) { Text("Save") }
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
