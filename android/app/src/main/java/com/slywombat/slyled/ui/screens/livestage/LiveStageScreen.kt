package com.slywombat.slyled.ui.screens.livestage

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
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
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
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
import kotlin.math.sqrt

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

    var selectedFixtureId by remember { mutableStateOf<Int?>(null) }

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
            selectedFixtureId = selectedFixtureId,
            onFixtureSelected = { selectedFixtureId = it },
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

        // Fixture info card overlay
        if (selectedFixtureId != null) {
            val selFixture = fixtures.find { it.id == selectedFixtureId }
            val selLayout = layout?.children?.find { it.id == selectedFixtureId }
            if (selFixture != null) {
                FixtureInfoCard(
                    fixture = selFixture,
                    layoutChild = selLayout,
                    liveData = fixturesLive[selFixture.id.toString()],
                    onDismiss = { selectedFixtureId = null },
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .fillMaxWidth()
                        .padding(start = 16.dp, end = 16.dp, bottom = 160.dp)
                )
            }
        }

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
    selectedFixtureId: Int? = null,
    onFixtureSelected: (Int?) -> Unit = {},
    modifier: Modifier = Modifier
) {
    val textMeasurer = rememberTextMeasurer()

    // Projected fixture positions for tap detection
    val projectedPositions = remember { mutableStateMapOf<Int, Offset>() }

    // Stage dimensions in mm — stage coords: X=width, Y=depth, Z=height
    val stageW = (stage.w * 1000).toFloat()
    val stageD = (stage.d * 1000).toFloat()
    val stageH = (stage.h * 1000).toFloat()

    // 3D camera: orbit around stage center
    // User can drag to orbit, pinch to zoom, double-tap to reset
    var orbitAzimuth by remember { mutableFloatStateOf(0f) }   // user rotation delta
    var orbitElevation by remember { mutableFloatStateOf(0f) } // user elevation delta
    var zoomFactor by remember { mutableFloatStateOf(1f) }
    var panX by remember { mutableFloatStateOf(0f) }
    var panY by remember { mutableFloatStateOf(0f) }

    Canvas(
        modifier = modifier
            .background(DeepSlate)
            .pointerInput(Unit) {
                detectTransformGestures { _, pan, gestureZoom, _ ->
                    val isTwoFinger = gestureZoom != 1f
                    if (isTwoFinger) {
                        // Two-finger: pinch = zoom, drag = pan
                        zoomFactor = (zoomFactor / gestureZoom).coerceIn(0.3f, 4f)
                        panX += pan.x
                        panY += pan.y
                    } else {
                        // One-finger: drag = orbit
                        orbitAzimuth += pan.x * 0.003f
                        orbitElevation = (orbitElevation + pan.y * 0.003f).coerceIn(-0.8f, 0.8f)
                    }
                }
            }
            .pointerInput(projectedPositions) {
                detectTapGestures(
                    onTap = { tapOffset ->
                        // Find nearest fixture within 40px threshold
                        var bestId: Int? = null
                        var bestDist = 40f
                        for ((id, pos) in projectedPositions) {
                            val dx = tapOffset.x - pos.x
                            val dy = tapOffset.y - pos.y
                            val dist = sqrt(dx * dx + dy * dy)
                            if (dist < bestDist) {
                                bestDist = dist
                                bestId = id
                            }
                        }
                        onFixtureSelected(bestId)
                    },
                    onDoubleTap = {
                        orbitAzimuth = 0f
                        orbitElevation = 0f
                        zoomFactor = 1f
                        panX = 0f
                        panY = 0f
                    }
                )
            }
    ) {
        if (stageW <= 0f || stageD <= 0f) return@Canvas

        val cw = size.width
        val ch = size.height

        // Stage center (in mm) — camera looks at this point
        val cx = stageW / 2f
        val cy = stageD / 2f
        val cz = stageH / 3f  // look slightly above floor

        // Default camera position — same formula as SPA:
        //   SPA: camera.position.set(sw*1.2, sh*1.0, sd*1.5)
        //   SPA target: (sw/2, sh/4, sd/2)
        // SPA camera position (in Three.js coords):
        //   position = (sw*1.2, sh*1.0, sd*1.5)  → Three.js X, Y(up), Z(depth)
        //   target = (sw/2, sh/4, sd/2)
        // Mapping Three.js → stage: X=X, Y=Z(height), Z=Y(depth)
        // So SPA camera in stage coords: X=sw*1.2, Y(depth)=sd*1.5, Z(height)=sh*1.0
        // But SPA Three.js Z points TOWARD viewer, and Y=0 is back wall (far from camera)
        // Our stage Y=0 is back wall. Camera must be at NEGATIVE Y to look at back wall from front.
        // Camera position in stage coordinates (mm)
        // Behind stage, elevated, looking forward — floor at bottom, ceiling at top
        val defaultCamX = stageW / 2f       // center width
        val defaultCamY = stageD * 1.3f     // behind stage (past front edge)
        val defaultCamZ = stageH * 0.6f     // slightly above mid-height

        // Look at center of stage
        val lookX = stageW / 2f
        val lookY = stageD * 0.4f           // toward back wall
        val lookZ = stageH * 0.3f           // slightly above floor

        // Rotate default camera offset around look target by user orbit
        val dx0 = (defaultCamX - lookX) * zoomFactor
        val dy0 = (defaultCamY - lookY) * zoomFactor
        val dz0 = (defaultCamZ - lookZ) * zoomFactor

        // Apply azimuth rotation (around Z axis)
        val cosA = cos(orbitAzimuth); val sinA = sin(orbitAzimuth)
        val dx1 = dx0 * cosA - dy0 * sinA
        val dy1 = dx0 * sinA + dy0 * cosA

        // Apply elevation rotation (tilt up/down)
        val horizDist = kotlin.math.sqrt(dx1 * dx1 + dy1 * dy1)
        val currentElev = kotlin.math.atan2(dz0, horizDist) + orbitElevation
        val totalDist = kotlin.math.sqrt(dx1 * dx1 + dy1 * dy1 + dz0 * dz0)
        val dz1 = totalDist * sin(currentElev)
        val horizScale = cos(currentElev) / (if (horizDist > 0.001f) horizDist / kotlin.math.sqrt(dx1 * dx1 + dy1 * dy1) else 1f)
        val dx2 = dx1 * cos(currentElev) * totalDist / horizDist.coerceAtLeast(1f)
        val dy2 = dy1 * cos(currentElev) * totalDist / horizDist.coerceAtLeast(1f)

        val camX = lookX + dx2
        val camY = lookY + dy2
        val camZ = lookZ + dz1

        // Forward, right, up vectors for view matrix
        var fwdX = lookX - camX; var fwdY = lookY - camY; var fwdZ = lookZ - camZ
        val fwdLen = kotlin.math.sqrt(fwdX * fwdX + fwdY * fwdY + fwdZ * fwdZ)
        if (fwdLen < 0.001f) return@Canvas
        fwdX /= fwdLen; fwdY /= fwdLen; fwdZ /= fwdLen

        // World up = Z (right-handed Z-up coordinate system)
        val upX = 0f; val upY = 0f; val upZ = 1f
        // Right = worldUp × forward (standard for Z-up right-handed)
        var rX = upY * fwdZ - upZ * fwdY
        var rY = upZ * fwdX - upX * fwdZ
        var rZ = upX * fwdY - upY * fwdX
        val rLen = kotlin.math.sqrt(rX * rX + rY * rY + rZ * rZ)
        if (rLen > 0.001f) { rX /= rLen; rY /= rLen; rZ /= rLen }

        // Camera up = forward × right (Z-up convention)
        val cuX = fwdY * rZ - fwdZ * rY
        val cuY = fwdZ * rX - fwdX * rZ
        val cuZ = fwdX * rY - fwdY * rX

        // Perspective projection: project 3D stage point → 2D screen
        val fov = 50f * (Math.PI.toFloat() / 180f)
        val aspect = cw / ch
        val focalLen = (ch / 2f) / kotlin.math.tan(fov / 2f)

        fun project(sx: Float, sy: Float, sz: Float): Offset? {
            // Translate to camera space
            val dx = sx - camX; val dy = sy - camY; val dz = sz - camZ
            // Dot with camera axes
            val vx = dx * rX + dy * rY + dz * rZ          // screen X
            val vy = dx * cuX + dy * cuY + dz * cuZ       // screen Y (up)
            val vz = dx * fwdX + dy * fwdY + dz * fwdZ    // depth (forward)
            if (vz < 10f) return null  // behind camera
            val px = cw / 2f + (vx / vz) * focalLen + panX
            val py = ch / 2f - (vy / vz) * focalLen + panY
            return Offset(px, py)
        }

        // Scale factor at a given depth (for sizing objects)
        fun scaleAt(sx: Float, sy: Float, sz: Float): Float {
            val dx = sx - camX; val dy = sy - camY; val dz = sz - camZ
            val vz = dx * fwdX + dy * fwdY + dz * fwdZ
            return if (vz > 10f) focalLen / vz else 0f
        }

        // --- 1. Stage floor grid ---
        // Draw floor quad (Z=0)
        val f00 = project(0f, 0f, 0f)
        val f10 = project(stageW, 0f, 0f)
        val f11 = project(stageW, stageD, 0f)
        val f01 = project(0f, stageD, 0f)
        if (f00 != null && f10 != null && f11 != null && f01 != null) {
            val floorPath = Path().apply {
                moveTo(f00.x, f00.y); lineTo(f10.x, f10.y)
                lineTo(f11.x, f11.y); lineTo(f01.x, f01.y); close()
            }
            drawPath(floorPath, color = Color(0xFF0D1B2A), style = Fill)
            drawPath(floorPath, color = MutedSlate.copy(alpha = 0.4f), style = Stroke(2f))
        }

        // Grid lines on floor
        val gridStep = 1000f
        var gx = 0f
        while (gx <= stageW) {
            val p0 = project(gx, 0f, 0f)
            val p1 = project(gx, stageD, 0f)
            if (p0 != null && p1 != null) {
                val a = if (gx.toInt() % 2000 == 0) 0.2f else 0.08f
                drawLine(MutedSlate.copy(alpha = a), p0, p1, strokeWidth = if (gx.toInt() % 2000 == 0) 1.5f else 0.5f)
            }
            gx += gridStep
        }
        var gy = 0f
        while (gy <= stageD) {
            val p0 = project(0f, gy, 0f)
            val p1 = project(stageW, gy, 0f)
            if (p0 != null && p1 != null) {
                val a = if (gy.toInt() % 2000 == 0) 0.2f else 0.08f
                drawLine(MutedSlate.copy(alpha = a), p0, p1, strokeWidth = if (gy.toInt() % 2000 == 0) 1.5f else 0.5f)
            }
            gy += gridStep
        }

        // Floor border glow
        if (f00 != null && f10 != null && f11 != null && f01 != null) {
            val borderPath = Path().apply {
                moveTo(f00.x, f00.y); lineTo(f10.x, f10.y)
                lineTo(f11.x, f11.y); lineTo(f01.x, f01.y); close()
            }
            drawPath(borderPath, color = CyanSecondary.copy(alpha = 0.1f), style = Stroke(4f))
        }

        // --- Stage volume wireframe (W × D × H) ---
        if (stageH > 0f) {
            // Ceiling corners
            val c00 = project(0f, 0f, stageH)
            val c10 = project(stageW, 0f, stageH)
            val c11 = project(stageW, stageD, stageH)
            val c01 = project(0f, stageD, stageH)
            val wireColor = CyanSecondary.copy(alpha = 0.25f)
            val wireStroke = Stroke(1.5f)
            // Ceiling quad
            if (c00 != null && c10 != null && c11 != null && c01 != null) {
                val ceilPath = Path().apply {
                    moveTo(c00.x, c00.y); lineTo(c10.x, c10.y)
                    lineTo(c11.x, c11.y); lineTo(c01.x, c01.y); close()
                }
                drawPath(ceilPath, wireColor, style = wireStroke)
            }
            // Vertical edges (floor to ceiling)
            val floors = listOf(f00, f10, f11, f01)
            val ceils = listOf(c00, c10, c11, c01)
            for (i in 0..3) {
                val fl = floors[i]; val cl = ceils[i]
                if (fl != null && cl != null) {
                    drawLine(wireColor, fl, cl, strokeWidth = 1.5f)
                }
            }
        }

        // --- Origin axes (RGB arrows at 0,0,0 with labels) ---
        val axisLen = min(stageW, stageD) * 0.15f
        val origin = project(0f, 0f, 0f)
        val xEnd = project(axisLen, 0f, 0f)
        val yEnd = project(0f, axisLen, 0f)
        val zEnd = project(0f, 0f, axisLen)
        if (origin != null) {
            val axisStroke = 2.5f
            if (xEnd != null) {
                drawLine(Color.Red.copy(alpha = 0.7f), origin, xEnd, strokeWidth = axisStroke)
                val xl = textMeasurer.measure("X", TextStyle(color = Color.Red.copy(alpha = 0.8f), fontSize = 12.sp))
                drawText(xl, topLeft = Offset(xEnd.x + 4f, xEnd.y - xl.size.height / 2f))
            }
            if (yEnd != null) {
                drawLine(Color.Green.copy(alpha = 0.7f), origin, yEnd, strokeWidth = axisStroke)
                val yl = textMeasurer.measure("Y", TextStyle(color = Color.Green.copy(alpha = 0.8f), fontSize = 12.sp))
                drawText(yl, topLeft = Offset(yEnd.x + 4f, yEnd.y - yl.size.height / 2f))
            }
            if (zEnd != null) {
                drawLine(Color.Blue.copy(alpha = 0.7f), origin, zEnd, strokeWidth = axisStroke)
                val zl = textMeasurer.measure("Z", TextStyle(color = Color.Blue.copy(alpha = 0.8f), fontSize = 12.sp))
                drawText(zl, topLeft = Offset(zEnd.x + 4f, zEnd.y - zl.size.height / 2f))
            }
            // Origin dot
            drawCircle(Color.White.copy(alpha = 0.6f), 4f, origin)
        }

        // --- 2. Static objects (walls as 3D boxes) ---
        for (obj in objects) {
            if (obj.temporal || obj.mobility == "moving") continue
            val pos = obj.transform.pos
            val scl = obj.transform.scale
            val ox = pos[0].toFloat(); val oy = pos[1].toFloat(); val oz = pos[2].toFloat()
            val ow = scl[0].toFloat()
            val oh = if (scl.size > 1) scl[1].toFloat() else 100f  // height (Z)
            val od = if (scl.size > 2) scl[2].toFloat() else 100f  // depth (Y)
            val objColor = try { Color(android.graphics.Color.parseColor(obj.color ?: "#334155")) } catch (_: Exception) { Color(0xFF334155) }
            val alpha = (obj.opacity ?: 30) / 100f

            // Draw as 3D box: front face + top face + side face
            val pts = arrayOf(
                project(ox, oy, oz), project(ox + ow, oy, oz),
                project(ox + ow, oy + od, oz), project(ox, oy + od, oz),
                project(ox, oy, oz + oh), project(ox + ow, oy, oz + oh),
                project(ox + ow, oy + od, oz + oh), project(ox, oy + od, oz + oh)
            )
            // Draw top face
            val t0 = pts[4]; val t1 = pts[5]; val t2 = pts[6]; val t3 = pts[7]
            if (t0 != null && t1 != null && t2 != null && t3 != null) {
                val topPath = Path().apply { moveTo(t0.x, t0.y); lineTo(t1.x, t1.y); lineTo(t2.x, t2.y); lineTo(t3.x, t3.y); close() }
                drawPath(topPath, objColor.copy(alpha = alpha * 0.3f), style = Fill)
                drawPath(topPath, objColor.copy(alpha = alpha * 0.6f), style = Stroke(1f))
            }
            // Draw front face
            val b0 = pts[0]; val b1 = pts[1]; val b5 = pts[5]; val b4 = pts[4]
            if (b0 != null && b1 != null && b5 != null && b4 != null) {
                val frontPath = Path().apply { moveTo(b0.x, b0.y); lineTo(b1.x, b1.y); lineTo(b5.x, b5.y); lineTo(b4.x, b4.y); close() }
                drawPath(frontPath, objColor.copy(alpha = alpha * 0.2f), style = Fill)
                drawPath(frontPath, objColor.copy(alpha = alpha * 0.5f), style = Stroke(1f))
            }
        }

        // --- 3. Build position map from layout ---
        val posMap = mutableMapOf<Int, LayoutChild>()
        layout?.children?.forEach { lc -> posMap[lc.id] = lc }

        // --- 4. Draw fixtures ---
        projectedPositions.clear()
        for (fixture in fixtures) {
            val lc = posMap[fixture.id]
            val fx = (lc?.x ?: fixture.x).toFloat()
            val fy = (lc?.y ?: fixture.y).toFloat()
            val fz = (lc?.z ?: fixture.z).toFloat()
            if (fx == 0f && fy == 0f && lc == null && !fixture.positioned) continue
            val p = project(fx, fy, fz) ?: continue
            val s = scaleAt(fx, fy, fz)

            // Store projected position for tap detection
            projectedPositions[fixture.id] = p

            val liveData = fixturesLive[fixture.id.toString()]
            val liveColor = parseLiveColor(liveData)

            when (fixture.fixtureType) {
                "camera" -> {
                    val sz = 8f * s
                    drawRect(CyanSecondary, Offset(p.x - sz, p.y - sz), Size(sz * 2, sz * 2))
                    drawRect(Color(0xFF0E7490), Offset(p.x - sz, p.y - sz), Size(sz * 2, sz * 2), style = Stroke(1.5f))
                }
                "dmx" -> {
                    val effectiveColor = liveColor ?: DmxPurple
                    // Beam cone from fixture to floor
                    if (liveColor != null && liveColor != Color.Black && fz > 100f) {
                        val floorP = project(fx, fy, 0f)
                        if (floorP != null) {
                            val spread = 300f * s  // beam spread on screen
                            val conePath = Path().apply {
                                moveTo(p.x, p.y)
                                lineTo(floorP.x - spread, floorP.y)
                                lineTo(floorP.x + spread, floorP.y)
                                close()
                            }
                            drawPath(conePath, liveColor.copy(alpha = 0.12f), style = Fill)
                            drawPath(conePath, liveColor.copy(alpha = 0.3f), style = Stroke(1f))
                        }
                    }
                    // Fixture body
                    val triSize = 10f * s
                    val triPath = Path().apply {
                        moveTo(p.x, p.y - triSize)
                        lineTo(p.x - triSize * 0.866f, p.y + triSize * 0.5f)
                        lineTo(p.x + triSize * 0.866f, p.y + triSize * 0.5f)
                        close()
                    }
                    drawPath(triPath, effectiveColor, style = Fill)
                    drawPath(triPath, Color.White.copy(alpha = 0.3f), style = Stroke(1f))
                    // Hot spot glow
                    drawCircle(effectiveColor.copy(alpha = 0.2f), 14f * s, p)
                }
                else -> {
                    val ledColor = liveColor ?: GreenOnline
                    drawCircle(ledColor, 6f * s, p)
                    drawCircle(Color.White.copy(alpha = 0.2f), 6f * s, p, style = Stroke(1f))
                    if (liveColor != null) drawCircle(liveColor.copy(alpha = 0.15f), 12f * s, p)
                }
            }

            // Selection highlight ring
            if (fixture.id == selectedFixtureId) {
                drawCircle(CyanSecondary.copy(alpha = 0.5f), 20f * s, p, style = Stroke(3f))
                drawCircle(CyanSecondary.copy(alpha = 0.15f), 24f * s, p)
            }

            // Label
            if (s > 0.12f && fixture.name.isNotBlank()) {
                val labelStyle = TextStyle(
                    color = NearWhite.copy(alpha = 0.7f),
                    fontSize = (10f * s).coerceIn(7f, 14f).sp
                )
                val labelResult = textMeasurer.measure(fixture.name, labelStyle, maxLines = 1, overflow = TextOverflow.Ellipsis)
                drawText(labelResult, topLeft = Offset(p.x - labelResult.size.width / 2f, p.y + 12f * s))
            }
        }

        // --- 5. Tracked objects (temporal/moving) — 3D wireframe box ---
        for (obj in objects) {
            if (!obj.temporal && obj.mobility != "moving") continue
            val pos = obj.transform.pos
            val ox = pos[0].toFloat(); val oy = pos[1].toFloat(); val oz = pos[2].toFloat()
            val s = scaleAt(ox, oy, oz)
            if (s <= 0f) continue
            val col = try { Color(android.graphics.Color.parseColor(obj.color ?: "#f472b6")) } catch (_: Exception) { Color(0xFFf472b6) }

            // Box dimensions from transform.scale: [width(X), height(Z), depth(Y)]
            val scl = obj.transform.scale
            val ow = (if (scl.isNotEmpty()) scl[0].toFloat() else 400f)
            val oh = (if (scl.size > 1) scl[1].toFloat() else 1700f)
            val od = (if (scl.size > 2) scl[2].toFloat() else 400f)

            // 8 corners of the box centered on (ox, oy, oz)
            val hw = ow / 2f; val hd = od / 2f
            val corners = arrayOf(
                project(ox - hw, oy - hd, oz),      project(ox + hw, oy - hd, oz),
                project(ox + hw, oy + hd, oz),      project(ox - hw, oy + hd, oz),
                project(ox - hw, oy - hd, oz + oh), project(ox + hw, oy - hd, oz + oh),
                project(ox + hw, oy + hd, oz + oh), project(ox - hw, oy + hd, oz + oh)
            )
            // Draw 12 edges
            val edges = listOf(0 to 1, 1 to 2, 2 to 3, 3 to 0,
                               4 to 5, 5 to 6, 6 to 7, 7 to 4,
                               0 to 4, 1 to 5, 2 to 6, 3 to 7)
            for ((a, b) in edges) {
                val pa = corners[a]; val pb = corners[b]
                if (pa != null && pb != null) {
                    drawLine(col.copy(alpha = 0.6f), pa, pb, strokeWidth = 1.5f)
                }
            }

            // Floor circle as ground marker
            val floorP = project(ox, oy, oz)
            if (floorP != null) {
                drawCircle(col.copy(alpha = 0.3f), 12f * s, floorP)
                drawCircle(col.copy(alpha = 0.6f), 12f * s, floorP, style = Stroke(2f))
            }

            // Label above box top
            val topCenter = project(ox, oy, oz + oh)
            val labelP = topCenter ?: floorP ?: continue
            val label = obj.name.ifBlank { "?" }
            val labelStyle = TextStyle(color = col, fontSize = (11f * s).coerceIn(8f, 16f).sp)
            val labelResult = textMeasurer.measure(label, labelStyle, maxLines = 1, overflow = TextOverflow.Ellipsis)
            drawText(labelResult, topLeft = Offset(labelP.x - labelResult.size.width / 2f, labelP.y - labelResult.size.height - 4f * s))
        }
    }
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

@Composable
private fun FixtureInfoCard(
    fixture: Fixture,
    layoutChild: LayoutChild?,
    liveData: JsonElement?,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier
) {
    val posX = layoutChild?.x ?: fixture.x
    val posY = layoutChild?.y ?: fixture.y
    val posZ = layoutChild?.z ?: fixture.z

    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.92f)
        ),
        shape = RoundedCornerShape(12.dp)
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            // Header row: name + type badge + dismiss
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.weight(1f)
                ) {
                    Text(
                        fixture.name.ifBlank { "Fixture #${fixture.id}" },
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onSurface
                    )
                    Spacer(Modifier.width(8.dp))
                    val (typeLabel, typeColor) = when (fixture.fixtureType) {
                        "dmx" -> "DMX" to DmxPurple
                        "camera" -> "Camera" to CyanSecondary
                        else -> "LED" to GreenOnline
                    }
                    SuggestionChip(
                        onClick = {},
                        label = { Text(typeLabel, style = MaterialTheme.typography.labelSmall) },
                        colors = SuggestionChipDefaults.suggestionChipColors(
                            containerColor = typeColor.copy(alpha = 0.15f),
                            labelColor = typeColor
                        ),
                        border = null
                    )
                }
                IconButton(onClick = onDismiss, modifier = Modifier.size(32.dp)) {
                    Icon(
                        Icons.Default.Close,
                        contentDescription = "Dismiss",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(18.dp)
                    )
                }
            }

            Spacer(Modifier.height(8.dp))

            // Position row
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                FixtureDetail("X", "${posX}mm")
                FixtureDetail("Y", "${posY}mm")
                FixtureDetail("Z", "${posZ}mm")
            }

            // DMX address if applicable
            if (fixture.fixtureType == "dmx" && fixture.dmxUniverse != null && fixture.dmxStartAddr != null) {
                Spacer(Modifier.height(4.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                    FixtureDetail("Universe", "${fixture.dmxUniverse}")
                    FixtureDetail("Address", "${fixture.dmxStartAddr}")
                    if (fixture.dmxChannelCount != null) {
                        FixtureDetail("Channels", "${fixture.dmxChannelCount}")
                    }
                }
            }

            // Live output (RGB + dimmer)
            if (liveData != null) {
                Spacer(Modifier.height(8.dp))
                val liveColor = parseLiveColor(liveData)
                val obj = try { liveData.jsonObject } catch (_: Exception) { null }
                val r = obj?.get("r")?.jsonPrimitive?.intOrNull ?: 0
                val g = obj?.get("g")?.jsonPrimitive?.intOrNull ?: 0
                val b = obj?.get("b")?.jsonPrimitive?.intOrNull ?: 0
                val dimmer = obj?.get("dimmer")?.jsonPrimitive?.intOrNull ?: 255
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    // Color swatch
                    if (liveColor != null) {
                        Canvas(modifier = Modifier.size(24.dp)) {
                            drawCircle(liveColor, radius = 12f)
                            drawCircle(Color.White.copy(alpha = 0.3f), radius = 12f, style = Stroke(1.5f))
                        }
                    }
                    FixtureDetail("RGB", "$r, $g, $b")
                    FixtureDetail("Dimmer", "$dimmer")
                }
            }
        }
    }
}

@Composable
private fun FixtureDetail(label: String, value: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(
            "$label: ",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        Text(
            value,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Medium,
            color = MaterialTheme.colorScheme.onSurface
        )
    }
}
