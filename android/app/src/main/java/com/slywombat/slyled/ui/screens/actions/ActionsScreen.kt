package com.slywombat.slyled.ui.screens.actions

import androidx.compose.runtime.LaunchedEffect
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.slywombat.slyled.data.model.Action
import com.slywombat.slyled.data.model.ActionTypes
import com.slywombat.slyled.viewmodel.ActionsViewModel
import kotlin.math.roundToInt

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ActionsScreen(viewModel: ActionsViewModel = hiltViewModel()) {
    // Reload on every screen visit
    LaunchedEffect(Unit) { viewModel.loadActions() }

    val actions by viewModel.actions.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()
    val error by viewModel.error.collectAsState()

    var showEditor by remember { mutableStateOf(false) }
    var editingAction by remember { mutableStateOf<Action?>(null) }
    var showDeleteConfirm by remember { mutableStateOf<Action?>(null) }

    Scaffold(
        floatingActionButton = {
            FloatingActionButton(onClick = {
                editingAction = null
                showEditor = true
            }) {
                Icon(Icons.Default.Add, contentDescription = "New action")
            }
        }
    ) { padding ->
        Column(modifier = Modifier.fillMaxSize().padding(padding).padding(16.dp)) {
            error?.let { msg ->
                Card(
                    modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)
                ) {
                    Row(
                        modifier = Modifier.padding(12.dp).fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(msg, color = MaterialTheme.colorScheme.onErrorContainer,
                            modifier = Modifier.weight(1f))
                        TextButton(onClick = { viewModel.clearError() }) { Text("Dismiss") }
                    }
                }
            }

            if (isLoading && actions.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
            } else if (actions.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text("No actions yet. Tap + to create one.",
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            } else {
                LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(actions, key = { it.id }) { action ->
                        ActionCard(
                            action = action,
                            onEdit = {
                                editingAction = action
                                showEditor = true
                            },
                            onDelete = { showDeleteConfirm = action }
                        )
                    }
                }
            }
        }
    }

    // Delete confirmation
    showDeleteConfirm?.let { action ->
        AlertDialog(
            onDismissRequest = { showDeleteConfirm = null },
            title = { Text("Delete Action") },
            text = { Text("Delete \"${action.name}\"?") },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.deleteAction(action.id)
                    showDeleteConfirm = null
                }) { Text("Delete", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = {
                TextButton(onClick = { showDeleteConfirm = null }) { Text("Cancel") }
            }
        )
    }

    // Editor sheet
    if (showEditor) {
        ActionEditorDialog(
            initial = editingAction,
            onSave = { action ->
                if (editingAction != null) {
                    viewModel.updateAction(editingAction!!.id, action)
                } else {
                    viewModel.createAction(action)
                }
                showEditor = false
            },
            onDismiss = { showEditor = false }
        )
    }
}

