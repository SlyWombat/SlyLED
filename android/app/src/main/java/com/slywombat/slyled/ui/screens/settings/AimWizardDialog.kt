package com.slywombat.slyled.ui.screens.settings

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.slywombat.slyled.data.repository.SlyLedRepository
import kotlinx.coroutines.launch

/**
 * #826 — Empirical aim-axis calibration wizard.
 *
 * Captures three quaternion poses from the phone's TYPE_ROTATION_VECTOR
 * sensor (neutral, pitch-forward, yaw-left) and posts to
 * `/api/remotes/aim-wizard`. Server derives body-frame forward_local +
 * up_local from the rotation deltas and persists them on the Remote.
 *
 * Replaces the grip-publish guesswork: instead of guessing the pointer
 * axis from `Surface.ROTATION_*`, the operator's actual gestures
 * measure it directly. Independent of phone model, OS version, sensor
 * fusion algorithm, ENU vs NED, and chirality assumption.
 *
 * Operator instructions (matches issue #826's wizard flow):
 *   1. "Hold the phone in your normal grip aimed at the head's current
 *      direction. Press capture." → Q_neutral
 *   2. "From neutral, tip the phone forward toward the floor — the
 *      gesture you'd use to tilt the head DOWN. Press capture." → Q_pitch_fwd
 *   3. "Return to neutral. Now yaw the phone in the direction you'd
 *      use to pan the head to STAGE-LEFT (the actor's left as the
 *      actor faces the audience — typically the audience's RIGHT).
 *      Press capture." → Q_yaw_left
 *      Stage-anchored phrasing (per #826 operator comment 2026-05-09):
 *      "your left" is ambiguous — operator-LEFT can be audience-RIGHT
 *      or audience-LEFT depending on whether they read "your" from the
 *      audience or actor perspective. A wrong-direction yaw here sign-
 *      inverts up_local and silently flips every subsequent yaw aim.
 */

private data class WizardStep(val role: String, val label: String, val instructions: String)

private val WIZARD_STEPS = listOf(
    WizardStep(
        "neutral",
        "Step 1 — Neutral",
        "Hold the phone in your normal grip, pointing at the head's current direction. " +
            "Press CAPTURE when steady.",
    ),
    WizardStep(
        "pitch_forward",
        "Step 2 — Tilt down",
        "From neutral, tip the phone FORWARD toward the floor — the gesture you'd " +
            "use to tilt the head DOWN. Press CAPTURE at the end of the gesture.",
    ),
    WizardStep(
        "yaw_left",
        "Step 3 — Pan to stage-left",
        // #826 — anchor the gesture to STAGE-LEFT, not "your left".
        // Operator-LEFT is ambiguous (audience-perspective vs actor-
        // perspective vs facing-the-rig); a wrong-direction yaw here
        // sign-inverts up_local and silently flips every yaw aim in
        // the field. Stage-LEFT (the actor's left as the actor faces
        // the audience — typically the audience's RIGHT) is the
        // single anchor the orchestrator's stage frame uses.
        "Return to neutral. Now yaw the phone in the direction you'd use " +
            "to pan the head to STAGE-LEFT — the actor's left as the actor " +
            "faces the audience (typically the audience's RIGHT). " +
            "Press CAPTURE at the end of the gesture.",
    ),
)

