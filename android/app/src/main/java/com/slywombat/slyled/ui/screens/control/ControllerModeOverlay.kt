package com.slywombat.slyled.ui.screens.control

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.waitForUpOrCancellation
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.MyLocation
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.slywombat.slyled.ui.theme.*
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Controller mode — full-screen overlay with compact crosshair,
 * orientation readout, color/dimmer sliders, hold-to-calibrate.
 *
 * Sends raw device orientation (roll, pitch, yaw) to the server via
 * the unified /api/mover-control endpoints. The server handles all
 * pan/tilt computation, calibration reference, and DMX output.
 */
@Composable
fun ControllerModeOverlay(
    fixtureName: String,
    connected: Boolean = true,
    // #479 — live mover-control status. Null when no claim is yet
    // visible from the server's perspective (e.g. first 2 s before the
    // ControlViewModel poll lands its first response).
    statusClaim: com.slywombat.slyled.data.model.MoverControlClaim? = null,
    engineRunning: Boolean = true,
    onOrient: (roll: Float, pitch: Float, yaw: Float, quat: FloatArray) -> Unit,
    onCalibrateStart: (roll: Float, pitch: Float, yaw: Float) -> Unit,
    onCalibrateEnd: (roll: Float, pitch: Float, yaw: Float) -> Unit,
    onColorChange: (r: Int, g: Int, b: Int, dimmer: Int?) -> Unit,
    onFlash: (on: Boolean) -> Unit,
    onSmoothing: (smoothing: Float) -> Unit,
    onDismiss: () -> Unit
) {
    val context = LocalContext.current
    // Intercept back gesture / swipe so the overlay doesn't close accidentally
    BackHandler { onDismiss() }

    val sensorManager = remember { context.getSystemService(Context.SENSOR_SERVICE) as SensorManager }
    val rotationSensor = remember { sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR) }

    var holdingCalibrate by remember { mutableStateOf(false) }
    // Ref for sensor callback — Compose state isn't visible inside DisposableEffect closures
    val holdingCalibrateRef = remember { java.util.concurrent.atomic.AtomicBoolean(false) }

    // Current orientation for display
    var currentAzimuth by remember { mutableFloatStateOf(0f) }
    var currentPitch by remember { mutableFloatStateOf(0f) }
    // Delta from calibration ref for crosshair display
    var deltaAzimuth by remember { mutableFloatStateOf(0f) }
    var deltaPitch by remember { mutableFloatStateOf(0f) }
    var refAzimuth by remember { mutableFloatStateOf(0f) }
    var refPitch by remember { mutableFloatStateOf(0f) }
    var hasRef by remember { mutableStateOf(false) }

    // Latest orientation for calibrate button callbacks
    val latestRoll = remember { java.util.concurrent.atomic.AtomicReference(0f) }
    val latestPitch = remember { java.util.concurrent.atomic.AtomicReference(0f) }
    val latestYaw = remember { java.util.concurrent.atomic.AtomicReference(0f) }

    var dimmer by remember { mutableFloatStateOf(1f) }
    var red by remember { mutableFloatStateOf(1f) }
    var green by remember { mutableFloatStateOf(1f) }
    var blue by remember { mutableFloatStateOf(1f) }
    var smoothing by remember { mutableFloatStateOf(0.15f) }

    // #755 BUG-D — 100 ms lift debounce for hold-to-calibrate so brief
    // unintentional finger lifts (operator drift, hand tremor) do not fire
    // a premature /api/mover-control/calibrate-end + release cascade.
    val calibrateScope = rememberCoroutineScope()
    val pendingCalibrateEnd = remember { java.util.concurrent.atomic.AtomicReference<Job?>(null) }

    DisposableEffect(rotationSensor) {
        val listener = object : SensorEventListener {
            private val rotationMatrix = FloatArray(9)
            private val orientation = FloatArray(3)
            // Android's getQuaternionFromVector returns [w, x, y, z] — matches wire order.
            private val quat = FloatArray(4)
            private var lastSendMs = 0L

            override fun onSensorChanged(event: SensorEvent) {
                SensorManager.getRotationMatrixFromVector(rotationMatrix, event.values)
                SensorManager.getOrientation(rotationMatrix, orientation)
                val azimuth = Math.toDegrees(orientation[0].toDouble()).toFloat()
                val pitch = Math.toDegrees(orientation[1].toDouble()).toFloat()
                val roll = Math.toDegrees(orientation[2].toDouble()).toFloat()

                SensorManager.getQuaternionFromVector(quat, event.values)

                // Store latest for calibrate callbacks
                latestRoll.set(roll)
                latestPitch.set(pitch)
                latestYaw.set(azimuth)

                // While holding calibrate: freeze display — no movement, no sending
                if (holdingCalibrateRef.get()) return

                currentAzimuth = azimuth
                currentPitch = pitch

                // Update crosshair display delta
                if (!hasRef) {
                    refAzimuth = azimuth
                    refPitch = pitch
                    hasRef = true
                }
                var dAz = azimuth - refAzimuth
                if (dAz > 180) dAz -= 360
                if (dAz < -180) dAz += 360
                deltaAzimuth = dAz
                deltaPitch = pitch - refPitch

                val now = System.currentTimeMillis()
                if (now - lastSendMs >= 50) {
                    lastSendMs = now
                    onOrient(roll, pitch, azimuth, quat.copyOf())
                }
            }

            override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
        }
        if (rotationSensor != null) {
            sensorManager.registerListener(listener, rotationSensor, SensorManager.SENSOR_DELAY_GAME)
        }
        onDispose { sensorManager.unregisterListener(listener) }
    }

    // Guarantee release+blackout fires whenever this overlay leaves composition
    // (tab switch, process recomposition, etc.) — #483. onDismiss is idempotent
    // on the viewmodel, so a stacking Close-button + dispose call is harmless.
    DisposableEffect(Unit) {
        onDispose { onDismiss() }
    }

    // Full-screen overlay
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFF0A0E1A))
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 20.dp)
                .verticalScroll(rememberScrollState())
        ) {
            Spacer(Modifier.height(48.dp))

            // Header
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Text("CONTROLLER", style = MaterialTheme.typography.labelSmall,
                            color = CyanSecondary, fontWeight = FontWeight.Bold, letterSpacing = 1.5.sp)
                        if (!connected) {
                            Text("DISCONNECTED", style = MaterialTheme.typography.labelSmall,
                                color = Color(0xFFEF4444), fontWeight = FontWeight.Bold)
                        }
                        if (holdingCalibrate) {
                            Text("CALIBRATING", style = MaterialTheme.typography.labelSmall,
                                color = Color(0xFFFBBF24), fontWeight = FontWeight.Bold)
                        }
                    }
                    Text(fixtureName, style = MaterialTheme.typography.titleMedium,
                        color = Color.White, fontWeight = FontWeight.Bold)
                }
                IconButton(onClick = onDismiss) {
                    Icon(Icons.Default.Close, "Exit", tint = Color(0xFF64748B))
                }
            }

            // #479 — live status row (shared with PointerModeOverlay via
            // MoverStatusRow.kt). Green dot when fresh + state, dim amber
            // when stale/no-data, red when engine isn't running.
            Spacer(Modifier.height(8.dp))
            MoverStatusRow(statusClaim = statusClaim, engineRunning = engineRunning)

            Spacer(Modifier.height(16.dp))

            // Crosshair + readout + calibrate row
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(16.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                // Compact crosshair
                Box(
                    modifier = Modifier
                        .size(110.dp)
                        .background(Color(0xFF1E293B), RoundedCornerShape(12.dp))
                ) {
                    Canvas(modifier = Modifier.fillMaxSize().padding(6.dp)) {
                        val cx = size.width / 2
                        val cy = size.height / 2
                        val r = size.width / 2 - 2.dp.toPx()
                        drawCircle(CyanSecondary.copy(alpha = 0.2f), r, Offset(cx, cy), style = Stroke(1.dp.toPx()))
                        drawLine(CyanSecondary.copy(alpha = 0.25f), Offset(cx - r, cy), Offset(cx + r, cy), 0.5f.dp.toPx())
                        drawLine(CyanSecondary.copy(alpha = 0.25f), Offset(cx, cy - r), Offset(cx, cy + r), 0.5f.dp.toPx())
                        val dotX = cx + (deltaAzimuth / 90f) * r
                        val dotY = cy + (deltaPitch / 45f) * r
                        drawCircle(CyanSecondary.copy(alpha = 0.3f), 10.dp.toPx(), Offset(dotX, dotY))
                        drawCircle(CyanSecondary, 4.dp.toPx(), Offset(dotX, dotY))
                        drawLine(CyanSecondary.copy(alpha = 0.4f), Offset(cx, cy), Offset(dotX, dotY), 1.dp.toPx(), cap = StrokeCap.Round)
                    }
                }

                // Readout + calibrate
                Column(modifier = Modifier.weight(1f)) {
                    Row(horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                        Column {
                            Text("YAW", style = MaterialTheme.typography.labelSmall, color = Color(0xFF64748B))
                            Text("%.1f\u00b0".format(currentAzimuth),
                                style = MaterialTheme.typography.headlineSmall,
                                color = CyanSecondary, fontWeight = FontWeight.Bold)
                        }
                        Column {
                            Text("PITCH", style = MaterialTheme.typography.labelSmall, color = Color(0xFF64748B))
                            Text("%.1f\u00b0".format(currentPitch),
                                style = MaterialTheme.typography.headlineSmall,
                                color = CyanSecondary, fontWeight = FontWeight.Bold)
                        }
                    }
                    Spacer(Modifier.height(10.dp))
                    // #755 BUG-D — large hit area + drift-tolerant gesture
                    // (initial down is consumed so the parent verticalScroll
                    // can't claim it during a vertical drift) + 100 ms lift
                    // debounce so brief finger lifts don't fire calibrate-end.
                    // Yellow halo while held = strong visual feedback that
                    // the press is captured even past the visible button.
                    Box(
                        contentAlignment = Alignment.Center,
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(96.dp)
                            .clip(RoundedCornerShape(14.dp))
                            .background(
                                if (holdingCalibrate)
                                    Color(0xFFFBBF24).copy(alpha = 0.22f)
                                else Color.Transparent
                            )
                            .pointerInput(Unit) {
                                awaitEachGesture {
                                    val firstDown = awaitFirstDown(requireUnconsumed = false)
                                    firstDown.consume()  // claim ownership; parent scroll can't steal it

                                    val pending = pendingCalibrateEnd.getAndSet(null)
                                    if (pending != null && pending.isActive) {
                                        // Re-press inside 100 ms debounce — cancel pending end
                                        pending.cancel()
                                    } else {
                                        // Fresh hold start
                                        holdingCalibrate = true
                                        holdingCalibrateRef.set(true)
                                        onCalibrateStart(
                                            latestRoll.get(),
                                            latestPitch.get(),
                                            latestYaw.get()
                                        )
                                    }

                                    // Track pointer; consume every event to keep ownership
                                    var stillPressed = true
                                    while (stillPressed) {
                                        val event = awaitPointerEvent()
                                        val change = event.changes.firstOrNull { it.id == firstDown.id }
                                        change?.consume()
                                        stillPressed = change?.pressed == true
                                    }

                                    // Schedule debounced calibrate-end. A fresh down within
                                    // 100 ms will cancel this and continue the same hold.
                                    val endJob = calibrateScope.launch {
                                        delay(100L)
                                        onCalibrateEnd(
                                            latestRoll.get(),
                                            latestPitch.get(),
                                            latestYaw.get()
                                        )
                                        hasRef = false
                                        holdingCalibrate = false
                                        holdingCalibrateRef.set(false)
                                        pendingCalibrateEnd.set(null)
                                    }
                                    pendingCalibrateEnd.set(endJob)
                                }
                            }
                    ) {
                        // Inner visible button (small, centred) — purely visual
                        Box(
                            contentAlignment = Alignment.Center,
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(40.dp)
                                .clip(RoundedCornerShape(8.dp))
                                .background(
                                    if (holdingCalibrate)
                                        Color(0xFFFBBF24).copy(alpha = 0.85f)
                                    else Color(0xFF1E293B)
                                )
                        ) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Icon(
                                    Icons.Default.MyLocation, null,
                                    tint = if (holdingCalibrate) Color(0xFF0F0F10)
                                           else CyanSecondary,
                                    modifier = Modifier.size(16.dp)
                                )
                                Spacer(Modifier.width(6.dp))
                                Text(
                                    if (holdingCalibrate) "Hold steady…" else "Hold to Calibrate",
                                    color = if (holdingCalibrate) Color(0xFF0F0F10)
                                            else CyanSecondary,
                                    style = MaterialTheme.typography.labelMedium,
                                    fontWeight = if (holdingCalibrate) FontWeight.Bold
                                                 else FontWeight.Normal
                                )
                            }
                        }
                    }
                }
            }

            Spacer(Modifier.height(24.dp))
            HorizontalDivider(color = Color(0xFF1E293B))
            Spacer(Modifier.height(16.dp))

            // Channel sliders
            Text("OUTPUT", style = MaterialTheme.typography.labelSmall,
                color = Color(0xFF64748B), fontWeight = FontWeight.Bold, letterSpacing = 1.sp)
            Spacer(Modifier.height(12.dp))

            ChannelSlider("Dimmer", dimmer, Color.White) { v ->
                dimmer = v
                onColorChange(
                    (red * 255).toInt(), (green * 255).toInt(), (blue * 255).toInt(),
                    (dimmer * 255).toInt()
                )
            }

            Spacer(Modifier.height(16.dp))

            // Color wheel
            Text("COLOR", style = MaterialTheme.typography.labelSmall,
                color = Color(0xFF64748B), fontWeight = FontWeight.Bold, letterSpacing = 1.sp)
            Spacer(Modifier.height(8.dp))

            // Selected color preview
            val selectedColor = Color(red, green, blue)
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(24.dp)
                    .background(selectedColor, RoundedCornerShape(6.dp))
            )
            Spacer(Modifier.height(8.dp))

            // Hue wheel — tap to pick color
            ColorWheelPicker(
                currentRed = red,
                currentGreen = green,
                currentBlue = blue,
                onColorSelected = { r, g, b ->
                    red = r; green = g; blue = b
                    onColorChange(
                        (red * 255).toInt(), (green * 255).toInt(), (blue * 255).toInt(),
                        (dimmer * 255).toInt()
                    )
                }
            )

            Spacer(Modifier.height(20.dp))
            HorizontalDivider(color = Color(0xFF1E293B))
            Spacer(Modifier.height(16.dp))

            // Flash (press-and-hold strobe) — #482
            var flashDown by remember { mutableStateOf(false) }
            Box(
                contentAlignment = Alignment.Center,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(52.dp)
                    .clip(RoundedCornerShape(10.dp))
                    .background(
                        if (flashDown) Color(0xFFFBBF24)
                        else Color(0xFF1E293B)
                    )
                    .pointerInput(Unit) {
                        awaitEachGesture {
                            awaitFirstDown()
                            flashDown = true
                            onFlash(true)
                            waitForUpOrCancellation()
                            flashDown = false
                            onFlash(false)
                        }
                    }
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        Icons.Default.Bolt, null,
                        tint = if (flashDown) Color.Black else Color(0xFFFBBF24),
                        modifier = Modifier.size(20.dp)
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        "Hold for Strobe",
                        color = if (flashDown) Color.Black else Color(0xFFFBBF24),
                        style = MaterialTheme.typography.labelLarge,
                        fontWeight = FontWeight.Bold
                    )
                }
            }

            Spacer(Modifier.height(16.dp))

            // Smoothing slider — #481 (EMA factor; higher = snappier, lower = smoother).
            Text("SMOOTHING", style = MaterialTheme.typography.labelSmall,
                color = Color(0xFF64748B), fontWeight = FontWeight.Bold, letterSpacing = 1.sp)
            Spacer(Modifier.height(4.dp))
            Row(
                modifier = Modifier.fillMaxWidth().height(40.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text("Smooth", style = MaterialTheme.typography.bodySmall,
                    color = Color(0xFF94A3B8), modifier = Modifier.width(56.dp))
                Slider(
                    value = smoothing,
                    onValueChange = {
                        smoothing = it
                        onSmoothing(it)
                    },
                    valueRange = 0.05f..1.0f,
                    modifier = Modifier.weight(1f),
                    colors = SliderDefaults.colors(
                        thumbColor = CyanSecondary,
                        activeTrackColor = CyanSecondary
                    )
                )
                Text("%.2f".format(smoothing),
                    style = MaterialTheme.typography.bodySmall,
                    color = Color(0xFF94A3B8), modifier = Modifier.width(44.dp))
            }

            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun ChannelSlider(
    label: String,
    value: Float,
    color: Color,
    onValueChange: (Float) -> Unit
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(40.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(label, style = MaterialTheme.typography.bodySmall,
            color = Color(0xFF94A3B8), modifier = Modifier.width(60.dp))
        Slider(
            value = value,
            onValueChange = onValueChange,
            modifier = Modifier.weight(1f),
            colors = SliderDefaults.colors(thumbColor = color, activeTrackColor = color)
        )
        Text("${(value * 255).toInt()}", style = MaterialTheme.typography.bodySmall,
            color = Color(0xFF94A3B8), modifier = Modifier.width(32.dp))
    }
}

/**
 * HSV color wheel — tap/drag to pick a color.
 * Draws a circular hue ring with saturation from center to edge.
 */
@Composable
private fun ColorWheelPicker(
    currentRed: Float,
    currentGreen: Float,
    currentBlue: Float,
    onColorSelected: (r: Float, g: Float, b: Float) -> Unit
) {
    val wheelSize = 180.dp

    Box(
        modifier = Modifier.fillMaxWidth(),
        contentAlignment = Alignment.Center
    ) {
        Canvas(
            modifier = Modifier
                .size(wheelSize)
                .pointerInput(Unit) {
                    awaitEachGesture {
                        val down = awaitFirstDown()
                        handleColorTouch(down.position.x, down.position.y,
                            size.width.toFloat(), size.height.toFloat(), onColorSelected)
                        do {
                            val event = awaitPointerEvent()
                            val pos = event.changes.firstOrNull()?.position ?: break
                            event.changes.forEach { it.consume() }
                            handleColorTouch(pos.x, pos.y,
                                size.width.toFloat(), size.height.toFloat(), onColorSelected)
                        } while (event.changes.any { it.pressed })
                    }
                }
        ) {
            val cx = size.width / 2
            val cy = size.height / 2
            val radius = size.width / 2

            // Draw hue/saturation wheel pixel by pixel using drawCircle for efficiency
            // Use concentric rings at different hues
            val steps = 360
            val satSteps = 8
            for (s in satSteps downTo 1) {
                val sat = s.toFloat() / satSteps
                val ringRadius = radius * sat
                val ringWidth = radius / satSteps + 1f
                for (h in 0 until steps) {
                    val hue = h.toFloat()
                    val hsv = floatArrayOf(hue, sat, 1f)
                    val argb = android.graphics.Color.HSVToColor(hsv)
                    val c = Color(argb)
                    val angle = Math.toRadians(hue.toDouble())
                    val x = cx + (ringRadius * kotlin.math.cos(angle)).toFloat()
                    val y = cy - (ringRadius * kotlin.math.sin(angle)).toFloat()
                    drawCircle(c, ringWidth / 2, Offset(x, y))
                }
            }

            // Center white dot
            drawCircle(Color.White, radius / satSteps, Offset(cx, cy))

            // Selection indicator — show current color position
            val hsv = floatArrayOf(0f, 0f, 0f)
            android.graphics.Color.RGBToHSV(
                (currentRed * 255).toInt(), (currentGreen * 255).toInt(), (currentBlue * 255).toInt(), hsv
            )
            if (hsv[1] > 0.05f || (currentRed > 0.05f || currentGreen > 0.05f || currentBlue > 0.05f)) {
                val selAngle = Math.toRadians(hsv[0].toDouble())
                val selR = radius * hsv[1]
                val selX = cx + (selR * kotlin.math.cos(selAngle)).toFloat()
                val selY = cy - (selR * kotlin.math.sin(selAngle)).toFloat()
                drawCircle(Color.White, 8.dp.toPx(), Offset(selX, selY), style = Stroke(2.dp.toPx()))
                drawCircle(Color.Black.copy(alpha = 0.5f), 6.dp.toPx(), Offset(selX, selY), style = Stroke(1.dp.toPx()))
            }
        }
    }
}

private fun handleColorTouch(
    touchX: Float, touchY: Float,
    width: Float, height: Float,
    onColorSelected: (r: Float, g: Float, b: Float) -> Unit
) {
    val cx = width / 2
    val cy = height / 2
    val dx = touchX - cx
    val dy = -(touchY - cy)  // flip Y for standard math coordinates
    val dist = kotlin.math.sqrt(dx * dx + dy * dy)
    val radius = width / 2

    if (dist > radius * 1.1f) return  // ignore touches outside wheel

    val hue = (Math.toDegrees(kotlin.math.atan2(dy.toDouble(), dx.toDouble())).toFloat() + 360) % 360
    val sat = (dist / radius).coerceIn(0f, 1f)
    val argb = android.graphics.Color.HSVToColor(floatArrayOf(hue, sat, 1f))
    val r = android.graphics.Color.red(argb) / 255f
    val g = android.graphics.Color.green(argb) / 255f
    val b = android.graphics.Color.blue(argb) / 255f
    onColorSelected(r, g, b)
}
