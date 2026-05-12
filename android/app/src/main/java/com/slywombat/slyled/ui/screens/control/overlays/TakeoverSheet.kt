package com.slywombat.slyled.ui.screens.control.overlays

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.WarningAmber
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.slywombat.slyled.ui.screens.control.haptics.HapticEvent
import com.slywombat.slyled.ui.screens.control.haptics.rememberHaptics
import com.slywombat.slyled.ui.theme.DmxPurple

/**
 * Shown when the operator taps a mover that is currently held by
 * another client (SPA, second phone). Loud — interrupts to confirm the
 * takeover. Design doc §6.2. #888.
 */
@Composable
fun TakeoverSheet(
    fixtureName: String,
    heldBy: String,
    onConfirm: () -> Unit,
    onCancel: () -> Unit,
) {
    val haptic = rememberHaptics()
    AlertDialog(
        onDismissRequest = onCancel,
        icon = {
            Icon(
                Icons.Filled.WarningAmber,
                contentDescription = null,
                tint = DmxPurple,
                modifier = Modifier.size(36.dp),
            )
        },
        title = {
            Text("Mover already held", fontWeight = FontWeight.Bold)
        },
        text = {
            Column {
                Text(
                    "$fixtureName is currently controlled by $heldBy.",
                    style = MaterialTheme.typography.bodyMedium,
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    "Taking over will release their session. Confirm to grab.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    haptic(HapticEvent.HEAVY_THUD)
                    onConfirm()
                },
            ) { Text("Take over") }
        },
        dismissButton = {
            TextButton(onClick = onCancel) { Text("Cancel") }
        },
    )
}
