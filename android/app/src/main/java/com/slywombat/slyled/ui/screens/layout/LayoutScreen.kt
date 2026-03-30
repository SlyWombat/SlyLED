package com.slywombat.slyled.ui.screens.layout

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.*
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.slywombat.slyled.data.model.*
import com.slywombat.slyled.viewmodel.LayoutViewModel

@Composable
fun LayoutScreen(viewModel: LayoutViewModel = hiltViewModel()) {
    val layout by viewModel.layout.collectAsState()
    val children by viewModel.children.collectAsState()
    val surfaces by viewModel.surfaces.collectAsState()
    val fixtures by viewModel.fixtures.collectAsState()
    val stage by viewModel.stage.collectAsState()
    val message by viewModel.message.collectAsState()
    var dragChildId by remember { mutableIntStateOf(-1) }

    LaunchedEffect(Unit) { viewModel.load() }

    Column(modifier = Modifier.fillMaxSize().padding(8.dp)) {
        // Header
        Row(
            modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text("Stage Layout", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.weight(1f))
            FilledTonalButton(onClick = { viewModel.autoCreateFixtures() }) {
                Text("Auto Fixtures", fontSize = 12.sp)
            }
            Button(onClick = { viewModel.saveLayout() }) {
                Text("Save", fontSize = 12.sp)
            }
        }

        // Stage info
        Text(
            "Stage: ${stage.w}m x ${stage.h}m | ${children.size} performers | ${surfaces.size} surfaces",
            fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(bottom = 4.dp)
        )

        // Canvas
        val canvasW = layout?.canvasW ?: 10000
        val canvasH = layout?.canvasH ?: 5000
        val textMeasurer = rememberTextMeasurer()

        Canvas(
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(canvasW.toFloat() / canvasH)
                .background(Color(0xFF0D0D0D))
                .pointerInput(Unit) {
                    detectDragGestures(
                        onDragStart = { offset ->
                            val w = size.width.toFloat()
                            val h = size.height.toFloat()
                            // Find closest child to tap
                            val lc = layout?.children ?: emptyList()
                            dragChildId = lc.minByOrNull { c ->
                                val cx = c.x.toFloat() * w / canvasW
                                val cy = h - c.y.toFloat() * h / canvasH
                                (cx - offset.x).let { it * it } + (cy - offset.y).let { it * it }
                            }?.id ?: -1
                        },
                        onDrag = { change, _ ->
                            if (dragChildId >= 0) {
                                val w = size.width.toFloat()
                                val h = size.height.toFloat()
                                val pos = change.position
                                val physX = (pos.x * canvasW / w).toInt().coerceIn(0, canvasW)
                                val physY = ((h - pos.y) * canvasH / h).toInt().coerceIn(0, canvasH)
                                viewModel.moveChild(dragChildId, physX, physY)
                            }
                        },
                        onDragEnd = { dragChildId = -1 }
                    )
                }
        ) {
            val w = size.width
            val h = size.height

            // Grid
            val gridColor = Color(0xFF1E1E1E)
            for (gx in 0..10) {
                val x = gx * w / 10
                drawLine(gridColor, Offset(x, 0f), Offset(x, h))
            }
            for (gy in 0..5) {
                val y = gy * h / 5
                drawLine(gridColor, Offset(0f, y), Offset(w, y))
            }

            // Stage border
            drawRect(Color(0xFF1E3A5F), style = Stroke(2f))

            // Surfaces
            surfaces.forEach { s ->
                val t = s.transform
                val sx = t.pos[0].toFloat() * w / canvasW
                val sy = h - t.pos[1].toFloat() * h / canvasH
                val sw = t.scale[0].toFloat() * w / canvasW
                val sh = t.scale[1].toFloat() * h / canvasH
                val col = try { Color(android.graphics.Color.parseColor(s.color)) } catch (_: Exception) { Color(0xFF334155) }
                drawRect(col.copy(alpha = s.opacity / 100f), Offset(sx, sy - sh), Size(sw, sh))
                drawRect(col.copy(alpha = 0.6f), Offset(sx, sy - sh), Size(sw, sh), style = Stroke(1f))
            }

            // Children (performers)
            val layoutChildren = layout?.children ?: emptyList()
            children.forEach { child ->
                val lc = layoutChildren.find { it.id == child.id }
                if (lc != null && (lc.x != 0 || lc.y != 0)) {
                    val cx = lc.x.toFloat() * w / canvasW
                    val cy = h - lc.y.toFloat() * h / canvasH

                    // LED strings
                    val strColors = listOf(Color.Cyan, Color.Magenta, Color.Yellow, Color.Green)
                    for (si in 0 until child.sc.coerceAtMost(child.strings.size)) {
                        val s = child.strings[si]
                        if (s.leds <= 0) continue
                        val lenMm = if (s.lengthMm < 500) (s.leds * 16).coerceAtLeast(500) else s.lengthMm
                        val dirX = when (s.stripDirection) { 0 -> 1f; 2 -> -1f; else -> 0f }
                        val dirY = when (s.stripDirection) { 1 -> -1f; 3 -> 1f; else -> 0f }
                        val pxLen = if (dirX != 0f) lenMm * w / canvasW else lenMm * h / canvasH
                        val endX = cx + dirX * pxLen.coerceAtLeast(20f)
                        val endY = cy + dirY * pxLen.coerceAtLeast(20f)
                        val col = strColors[si % strColors.size]
                        drawLine(col, Offset(cx, cy), Offset(endX, endY), strokeWidth = 3f)
                    }

                    // Node circle
                    val nodeColor = if (child.status == 1) Color(0xFF22CC66) else Color(0xFF555555)
                    drawCircle(nodeColor, 10f, Offset(cx, cy))
                    drawCircle(nodeColor.copy(alpha = 0.4f), 14f, Offset(cx, cy), style = Stroke(2f))

                    // Label
                    val label = child.name.ifEmpty { child.hostname }
                    val textResult = textMeasurer.measure(
                        AnnotatedString(label),
                        style = TextStyle(fontSize = 9.sp, color = Color.White)
                    )
                    drawText(textResult, topLeft = Offset(cx - textResult.size.width / 2f, cy + 16f))
                }
            }
        }

        // Unplaced performers
        val placed = (layout?.children ?: emptyList()).filter { it.x != 0 || it.y != 0 }.map { it.id }.toSet()
        val unplaced = children.filter { it.id !in placed }
        if (unplaced.isNotEmpty()) {
            Spacer(Modifier.height(12.dp))
            Text("Unplaced Performers", style = MaterialTheme.typography.titleSmall)
            LazyColumn(modifier = Modifier.fillMaxWidth()) {
                items(unplaced) { c ->
                    Card(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp),
                        onClick = { viewModel.placeChild(c.id, 5000, 2500) }
                    ) {
                        Text(
                            "${c.name.ifEmpty { c.hostname }} — tap to place",
                            modifier = Modifier.padding(12.dp), fontSize = 13.sp
                        )
                    }
                }
            }
        }

        message?.let {
            Text(it, color = MaterialTheme.colorScheme.primary, fontSize = 12.sp, modifier = Modifier.padding(top = 4.dp))
        }
    }
}