@Composable
private fun ActionCard(action: Action, onEdit: () -> Unit, onDelete: () -> Unit) {
    val typeName = ActionTypes.names.getOrElse(action.type) { "Unknown" }

    Card(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.padding(12.dp).fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            // Color swatch
            Box(
                modifier = Modifier
                    .size(36.dp)
                    .clip(CircleShape)
                    .background(Color(action.r, action.g, action.b))
            )
            Spacer(Modifier.width(12.dp))

            // Info
            Column(modifier = Modifier.weight(1f)) {
                Text(action.name.ifBlank { "Untitled" },
                    style = MaterialTheme.typography.titleSmall)
                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(typeName, style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    SuggestionChip(
                        onClick = {},
                        label = { Text(action.scope, style = MaterialTheme.typography.labelSmall) }
                    )
                }
            }

            // Buttons
            IconButton(onClick = onEdit) {
                Icon(Icons.Default.Edit, contentDescription = "Edit",
                    tint = MaterialTheme.colorScheme.primary)
            }
            IconButton(onClick = onDelete) {
                Icon(Icons.Default.Delete, contentDescription = "Delete",
                    tint = MaterialTheme.colorScheme.error)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ActionEditorDialog(
    initial: Action?,
    onSave: (Action) -> Unit,
    onDismiss: () -> Unit
) {
    var name by remember { mutableStateOf(initial?.name ?: "") }
    var type by remember { mutableIntStateOf(initial?.type ?: 1) }
    var scope by remember { mutableStateOf(initial?.scope ?: "performer") }
    var r by remember { mutableIntStateOf(initial?.r ?: 255) }
    var g by remember { mutableIntStateOf(initial?.g ?: 0) }
    var b by remember { mutableIntStateOf(initial?.b ?: 0) }
    var r2 by remember { mutableIntStateOf(initial?.r2 ?: 0) }
    var g2 by remember { mutableIntStateOf(initial?.g2 ?: 0) }
    var b2 by remember { mutableIntStateOf(initial?.b2 ?: 0) }
    var speedMs by remember { mutableIntStateOf(initial?.speedMs ?: 50) }
    var periodMs by remember { mutableIntStateOf(initial?.periodMs ?: 2000) }
    var spawnMs by remember { mutableIntStateOf(initial?.spawnMs ?: 200) }
    var minBri by remember { mutableIntStateOf(initial?.minBri ?: 20) }
    var spacing by remember { mutableIntStateOf(initial?.spacing ?: 5) }
    var paletteId by remember { mutableIntStateOf(initial?.paletteId ?: 0) }
    var cooling by remember { mutableIntStateOf(initial?.cooling ?: 55) }
    var sparking by remember { mutableIntStateOf(initial?.sparking ?: 120) }
    var direction by remember { mutableIntStateOf(initial?.direction ?: 0) }
    var tailLen by remember { mutableIntStateOf(initial?.tailLen ?: 10) }
    var density by remember { mutableIntStateOf(initial?.density ?: 80) }
    var decay by remember { mutableIntStateOf(initial?.decay ?: 64) }
    var fadeSpeed by remember { mutableIntStateOf(initial?.fadeSpeed ?: 20) }

    var showColorPicker by remember { mutableStateOf(false) }
    var showColor2Picker by remember { mutableStateOf(false) }
    var typeExpanded by remember { mutableStateOf(false) }
    var directionExpanded by remember { mutableStateOf(false) }
    var paletteExpanded by remember { mutableStateOf(false) }

    val isEditing = initial != null

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (isEditing) "Edit Action" else "New Action") },
        text = {
            Column(
                modifier = Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                // Name
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("Name") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )

                // Type dropdown
                ExposedDropdownMenuBox(
                    expanded = typeExpanded,
                    onExpandedChange = { typeExpanded = it }
                ) {
                    OutlinedTextField(
                        value = ActionTypes.names.getOrElse(type) { "Unknown" },
                        onValueChange = {},
                        readOnly = true,
                        label = { Text("Type") },
                        trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(typeExpanded) },
                        modifier = Modifier.fillMaxWidth().menuAnchor()
                    )
                    ExposedDropdownMenu(
                        expanded = typeExpanded,
                        onDismissRequest = { typeExpanded = false }
                    ) {
                        ActionTypes.names.forEachIndexed { idx, typeName ->
                            DropdownMenuItem(
                                text = { Text(typeName) },
                                onClick = { type = idx; typeExpanded = false }
                            )
                        }
                    }
                }

                // Primary color
                Text("Color", style = MaterialTheme.typography.labelMedium)
                Box(
                    modifier = Modifier
                        .size(48.dp)
                        .clip(CircleShape)
                        .background(Color(r, g, b))
                        .clickable { showColorPicker = true }
                )

                // Type-specific params
                when (type) {
                    ActionTypes.FADE -> {
                        Text("Second Color", style = MaterialTheme.typography.labelMedium)
                        Box(
                            modifier = Modifier
                                .size(48.dp)
                                .clip(CircleShape)
                                .background(Color(r2, g2, b2))
                                .clickable { showColor2Picker = true }
                        )
                        LabeledSlider("Speed (ms)", speedMs, 10, 2000) { speedMs = it }
                    }
                    ActionTypes.BREATHE -> {
                        LabeledSlider("Period (ms)", periodMs, 500, 10000) { periodMs = it }
                        LabeledSlider("Min Brightness", minBri, 0, 255) { minBri = it }
                    }
                    ActionTypes.CHASE -> {
                        LabeledSlider("Speed (ms)", speedMs, 10, 500) { speedMs = it }
                        LabeledSlider("Spacing", spacing, 1, 50) { spacing = it }
                        DirectionDropdown(direction, directionExpanded,
                            onExpand = { directionExpanded = it },
                            onSelect = { direction = it; directionExpanded = false })
                    }
                    ActionTypes.RAINBOW -> {
                        LabeledSlider("Speed (ms)", speedMs, 5, 500) { speedMs = it }
                        PaletteDropdown(paletteId, paletteExpanded,
                            onExpand = { paletteExpanded = it },
                            onSelect = { paletteId = it; paletteExpanded = false })
                        DirectionDropdown(direction, directionExpanded,
                            onExpand = { directionExpanded = it },
                            onSelect = { direction = it; directionExpanded = false })
                    }
                    ActionTypes.FIRE -> {
                        LabeledSlider("Speed (ms)", speedMs, 10, 500) { speedMs = it }
                        LabeledSlider("Cooling", cooling, 10, 200) { cooling = it }
                        LabeledSlider("Sparking", sparking, 10, 255) { sparking = it }
                    }
                    ActionTypes.COMET -> {
                        LabeledSlider("Speed (ms)", speedMs, 5, 500) { speedMs = it }
                        LabeledSlider("Tail Length", tailLen, 3, 50) { tailLen = it }
                        LabeledSlider("Decay", decay, 10, 200) { decay = it }
                        DirectionDropdown(direction, directionExpanded,
                            onExpand = { directionExpanded = it },
                            onSelect = { direction = it; directionExpanded = false })
                    }
                    ActionTypes.TWINKLE -> {
                        LabeledSlider("Spawn (ms)", spawnMs, 10, 1000) { spawnMs = it }
                        LabeledSlider("Density", density, 10, 255) { density = it }
                        LabeledSlider("Fade Speed", fadeSpeed, 1, 100) { fadeSpeed = it }
                    }
                }

                // Scope
                Text("Scope", style = MaterialTheme.typography.labelMedium)
                val scopes = listOf("performer" to "All Performers",
                    "selected" to "Selected", "canvas" to "Canvas")
                scopes.forEach { (value, label) ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(selected = scope == value,
                            onClick = { scope = value })
                        Text(label, modifier = Modifier.clickable { scope = value })
                    }
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    val action = Action(
                        id = initial?.id ?: -1,
                        name = name,
                        type = type,
                        scope = scope,
                        r = r, g = g, b = b,
                        r2 = r2, g2 = g2, b2 = b2,
                        speedMs = speedMs.takeIf { type in listOf(2, 4, 5, 6, 7) },
                        periodMs = periodMs.takeIf { type == 3 },
                        spawnMs = spawnMs.takeIf { type == 8 },
                        minBri = minBri.takeIf { type == 3 },
                        spacing = spacing.takeIf { type == 4 },
                        paletteId = paletteId.takeIf { type == 5 },
                        cooling = cooling.takeIf { type == 6 },
                        sparking = sparking.takeIf { type == 6 },
                        direction = direction.takeIf { type in listOf(4, 5, 7) },
                        tailLen = tailLen.takeIf { type == 7 },
                        density = density.takeIf { type == 8 },
                        decay = decay.takeIf { type == 7 },
                        fadeSpeed = fadeSpeed.takeIf { type == 8 }
                    )
                    onSave(action)
                },
                enabled = name.isNotBlank()
            ) { Text("Save") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        }
    )

    // RGB picker dialogs
    if (showColorPicker) {
        RgbPickerDialog(r, g, b, onConfirm = { nr, ng, nb ->
            r = nr; g = ng; b = nb; showColorPicker = false
        }, onDismiss = { showColorPicker = false })
    }
    if (showColor2Picker) {
        RgbPickerDialog(r2, g2, b2, onConfirm = { nr, ng, nb ->
            r2 = nr; g2 = ng; b2 = nb; showColor2Picker = false
        }, onDismiss = { showColor2Picker = false })
    }
}

