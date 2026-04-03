package com.slywombat.slyled.ui.screens.layout

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.*
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.slywombat.slyled.data.model.*
import com.slywombat.slyled.viewmodel.LayoutViewModel

@Composable
fun LayoutScreen(viewModel: LayoutViewModel = hiltViewModel()) {
    val layout by viewModel.layout.collectAsState()
    val surfaces by viewModel.surfaces.collectAsState()
    val fixtures by viewModel.fixtures.collectAsState()
    val stage by viewModel.stage.collectAsState()
    val message by viewModel.message.collectAsState()
    var dragFixtureId by remember { mutableIntStateOf(-1) }
    var showStrings by remember { mutableStateOf(true) }
    var editFixture by remember { mutableStateOf<Fixture?>(null) }
    var placingFixtureId by remember { mutableIntStateOf(-1) }

    // Zoom and pan
    var zoom by remember { mutableFloatStateOf(1f) }
    var panOffset by remember { mutableStateOf(Offset.Zero) }
    val transformState = rememberTransformableState { zoomChange, panChange, _ ->
        zoom = (zoom * zoomChange).coerceIn(0.5f, 5f)
        panOffset += panChange
    }

    LaunchedEffect(Unit) { viewModel.load() }

    // Canvas coordinate space = canvasW × canvasH in mm
    // canvasW = stage.w * 1000, canvasH = stage.h * 1000 (synced by server)
    val canvasW = layout?.canvasW ?: (stage.w * 1000).toInt().coerceAtLeast(1000)
    val canvasH = layout?.canvasH ?: (stage.h * 1000).toInt().coerceAtLeast(1000)
    val stageWm = canvasW / 1000f  // meters for labels
    val stageHm = canvasH / 1000f

    Column(modifier = Modifier.fillMaxSize().padding(8.dp)) {
        // Header
        Row(
            modifier = Modifier.fillMaxWidth().padding(bottom = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text("Stage Layout", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.weight(1f))
            if (zoom != 1f || panOffset != Offset.Zero) {
                TextButton(onClick = { zoom = 1f; panOffset = Offset.Zero }) { Text("Reset", fontSize = 11.sp) }
            }
            Button(onClick = { viewModel.saveLayout() }) { Text("Save", fontSize = 12.sp) }
        }

        // Info bar
        Row(modifier = Modifier.fillMaxWidth().padding(bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("${stageWm}m × ${stageHm}m | ${fixtures.size} fixtures",
                fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = showStrings, onCheckedChange = { showStrings = it })
                Text("Strings", fontSize = 11.sp)
            }
        }

        // Placement mode indicator
        if (placingFixtureId >= 0) {
            val pf = fixtures.find { it.id == placingFixtureId }
            Card(
                modifier = Modifier.fillMaxWidth().padding(bottom = 4.dp),
                colors = CardDefaults.cardColors(containerColor = Color(0xFF1A3A1A))
            ) {
                Text(
                    "Tap canvas to place: ${pf?.name ?: "Fixture $placingFixtureId"}",
                    modifier = Modifier.padding(8.dp), fontSize = 12.sp, color = Color(0xFF86EFAC)
                )
            }
        }

        val textMeasurer = rememberTextMeasurer()

        // Zoomable + pannable canvas
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(canvasW.toFloat() / canvasH)
                .background(Color(0xFF0A0A0A))
                .transformable(transformState)
        ) {
            Canvas(
                modifier = Modifier
                    .fillMaxSize()
                    .pointerInput(fixtures, zoom, panOffset, placingFixtureId) {
                        detectDragGestures(
                            onDragStart = { offset ->
                                // If in placement mode, place the fixture at tap location
                                if (placingFixtureId >= 0) {
                                    val w = size.width.toFloat()
                                    val h = size.height.toFloat()
                                    val rawX = (offset.x - panOffset.x) / zoom
                                    val rawY = (offset.y - panOffset.y) / zoom
                                    val physX = (rawX * canvasW / w).toInt().coerceIn(0, canvasW)
                                    val physY = ((h - rawY) * canvasH / h).toInt().coerceIn(0, canvasH)
                                    viewModel.moveFixture(placingFixtureId, physX, physY)
                                    placingFixtureId = -1
                                    dragFixtureId = -1
                                    return@detectDragGestures
                                }
                                // Normal drag mode — find nearest fixture
                                val w = size.width.toFloat()
                                val h = size.height.toFloat()
                                val placed = fixtures.filter { it.positioned }
                                dragFixtureId = placed.minByOrNull { f ->
                                    val cx = f.x.toFloat() * w / canvasW * zoom + panOffset.x
                                    val cy = (h - f.y.toFloat() * h / canvasH) * zoom + panOffset.y
                                    (cx - offset.x).let { it * it } + (cy - offset.y).let { it * it }
                                }?.id ?: -1
                            },
                            onDrag = { change, _ ->
                                if (dragFixtureId >= 0) {
                                    val w = size.width.toFloat()
                                    val h = size.height.toFloat()
                                    val pos = change.position
                                    val rawX = (pos.x - panOffset.x) / zoom
                                    val rawY = (pos.y - panOffset.y) / zoom
                                    val physX = (rawX * canvasW / w).toInt().coerceIn(0, canvasW)
                                    val physY = ((h - rawY) * canvasH / h).toInt().coerceIn(0, canvasH)
                                    viewModel.moveFixture(dragFixtureId, physX, physY)
                                }
                            },
                            onDragEnd = { dragFixtureId = -1 }
                        )
                    }
            ) {
                val w = size.width
                val h = size.height

                withTransform({
                    translate(panOffset.x, panOffset.y)
                    scale(zoom, zoom, Offset.Zero)
                }) {
                    // Grid — 1m spacing
                    val gridColor = Color(0xFF1A1A1A)
                    val gridStepsX = stageWm.toInt().coerceAtLeast(1)
                    val gridStepsY = stageHm.toInt().coerceAtLeast(1)
                    for (gx in 0..gridStepsX) {
                        val x = gx * w / gridStepsX
                        drawLine(gridColor, Offset(x, 0f), Offset(x, h), 1f)
                    }
                    for (gy in 0..gridStepsY) {
                        val y = gy * h / gridStepsY
                        drawLine(gridColor, Offset(0f, y), Offset(w, y), 1f)
                    }
                    drawRect(Color(0xFF1E3A5F), style = Stroke(2f))

                    // Dimension labels
                    val wLabel = textMeasurer.measure(AnnotatedString("${stageWm}m"),
                        style = TextStyle(fontSize = 8.sp, color = Color(0xFF4A6A8F)))
                    drawText(wLabel, topLeft = Offset(w / 2 - wLabel.size.width / 2f, h - wLabel.size.height - 2f))
                    val hLabel = textMeasurer.measure(AnnotatedString("${stageHm}m"),
                        style = TextStyle(fontSize = 8.sp, color = Color(0xFF4A6A8F)))
                    drawText(hLabel, topLeft = Offset(4f, h / 2 - hLabel.size.height / 2f))

                    // Surfaces — clipped to stage bounds, with name labels
                    surfaces.forEach { s ->
                        val t = s.transform
                        val sx = t.pos[0].toFloat() * w / canvasW
                        val sy = h - t.pos[1].toFloat() * h / canvasH
                        val sw = t.scale[0].toFloat() * w / canvasW
                        val sh = t.scale[1].toFloat() * h / canvasH
                        // Clip to canvas bounds
                        val clX = sx.coerceAtLeast(0f)
                        val clY = (sy - sh).coerceAtLeast(0f)
                        val clW = (sx + sw).coerceAtMost(w) - clX
                        val clH = sy.coerceAtMost(h) - clY
                        if (clW > 0 && clH > 0) {
                            val col = try { Color(android.graphics.Color.parseColor(s.color)) }
                                      catch (_: Exception) { Color(0xFF334155) }
                            drawRect(col.copy(alpha = s.opacity / 100f), Offset(clX, clY), Size(clW, clH))
                            drawRect(col.copy(alpha = 0.5f), Offset(clX, clY), Size(clW, clH), style = Stroke(1f))
                            // Surface name label
                            val name = s.name.ifEmpty { "Surface ${s.id}" }
                            val sLabel = textMeasurer.measure(AnnotatedString(name),
                                style = TextStyle(fontSize = 8.sp, color = Color(0xFFAAAAAA)))
                            drawText(sLabel, topLeft = Offset(clX + 4f, clY + 2f))
                        }
                    }

                    // Fixtures
                    fixtures.filter { it.positioned }.forEach { fixture ->
                        val cx = fixture.x.toFloat() * w / canvasW
                        val cy = h - fixture.y.toFloat() * h / canvasH

                        // LED strings
                        if (showStrings && fixture.fixtureType != "dmx") {
                            val strColors = listOf(Color.Cyan, Color.Magenta, Color.Yellow, Color.Green)
                            fixture.strings.forEachIndexed { si, s ->
                                if (s.leds <= 0) return@forEachIndexed
                                val lenMm = if (s.mm < 500) (s.leds * 16).coerceAtLeast(500) else s.mm
                                val dirX = when (s.sdir) { 0 -> 1f; 2 -> -1f; else -> 0f }
                                val dirY = when (s.sdir) { 1 -> -1f; 3 -> 1f; else -> 0f }
                                val pxLen = if (dirX != 0f) lenMm * w / canvasW else lenMm * h / canvasH
                                drawLine(strColors[si % strColors.size],
                                    Offset(cx, cy), Offset(cx + dirX * pxLen.coerceAtLeast(20f), cy + dirY * pxLen.coerceAtLeast(20f)),
                                    strokeWidth = 3f)
                            }
                        }

                        // DMX beam cone
                        if (fixture.fixtureType == "dmx" && fixture.aimPoint != null) {
                            val aimX = fixture.aimPoint.getOrNull(0)?.toFloat()
                            val aimZ = fixture.aimPoint.getOrNull(2)?.toFloat()
                            if (aimX != null && aimZ != null) {
                                val ax = aimX * w / canvasW
                                val ay = h - aimZ * h / canvasH
                                val bLen = kotlin.math.sqrt((ax - cx) * (ax - cx) + (ay - cy) * (ay - cy))
                                if (bLen > 5f) {
                                    val halfW = kotlin.math.tan(15f * Math.PI.toFloat() / 360f) * bLen
                                    val angle = kotlin.math.atan2(ay - cy, ax - cx)
                                    val perpX = -kotlin.math.sin(angle); val perpY = kotlin.math.cos(angle)
                                    val path = Path()
                                    path.moveTo(cx, cy)
                                    path.lineTo(ax + perpX * halfW, ay + perpY * halfW)
                                    path.lineTo(ax - perpX * halfW, ay - perpY * halfW)
                                    path.close()
                                    drawPath(path, Color(0x1A7C3AED))
                                    drawCircle(Color(0xFFFF4444), 5f, Offset(ax, ay))
                                }
                            }
                        }

                        // Node
                        val nodeColor = if (fixture.fixtureType == "dmx") Color(0xFF9966FF) else Color(0xFF22CC66)
                        drawCircle(nodeColor, 10f, Offset(cx, cy))
                        drawCircle(nodeColor.copy(alpha = 0.4f), 14f, Offset(cx, cy), style = Stroke(2f))

                        // Label
                        val label = fixture.name.ifEmpty { "Fixture ${fixture.id}" }
                        val tr = textMeasurer.measure(AnnotatedString(label),
                            style = TextStyle(fontSize = 9.sp, color = Color.White))
                        drawText(tr, topLeft = Offset(cx - tr.size.width / 2f, cy + 16f))
                    }
                }

                // Zoom indicator
                if (zoom != 1f) {
                    val zl = textMeasurer.measure(AnnotatedString("${(zoom * 100).toInt()}%"),
                        style = TextStyle(fontSize = 10.sp, color = Color(0xFF666666)))
                    drawText(zl, topLeft = Offset(w - zl.size.width - 8f, h - zl.size.height - 4f))
                }
            }
        }

        // Placed fixtures
        val placed = fixtures.filter { it.positioned }
        if (placed.isNotEmpty()) {
            Spacer(Modifier.height(4.dp))
            Text("Placed (${placed.size})", fontSize = 12.sp, fontWeight = androidx.compose.ui.text.font.FontWeight.Bold)
            LazyColumn(modifier = Modifier.fillMaxWidth().heightIn(max = 100.dp)) {
                items(placed) { f ->
                    Card(modifier = Modifier.fillMaxWidth().padding(vertical = 1.dp), onClick = { editFixture = f }) {
                        Row(modifier = Modifier.padding(6.dp).fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                            val tag = if (f.fixtureType == "dmx") "[DMX]" else "[LED]"
                            Text("$tag ${f.name.ifEmpty { "Fixture ${f.id}" }}", fontSize = 12.sp)
                            Text("(${f.x}, ${f.y})", fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }

        // Unplaced fixtures — tap to enter placement mode
        val unplaced = fixtures.filter { !it.positioned }
        if (unplaced.isNotEmpty()) {
            Spacer(Modifier.height(4.dp))
            Text("Unplaced (${unplaced.size})", fontSize = 12.sp, fontWeight = androidx.compose.ui.text.font.FontWeight.Bold)
            LazyColumn(modifier = Modifier.fillMaxWidth().heightIn(max = 80.dp)) {
                items(unplaced) { f ->
                    Card(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 1.dp)
                            .then(if (placingFixtureId == f.id) Modifier.border(2.dp, Color(0xFF22C55E), MaterialTheme.shapes.medium) else Modifier),
                        onClick = { placingFixtureId = if (placingFixtureId == f.id) -1 else f.id }
                    ) {
                        Row(modifier = Modifier.padding(8.dp)) {
                            val tag = if (f.fixtureType == "dmx") "[DMX]" else "[LED]"
                            Text("$tag ${f.name.ifEmpty { "Fixture ${f.id}" }} — tap then tap canvas",
                                fontSize = 12.sp, color = if (placingFixtureId == f.id) Color(0xFF86EFAC) else Color.Unspecified)
                        }
                    }
                }
            }
        }

        // Surfaces list
        if (surfaces.isNotEmpty()) {
            Spacer(Modifier.height(4.dp))
            Text("Surfaces (${surfaces.size})", fontSize = 12.sp,
                fontWeight = androidx.compose.ui.text.font.FontWeight.Bold)
            LazyColumn(modifier = Modifier.fillMaxWidth().heightIn(max = 80.dp)) {
                items(surfaces) { s ->
                    Card(modifier = Modifier.fillMaxWidth().padding(vertical = 1.dp)) {
                        Row(modifier = Modifier.padding(6.dp).fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically) {
                            val t = s.transform
                            val wm = String.format("%.1f", t.scale[0] / 1000.0)
                            val hm = String.format("%.1f", t.scale[1] / 1000.0)
                            Text(s.name.ifEmpty { "Surface ${s.id}" }, fontSize = 12.sp)
                            Text("${wm}m × ${hm}m", fontSize = 10.sp,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }

        message?.let {
            Text(it, color = MaterialTheme.colorScheme.primary, fontSize = 11.sp, modifier = Modifier.padding(top = 2.dp))
        }
    }

    // Coordinate edit dialog
    editFixture?.let { fixture ->
        var xText by remember(fixture.id) { mutableStateOf(fixture.x.toString()) }
        var yText by remember(fixture.id) { mutableStateOf(fixture.y.toString()) }
        var zText by remember(fixture.id) { mutableStateOf(fixture.z.toString()) }
        AlertDialog(
            onDismissRequest = { editFixture = null },
            title = { Text(fixture.name.ifEmpty { "Fixture ${fixture.id}" }) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedTextField(value = xText, onValueChange = { xText = it },
                        label = { Text("X (mm)") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(value = yText, onValueChange = { yText = it },
                        label = { Text("Y (mm)") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(value = zText, onValueChange = { zText = it },
                        label = { Text("Z (mm)") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        modifier = Modifier.fillMaxWidth())
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.updateFixturePosition(fixture.id,
                        xText.toIntOrNull() ?: fixture.x, yText.toIntOrNull() ?: fixture.y, zText.toIntOrNull() ?: fixture.z)
                    editFixture = null
                }) { Text("OK") }
            },
            dismissButton = {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(onClick = { viewModel.removeFixture(fixture.id); editFixture = null }) {
                        Text("Remove", color = MaterialTheme.colorScheme.error)
                    }
                    TextButton(onClick = { editFixture = null }) { Text("Cancel") }
                }
            }
        )
    }
}
