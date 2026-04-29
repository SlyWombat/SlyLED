package com.slywombat.slyled.ui.screens.control

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.waitForUpOrCancellation
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.MyLocation
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.slywombat.slyled.data.repository.UserPosition
import com.slywombat.slyled.ui.theme.*
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.sin

/**
 * #427 — Pointer mode. Phone acts as a laser pointer in stage space.
 *
 * Math:
 *   Android's TYPE_ROTATION_VECTOR + getRotationMatrixFromVector returns a
 *   3x3 rotation matrix that transforms device-frame vectors into the
 *   East-North-Up world frame. The phone's "pointer" direction is the
 *   +Y device axis (top edge of the phone). Multiplying R * [0,1,0] gives
 *   the world-frame forward vector.
 *
 *   Stage frame is rotated about the world up axis by an unknown
 *   heading offset (the operator's "stage forward" isn't aligned with
 *   magnetic north). The hold-to-calibrate gesture captures the current
 *   forward yaw and stores it as `headingOffsetRad`; subsequent samples
 *   rotate forward by -headingOffsetRad to land in stage coords.
 *
 *   Floor intersection: with operator at (ux, uy, uz) and forward
 *   (fx, fy, fz), solve uz + t * fz = 0 → t = -uz / fz. Skip when
 *   fz >= 0 (phone aimed level/up — no intersection on the floor).
 */
