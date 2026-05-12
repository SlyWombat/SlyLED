package com.slywombat.slyled.ui.screens.control.conn

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.slywombat.slyled.ui.theme.GreenOnline
import com.slywombat.slyled.ui.theme.OrangeWled
import com.slywombat.slyled.ui.theme.RedError

/**
 * Top-bar widget that shows the current link state and triggers a
 * manual reconnect on tap. Design doc §6.1.
 *
 * Connected = solid green dot + "Connected".
 * Degraded  = orange dot + "Reconnecting…" with slow alpha pulse.
 * Disconnected = red dot + "Offline" with fast pulse.
 */
@Composable
fun ConnectionPill(
    modifier: Modifier = Modifier,
    vm: LinkStateViewModel = hiltViewModel(),
) {
    val state by vm.state.collectAsState()

    val (color, label) = when (state) {
        LinkState.CONNECTED    -> GreenOnline to "Connected"
        LinkState.DEGRADED     -> OrangeWled to "Reconnecting…"
        LinkState.DISCONNECTED -> RedError to "Offline"
    }

    // Pulse alpha for non-Connected states.
    val pulse by if (state == LinkState.CONNECTED) {
        animateFloatAsState(targetValue = 1f, label = "pill-pulse")
    } else {
        val transition = rememberInfiniteTransition(label = "pill-pulse-anim")
        transition.animateFloat(
            initialValue = 0.55f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(
                animation = tween(
                    durationMillis = if (state == LinkState.DEGRADED) 1200 else 500,
                ),
                repeatMode = RepeatMode.Reverse,
            ),
            label = "pill-pulse-anim-value",
        )
    }

    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        modifier = modifier
            .clickable { vm.retry() }
            .background(
                color = Color.Transparent,
                shape = RoundedCornerShape(12.dp),
            )
            .border(
                width = 1.dp,
                color = color.copy(alpha = 0.4f),
                shape = RoundedCornerShape(12.dp),
            )
            .padding(horizontal = 10.dp, vertical = 4.dp)
            .alpha(pulse),
    ) {
        Box(
            modifier = Modifier
                .size(8.dp)
                .background(color = color, shape = RoundedCornerShape(50)),
        )
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = color,
            fontWeight = FontWeight.Medium,
        )
    }
}
