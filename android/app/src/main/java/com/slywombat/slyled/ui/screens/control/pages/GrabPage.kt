package com.slywombat.slyled.ui.screens.control.pages

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ControlCamera
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.StarBorder
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.Canvas
import androidx.hilt.navigation.compose.hiltViewModel
import com.slywombat.slyled.data.model.DmxProfile
import com.slywombat.slyled.data.model.Fixture
import com.slywombat.slyled.ui.screens.control.haptics.HapticEvent
import com.slywombat.slyled.ui.screens.control.haptics.rememberHaptics
import com.slywombat.slyled.ui.theme.CyanSecondary
import com.slywombat.slyled.ui.theme.DmxPurple
import com.slywombat.slyled.ui.theme.NearWhite
import com.slywombat.slyled.viewmodel.ControlViewModel
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.sin

/**
 * Grab page — moving heads as tiles with current colour + pan/tilt
 * arrow. Tap → ControllerModeOverlay. Long-press star to favourite.
 * Top button "Send all home" calls /api/mover-control/all-home.
 * Design doc §5.2 + §6.5. #888.
 */
@Composable
fun GrabPage(
    onGrab: (Int) -> Unit,
    modifier: Modifier = Modifier,
    vm: ControlViewModel = hiltViewModel(),
) {
    val haptic = rememberHaptics()
    val fixtures by vm.fixtures.collectAsState()
    val profiles by vm.profiles.collectAsState()
    val favourites by vm.favouriteMovers.collectAsState()
    val live by vm.fixturesLive.collectAsState()

    // Filter to mover-class DMX fixtures (profile has panRange > 0).
    val movers = remember(fixtures, profiles) {
        val byId = profiles.associateBy { it.id }
        fixtures.filter {
            it.fixtureType == "dmx" && (byId[it.dmxProfileId ?: ""]?.panRange ?: 0) > 0
        }
    }
    val favSorted = movers.filter { it.id in favourites }
        .sortedBy { it.name }
    val others = movers.filter { it.id !in favourites }
        .sortedBy { it.name }

    Column(modifier = modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 12.dp)) {
        // Header row with "Send all home" safety button.
        Row(
            modifier = Modifier.fillMaxWidth().padding(bottom = 12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "MOVING HEADS",
                style = MaterialTheme.typography.labelMedium,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            OutlinedButton(
                onClick = {
                    haptic(HapticEvent.HEAVY_THUD)
                    vm.sendAllMoversHome()
                },
                enabled = movers.isNotEmpty(),
            ) {
                Icon(Icons.Filled.Home, contentDescription = null, modifier = Modifier.size(16.dp))
                Spacer(Modifier.width(6.dp))
                Text("Send all home", style = MaterialTheme.typography.labelMedium)
            }
        }

        if (movers.isEmpty()) {
            EmptyMovers()
            return@Column
        }

        // Favourites row (horizontal) when any present.
        if (favSorted.isNotEmpty()) {
            Text(
                "Favourites",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(bottom = 6.dp),
            )
            LazyRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                contentPadding = PaddingValues(end = 16.dp),
                modifier = Modifier.fillMaxWidth().padding(bottom = 12.dp),
            ) {
                items(favSorted, key = { it.id }) { fix ->
                    MoverTile(
                        fixture = fix,
                        live = live[fix.id.toString()],
                        starred = true,
                        onTap = {
                            haptic(HapticEvent.LIGHT_TICK)
                            onGrab(fix.id)
                        },
                        onToggleStar = {
                            haptic(HapticEvent.LIGHT_TICK)
                            vm.toggleFavouriteMover(fix.id)
                        },
                    )
                }
            }
        }

        // Full list below.
        Text(
            if (favSorted.isEmpty()) "All movers" else "All other movers",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(bottom = 6.dp),
        )
        LazyColumn(
            verticalArrangement = Arrangement.spacedBy(6.dp),
            modifier = Modifier.fillMaxSize(),
        ) {
            items(others, key = { it.id }) { fix ->
                MoverRow(
                    fixture = fix,
                    live = live[fix.id.toString()],
                    starred = false,
                    onTap = {
                        haptic(HapticEvent.LIGHT_TICK)
                        onGrab(fix.id)
                    },
                    onToggleStar = {
                        haptic(HapticEvent.LIGHT_TICK)
                        vm.toggleFavouriteMover(fix.id)
                    },
                )
            }
        }
    }
}