@Composable
fun PointerModeOverlay(
    fixtureName: String,
    connected: Boolean = true,
    statusClaim: com.slywombat.slyled.data.model.MoverControlClaim? = null,
    engineRunning: Boolean = true,
    userPosition: UserPosition,
    onSetUserPosition: (xMm: Float, yMm: Float, zMm: Float) -> Unit,
    onAimTarget: (targetX: Double, targetY: Double, targetZ: Double) -> Unit,
    onDismiss: () -> Unit,
) {
    val context = LocalContext.current
    BackHandler { onDismiss() }

    val sensorManager = remember { context.getSystemService(Context.SENSOR_SERVICE) as SensorManager }
    val rotationSensor = remember { sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR) }

    var holdingCalibrate by remember { mutableStateOf(false) }
    val holdingCalibrateRef = remember { java.util.concurrent.atomic.AtomicBoolean(false) }

    // Heading offset captured by the calibrate gesture: world-yaw of the
    // phone's forward vector at the moment the operator presses the
    // button while pointing at "stage forward" from where they stand.
    var headingOffsetRad by remember { mutableFloatStateOf(0f) }
    var hasHeading by remember { mutableStateOf(false) }
    val headingOffsetRef = remember { java.util.concurrent.atomic.AtomicReference(0f) }
    val hasHeadingRef = remember { java.util.concurrent.atomic.AtomicBoolean(false) }

    // Latest forward vector in world frame (for the calibrate-press capture
    // — we read it at the moment the press lifts so the operator's heading
    // is locked at the apex of their aim).
    val latestWorldForward = remember { java.util.concurrent.atomic.AtomicReference(floatArrayOf(0f, 1f, 0f)) }

    // Display state for the floor target (mm).
    var lastTargetX by remember { mutableFloatStateOf(0f) }
    var lastTargetY by remember { mutableFloatStateOf(0f) }
    var hasTarget by remember { mutableStateOf(false) }

    // User-position editor state — populated from the persistent prefs.
    var userXText by remember(userPosition) { mutableStateOf(userPosition.xMm.toInt().toString()) }
    var userYText by remember(userPosition) { mutableStateOf(userPosition.yMm.toInt().toString()) }
    var userZText by remember(userPosition) { mutableStateOf(userPosition.zMm.toInt().toString()) }

    DisposableEffect(rotationSensor) {
        val listener = object : SensorEventListener {
            private val rotationMatrix = FloatArray(9)
            private var lastSendMs = 0L

            override fun onSensorChanged(event: SensorEvent) {
                SensorManager.getRotationMatrixFromVector(rotationMatrix, event.values)

                // World-frame forward = R * [0,1,0]. Column-major in
                // SensorManager's matrix: matrix[1], matrix[4], matrix[7].
                val fxWorld = rotationMatrix[1]
                val fyWorld = rotationMatrix[4]
                val fzWorld = rotationMatrix[7]
                latestWorldForward.set(floatArrayOf(fxWorld, fyWorld, fzWorld))

                if (holdingCalibrateRef.get()) return
                if (!hasHeadingRef.get()) return  // wait for first calibrate

                // Rotate world forward by -headingOffsetRad about world Z
                // (up) to land in stage X/Y. Z stays the same — both
                // frames share "up".
                val ho = headingOffsetRef.get()
                val cosH = cos(-ho)
                val sinH = sin(-ho)
                val fxStage = fxWorld * cosH - fyWorld * sinH
                val fyStage = fxWorld * sinH + fyWorld * cosH
                val fzStage = fzWorld

                // Throttle to ~20 Hz, like Controller mode.
                val now = System.currentTimeMillis()
                if (now - lastSendMs < 50) return
                lastSendMs = now

                val target = computeFloorIntersection(
                    userPosition.xMm, userPosition.yMm, userPosition.zMm,
                    fxStage, fyStage, fzStage
                )
                if (target != null) {
                    lastTargetX = target.first
                    lastTargetY = target.second
                    hasTarget = true
                    onAimTarget(target.first.toDouble(), target.second.toDouble(), 0.0)
                } else {
                    hasTarget = false
                }
            }

            override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
        }
        if (rotationSensor != null) {
            sensorManager.registerListener(listener, rotationSensor, SensorManager.SENSOR_DELAY_GAME)
        }
        onDispose { sensorManager.unregisterListener(listener) }
    }

    // Guarantee release+blackout fires whenever this overlay leaves
    // composition (#483, mirrored from ControllerModeOverlay).
    DisposableEffect(Unit) { onDispose { onDismiss() } }

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
                        Text("POINTER", style = MaterialTheme.typography.labelSmall,
                            color = CyanSecondary, fontWeight = FontWeight.Bold,
                            letterSpacing = 1.5.sp)
                        if (!connected) {
                            Text("DISCONNECTED", style = MaterialTheme.typography.labelSmall,
                                color = Color(0xFFEF4444), fontWeight = FontWeight.Bold)
                        }
                        if (holdingCalibrate) {
                            Text("CALIBRATING", style = MaterialTheme.typography.labelSmall,
                                color = Color(0xFFFBBF24), fontWeight = FontWeight.Bold)
                        }
                        if (!hasHeading && !holdingCalibrate) {
                            Text("UNCALIBRATED", style = MaterialTheme.typography.labelSmall,
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

            Spacer(Modifier.height(8.dp))
            MoverStatusRow(statusClaim = statusClaim, engineRunning = engineRunning)

            Spacer(Modifier.height(16.dp))

            // Aim readout — shows the floor target the overlay is currently
            // streaming to the server. Friendly hint when the phone is
            // pointed level or up so the operator knows why the beam isn't
            // moving.
            Card(
                colors = CardDefaults.cardColors(containerColor = Color(0xFF1E293B)),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text("FLOOR TARGET", style = MaterialTheme.typography.labelSmall,
                        color = Color(0xFF64748B), fontWeight = FontWeight.Bold,
                        letterSpacing = 1.sp)
                    Spacer(Modifier.height(8.dp))
                    if (!hasHeading) {
                        Text(
                            "Hold the calibrate button while pointing at stage forward, " +
                            "then release.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = Color(0xFFFBBF24)
                        )
                    } else if (!hasTarget) {
                        Text(
                            "Aim the phone down at the floor — the beam follows the tip.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = Color(0xFFFBBF24)
                        )
                    } else {
                        Row(horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                            Column {
                                Text("X", style = MaterialTheme.typography.labelSmall,
                                    color = Color(0xFF64748B))
                                Text("${lastTargetX.toInt()} mm",
                                    style = MaterialTheme.typography.headlineSmall,
                                    color = CyanSecondary, fontWeight = FontWeight.Bold)
                            }
                            Column {
                                Text("Y", style = MaterialTheme.typography.labelSmall,
                                    color = Color(0xFF64748B))
                                Text("${lastTargetY.toInt()} mm",
                                    style = MaterialTheme.typography.headlineSmall,
                                    color = CyanSecondary, fontWeight = FontWeight.Bold)
                            }
                        }
                    }
                }
            }

            Spacer(Modifier.height(16.dp))

            // Hold-to-calibrate. While held, freeze updates. On release,
            // capture the world-yaw of the phone's forward vector as the
            // stage-forward offset.
            Box(
                contentAlignment = Alignment.Center,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(52.dp)
                    .clip(RoundedCornerShape(10.dp))
                    .background(
                        if (holdingCalibrate) CyanSecondary.copy(alpha = 0.3f)
                        else Color(0xFF1E293B)
                    )
                    .pointerInput(Unit) {
                        awaitEachGesture {
                            awaitFirstDown()
                            holdingCalibrate = true
                            holdingCalibrateRef.set(true)
                            waitForUpOrCancellation()
                            // Snapshot the world-frame forward vector at the
                            // moment the operator releases — they're aiming
                            // at "stage forward" from their position. Yaw
                            // = atan2(fx, fy): atan2(East, North) = 0 when
                            // forward points north.
                            val fwd = latestWorldForward.get()
                            val yaw = atan2(fwd[0], fwd[1])
                            headingOffsetRad = yaw
                            headingOffsetRef.set(yaw)
                            hasHeading = true
                            hasHeadingRef.set(true)
                            holdingCalibrate = false
                            holdingCalibrateRef.set(false)
                        }
                    }
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        Icons.Default.MyLocation, null,
                        tint = CyanSecondary, modifier = Modifier.size(18.dp)
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        if (holdingCalibrate) "Aim at stage forward, then release"
                        else if (hasHeading) "Re-calibrate heading"
                        else "Hold + aim at stage forward",
                        color = CyanSecondary,
                        style = MaterialTheme.typography.labelLarge,
                        fontWeight = FontWeight.Bold
                    )
                }
            }

            Spacer(Modifier.height(24.dp))
            HorizontalDivider(color = Color(0xFF1E293B))
            Spacer(Modifier.height(16.dp))

            // ── My position panel ─────────────────────────────────────
            Text(
                "MY POSITION (mm)",
                style = MaterialTheme.typography.labelSmall,
                color = Color(0xFF64748B),
                fontWeight = FontWeight.Bold,
                letterSpacing = 1.sp
            )
            Spacer(Modifier.height(4.dp))
            Text(
                "Where you're standing on the stage. Default is centre at " +
                "standing-eye height. (TODO #427: ArUco / BLE auto-detect.)",
                style = MaterialTheme.typography.labelSmall,
                color = Color(0xFF94A3B8)
            )
            Spacer(Modifier.height(8.dp))
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                UserPosField("X", userXText, Modifier.weight(1f)) { userXText = it }
                UserPosField("Y", userYText, Modifier.weight(1f)) { userYText = it }
                UserPosField("Z (height)", userZText, Modifier.weight(1f)) { userZText = it }
            }
            Spacer(Modifier.height(8.dp))
            FilledTonalButton(
                onClick = {
                    val x = userXText.toFloatOrNull() ?: userPosition.xMm
                    val y = userYText.toFloatOrNull() ?: userPosition.yMm
                    val z = userZText.toFloatOrNull() ?: userPosition.zMm
                    onSetUserPosition(x, y, z)
                },
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Save Position")
            }

            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun UserPosField(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    onValueChange: (String) -> Unit,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label) },
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        modifier = modifier,
        colors = OutlinedTextFieldDefaults.colors(
            focusedBorderColor = CyanSecondary,
            focusedLabelColor = CyanSecondary,
            unfocusedTextColor = Color.White,
            focusedTextColor = Color.White,
        )
    )
}

/**
 * Phone-aim → floor intersection.
 *
 * @param ux User x in stage mm.
 * @param uy User y in stage mm.
 * @param uz User z (eye height) in stage mm.
 * @param fx Forward vector x component (stage frame).
 * @param fy Forward vector y component (stage frame).
 * @param fz Forward vector z component (stage frame). Negative when phone
 *           aims down toward the floor.
 * @return (targetX, targetY) in stage mm at z=0, or null when the phone
 *         is aimed level/up (fz >= -0.05) and the ray never meets the floor.
 */
internal fun computeFloorIntersection(
    ux: Float, uy: Float, uz: Float,
    fx: Float, fy: Float, fz: Float,
): Pair<Float, Float>? {
    if (fz >= -0.05f) return null  // tipping below 'level' is required
    val t = -uz / fz
    if (t <= 0f) return null
    return Pair(ux + t * fx, uy + t * fy)
}
