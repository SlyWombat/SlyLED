package com.slywombat.slyled.ui.screens.layout

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
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

    // Zoom and pan state
    var zoom by remember { mutableFloatStateOf(1f) }
    var panOffset by remember { mutableStateOf(Offset.Zero) }
    val transformState = rememberTransformableState { zoomChange, panChange, _ ->
        zoom = (zoom * zoomChange).coerceIn(0.5f, 5f)
        panOffset += panChange
    }

    LaunchedEffect(Unit) { viewModel.load() }

    // Use stage dimensions (meters → mm) for the coordinate system
    // canvasW/H are the logical coordinate space in mm
    val stageWmm = (stage.w * 1000).toInt().coerceAtLeast(1000)
    val stageHmm = (stage.d * 1000).toInt().coerceAtLeast(1000)  // d = depth = stage Z axis shown as Y on 2D
    val canvasW = layout?.canvasW ?: stageWmm
    val canvasH = layout?.canvasH ?: stageHmm

    Column(modifier = Modifier.fillMaxSize().padding(8.dp)) {
        // Header
        Row(
            modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text("Stage Layout", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.weight(1f))
            // Reset zoom
            if (zoom != 1f || panOffset != Offset.Zero) {
                TextButton(onClick = { zoom = 1f; panOffset = Offset.Zero }) {
                    Text("Reset", fontSize = 11.sp)
                }
            }
            Button(onClick = { viewModel.saveLayout() }) {
                Text("Save", fontSize = 12.sp)
            }
        }

        // Stage info + show strings toggle
        Row(
            modifier = Modifier.fillMaxWidth().padding(bottom = 4.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                "Stage: ${stage.w}m x ${stage.h}m x ${stage.d}m | ${fixtures.size} fixtures",
                fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f)
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = showStrings, onCheckedChange = { showStrings = it })
                Text("Strings", fontSize = 11.sp)
            }
        }

        val textMeasurer = rememberTextMeasurer()

        // Zoomable + pannable canvas
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f)
                .background(Color(0xFF0A0A0A))
                .transformable(transformState)
        ) {
            Canvas(
                modifier = Modifier
                    .fillMaxSize()
                    .pointerInput(fixtures, zoom, panOffset) {
                        detectDragGestures(
                            onDragStart = { offset ->
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
                                    // Reverse the zoom+pan transform to get physical coords
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
                    // Grid — scaled to stage dimensions
                    val gridColor = Color(0xFF1A1A1A)
                    val gridLineColor = Color(0xFF252525)
                    val gridStepsX = (stage.w).toInt().coerceAtLeast(2)
                    val gridStepsY = (stage.d).toInt().coerceAtLeast(2)
                    for (gx in 0..gridStepsX) {
                        val x = gx * w / gridStepsX
                        drawLine(if (gx == 0 || gx == gridStepsX) gridLineColor else gridColor,
                            Offset(x, 0f), Offset(x, h), 1f)
                    }
                    for (gy in 0..gridStepsY) {
                        val y = gy * h / gridStepsY
                        drawLine(if (gy == 0 || gy == gridStepsY) gridLineColor else gridColor,
                            Offset(0f, y), Offset(w, y), 1f)
                    }

                    // Stage border
                    drawRect(Color(0xFF1E3A5F), style = Stroke(2f))

                    // Stage dimension labels
                    val wLabel = textMeasurer.measure(
                        AnnotatedString("${stage.w}m"),
                        style = TextStyle(fontSize = 8.sp, color = Color(0xFF4A6A8F))
                    )
                    drawText(wLabel, topLeft = Offset(w / 2 - wLabel.size.width / 2f, 4f))
                    val dLabel = textMeasurer.measure(
                        AnnotatedString("${stage.d}m"),
                        style = TextStyle(fontSize = 8.sp, color = Color(0xFF4A6A8F))
                    )
                    drawText(dLabel, topLeft = Offset(4f, h / 2 - dLabel.size.height / 2f))

                    // Surfaces
                    surfaces.forEach { s ->
                        val t = s.transform
                        val sx = t.pos[0].toFloat() * w / canvasW
                        val sy = h - t.pos[1].toFloat() * h / canvasH
                        val sw = t.scale[0].toFloat() * w / canvasW
                        val sh = t.scale[1].toFloat() * h / canvasH
                        val col = try { Color(android.graphics.Color.parseColor(s.color)) }
                                  catch (_: Exception) { Color(0xFF334155) }
                        drawRect(col.copy(alpha = s.opacity / 100f), Offset(sx, sy - sh), Size(sw, sh))
                        drawRect(col.copy(alpha = 0.6f), Offset(sx, sy - sh), Size(sw, sh), style = Stroke(1f))
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
                                val endX = cx + dirX * pxLen.coerceAtLeast(20f)
                                val endY = cy + dirY * pxLen.coerceAtLeast(20f)
                                val col = strColors[si % strColors.size]
                                drawLine(col, Offset(cx, cy), Offset(endX, endY), strokeWidth = 3f)
                            }
                        }

                        // DMX beam cone
                        if (fixture.fixtureType == "dmx" && fixture.aimPoint != null) {
                            val aimX = fixture.aimPoint.getOrNull(0)?.toFloat()
                            val aimZ = fixture.aimPoint.getOrNull(2)?.toFloat()
                            if (aimX != null && aimZ != null) {
                                val ax = aimX.toFloat() * w / canvasW
                                val ay = h - aimZ.toFloat() * h / canvasH
                                val bLen = kotlin.math.sqrt((ax - cx) * (ax - cx) + (ay - cy) * (ay - cy))
                                if (bLen > 5f) {
                                    val bwRad = 15f * Math.PI.toFloat() / 180f
                                    val halfW = kotlin.math.tan(bwRad / 2) * bLen
                                    val angle = kotlin.math.atan2(ay - cy, ax - cx)
                                    val perpX = -kotlin.math.sin(angle)
                                    val perpY = kotlin.math.cos(angle)
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

                        // Node circle
                        val nodeColor = if (fixture.fixtureType == "dmx") Color(0xFF9966FF) else Color(0xFF22CC66)
                        drawCircle(nodeColor, 10f, Offset(cx, cy))
                        drawCircle(nodeColor.copy(alpha = 0.4f), 14f, Offset(cx, cy), style = Stroke(2f))

                        // Label
                        val label = fixture.name.ifEmpty { "Fixture ${fixture.id}" }
                        val textResult = textMeasurer.measure(
                            AnnotatedString(label),
                            style = TextStyle(fontSize = 9.sp, color = Color.White)
                        )
                        drawText(textResult, topLeft = Offset(cx - textResult.size.width / 2f, cy + 16f))
                    }
                }

                // Zoom indicator (outside transform so it stays readable)
                if (zoom != 1f) {
                    val zoomLabel = textMeasurer.measure(
                        AnnotatedString("${(zoom * 100).toInt()}%"),
                        style = TextStyle(fontSize = 10.sp, color = Color(0xFF666666))
                    )
                    drawText(zoomLabel, topLeft = Offset(w - zoomLabel.size.width - 8f, h - zoomLabel.size.height - 4f))
                }
            }
        }

        // Placed fixtures list
        val placed = fixtures.filter { it.positioned }
        if (placed.isNotEmpty()) {
            Spacer(Modifier.height(6.dp))
            Text("Placed (${placed.size})", style = MaterialTheme.typography.titleSmall, fontSize = 12.sp)
            LazyColumn(modifier = Modifier.fillMaxWidth().heightIn(max = 120.dp)) {
                items(placed) { f ->
                    Card(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp),
                        onClick = { editFixture = f }
                    ) {
                        Row(
                            modifier = Modifier.padding(8.dp).fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            val typeTag = if (f.fixtureType == "dmx") "[DMX]" else "[LED]"
                            Text("$typeTag ${f.name.ifEmpty { "Fixture ${f.id}" }}", fontSize = 13.sp)
                            Text("(${f.x}, ${f.y}, ${f.z})", fontSize = 11.sp,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }

        // Unplaced fixtures
        val unplaced = fixtures.filter { !it.positioned }
        if (unplaced.isNotEmpty()) {
            Spacer(Modifier.height(6.dp))
            Text("Unplaced (${unplaced.size})", style = MaterialTheme.typography.titleSmall)
            LazyColumn(modifier = Modifier.fillMaxWidth().heightIn(max = 100.dp)) {
                items(unplaced) { f ->
                    Card(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp),
                        onClick = { viewModel.placeFixture(f.id) }
                    ) {
                        Row(modifier = Modifier.padding(12.dp)) {
                            val typeTag = if (f.fixtureType == "dmx") "[DMX]" else "[LED]"
                            Text("$typeTag ${f.name.ifEmpty { "Fixture ${f.id}" }} — tap to place", fontSize = 13.sp)
                        }
                    }
                }
            }
        }

        message?.let {
            Text(it, color = MaterialTheme.colorScheme.primary, fontSize = 12.sp, modifier = Modifier.padding(top = 4.dp))
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
                        label = { Text("X (mm)") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(value = yText, onValueChange = { yText = it },
                        label = { Text("Y (mm)") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(value = zText, onValueChange = { zText = it },
                        label = { Text("Z (mm)") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        modifier = Modifier.fillMaxWidth())
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    val x = xText.toIntOrNull() ?: fixture.x
                    val y = yText.toIntOrNull() ?: fixture.y
                    val z = zText.toIntOrNull() ?: fixture.z
                    viewModel.updateFixturePosition(fixture.id, x, y, z)
                    editFixture = null
                }) { Text("OK") }
            },
            dismissButton = {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(onClick = {
                        viewModel.removeFixture(fixture.id)
                        editFixture = null
                    }) { Text("Remove", color = MaterialTheme.colorScheme.error) }
                    TextButton(onClick = { editFixture = null }) { Text("Cancel") }
                }
            }
        )
    }
}
