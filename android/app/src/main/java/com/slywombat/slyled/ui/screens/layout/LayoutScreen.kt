package com.slywombat.slyled.ui.screens.layout

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CenterFocusStrong
import androidx.compose.material.icons.filled.GridView
import androidx.compose.material.icons.filled.Layers
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.OpenWith
import androidx.compose.material.icons.filled.RotateRight
import androidx.compose.material.icons.filled.Save
import androidx.compose.material.icons.filled.VerticalAlignTop
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material.icons.filled.Vrpano
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
    val stageObjects by viewModel.objects.collectAsState()
    val fixtures by viewModel.fixtures.collectAsState()
    val stage by viewModel.stage.collectAsState()
    val message by viewModel.message.collectAsState()
    val isRotateMode by viewModel.rotateMode.collectAsState()
    var dragFixtureId by remember { mutableIntStateOf(-1) }
    var showStrings by remember { mutableStateOf(true) }
    var is3dMode by remember { mutableStateOf(false) }
    var editFixture by remember { mutableStateOf<Fixture?>(null) }
    var placingFixtureId by remember { mutableIntStateOf(-1) }
    var editObject by remember { mutableStateOf<StageObject?>(null) }
    var compassFixtureId by remember { mutableIntStateOf(-1) }
    var compassDragging by remember { mutableStateOf(false) }

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
            // Move / Rotate mode toggle
            IconButton(onClick = { viewModel.toggleRotateMode(); compassFixtureId = -1 }) {
                Icon(
                    if (isRotateMode) Icons.Default.RotateRight else Icons.Default.OpenWith,
                    contentDescription = if (isRotateMode) "Rotate mode" else "Move mode",
                    tint = if (isRotateMode) Color(0xFFE9D5FF) else MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
            // Auto-arrange DMX: evenly space along top, aimed down
            IconButton(onClick = { viewModel.autoArrangeDmx() }) {
                Icon(Icons.Default.GridView, contentDescription = "Auto-arrange DMX fixtures",
                    tint = Color(0xFFE9D5FF))
            }
            // 2D/3D mode toggle
            IconButton(onClick = { is3dMode = !is3dMode }) {
                Icon(Icons.Default.Layers, contentDescription = "Toggle 2D/3D mode",
                    tint = if (is3dMode) Color(0xFF86EFAC) else MaterialTheme.colorScheme.onSurfaceVariant)
            }
            // Quick view controls: recenter, top, front
            IconButton(onClick = { zoom = 1f; panOffset = Offset.Zero }) {
                Icon(Icons.Default.CenterFocusStrong, contentDescription = "Recenter view",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(20.dp))
            }
            IconButton(onClick = { /* top view — reserved for 3D */ }) {
                Icon(Icons.Default.VerticalAlignTop, contentDescription = "Top view",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(20.dp))
            }
            IconButton(onClick = { /* front view — reserved for 3D */ }) {
                Icon(Icons.Default.Vrpano, contentDescription = "Front view",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(20.dp))
            }
            // Show/hide LED strings toggle
            IconButton(onClick = { showStrings = !showStrings }) {
                Icon(
                    if (showStrings) Icons.Default.Visibility else Icons.Default.VisibilityOff,
                    contentDescription = "Show/hide LED strings",
                    tint = if (showStrings) Color(0xFF22C55E) else MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
            // Save layout
            IconButton(onClick = { viewModel.saveLayout() }) {
                Icon(Icons.Default.Save, contentDescription = "Save layout")
            }
        }

        // Info bar
        Row(modifier = Modifier.fillMaxWidth().padding(bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("${stageWm}m × ${stageHm}m | ${fixtures.size} fixtures",
                fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.width(8.dp))
            Text(
                if (isRotateMode) "Rotate mode — tap DMX fixture to aim" else "Move mode — drag to reposition",
                fontSize = 10.sp, color = if (isRotateMode) Color(0xFFE9D5FF) else Color(0xFF64748B)
            )
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

        // Zoomable + pannable canvas — clipToBounds prevents zoom overflow into lists
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(canvasW.toFloat() / canvasH)
                .clipToBounds()
                .background(Color(0xFF0A0A0A))
                .transformable(transformState)
        ) {
            Canvas(
                modifier = Modifier
                    .fillMaxSize()
                    .pointerInput(fixtures, zoom, panOffset, placingFixtureId, isRotateMode, compassFixtureId) {
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
                                val w = size.width.toFloat()
                                val h = size.height.toFloat()
                                val placed = fixtures.filter { it.positioned }
                                // Rotate mode
                                if (isRotateMode) {
                                    // Check compass drag first
                                    if (compassFixtureId >= 0) {
                                        val cf = placed.find { it.id == compassFixtureId }
                                        if (cf != null) {
                                            val ccx = cf.x.toFloat() * w / canvasW * zoom + panOffset.x
                                            val ccy = (h - cf.y.toFloat() * h / canvasH) * zoom + panOffset.y
                                            val dist = kotlin.math.sqrt((offset.x - ccx) * (offset.x - ccx) + (offset.y - ccy) * (offset.y - ccy))
                                            if (dist >= 20f && dist <= 120f) {
                                                compassDragging = true
                                                val angle = kotlin.math.atan2(-(offset.y - ccy), offset.x - ccx) * 180f / Math.PI.toFloat()
                                                viewModel.setAimFromAngle(compassFixtureId, angle)
                                                return@detectDragGestures
                                            }
                                        }
                                    }
                                    // Tap to select fixture for compass (only dmx/camera)
                                    val nearest = placed.filter { it.fixtureType == "dmx" || it.fixtureType == "camera" }
                                        .minByOrNull { f ->
                                            val fcx = f.x.toFloat() * w / canvasW * zoom + panOffset.x
                                            val fcy = (h - f.y.toFloat() * h / canvasH) * zoom + panOffset.y
                                            (fcx - offset.x).let { it * it } + (fcy - offset.y).let { it * it }
                                        }
                                    if (nearest != null) {
                                        val ncx = nearest.x.toFloat() * w / canvasW * zoom + panOffset.x
                                        val ncy = (h - nearest.y.toFloat() * h / canvasH) * zoom + panOffset.y
                                        val ndist = kotlin.math.sqrt((offset.x - ncx) * (offset.x - ncx) + (offset.y - ncy) * (offset.y - ncy))
                                        if (ndist < 60f) {
                                            compassFixtureId = nearest.id
                                        } else {
                                            compassFixtureId = -1
                                        }
                                    }
                                    return@detectDragGestures
                                }
                                // Move mode — find nearest fixture
                                dragFixtureId = placed.minByOrNull { f ->
                                    val cx = f.x.toFloat() * w / canvasW * zoom + panOffset.x
                                    val cy = (h - f.y.toFloat() * h / canvasH) * zoom + panOffset.y
                                    (cx - offset.x).let { it * it } + (cy - offset.y).let { it * it }
                                }?.id ?: -1
                            },
                            onDrag = { change, _ ->
                                if (compassDragging && compassFixtureId >= 0) {
                                    // Compass drag — compute angle from fixture center
                                    val w = size.width.toFloat()
                                    val h = size.height.toFloat()
                                    val cf = fixtures.find { it.id == compassFixtureId }
                                    if (cf != null) {
                                        val ccx = cf.x.toFloat() * w / canvasW * zoom + panOffset.x
                                        val ccy = (h - cf.y.toFloat() * h / canvasH) * zoom + panOffset.y
                                        val angle = kotlin.math.atan2(-(change.position.y - ccy), change.position.x - ccx) * 180f / Math.PI.toFloat()
                                        viewModel.setAimFromAngle(compassFixtureId, angle)
                                    }
                                } else if (dragFixtureId >= 0) {
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
                            onDragEnd = {
                                if (compassDragging && compassFixtureId >= 0) {
                                    viewModel.saveAimPoint(compassFixtureId)
                                }
                                compassDragging = false
                                dragFixtureId = -1
                            }
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

                    // Stage objects — clipped to stage bounds, with name labels (temporal objects filtered out)
                    stageObjects.filter { !it.temporal }.forEach { s ->
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
                            // Object name label
                            val name = s.name.ifEmpty { "Object ${s.id}" }
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
                        if (showStrings && fixture.fixtureType == "led") {
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

                        // DMX beam cone — aimPoint[0]=X, aimPoint[1]=Y (height), matching SPA front view
                        if (fixture.fixtureType == "dmx" && fixture.aimPoint != null) {
                            val aimX = fixture.aimPoint.getOrNull(0)?.toFloat()
                            val aimY = fixture.aimPoint.getOrNull(1)?.toFloat()
                            if (aimX != null && aimY != null) {
                                val ax = aimX * w / canvasW
                                val ay = h - aimY * h / canvasH
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

                        // Camera FOV cone
                        if (fixture.fixtureType == "camera" && fixture.aimPoint != null) {
                            val aimX = fixture.aimPoint.getOrNull(0)?.toFloat()
                            val aimY = fixture.aimPoint.getOrNull(1)?.toFloat()
                            if (aimX != null && aimY != null) {
                                val ax = aimX * w / canvasW
                                val ay = h - aimY * h / canvasH
                                val bLen = kotlin.math.sqrt((ax - cx) * (ax - cx) + (ay - cy) * (ay - cy))
                                if (bLen > 5f) {
                                    val fovDeg = fixture.fovDeg?.toFloat() ?: 60f
                                    val halfW = kotlin.math.tan(fovDeg * Math.PI.toFloat() / 360f) * bLen
                                    val angle = kotlin.math.atan2(ay - cy, ax - cx)
                                    val perpX = -kotlin.math.sin(angle); val perpY = kotlin.math.cos(angle)
                                    val path = Path()
                                    path.moveTo(cx, cy)
                                    path.lineTo(ax + perpX * halfW, ay + perpY * halfW)
                                    path.lineTo(ax - perpX * halfW, ay - perpY * halfW)
                                    path.close()
                                    drawPath(path, Color(0x1A0E7490))
                                    drawCircle(Color(0xFFFF4444), 5f, Offset(ax, ay))
                                }
                            }
                        }

                        // Node
                        val nodeColor = when (fixture.fixtureType) {
                            "dmx" -> Color(0xFF9966FF)
                            "camera" -> Color(0xFF22D3EE)
                            else -> Color(0xFF22CC66)
                        }
                        drawCircle(nodeColor, 10f, Offset(cx, cy))
                        drawCircle(nodeColor.copy(alpha = 0.4f), 14f, Offset(cx, cy), style = Stroke(2f))

                        // Label
                        val label = fixture.name.ifEmpty { "Fixture ${fixture.id}" }
                        val tr = textMeasurer.measure(AnnotatedString(label),
                            style = TextStyle(fontSize = 9.sp, color = Color.White))
                        drawText(tr, topLeft = Offset(cx - tr.size.width / 2f, cy + 16f))
                    }

                    // Compass ring for selected fixture in rotate mode
                    if (isRotateMode && compassFixtureId >= 0) {
                        val cf = fixtures.find { it.id == compassFixtureId && it.positioned }
                        if (cf != null) {
                            val ccx = cf.x.toFloat() * w / canvasW
                            val ccy = h - cf.y.toFloat() * h / canvasH
                            val R = 60f  // compass radius (larger for touch)
                            // Outer ring
                            drawCircle(Color(0x8022D3EE), R, Offset(ccx, ccy), style = Stroke(3f))
                            drawCircle(Color(0x2622D3EE), R - 15f, Offset(ccx, ccy), style = Stroke(1f))
                            // Cardinal ticks
                            val cardinals = listOf(
                                Triple(0f, "+X", Color(0xFFFF6666)),
                                Triple(90f, "+Y", Color(0xFF66FF66)),
                                Triple(180f, "-X", Color(0xFFFF6666)),
                                Triple(270f, "-Y", Color(0xFF66FF66)),
                            )
                            cardinals.forEach { (deg, lbl, col) ->
                                val rad = deg * Math.PI.toFloat() / 180f
                                val x1 = ccx + kotlin.math.cos(rad) * (R - 8f)
                                val y1 = ccy - kotlin.math.sin(rad) * (R - 8f)
                                val x2 = ccx + kotlin.math.cos(rad) * (R + 8f)
                                val y2 = ccy - kotlin.math.sin(rad) * (R + 8f)
                                drawLine(col, Offset(x1, y1), Offset(x2, y2), 2f)
                                val lt = textMeasurer.measure(AnnotatedString(lbl),
                                    style = TextStyle(fontSize = 7.sp, color = col))
                                val lx = ccx + kotlin.math.cos(rad) * (R + 18f) - lt.size.width / 2f
                                val ly = ccy - kotlin.math.sin(rad) * (R + 18f) - lt.size.height / 2f
                                drawText(lt, topLeft = Offset(lx, ly))
                            }
                            // Current aim direction arrow
                            val aim = cf.aimPoint
                            if (aim != null && aim.size >= 2) {
                                val dx = (aim[0] - cf.x).toFloat()
                                val dy = (aim[1] - cf.y).toFloat()
                                val aimAngle = kotlin.math.atan2(dy, dx)
                                val ax = ccx + kotlin.math.cos(aimAngle) * R
                                val ay = ccy - kotlin.math.sin(aimAngle) * R
                                drawLine(Color(0xFF22D3EE), Offset(ccx, ccy), Offset(ax, ay), 4f)
                                // Arrowhead
                                val path = Path()
                                path.moveTo(ax, ay)
                                path.lineTo(
                                    ax - 12f * kotlin.math.cos(aimAngle - 0.3f),
                                    ay + 12f * kotlin.math.sin(aimAngle - 0.3f))
                                path.lineTo(
                                    ax - 12f * kotlin.math.cos(aimAngle + 0.3f),
                                    ay + 12f * kotlin.math.sin(aimAngle + 0.3f))
                                path.close()
                                drawPath(path, Color(0xFF22D3EE))
                                // Degree label
                                val deg = Math.round(aimAngle * 180f / Math.PI.toFloat())
                                val dl = textMeasurer.measure(AnnotatedString("${deg}°"),
                                    style = TextStyle(fontSize = 9.sp, color = Color(0xFFE2E8F0)))
                                drawText(dl, topLeft = Offset(ccx - dl.size.width / 2f, ccy + R + 12f))
                            }
                        }
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
                            val tag = when (f.fixtureType) { "dmx" -> "[DMX]"; "camera" -> "[CAM]"; else -> "[LED]" }
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
                            val tag = when (f.fixtureType) { "dmx" -> "[DMX]"; "camera" -> "[CAM]"; else -> "[LED]" }
                            Text("$tag ${f.name.ifEmpty { "Fixture ${f.id}" }} — tap then tap canvas",
                                fontSize = 12.sp, color = if (placingFixtureId == f.id) Color(0xFF86EFAC) else Color.Unspecified)
                        }
                    }
                }
            }
        }

        // Stage objects list — tap to edit (temporal objects filtered out)
        val visibleObjects = stageObjects.filter { !it.temporal }
        if (visibleObjects.isNotEmpty()) {
            Spacer(Modifier.height(4.dp))
            Text("Objects (${visibleObjects.size})", fontSize = 12.sp,
                fontWeight = androidx.compose.ui.text.font.FontWeight.Bold)
            LazyColumn(modifier = Modifier.fillMaxWidth().heightIn(max = 80.dp)) {
                items(visibleObjects) { s ->
                    Card(modifier = Modifier.fillMaxWidth().padding(vertical = 1.dp),
                        onClick = { editObject = s }) {
                        Row(modifier = Modifier.padding(6.dp).fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically) {
                            val t = s.transform
                            val wm = String.format("%.1f", t.scale[0] / 1000.0)
                            val hm = String.format("%.1f", t.scale[1] / 1000.0)
                            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                                Text(s.name.ifEmpty { "Object ${s.id}" }, fontSize = 12.sp)
                                if (s.stageLocked) Icon(Icons.Default.Lock, contentDescription = "Locked to stage",
                                    modifier = Modifier.size(12.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            Text("${wm}m × ${hm}m  ${s.mobility}  pos(${t.pos[0].toInt()}, ${t.pos[1].toInt()})",
                                fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
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

    // Object edit dialog
    editObject?.let { obj ->
        val t = obj.transform
        val locked = obj.stageLocked
        var posX by remember(obj.id) { mutableStateOf(t.pos[0].toInt().toString()) }
        var posY by remember(obj.id) { mutableStateOf(t.pos[1].toInt().toString()) }
        var scaleW by remember(obj.id) { mutableStateOf(t.scale[0].toInt().toString()) }
        var scaleH by remember(obj.id) { mutableStateOf(t.scale[1].toInt().toString()) }
        var opacity by remember(obj.id) { mutableStateOf(obj.opacity.toString()) }

        AlertDialog(
            onDismissRequest = { editObject = null },
            title = {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(obj.name.ifEmpty { "Object ${obj.id}" })
                    if (locked) Icon(Icons.Default.Lock, contentDescription = "Locked to stage",
                        modifier = Modifier.size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Mobility: ${obj.mobility}", fontSize = 11.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (locked) {
                        Text("Dimensions locked to stage size", fontSize = 11.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedTextField(value = posX, onValueChange = { posX = it },
                            label = { Text("X (mm)") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                            modifier = Modifier.weight(1f), enabled = !locked)
                        OutlinedTextField(value = posY, onValueChange = { posY = it },
                            label = { Text("Y (mm)") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                            modifier = Modifier.weight(1f), enabled = !locked)
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedTextField(value = scaleW, onValueChange = { scaleW = it },
                            label = { Text("Width (mm)") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                            modifier = Modifier.weight(1f), enabled = !locked)
                        OutlinedTextField(value = scaleH, onValueChange = { scaleH = it },
                            label = { Text("Height (mm)") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                            modifier = Modifier.weight(1f), enabled = !locked)
                    }
                    OutlinedTextField(value = opacity, onValueChange = { opacity = it },
                        label = { Text("Opacity (0-100)") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        modifier = Modifier.fillMaxWidth())
                }
            },
            confirmButton = {
                if (!locked) {
                    TextButton(onClick = {
                        viewModel.updateObject(obj.id,
                            posX.toIntOrNull() ?: t.pos[0].toInt(),
                            posY.toIntOrNull() ?: t.pos[1].toInt(),
                            scaleW.toIntOrNull() ?: t.scale[0].toInt(),
                            scaleH.toIntOrNull() ?: t.scale[1].toInt(),
                            opacity.toIntOrNull() ?: obj.opacity)
                        editObject = null
                    }) { Text("OK") }
                } else {
                    TextButton(onClick = { editObject = null }) { Text("Close") }
                }
            },
            dismissButton = {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(onClick = { viewModel.deleteObject(obj.id); editObject = null }) {
                        Text("Delete", color = MaterialTheme.colorScheme.error)
                    }
                    if (!locked) TextButton(onClick = { editObject = null }) { Text("Cancel") }
                }
            }
        )
    }
}
