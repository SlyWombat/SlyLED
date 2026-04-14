package com.slywombat.slyled.ui.screens.livestage

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Fill
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.slywombat.slyled.data.model.*
import com.slywombat.slyled.ui.theme.*
import com.slywombat.slyled.viewmodel.LiveStageViewModel
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.intOrNull
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin

@Composable
fun LiveStageScreen(viewModel: LiveStageViewModel = hiltViewModel()) {
    LaunchedEffect(Unit) { viewModel.load() }

    val fixtures by viewModel.fixtures.collectAsState()
    val fixturesLive by viewModel.fixturesLive.collectAsState()
    val objects by viewModel.objects.collectAsState()
    val stage by viewModel.stage.collectAsState()
    val settings by viewModel.settings.collectAsState()
    val timelineStatus by viewModel.timelineStatus.collectAsState()
    val timelines by viewModel.timelines.collectAsState()
    val layout by viewModel.layout.collectAsState()

    val isRunning = settings.runnerRunning
    val brightness = settings.globalBrightness ?: 255
    var brightnessSlider by remember { mutableFloatStateOf(brightness.toFloat()) }

    // Sync slider when server value changes
    LaunchedEffect(brightness) {
        brightnessSlider = brightness.toFloat()
    }

    Box(modifier = Modifier.fillMaxSize().background(DeepSlate)) {
        // Stage canvas (full screen background) with gesture support
        StageCanvas(
            fixtures = fixtures,
            fixturesLive = fixturesLive,
            objects = objects,
            stage = stage,
            layout = layout,
            modifier = Modifier.fillMaxSize()
        )

        // HUD overlay at top
        HudOverlay(
            settings = settings,
            timelineStatus = timelineStatus,
            timelines = timelines,
            modifier = Modifier
                .align(Alignment.TopCenter)
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp)
        )

        // Controls at bottom
        Column(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            // Brightness slider
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.85f)
                ),
                shape = RoundedCornerShape(12.dp)
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        "Brightness",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurface,
                        modifier = Modifier.width(80.dp)
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
                    Text(
                        "${brightnessSlider.toInt()}",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurface,
                        modifier = Modifier.width(36.dp)
                    )
                }
            }

            // Play/Stop FAB
            FloatingActionButton(
                onClick = { viewModel.toggleShow() },
                containerColor = if (isRunning) RedError else GreenOnline,
                contentColor = Color.White,
                modifier = Modifier.align(Alignment.CenterHorizontally)
            ) {
                Icon(
                    if (isRunning) Icons.Default.Stop else Icons.Default.PlayArrow,
                    contentDescription = if (isRunning) "Stop" else "Play"
                )
            }
        }
    }
}