@Composable
fun AimWizardDialog(
    onDismiss: () -> Unit,
    submitWizard: suspend (List<Pair<String, FloatArray>>) -> SlyLedRepository.AimWizardResult,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    val sensorManager = remember { context.getSystemService(Context.SENSOR_SERVICE) as SensorManager }
    val rotationSensor = remember { sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR) }

    // Latest quat from sensor — captured on each "Capture" press.
    val currentQuat = remember { mutableStateOf(floatArrayOf(1f, 0f, 0f, 0f)) }
    val captured = remember { mutableStateMapOf<String, FloatArray>() }
    var stepIdx by remember { mutableIntStateOf(0) }
    var submitting by remember { mutableStateOf(false) }
    var resultText by remember { mutableStateOf<String?>(null) }
    var resultIsError by remember { mutableStateOf(false) }

    // Bind the rotation-vector sensor for the lifetime of the dialog.
    DisposableEffect(rotationSensor) {
        if (rotationSensor == null) {
            resultText = "This phone has no TYPE_ROTATION_VECTOR sensor — wizard unavailable."
            resultIsError = true
            onDispose { }
        } else {
            val listener = object : SensorEventListener {
                override fun onSensorChanged(event: SensorEvent) {
                    val quat = FloatArray(4)
                    SensorManager.getQuaternionFromVector(quat, event.values)
                    // Android quat = [w, x, y, z] — matches the wizard's
                    // wire format expectation.
                    currentQuat.value = quat
                }
                override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
            }
            sensorManager.registerListener(listener, rotationSensor, SensorManager.SENSOR_DELAY_GAME)
            onDispose { sensorManager.unregisterListener(listener) }
        }
    }

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Surface(
            shape = RoundedCornerShape(16.dp),
            color = MaterialTheme.colorScheme.surface,
            modifier = Modifier
                .fillMaxWidth(0.95f)
                .padding(16.dp),
        ) {
            Column(
                modifier = Modifier.padding(20.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Text(
                    "Aim Calibration Wizard",
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Bold,
                )
                Text(
                    "Three quick gestures so the head responds to your phone's actual pointing axis " +
                        "instead of a guess. Run once per device or grip change.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )

                if (resultText != null && !resultIsError) {
                    // Success — show resolved axes, allow dismiss.
                    SuccessSummary(resultText!!, onDismiss)
                    return@Column
                }

                // Step indicator bar.
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    WIZARD_STEPS.forEachIndexed { idx, _ ->
                        val isCaptured = captured.containsKey(WIZARD_STEPS[idx].role)
                        val isCurrent = idx == stepIdx
                        val color = when {
                            isCaptured -> MaterialTheme.colorScheme.primary
                            isCurrent -> MaterialTheme.colorScheme.tertiary
                            else -> MaterialTheme.colorScheme.surfaceVariant
                        }
                        Box(
                            modifier = Modifier
                                .weight(1f)
                                .height(4.dp)
                                .clip(RoundedCornerShape(2.dp))
                                .background(color),
                        )
                    }
                }

                val step = WIZARD_STEPS[stepIdx.coerceAtMost(WIZARD_STEPS.lastIndex)]
                Text(
                    step.label,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    step.instructions,
                    style = MaterialTheme.typography.bodyMedium,
                )

                if (resultText != null && resultIsError) {
                    Text(
                        resultText!!,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }

                // Live quat preview — gives the operator a visible signal
                // the sensor is reporting and what's about to be captured.
                val q = currentQuat.value
                Text(
                    "Live quat: w=%.3f x=%.3f y=%.3f z=%.3f".format(q[0], q[1], q[2], q[3]),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    fontSize = 11.sp,
                )

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    OutlinedButton(
                        onClick = {
                            // Restart wizard from scratch.
                            captured.clear()
                            stepIdx = 0
                            resultText = null
                            resultIsError = false
                        },
                        enabled = !submitting,
                    ) {
                        Icon(Icons.Default.Refresh, contentDescription = null,
                            modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(4.dp))
                        Text("Restart")
                    }
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = onDismiss, enabled = !submitting) {
                        Text("Cancel")
                    }
                    Button(
                        onClick = {
                            // Snapshot current quat for this step.
                            captured[step.role] = currentQuat.value.copyOf()
                            resultText = null
                            resultIsError = false
                            if (stepIdx < WIZARD_STEPS.lastIndex) {
                                stepIdx += 1
                            } else {
                                // All three captured — submit.
                                submitting = true
                                scope.launch {
                                    val poses = WIZARD_STEPS.mapNotNull { s ->
                                        captured[s.role]?.let { s.role to it }
                                    }
                                    val result = try {
                                        submitWizard(poses)
                                    } catch (e: Exception) {
                                        SlyLedRepository.AimWizardResult(
                                            ok = false,
                                            errCode = "network",
                                            errDetail = "Network error: ${e.message}",
                                        )
                                    }
                                    if (result.ok) {
                                        val fwd = result.forwardLocal?.joinToString(", ") {
                                            "%.3f".format(it)
                                        } ?: "?"
                                        val up = result.upLocal?.joinToString(", ") {
                                            "%.3f".format(it)
                                        } ?: "?"
                                        resultText = "Calibration saved.\n" +
                                            "Forward axis: ($fwd)\nUp axis: ($up)"
                                        resultIsError = false
                                    } else {
                                        resultText = result.errDetail
                                            ?: "Wizard failed (${result.errCode})."
                                        resultIsError = true
                                        // Reset to re-capture the failing step. For
                                        // role-specific errors point the operator
                                        // at the right step.
                                        stepIdx = when (result.errCode) {
                                            "insufficient_pitch", "missing_pose" -> 1
                                            "insufficient_yaw", "degenerate_axes" -> 2
                                            else -> 0
                                        }
                                        captured.remove(WIZARD_STEPS[stepIdx].role)
                                    }
                                    submitting = false
                                }
                            }
                        },
                        enabled = !submitting && rotationSensor != null,
                    ) {
                        if (submitting) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(18.dp),
                                strokeWidth = 2.dp,
                            )
                        } else {
                            Text(
                                if (stepIdx < WIZARD_STEPS.lastIndex) "Capture & Next"
                                else "Capture & Finish",
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SuccessSummary(message: String, onDismiss: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Icon(
            Icons.Default.CheckCircle,
            contentDescription = null,
            tint = Color(0xFF22C55E),
            modifier = Modifier.size(28.dp),
        )
        Text(
            "Calibration saved",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
        )
    }
    Text(
        message,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Row(modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.End) {
        Button(onClick = onDismiss) { Text("Done") }
    }
}