@Composable
private fun EmptyMovers() {
    Box(
        modifier = Modifier.fillMaxSize(),
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                "No moving heads in the layout",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                "Add one in the desktop app.",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun MoverTile(
    fixture: Fixture,
    live: JsonElement?,
    starred: Boolean,
    onTap: () -> Unit,
    onToggleStar: () -> Unit,
) {
    val rgb = liveRgb(live)
    val panDeg = liveAngle(live, "panDeg") ?: 0f
    val tiltDeg = liveAngle(live, "tiltDeg") ?: 0f

    Surface(
        modifier = Modifier
            .width(88.dp)
            .height(120.dp)
            .clickable(onClick = onTap),
        shape = RoundedCornerShape(10.dp),
        color = MaterialTheme.colorScheme.surface,
        border = androidx.compose.foundation.BorderStroke(
            width = 1.dp, color = DmxPurple.copy(alpha = 0.4f),
        ),
    ) {
        Column(
            modifier = Modifier.padding(8.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            MoverPreview(rgb = rgb, panDeg = panDeg, tiltDeg = tiltDeg, size = 56)
            Spacer(Modifier.height(6.dp))
            Text(
                text = fixture.name.ifBlank { "Mover ${fixture.id}" },
                style = MaterialTheme.typography.labelSmall,
                color = NearWhite,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                textAlign = TextAlign.Center,
                modifier = Modifier.weight(1f).fillMaxWidth(),
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onToggleStar, modifier = Modifier.size(22.dp)) {
                    Icon(
                        if (starred) Icons.Filled.Star else Icons.Filled.StarBorder,
                        contentDescription = "Favourite",
                        tint = if (starred) CyanSecondary else MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(18.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun MoverRow(
    fixture: Fixture,
    live: JsonElement?,
    starred: Boolean,
    onTap: () -> Unit,
    onToggleStar: () -> Unit,
) {
    val rgb = liveRgb(live)
    val panDeg = liveAngle(live, "panDeg") ?: 0f
    val tiltDeg = liveAngle(live, "tiltDeg") ?: 0f

    Surface(
        modifier = Modifier.fillMaxWidth().clickable(onClick = onTap),
        shape = RoundedCornerShape(8.dp),
        color = MaterialTheme.colorScheme.surface,
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            MoverPreview(rgb = rgb, panDeg = panDeg, tiltDeg = tiltDeg, size = 40)
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = fixture.name.ifBlank { "Mover ${fixture.id}" },
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium,
                )
                Text(
                    text = "pan ${panDeg.toInt()}°  tilt ${tiltDeg.toInt()}°",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Icon(
                Icons.Filled.ControlCamera,
                contentDescription = "Grab",
                tint = DmxPurple,
                modifier = Modifier.size(20.dp),
            )
            IconButton(onClick = onToggleStar, modifier = Modifier.size(28.dp)) {
                Icon(
                    if (starred) Icons.Filled.Star else Icons.Filled.StarBorder,
                    contentDescription = "Favourite",
                    tint = if (starred) CyanSecondary
                           else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

/**
 * Round preview that shows the fixture's current colour as fill and the
 * pan/tilt direction as a short arrow drawn from centre. Pan rotates
 * the arrow around the circle; tilt scales its length (longer = lower
 * tilt, shorter = closer to overhead). Design doc §5.2.
 */
@Composable
private fun MoverPreview(
    rgb: Triple<Int, Int, Int>,
    panDeg: Float,
    tiltDeg: Float,
    size: Int,
) {
    val color = Color(rgb.first, rgb.second, rgb.third)
    val isDark = rgb.first + rgb.second + rgb.third < 60
    Box(
        modifier = Modifier
            .size(size.dp)
            .clip(CircleShape)
            .background(
                color = if (isDark) Color(0xFF1F2937) else color,
            )
            .border(
                width = 1.dp,
                color = if (isDark) MaterialTheme.colorScheme.outline else Color.Black.copy(alpha = 0.3f),
                shape = CircleShape,
            ),
        contentAlignment = Alignment.Center,
    ) {
        // Arrow: pan rotates, tilt scales length 0.3..0.9. tilt = +90 (sky)
        // = short arrow; tilt = -90 (floor) = long arrow.
        val tiltN = ((90f - tiltDeg.coerceIn(-90f, 90f)) / 180f).coerceIn(0f, 1f)
        val arrowLen = (0.3f + 0.6f * tiltN) * (size / 2f)
        Canvas(modifier = Modifier.size(size.dp)) {
            val cx = this.size.width / 2f
            val cy = this.size.height / 2f
            val rad = Math.toRadians(panDeg.toDouble())
            val ex = cx + (sin(rad).toFloat() * arrowLen.dp.toPx())
            val ey = cy - (cos(rad).toFloat() * arrowLen.dp.toPx())
            drawLine(
                color = if (isDark) NearWhite else Color.Black.copy(alpha = 0.65f),
                start = Offset(cx, cy),
                end = Offset(ex, ey),
                strokeWidth = 2f.dp.toPx(),
                cap = StrokeCap.Round,
            )
        }
    }
}

/** Pull (r,g,b) from the live JSON blob; defaults to off (0,0,0). */
private fun liveRgb(el: JsonElement?): Triple<Int, Int, Int> {
    if (el == null) return Triple(0, 0, 0)
    return try {
        val o = el.jsonObject
        Triple(
            o["r"]?.jsonPrimitive?.content?.toIntOrNull() ?: 0,
            o["g"]?.jsonPrimitive?.content?.toIntOrNull() ?: 0,
            o["b"]?.jsonPrimitive?.content?.toIntOrNull() ?: 0,
        )
    } catch (_: Throwable) { Triple(0, 0, 0) }
}

/** Pull a Float field (e.g. panDeg) from the live JSON; null if missing. */
private fun liveAngle(el: JsonElement?, key: String): Float? {
    if (el == null) return null
    return try {
        el.jsonObject[key]?.jsonPrimitive?.content?.toFloatOrNull()
    } catch (_: Throwable) { null }
}