@Composable
private fun HudOverlay(
    settings: Settings,
    timelineStatus: TimelineStatus?,
    timelines: List<Timeline>,
    modifier: Modifier = Modifier
) {
    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.85f)
        ),
        shape = RoundedCornerShape(12.dp)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 10.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            if (settings.runnerRunning && timelineStatus != null) {
                val name = timelineStatus.name.ifBlank {
                    timelines.find { it.id == timelineStatus.id }?.name ?: "Timeline #${timelineStatus.id}"
                }
                Column {
                    Text(
                        name,
                        style = MaterialTheme.typography.titleSmall,
                        color = GreenOnline
                    )
                    val elapsed = timelineStatus.elapsed
                    val duration = timelineStatus.durationS
                    Text(
                        "${formatTime(elapsed)} / ${formatTime(duration)}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                if (timelineStatus.durationS > 0) {
                    LinearProgressIndicator(
                        progress = { (timelineStatus.elapsed.toFloat() / timelineStatus.durationS).coerceIn(0f, 1f) },
                        modifier = Modifier
                            .width(100.dp)
                            .height(6.dp),
                        color = CyanSecondary,
                        trackColor = MaterialTheme.colorScheme.outlineVariant,
                    )
                }
            } else {
                Text(
                    "No show running",
                    style = MaterialTheme.typography.titleSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}

@Composable
private fun StageCanvas(
    fixtures: List<Fixture>,
    fixturesLive: Map<String, JsonElement>,
    objects: List<StageObject>,
    stage: Stage,
    layout: Layout?,
    modifier: Modifier = Modifier
) {
    val textMeasurer = rememberTextMeasurer()

    // Stage dimensions in mm
    val stageW = (stage.w * 1000).toFloat()
    val stageD = (stage.d * 1000).toFloat()

    // Gesture state: zoom and pan
    var zoom by remember { mutableFloatStateOf(1f) }
    var panX by remember { mutableFloatStateOf(0f) }
    var panY by remember { mutableFloatStateOf(0f) }

    Canvas(
        modifier = modifier
            .background(DeepSlate)
            .pointerInput(Unit) {
                detectTransformGestures { _, pan, gestureZoom, _ ->
                    zoom = (zoom * gestureZoom).coerceIn(0.5f, 4f)
                    panX += pan.x
                    panY += pan.y
                }
            }
            .pointerInput(Unit) {
                detectTapGestures(
                    onDoubleTap = {
                        zoom = 1f
                        panX = 0f
                        panY = 0f
                    }
                )
            }
    ) {
        if (stageW <= 0f || stageD <= 0f) return@Canvas

        val padding = 40f
        val availW = size.width - padding * 2
        val availH = size.height - padding * 2
        val baseScale = min(availW / stageW, availH / stageD)
        val scale = baseScale * zoom
        val offsetX = padding + (availW - stageW * baseScale) / 2f + panX
        val offsetY = padding + (availH - stageD * baseScale) / 2f + panY

        // --- 1. Stage floor with gradient ---
        val stageTopLeft = Offset(offsetX, offsetY)
        val stageSz = Size(stageW * scale, stageD * scale)

        // Dark floor with subtle vertical gradient (front lighter, back darker)
        drawRect(
            brush = Brush.verticalGradient(
                colors = listOf(Color(0xFF0D1B2A), Color(0xFF0F172A), Color(0xFF0A0F13)),
                startY = offsetY,
                endY = offsetY + stageD * scale
            ),
            topLeft = stageTopLeft,
            size = stageSz
        )

        // Grid lines every 1000mm
        val gridStep = 1000f
        var gx = 0f
        while (gx <= stageW) {
            val sx = offsetX + gx * scale
            val gridAlpha = if (gx.toInt() % 2000 == 0) 0.2f else 0.1f
            drawLine(
                color = MutedSlate.copy(alpha = gridAlpha),
                start = Offset(sx, offsetY),
                end = Offset(sx, offsetY + stageD * scale),
                strokeWidth = if (gx.toInt() % 2000 == 0) 1.5f else 0.5f
            )
            gx += gridStep
        }
        var gy = 0f
        while (gy <= stageD) {
            val sy = offsetY + gy * scale
            val gridAlpha = if (gy.toInt() % 2000 == 0) 0.2f else 0.1f
            drawLine(
                color = MutedSlate.copy(alpha = gridAlpha),
                start = Offset(offsetX, sy),
                end = Offset(offsetX + stageW * scale, sy),
                strokeWidth = if (gy.toInt() % 2000 == 0) 1.5f else 0.5f
            )
            gy += gridStep
        }

        // Stage border (outer glow effect)
        drawRect(
            color = CyanSecondary.copy(alpha = 0.08f),
            topLeft = Offset(stageTopLeft.x - 4f, stageTopLeft.y - 4f),
            size = Size(stageSz.width + 8f, stageSz.height + 8f),
            style = Stroke(width = 4f)
        )
        drawRect(
            color = MutedSlate.copy(alpha = 0.4f),
            topLeft = stageTopLeft,
            size = stageSz,
            style = Stroke(width = 2f)
        )

        // --- 2. Static objects (walls, obstacles) ---
        for (obj in objects) {
            if (obj.temporal || obj.mobility == "moving") continue
            drawStaticObject(obj, offsetX, offsetY, scale, textMeasurer)
        }

        // --- 3. Build position map from layout ---
        val posMap = mutableMapOf<Int, LayoutChild>()
        layout?.children?.forEach { lc -> posMap[lc.id] = lc }

        // --- 4. Draw fixtures (use layout positions, fall back to fixture x/y) ---
        for (fixture in fixtures) {
            val lc = posMap[fixture.id]
            val fx = (lc?.x ?: fixture.x).toFloat()
            val fy = (lc?.y ?: fixture.y).toFloat()
            // Skip fixtures with no position at all
            if (fx == 0f && fy == 0f && lc == null && !fixture.positioned) continue
            val sx = offsetX + fx * scale
            val sy = offsetY + fy * scale

            // Get live color
            val liveData = fixturesLive[fixture.id.toString()]
            val liveColor = parseLiveColor(liveData)

            when (fixture.fixtureType) {
                "camera" -> drawCameraFixture(sx, sy, zoom, fixture)
                "dmx" -> drawDmxFixture(sx, sy, zoom, scale, fixture, liveColor)
                else -> drawLedFixture(sx, sy, zoom, liveColor)
            }
        }

        // --- 5. Draw tracked (temporal/moving) objects ---
        for (obj in objects) {
            if (!obj.temporal && obj.mobility != "moving") continue
            drawTrackedObject(obj, offsetX, offsetY, scale, zoom, textMeasurer)
        }

        // --- 6. Fixture labels ---
        for (fixture in fixtures) {
            val lc = posMap[fixture.id]
            val fx = (lc?.x ?: fixture.x).toFloat()
            val fy = (lc?.y ?: fixture.y).toFloat()
            if (fx == 0f && fy == 0f && lc == null && !fixture.positioned) continue
            val sx = offsetX + fx * scale
            val sy = offsetY + fy * scale

            if (zoom >= 1.2f && fixture.name.isNotBlank()) {
                val labelStyle = TextStyle(
                    color = NearWhite.copy(alpha = 0.7f),
                    fontSize = (9f * zoom).coerceIn(8f, 14f).sp
                )
                val labelResult = textMeasurer.measure(
                    text = fixture.name,
                    style = labelStyle,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                drawText(
                    textLayoutResult = labelResult,
                    topLeft = Offset(
                        sx - labelResult.size.width / 2f,
                        sy + 14f * zoom
                    )
                )
            }
        }
    }
}

// ---- Drawing helpers ----

private fun DrawScope.drawStaticObject(
    obj: StageObject,
    offsetX: Float,
    offsetY: Float,
    scale: Float,
    textMeasurer: TextMeasurer
) {
    val pos = obj.transform.pos
    val scl = obj.transform.scale
    // Top-down view: X = stage X (width), Y = stage Y (depth)
    // Scale: [0]=width(X), [1]=height(Z, not visible in top-down), [2]=depth(Y)
    val ox = offsetX + pos[0].toFloat() * scale
    val oy = offsetY + pos[1].toFloat() * scale
    val ow = scl[0].toFloat() * scale
    val od = (if (scl.size > 2) scl[2].toFloat() else 100f) * scale  // depth = Y extent

    val objColor = try {
        val hex = obj.color ?: "#334155"
        Color(android.graphics.Color.parseColor(hex))
    } catch (_: Exception) { Color(0xFF334155) }
    val alpha = (obj.opacity ?: 30) / 100f

    // Fill
    drawRect(
        color = objColor.copy(alpha = alpha * 0.5f),
        topLeft = Offset(ox, oy),
        size = Size(ow, od)
    )
    // Border
    drawRect(
        color = objColor.copy(alpha = alpha),
        topLeft = Offset(ox, oy),
        size = Size(ow, od),
        style = Stroke(width = 1.5f)
    )

    // Label
    val label = obj.name.ifBlank { obj.objectType }
    val textStyle = TextStyle(
        color = objColor.copy(alpha = 0.8f),
        fontSize = 10.sp
    )
    val textResult = textMeasurer.measure(
        text = label,
        style = textStyle,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis
    )
    drawText(
        textLayoutResult = textResult,
        topLeft = Offset(ox - textResult.size.width / 2f, oy - od / 2 - textResult.size.height - 2f)
    )
}

private fun DrawScope.drawCameraFixture(
    sx: Float,
    sy: Float,
    zoom: Float,
    fixture: Fixture
) {
    val sz = 10f * zoom

    // Camera FOV arc
    val fov = (fixture.fovDeg ?: 60.0).toFloat()
    val arcLen = 50f * zoom
    val halfFov = fov / 2f * (Math.PI.toFloat() / 180f)
    val aimAngle = getAimAngle(fixture)

    val fovPath = Path().apply {
        moveTo(sx, sy)
        lineTo(
            sx + arcLen * cos(aimAngle - halfFov),
            sy + arcLen * sin(aimAngle - halfFov)
        )
        lineTo(
            sx + arcLen * cos(aimAngle + halfFov),
            sy + arcLen * sin(aimAngle + halfFov)
        )
        close()
    }
    drawPath(fovPath, color = CyanSecondary.copy(alpha = 0.08f), style = Fill)
    drawPath(fovPath, color = CyanSecondary.copy(alpha = 0.25f), style = Stroke(width = 1f))

    // Camera body: rounded square
    drawRect(
        color = CyanSecondary,
        topLeft = Offset(sx - sz, sy - sz),
        size = Size(sz * 2, sz * 2)
    )
    drawRect(
        color = Color(0xFF0E7490),
        topLeft = Offset(sx - sz, sy - sz),
        size = Size(sz * 2, sz * 2),
        style = Stroke(width = 1.5f)
    )
    // Lens dot
    drawCircle(
        color = Color(0xFF0E7490),
        radius = sz * 0.4f,
        center = Offset(sx, sy)
    )
}

private fun DrawScope.drawDmxFixture(
    sx: Float,
    sy: Float,
    zoom: Float,
    scale: Float,
    fixture: Fixture,
    liveColor: Color?
) {
    val effectiveColor = liveColor ?: DmxPurple

    // Beam cone (translucent triangle toward aim point)
    if (liveColor != null && liveColor != Color.Black) {
        val beamLen = 2000f * scale   // 2m beam length
        val beamSpread = 15f * (Math.PI.toFloat() / 180f)  // 15 deg half-angle
        val aimAngle = getAimAngle(fixture)

        val beamPath = Path().apply {
            moveTo(sx, sy)
            lineTo(
                sx + beamLen * cos(aimAngle - beamSpread),
                sy + beamLen * sin(aimAngle - beamSpread)
            )
            lineTo(
                sx + beamLen * cos(aimAngle + beamSpread),
                sy + beamLen * sin(aimAngle + beamSpread)
            )
            close()
        }

        // Gradient fill: bright at source, fading out
        drawPath(beamPath, color = liveColor.copy(alpha = 0.15f), style = Fill)
        drawPath(beamPath, color = liveColor.copy(alpha = 0.35f), style = Stroke(width = 1.5f))

        // Hot spot at source
        drawCircle(
            color = liveColor.copy(alpha = 0.3f),
            radius = 18f * zoom,
            center = Offset(sx, sy)
        )
    }

    // Fixture body: triangle
    val triSize = 12f * zoom
    val path = Path().apply {
        moveTo(sx, sy - triSize)
        lineTo(sx - triSize * 0.866f, sy + triSize * 0.5f)
        lineTo(sx + triSize * 0.866f, sy + triSize * 0.5f)
        close()
    }
    drawPath(path, color = effectiveColor, style = Fill)
    drawPath(path, color = DmxPurple.copy(alpha = 0.6f), style = Stroke(width = 1.5f))

    // Inner dot showing live color
    drawCircle(
        color = Color(0xFF0F172A),
        radius = 4f * zoom,
        center = Offset(sx, sy)
    )
    drawCircle(
        color = effectiveColor,
        radius = 2.5f * zoom,
        center = Offset(sx, sy)
    )
}

private fun DrawScope.drawLedFixture(
    sx: Float,
    sy: Float,
    zoom: Float,
    liveColor: Color?
) {
    val fillColor = liveColor ?: GreenOnline
    val radius = 8f * zoom

    // Glow ring
    if (liveColor != null && liveColor != Color.Black) {
        drawCircle(
            color = fillColor.copy(alpha = 0.15f),
            radius = radius * 2.5f,
            center = Offset(sx, sy)
        )
    }

    // Outer ring
    drawCircle(
        color = fillColor.copy(alpha = 0.4f),
        radius = radius + 2f,
        center = Offset(sx, sy),
        style = Stroke(width = 1.5f)
    )

    // Filled circle
    drawCircle(
        color = fillColor,
        radius = radius,
        center = Offset(sx, sy)
    )
}

private fun DrawScope.drawTrackedObject(
    obj: StageObject,
    offsetX: Float,
    offsetY: Float,
    scale: Float,
    zoom: Float,
    textMeasurer: TextMeasurer
) {
    val pos = obj.transform.pos
    val scl = obj.transform.scale
    val cx = offsetX + pos[0].toFloat() * scale
    val cy = offsetY + pos[1].toFloat() * scale
    val ow = scl[0].toFloat() * scale
    val od = scl[1].toFloat() * scale

    val objColor = try {
        Color(android.graphics.Color.parseColor(obj.color))
    } catch (_: Exception) {
        Color(0xFFF472B6)
    }

    // Outer pulse ring
    drawOval(
        color = objColor.copy(alpha = 0.12f),
        topLeft = Offset(cx - ow * 0.75f, cy - od * 0.75f),
        size = Size(ow * 1.5f, od * 1.5f)
    )

    // Body oval
    drawOval(
        color = objColor.copy(alpha = 0.2f),
        topLeft = Offset(cx - ow / 2, cy - od / 2),
        size = Size(ow, od)
    )
    drawOval(
        color = objColor.copy(alpha = 0.6f),
        topLeft = Offset(cx - ow / 2, cy - od / 2),
        size = Size(ow, od),
        style = Stroke(width = 2f)
    )

    // Center dot
    drawCircle(
        color = objColor,
        radius = 4f * zoom,
        center = Offset(cx, cy)
    )

    // Label
    val label = obj.name.ifBlank { obj.objectType }
    val textStyle = TextStyle(
        color = objColor,
        fontSize = 10.sp
    )
    val textResult = textMeasurer.measure(
        text = label,
        style = textStyle,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis
    )
    drawText(
        textLayoutResult = textResult,
        topLeft = Offset(cx - textResult.size.width / 2f, cy - od / 2 - textResult.size.height - 4f)
    )
}

// ---- Utility functions ----

private fun parseLiveColor(element: JsonElement?): Color? {
    if (element == null) return null
    try {
        val obj = element.jsonObject
        val r = obj["r"]?.jsonPrimitive?.intOrNull ?: 0
        val g = obj["g"]?.jsonPrimitive?.intOrNull ?: 0
        val b = obj["b"]?.jsonPrimitive?.intOrNull ?: 0
        val dimmer = obj["dimmer"]?.jsonPrimitive?.intOrNull ?: 255
        if (r == 0 && g == 0 && b == 0 && dimmer == 0) return Color.Black
        val factor = dimmer / 255f
        return Color(
            red = (r * factor / 255f).coerceIn(0f, 1f),
            green = (g * factor / 255f).coerceIn(0f, 1f),
            blue = (b * factor / 255f).coerceIn(0f, 1f),
            alpha = 1f
        )
    } catch (_: Exception) {
        return null
    }
}

private fun getAimAngle(fixture: Fixture): Float {
    val aim = fixture.aimPoint ?: return (-Math.PI / 2).toFloat() // default: up
    val dx = aim[0] - fixture.x
    val dy = aim[1] - fixture.y
    return kotlin.math.atan2(dy.toFloat(), dx.toFloat())
}

private fun formatTime(seconds: Int): String {
    val m = seconds / 60
    val s = seconds % 60
    return "%02d:%02d".format(m, s)
}
