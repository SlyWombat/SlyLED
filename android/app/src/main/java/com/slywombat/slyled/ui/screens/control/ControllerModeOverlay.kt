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
import androidx.compose.ui.graphics.Brush
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.MyLocation
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
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

/**
 * Controller mode — full-screen overlay with compact crosshair,
 * pan/tilt readout, color/dimmer/strobe sliders, hold-to-calibrate.
 */
@Composable
fun ControllerModeOverlay(
    fixtureName: String,
    panRangeDeg: Float = 540f,
    tiltRangeDeg: Float = 270f,
    initialPanNorm: Float = 0.5f,
    initialTiltNorm: Float = 0.5f,
    panSign: Int = 1,
    tiltSign: Int = -1,
    connected: Boolean = true,
    onAim: (panNorm: Float, tiltNorm: Float) -> Unit,
    onChannelChange: (dimmer: Float, red: Float, green: Float, blue: Float, white: Float, strobe: Float) -> Unit,
    onDismiss: () -> Unit
) {
    val context = LocalContext.current
    // Intercept back gesture / swipe so the overlay doesn't close accidentally
    BackHandler { onDismiss() }

    val sensorManager = remember { context.getSystemService(Context.SENSOR_SERVICE) as SensorManager }
    val rotationSensor = remember { sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR) }

    var refAzimuth by remember { mutableFloatStateOf(0f) }
    var refPitch by remember { mutableFloatStateOf(0f) }
    var calibrated by remember { mutableStateOf(false) }
    var refPanNorm by remember { mutableFloatStateOf(initialPanNorm) }
    var refTiltNorm by remember { mutableFloatStateOf(initialTiltNorm) }
    var currentPanNorm by remember { mutableFloatStateOf(initialPanNorm) }
    var currentTiltNorm by remember { mutableFloatStateOf(initialTiltNorm) }
    var deltaAzimuth by remember { mutableFloatStateOf(0f) }
    var deltaPitch by remember { mutableFloatStateOf(0f) }
    var holdingCalibrate by remember { mutableStateOf(false) }
    // Ref for sensor callback — Compose state isn't visible inside DisposableEffect closures
    val holdingCalibrateRef = remember { java.util.concurrent.atomic.AtomicBoolean(false) }

    var dimmer by remember { mutableFloatStateOf(1f) }
    var red by remember { mutableFloatStateOf(0f) }
    var green by remember { mutableFloatStateOf(0f) }
    var blue by remember { mutableFloatStateOf(0f) }
    var white by remember { mutableFloatStateOf(1f) }
    var strobe by remember { mutableFloatStateOf(0f) }

    val panSensitivity = panSign * 0.5f / 90f
    // Negate tilt so phone-down = beam-down on inverted fixtures
    val tiltSensitivity = -tiltSign * 0.5f / 45f

    DisposableEffect(rotationSensor) {
        val listener = object : SensorEventListener {
            private val rotationMatrix = FloatArray(9)
            private val orientation = FloatArray(3)
            private var lastSendMs = 0L

            override fun onSensorChanged(event: SensorEvent) {
                // While holding calibrate: freeze everything — no display update, no sending
                if (holdingCalibrateRef.get()) return

                SensorManager.getRotationMatrixFromVector(rotationMatrix, event.values)
                SensorManager.getOrientation(rotationMatrix, orientation)
                val azimuth = Math.toDegrees(orientation[0].toDouble()).toFloat()
                val pitch = Math.toDegrees(orientation[1].toDouble()).toFloat()

                if (!calibrated) {
                    refAzimuth = azimuth
                    refPitch = pitch
                    refPanNorm = currentPanNorm
                    refTiltNorm = currentTiltNorm
                    calibrated = true
                }

                var dAz = azimuth - refAzimuth
                if (dAz > 180) dAz -= 360
                if (dAz < -180) dAz += 360
                deltaAzimuth = dAz
                deltaPitch = pitch - refPitch

                val panNorm = (refPanNorm + dAz * panSensitivity).coerceIn(0f, 1f)
                val tiltNorm = (refTiltNorm + deltaPitch * tiltSensitivity).coerceIn(0f, 1f)
                currentPanNorm = panNorm
                currentTiltNorm = tiltNorm

                val now = System.currentTimeMillis()
                if (now - lastSendMs >= 50) {
                    lastSendMs = now
                    onAim(panNorm, tiltNorm)
                }
            }

            override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
        }
        if (rotationSensor != null) {
            sensorManager.registerListener(listener, rotationSensor, SensorManager.SENSOR_DELAY_GAME)
        }
        onDispose { sensorManager.unregisterListener(listener) }
    }

    val displayPanDeg = currentPanNorm * panRangeDeg
    val displayTiltDeg = currentTiltNorm * tiltRangeDeg

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
                            Text("PAN", style = MaterialTheme.typography.labelSmall, color = Color(0xFF64748B))
                            Text("%.1f\u00b0".format(displayPanDeg),
                                style = MaterialTheme.typography.headlineSmall,
                                color = CyanSecondary, fontWeight = FontWeight.Bold)
                        }
                        Column {
                            Text("TILT", style = MaterialTheme.typography.labelSmall, color = Color(0xFF64748B))
                            Text("%.1f\u00b0".format(displayTiltDeg),
                                style = MaterialTheme.typography.headlineSmall,
                                color = CyanSecondary, fontWeight = FontWeight.Bold)
                        }
                    }
                    Spacer(Modifier.height(10.dp))
                    Button(
                        onClick = { },
                        shape = RoundedCornerShape(8.dp),
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(40.dp)
                            .pointerInput(Unit) {
                                awaitEachGesture {
                                    awaitFirstDown()
                                    holdingCalibrate = true
                                    holdingCalibrateRef.set(true)
                                    waitForUpOrCancellation()
                                    calibrated = false
                                    holdingCalibrate = false
                                    holdingCalibrateRef.set(false)
                                }
                            },
                        contentPadding = PaddingValues(horizontal = 12.dp),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = if (holdingCalibrate) CyanSecondary.copy(alpha = 0.3f)
                            else Color(0xFF1E293B)
                        )
                    ) {
                        Icon(Icons.Default.MyLocation, null, tint = CyanSecondary, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.width(6.dp))
                        Text(
                            if (holdingCalibrate) "Reposition..." else "Hold to Calibrate",
                            color = CyanSecondary, style = MaterialTheme.typography.labelMedium
                        )
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
                dimmer = v; onChannelChange(dimmer, red, green, blue, white, strobe)
            }
            ChannelSlider("White", white, Color(0xFFF8FAFC)) { v ->
                white = v; onChannelChange(dimmer, red, green, blue, white, strobe)
            }
            ChannelSlider("Strobe", strobe, Color(0xFFFBBF24)) { v ->
                strobe = v; onChannelChange(dimmer, red, green, blue, white, strobe)
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
                    // Auto-zero white when picking a color so RGB is visible
                    if (r > 0.01f || g > 0.01f || b > 0.01f) {
                        white = 0f
                    }
                    onChannelChange(dimmer, red, green, blue, white, strobe)
                }
            )

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