@Composable
private fun LabeledSlider(label: String, value: Int, min: Int, max: Int, onValue: (Int) -> Unit) {
    Column {
        Text("$label: $value", style = MaterialTheme.typography.bodySmall)
        Slider(
            value = value.toFloat(),
            onValueChange = { onValue(it.roundToInt()) },
            valueRange = min.toFloat()..max.toFloat(),
            modifier = Modifier.fillMaxWidth()
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DirectionDropdown(
    direction: Int,
    expanded: Boolean,
    onExpand: (Boolean) -> Unit,
    onSelect: (Int) -> Unit
) {
    ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = onExpand) {
        OutlinedTextField(
            value = ActionTypes.directionNames.getOrElse(direction) { "East" },
            onValueChange = {},
            readOnly = true,
            label = { Text("Direction") },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
            modifier = Modifier.fillMaxWidth().menuAnchor()
        )
        ExposedDropdownMenu(expanded = expanded, onDismissRequest = { onExpand(false) }) {
            ActionTypes.directionNames.forEachIndexed { idx, name ->
                DropdownMenuItem(text = { Text(name) }, onClick = { onSelect(idx) })
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PaletteDropdown(
    paletteId: Int,
    expanded: Boolean,
    onExpand: (Boolean) -> Unit,
    onSelect: (Int) -> Unit
) {
    ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = onExpand) {
        OutlinedTextField(
            value = ActionTypes.paletteNames.getOrElse(paletteId) { "Classic" },
            onValueChange = {},
            readOnly = true,
            label = { Text("Palette") },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
            modifier = Modifier.fillMaxWidth().menuAnchor()
        )
        ExposedDropdownMenu(expanded = expanded, onDismissRequest = { onExpand(false) }) {
            ActionTypes.paletteNames.forEachIndexed { idx, name ->
                DropdownMenuItem(text = { Text(name) }, onClick = { onSelect(idx) })
            }
        }
    }
}

@Composable
private fun RgbPickerDialog(
    initR: Int, initG: Int, initB: Int,
    onConfirm: (Int, Int, Int) -> Unit,
    onDismiss: () -> Unit
) {
    var rv by remember { mutableIntStateOf(initR) }
    var gv by remember { mutableIntStateOf(initG) }
    var bv by remember { mutableIntStateOf(initB) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Pick Color") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(48.dp)
                        .clip(MaterialTheme.shapes.medium)
                        .background(Color(rv, gv, bv))
                )
                Text("Red: $rv", style = MaterialTheme.typography.bodySmall)
                Slider(
                    value = rv.toFloat(),
                    onValueChange = { rv = it.roundToInt() },
                    valueRange = 0f..255f,
                    colors = SliderDefaults.colors(thumbColor = Color.Red, activeTrackColor = Color.Red)
                )
                Text("Green: $gv", style = MaterialTheme.typography.bodySmall)
                Slider(
                    value = gv.toFloat(),
                    onValueChange = { gv = it.roundToInt() },
                    valueRange = 0f..255f,
                    colors = SliderDefaults.colors(thumbColor = Color.Green, activeTrackColor = Color.Green)
                )
                Text("Blue: $bv", style = MaterialTheme.typography.bodySmall)
                Slider(
                    value = bv.toFloat(),
                    onValueChange = { bv = it.roundToInt() },
                    valueRange = 0f..255f,
                    colors = SliderDefaults.colors(thumbColor = Color.Blue, activeTrackColor = Color.Blue)
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { onConfirm(rv, gv, bv) }) { Text("OK") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        }
    )
}
