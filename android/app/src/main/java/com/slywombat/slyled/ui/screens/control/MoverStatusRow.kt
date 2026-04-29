package com.slywombat.slyled.ui.screens.control

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.slywombat.slyled.data.model.MoverControlClaim

/**
 * #427 + #479 — shared mover-control status indicator. Used by both
 * Controller mode and Pointer mode so they show identical staleness +
 * engine-health badges. Green dot = fresh, amber = stale, red = engine
 * stopped, grey = waiting for first server response.
 */
@Composable
fun MoverStatusRow(
    statusClaim: MoverControlClaim?,
    engineRunning: Boolean,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        modifier = Modifier.fillMaxWidth()
    ) {
        val (dotColor, label) = when {
            !engineRunning -> Color(0xFFEF4444) to "DMX engine stopped"
            statusClaim == null -> Color(0xFF94A3B8) to "Waiting for server..."
            statusClaim.lastWriteAge > 5f ->
                Color(0xFFFBBF24) to "Stale ${statusClaim.lastWriteAge.toInt()}s · ${statusClaim.state}"
            else ->
                Color(0xFF4ADE80) to ("${statusClaim.state}" +
                    if (statusClaim.calibrated) " · calibrated" else "")
        }
        Box(
            modifier = Modifier
                .size(8.dp)
                .background(dotColor, RoundedCornerShape(4.dp))
        )
        Text(
            label,
            style = MaterialTheme.typography.labelSmall,
            color = Color(0xFFCBD5E1)
        )
        Spacer(Modifier.weight(1f))
        if (statusClaim?.deviceName?.isNotEmpty() == true) {
            Text(
                statusClaim.deviceName,
                style = MaterialTheme.typography.labelSmall,
                color = Color(0xFF64748B)
            )
        }
    }
}
