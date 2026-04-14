package com.slywombat.slyled.ui.screens.control

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.MyLocation
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.slywombat.slyled.ui.theme.*
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.sin

/**
 * Pointer mode overlay — use phone gyroscope to aim a moving head.
 * Phone orientation maps to pan/tilt delta from a calibrated center.
 *
 * Usage: press "Pointer Mode" on a selected DMX fixture → this overlay appears.
 * Tilt/rotate the phone → pan/tilt values sent to the server at ~20 Hz.
 * Tap "Recenter" to calibrate current orientation as center.
 * Tap X to exit.
 */
@Composable
fun PointerModeOverlay(
    fixtureName: String,
    panRange: Pair<Float, Float> = 0f to 540f,
    tiltRange: Pair<Float, Float> = 0f to 270f,
    onAim: (pan: Float, tilt: Float) -> Unit,
    onDismiss: () -> Unit
) {
    val context = LocalContext.current
    val sensorManager = remember { context.getSystemService(Context.SENSOR_SERVICE) as SensorManager }
    val rotationSensor = remember { sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR) }

    // Reference orientation (set on "Recenter")
    var refAzimuth by remember { mutableFloatStateOf(0f) }
    var refPitch by remember { mutableFloatStateOf(0f) }
    var calibrated by remember { mutableStateOf(false) }

    // Current orientation deltas
    var deltaAzimuth by remember { mutableFloatStateOf(0f) }
    var deltaPitch by remember { mutableFloatStateOf(0f) }

    // Current pan/tilt output
    var currentPan by remember { mutableFloatStateOf((panRange.first + panRange.second) / 2) }
    var currentTilt by remember { mutableFloatStateOf((tiltRange.first + tiltRange.second) / 2) }

    // Sensor listener
    DisposableEffect(rotationSensor) {
        val listener = object : SensorEventListener {
            private val rotationMatrix = FloatArray(9)
            private val orientation = FloatArray(3)
            private var lastSendMs = 0L

            override fun onSensorChanged(event: SensorEvent) {
                SensorManager.getRotationMatrixFromVector(rotationMatrix, event.values)
                SensorManager.getOrientation(rotationMatrix, orientation)

                val azimuth = Math.toDegrees(orientation[0].toDouble()).toFloat()  // -180..180
                val pitch = Math.toDegrees(orientation[1].toDouble()).toFloat()    // -90..90

                if (!calibrated) {
                    refAzimuth = azimuth
                    refPitch = pitch
                    calibrated = true
                }

                // Delta from reference
                var dAz = azimuth - refAzimuth
                if (dAz > 180) dAz -= 360
                if (dAz < -180) dAz += 360
                deltaAzimuth = dAz
                deltaPitch = pitch - refPitch

                // Map to pan/tilt range
                // Sensitivity: ±90° phone rotation = full range
                val panCenter = (panRange.first + panRange.second) / 2
                val panHalf = (panRange.second - panRange.first) / 2
                val tiltCenter = (tiltRange.first + tiltRange.second) / 2
                val tiltHalf = (tiltRange.second - tiltRange.first) / 2

                val pan = (panCenter + (dAz / 90f) * panHalf)
                    .coerceIn(panRange.first, panRange.second)
                val tilt = (tiltCenter + (deltaPitch / 45f) * tiltHalf)
                    .coerceIn(tiltRange.first, tiltRange.second)

                currentPan = pan
                currentTilt = tilt

                // Rate limit to ~20 Hz
                val now = System.currentTimeMillis()
                if (now - lastSendMs >= 50) {
                    lastSendMs = now
                    onAim(pan, tilt)
                }
            }

            override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
        }

        if (rotationSensor != null) {
            sensorManager.registerListener(listener, rotationSensor, SensorManager.SENSOR_DELAY_GAME)
        }

        onDispose {
            sensorManager.unregisterListener(listener)
        }
    }

    // Full-screen overlay
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black.copy(alpha = 0.85f))
    ) {
        // Crosshair canvas
        Canvas(modifier = Modifier.fillMaxSize()) {
            val cx = size.width / 2
            val cy = size.height / 2

            // Outer ring
            drawCircle(
                color = CyanSecondary.copy(alpha = 0.2f),
                radius = 120.dp.toPx(),
                center = Offset(cx, cy),
                style = Stroke(1.dp.toPx())
            )

            // Middle ring
            drawCircle(
                color = CyanSecondary.copy(alpha = 0.3f),
                radius = 60.dp.toPx(),
                center = Offset(cx, cy),
                style = Stroke(1.dp.toPx())
            )

            // Dead zone ring (inner 5°)
            drawCircle(
                color = Color(0xFF334155),
                radius = 15.dp.toPx(),
                center = Offset(cx, cy),
                style = Stroke(1.dp.toPx())
            )

            // Crosshair lines
            val lineLen = 140.dp.toPx()
            drawLine(CyanSecondary.copy(alpha = 0.4f), Offset(cx - lineLen, cy), Offset(cx + lineLen, cy), 1.dp.toPx())
            drawLine(CyanSecondary.copy(alpha = 0.4f), Offset(cx, cy - lineLen), Offset(cx, cy + lineLen), 1.dp.toPx())

            // Pointer dot — position reflects current delta
            val maxOffset = 120.dp.toPx()
            val dotX = cx + (deltaAzimuth / 90f) * maxOffset
            val dotY = cy + (deltaPitch / 45f) * maxOffset
            // Glow
            drawCircle(CyanSecondary.copy(alpha = 0.3f), 16.dp.toPx(), Offset(dotX, dotY))
            // Dot
            drawCircle(CyanSecondary, 6.dp.toPx(), Offset(dotX, dotY))
            // Trail line from center
            drawLine(
                CyanSecondary.copy(alpha = 0.5f),
                Offset(cx, cy),
                Offset(dotX, dotY),
                2.dp.toPx(),
                cap = StrokeCap.Round
            )
        }

        // Top bar: fixture name + close
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Text(
                    "POINTER MODE",
                    style = MaterialTheme.typography.labelSmall,
                    color = CyanSecondary,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 1.5.sp
                )
                Text(
                    fixtureName,
                    style = MaterialTheme.typography.titleMedium,
                    color = Color.White
                )
            }
            IconButton(onClick = onDismiss) {
                Icon(Icons.Default.Close, "Exit pointer mode", tint = Color.White)
            }
        }

        // Bottom: pan/tilt readout + recenter button
        Column(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            // Pan/Tilt readout
            Card(
                shape = RoundedCornerShape(12.dp),
                colors = CardDefaults.cardColors(containerColor = Color(0xFF0F172A).copy(alpha = 0.9f))
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 20.dp, vertical = 12.dp),
                    horizontalArrangement = Arrangement.spacedBy(24.dp)
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("PAN", style = MaterialTheme.typography.labelSmall, color = Color(0xFF64748B))
                        Text(
                            "%.1f°".format(currentPan),
                            style = MaterialTheme.typography.titleLarge,
                            color = CyanSecondary,
                            fontWeight = FontWeight.Bold
                        )
                    }
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("TILT", style = MaterialTheme.typography.labelSmall, color = Color(0xFF64748B))
                        Text(
                            "%.1f°".format(currentTilt),
                            style = MaterialTheme.typography.titleLarge,
                            color = CyanSecondary,
                            fontWeight = FontWeight.Bold
                        )
                    }
                }
            }

            Spacer(modifier = Modifier.height(16.dp))

            // Recenter button
            Button(
                onClick = {
                    calibrated = false  // Next sensor reading becomes new reference
                },
                shape = CircleShape,
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF1E293B))
            ) {
                Icon(Icons.Default.MyLocation, contentDescription = null, tint = CyanSecondary)
                Spacer(modifier = Modifier.width(8.dp))
                Text("Recenter", color = CyanSecondary)
            }

            Spacer(modifier = Modifier.height(8.dp))

            Text(
                "Point phone where you want the light. Tap Recenter to calibrate.",
                style = MaterialTheme.typography.bodySmall,
                color = Color(0xFF64748B)
            )
        }
    }
}
