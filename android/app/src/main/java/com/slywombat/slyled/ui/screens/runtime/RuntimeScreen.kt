package com.slywombat.slyled.ui.screens.runtime

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.slywombat.slyled.data.model.*
import com.slywombat.slyled.viewmodel.RuntimeViewModel
import kotlin.math.roundToInt

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RuntimeScreen(viewModel: RuntimeViewModel = hiltViewModel()) {
    val runners by viewModel.runners.collectAsState()
    val flights by viewModel.flights.collectAsState()
    val shows by viewModel.shows.collectAsState()
    val children by viewModel.children.collectAsState()
    val settings by viewModel.settings.collectAsState()
    val isLoading by viewModel.isLoading.collectAsState()
    val error by viewModel.error.collectAsState()
    val message by viewModel.message.collectAsState()

    var selectedTab by remember { mutableIntStateOf(0) }
    val tabTitles = listOf("Runners", "Flights", "Shows")

    // Dialogs
    var showNewRunner by remember { mutableStateOf(false) }
    var showNewFlight by remember { mutableStateOf(false) }
    var editingFlight by remember { mutableStateOf<Flight?>(null) }
    var showNewShow by remember { mutableStateOf(false) }
    var editingShow by remember { mutableStateOf<Show?>(null) }
    var deleteConfirm by remember { mutableStateOf<Triple<String, Int, String>?>(null) }

    // Brightness / loop local state synced from settings
    var brightness by remember { mutableIntStateOf(settings.globalBrightness ?: 255) }
    var loop by remember { mutableStateOf(settings.runnerLoop) }
    LaunchedEffect(settings) {
        brightness = settings.globalBrightness ?: 255
        loop = settings.runnerLoop
    }

    // Snackbar for messages
    val snackbarHostState = remember { SnackbarHostState() }
    LaunchedEffect(message) {
        message?.let {
            snackbarHostState.showSnackbar(it)
            viewModel.clearMessage()
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        floatingActionButton = {
            FloatingActionButton(onClick = {
                when (selectedTab) {
                    0 -> showNewRunner = true
                    1 -> { editingFlight = null; showNewFlight = true }
                    2 -> { editingShow = null; showNewShow = true }
                }
            }) {
                Icon(Icons.Default.Add, contentDescription = "New")
            }
        }
    ) { padding ->
        Column(modifier = Modifier.fillMaxSize().padding(padding)) {
            // Error banner
            error?.let { msg ->
                Card(
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
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

            // Tab row
            TabRow(selectedTabIndex = selectedTab) {
                tabTitles.forEachIndexed { idx, title ->
                    Tab(selected = selectedTab == idx, onClick = { selectedTab = idx },
                        text = { Text(title) })
                }
            }

            if (isLoading && runners.isEmpty() && flights.isEmpty() && shows.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
            } else {
                when (selectedTab) {
                    0 -> RunnersTab(
                        runners = runners,
                        brightness = brightness,
                        loop = loop,
                        onBrightnessChange = { brightness = it },
                        onBrightnessFinished = { viewModel.saveSettings(brightness, loop) },
                        onLoopChange = { loop = it; viewModel.saveSettings(brightness, it) },
                        onCompute = { viewModel.computeRunner(it) },
                        onSync = { viewModel.syncRunner(it) },
                        onStart = { viewModel.startRunner(it) },
                        onStopAll = { viewModel.stopRunners() },
                        onDelete = { id, name ->
                            deleteConfirm = Triple("runner", id, name)
                        }
                    )
                    1 -> FlightsTab(
                        flights = flights,
                        runners = runners,
                        children = children,
                        onEdit = { editingFlight = it; showNewFlight = true },
                        onDelete = { id, name ->
                            deleteConfirm = Triple("flight", id, name)
                        }
                    )
                    2 -> ShowsTab(
                        shows = shows,
                        flights = flights,
                        onStart = { viewModel.startShow(it) },
                        onStopAll = { viewModel.stopShows() },
                        onEdit = { editingShow = it; showNewShow = true },
                        onDelete = { id, name ->
                            deleteConfirm = Triple("show", id, name)
                        }
                    )
                }
            }
        }
    }

    // Delete confirmation
    deleteConfirm?.let { (kind, id, name) ->
        AlertDialog(
            onDismissRequest = { deleteConfirm = null },
            title = { Text("Delete ${kind.replaceFirstChar { it.uppercase() }}") },
            text = { Text("Delete \"$name\"?") },
            confirmButton = {
                TextButton(onClick = {
                    when (kind) {
                        "runner" -> viewModel.deleteRunner(id)
                        "flight" -> viewModel.deleteFlight(id)
                        "show" -> viewModel.deleteShow(id)
                    }
                    deleteConfirm = null
                }) { Text("Delete", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = {
                TextButton(onClick = { deleteConfirm = null }) { Text("Cancel") }
            }
        )
    }

    // New runner dialog
    if (showNewRunner) {
        NewRunnerDialog(
            onConfirm = { viewModel.createRunner(it); showNewRunner = false },
            onDismiss = { showNewRunner = false }
        )
    }

    // Flight editor dialog
    if (showNewFlight) {
        FlightEditorDialog(
            initial = editingFlight,
            runners = runners,
            children = children,
            onSave = { flight ->
                if (editingFlight != null) {
                    viewModel.updateFlight(editingFlight!!.id, flight)
                } else {
                    viewModel.createFlight(flight)
                }
                showNewFlight = false
                editingFlight = null
            },
            onDismiss = { showNewFlight = false; editingFlight = null }
        )
    }

    // Show editor dialog
    if (showNewShow) {
        ShowEditorDialog(
            initial = editingShow,
            flights = flights,
            onSave = { show ->
                if (editingShow != null) {
                    viewModel.updateShow(editingShow!!.id, show)
                } else {
                    viewModel.createShow(show)
                }
                showNewShow = false
                editingShow = null
            },
            onDismiss = { showNewShow = false; editingShow = null }
        )
    }
}

// ── Runners Tab ──────────────────────────────────────────────────────────────

@Composable
private fun RunnersTab(
    runners: List<RunnerSummary>,
    brightness: Int,
    loop: Boolean,
    onBrightnessChange: (Int) -> Unit,
    onBrightnessFinished: () -> Unit,
    onLoopChange: (Boolean) -> Unit,
    onCompute: (Int) -> Unit,
    onSync: (Int) -> Unit,
    onStart: (Int) -> Unit,
    onStopAll: () -> Unit,
    onDelete: (Int, String) -> Unit
) {
    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        // Global controls
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(12.dp)) {
                Text("Global Controls", style = MaterialTheme.typography.titleSmall)
                Spacer(Modifier.height(8.dp))

                Text("Brightness: $brightness", style = MaterialTheme.typography.bodySmall)
                Slider(
                    value = brightness.toFloat(),
                    onValueChange = { onBrightnessChange(it.roundToInt()) },
                    onValueChangeFinished = onBrightnessFinished,
                    valueRange = 0f..255f,
                    modifier = Modifier.fillMaxWidth()
                )

                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Loop", modifier = Modifier.weight(1f))
                    Switch(checked = loop, onCheckedChange = onLoopChange)
                }

                Spacer(Modifier.height(8.dp))
                Button(
                    onClick = onStopAll,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = MaterialTheme.colorScheme.error),
                    modifier = Modifier.fillMaxWidth()
                ) { Text("Stop All Runners") }
            }
        }

        Spacer(Modifier.height(12.dp))

        if (runners.isEmpty()) {
            Box(Modifier.fillMaxWidth().weight(1f), contentAlignment = Alignment.Center) {
                Text("No runners yet. Tap + to create one.",
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        } else {
            LazyColumn(
                verticalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.weight(1f)
            ) {
                items(runners, key = { it.id }) { runner ->
                    RunnerCard(
                        runner = runner,
                        onCompute = { onCompute(runner.id) },
                        onSync = { onSync(runner.id) },
                        onStart = { onStart(runner.id) },
                        onDelete = { onDelete(runner.id, runner.name) }
                    )
                }
            }
        }
    }
}

@Composable
private fun RunnerCard(
    runner: RunnerSummary,
    onCompute: () -> Unit,
    onSync: () -> Unit,
    onStart: () -> Unit,
    onDelete: () -> Unit
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(runner.name.ifBlank { "Untitled" },
                        style = MaterialTheme.typography.titleSmall)
                    Text("${runner.steps} steps / ${runner.totalDurationS}s",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (runner.computed) {
                    SuggestionChip(
                        onClick = {},
                        label = { Text("Computed", style = MaterialTheme.typography.labelSmall) },
                        colors = SuggestionChipDefaults.suggestionChipColors(
                            containerColor = MaterialTheme.colorScheme.primaryContainer)
                    )
                }
            }
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                FilledTonalButton(onClick = onCompute, modifier = Modifier.weight(1f),
                    contentPadding = PaddingValues(horizontal = 8.dp, vertical = 4.dp)) {
                    Text("Compute", style = MaterialTheme.typography.labelSmall)
                }
                FilledTonalButton(onClick = onSync, modifier = Modifier.weight(1f),
                    contentPadding = PaddingValues(horizontal = 8.dp, vertical = 4.dp)) {
                    Text("Sync", style = MaterialTheme.typography.labelSmall)
                }
                Button(onClick = onStart, modifier = Modifier.weight(1f),
                    contentPadding = PaddingValues(horizontal = 8.dp, vertical = 4.dp)) {
                    Icon(Icons.Default.PlayArrow, contentDescription = null,
                        modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(2.dp))
                    Text("Start", style = MaterialTheme.typography.labelSmall)
                }
                IconButton(onClick = onDelete) {
                    Icon(Icons.Default.Delete, contentDescription = "Delete",
                        tint = MaterialTheme.colorScheme.error)
                }
            }
        }
    }
}

// ── Flights Tab ──────────────────────────────────────────────────────────────

@Composable
private fun FlightsTab(
    flights: List<Flight>,
    runners: List<RunnerSummary>,
    children: List<Child>,
    onEdit: (Flight) -> Unit,
    onDelete: (Int, String) -> Unit
) {
    if (flights.isEmpty()) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Text("No flights yet. Tap + to create one.",
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    } else {
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            items(flights, key = { it.id }) { flight ->
                val runnerName = runners.find { it.id == flight.runnerId }?.name ?: "None"
                val performerNames = flight.performerIds.mapNotNull { pid ->
                    children.find { it.id == pid }?.let { it.name.ifBlank { it.hostname } }
                }

                Card(modifier = Modifier.fillMaxWidth()) {
                    Row(
                        modifier = Modifier.padding(12.dp).fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text(flight.name.ifBlank { "Untitled" },
                                style = MaterialTheme.typography.titleSmall)
                            Text("Runner: $runnerName",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                            if (performerNames.isNotEmpty()) {
                                Text("Performers: ${performerNames.joinToString(", ")}",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            Text("Priority: ${flight.priority}",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        IconButton(onClick = { onEdit(flight) }) {
                            Icon(Icons.Default.Edit, contentDescription = "Edit",
                                tint = MaterialTheme.colorScheme.primary)
                        }
                        IconButton(onClick = { onDelete(flight.id, flight.name) }) {
                            Icon(Icons.Default.Delete, contentDescription = "Delete",
                                tint = MaterialTheme.colorScheme.error)
                        }
                    }
                }
            }
        }
    }
}

// ── Shows Tab ────────────────────────────────────────────────────────────────

@Composable
private fun ShowsTab(
    shows: List<Show>,
    flights: List<Flight>,
    onStart: (Int) -> Unit,
    onStopAll: () -> Unit,
    onEdit: (Show) -> Unit,
    onDelete: (Int, String) -> Unit
) {
    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Button(
            onClick = onStopAll,
            colors = ButtonDefaults.buttonColors(
                containerColor = MaterialTheme.colorScheme.error),
            modifier = Modifier.fillMaxWidth()
        ) { Text("Stop All Shows") }

        Spacer(Modifier.height(12.dp))

        if (shows.isEmpty()) {
            Box(Modifier.fillMaxWidth().weight(1f), contentAlignment = Alignment.Center) {
                Text("No shows yet. Tap + to create one.",
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        } else {
            LazyColumn(
                verticalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.weight(1f)
            ) {
                items(shows, key = { it.id }) { show ->
                    val flightNames = show.flightIds.mapNotNull { fid ->
                        flights.find { it.id == fid }?.name
                    }

                    Card(modifier = Modifier.fillMaxWidth()) {
                        Column(modifier = Modifier.padding(12.dp)) {
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Column(modifier = Modifier.weight(1f)) {
                                    Text(show.name.ifBlank { "Untitled" },
                                        style = MaterialTheme.typography.titleSmall)
                                    if (flightNames.isNotEmpty()) {
                                        Text("Flights: ${flightNames.joinToString(", ")}",
                                            style = MaterialTheme.typography.bodySmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                                    }
                                }
                                if (show.loop) {
                                    SuggestionChip(
                                        onClick = {},
                                        label = { Text("Loop", style = MaterialTheme.typography.labelSmall) }
                                    )
                                }
                            }
                            Spacer(Modifier.height(8.dp))
                            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                                Button(onClick = { onStart(show.id) },
                                    modifier = Modifier.weight(1f)) {
                                    Icon(Icons.Default.PlayArrow, contentDescription = null,
                                        modifier = Modifier.size(16.dp))
                                    Spacer(Modifier.width(4.dp))
                                    Text("Start")
                                }
                                IconButton(onClick = { onEdit(show) }) {
                                    Icon(Icons.Default.Edit, contentDescription = "Edit",
                                        tint = MaterialTheme.colorScheme.primary)
                                }
                                IconButton(onClick = { onDelete(show.id, show.name) }) {
                                    Icon(Icons.Default.Delete, contentDescription = "Delete",
                                        tint = MaterialTheme.colorScheme.error)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

// ── Dialogs ──────────────────────────────────────────────────────────────────

@Composable
private fun NewRunnerDialog(onConfirm: (String) -> Unit, onDismiss: () -> Unit) {
    var name by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("New Runner") },
        text = {
            OutlinedTextField(
                value = name,
                onValueChange = { name = it },
                label = { Text("Runner name") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth()
            )
        },
        confirmButton = {
            TextButton(onClick = { onConfirm(name) }, enabled = name.isNotBlank()) {
                Text("Create")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        }
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun FlightEditorDialog(
    initial: Flight?,
    runners: List<RunnerSummary>,
    children: List<Child>,
    onSave: (Flight) -> Unit,
    onDismiss: () -> Unit
) {
    var name by remember { mutableStateOf(initial?.name ?: "") }
    var runnerId by remember { mutableStateOf(initial?.runnerId) }
    var selectedPerformers by remember {
        mutableStateOf(initial?.performerIds?.toSet() ?: emptySet())
    }
    var priority by remember { mutableIntStateOf(initial?.priority ?: 1) }
    var runnerExpanded by remember { mutableStateOf(false) }

    val isEditing = initial != null

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (isEditing) "Edit Flight" else "New Flight") },
        text = {
            Column(
                modifier = Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("Flight name") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )

                // Runner picker
                ExposedDropdownMenuBox(
                    expanded = runnerExpanded,
                    onExpandedChange = { runnerExpanded = it }
                ) {
                    OutlinedTextField(
                        value = runners.find { it.id == runnerId }?.name ?: "Select runner",
                        onValueChange = {},
                        readOnly = true,
                        label = { Text("Runner") },
                        trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(runnerExpanded) },
                        modifier = Modifier.fillMaxWidth().menuAnchor()
                    )
                    ExposedDropdownMenu(
                        expanded = runnerExpanded,
                        onDismissRequest = { runnerExpanded = false }
                    ) {
                        runners.forEach { runner ->
                            DropdownMenuItem(
                                text = { Text(runner.name) },
                                onClick = { runnerId = runner.id; runnerExpanded = false }
                            )
                        }
                    }
                }

                // Performer checkboxes
                Text("Performers", style = MaterialTheme.typography.labelMedium)
                children.forEach { child ->
                    val label = child.name.ifBlank { child.hostname }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(
                            checked = child.id in selectedPerformers,
                            onCheckedChange = { checked ->
                                selectedPerformers = if (checked) {
                                    selectedPerformers + child.id
                                } else {
                                    selectedPerformers - child.id
                                }
                            }
                        )
                        Text(label, modifier = Modifier.clickable {
                            selectedPerformers = if (child.id in selectedPerformers) {
                                selectedPerformers - child.id
                            } else {
                                selectedPerformers + child.id
                            }
                        })
                    }
                }

                // Priority
                Text("Priority: $priority", style = MaterialTheme.typography.bodySmall)
                Slider(
                    value = priority.toFloat(),
                    onValueChange = { priority = it.roundToInt() },
                    valueRange = 1f..10f,
                    steps = 8,
                    modifier = Modifier.fillMaxWidth()
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    onSave(Flight(
                        id = initial?.id ?: -1,
                        name = name,
                        runnerId = runnerId,
                        performerIds = selectedPerformers.toList(),
                        priority = priority
                    ))
                },
                enabled = name.isNotBlank()
            ) { Text("Save") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        }
    )
}

@Composable
private fun ShowEditorDialog(
    initial: Show?,
    flights: List<Flight>,
    onSave: (Show) -> Unit,
    onDismiss: () -> Unit
) {
    var name by remember { mutableStateOf(initial?.name ?: "") }
    var selectedFlights by remember {
        mutableStateOf(initial?.flightIds?.toSet() ?: emptySet())
    }
    var loop by remember { mutableStateOf(initial?.loop ?: true) }

    val isEditing = initial != null

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (isEditing) "Edit Show" else "New Show") },
        text = {
            Column(
                modifier = Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("Show name") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )

                // Flight checkboxes
                Text("Flights", style = MaterialTheme.typography.labelMedium)
                if (flights.isEmpty()) {
                    Text("No flights available",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                } else {
                    flights.forEach { flight ->
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Checkbox(
                                checked = flight.id in selectedFlights,
                                onCheckedChange = { checked ->
                                    selectedFlights = if (checked) {
                                        selectedFlights + flight.id
                                    } else {
                                        selectedFlights - flight.id
                                    }
                                }
                            )
                            Text(flight.name.ifBlank { "Flight #${flight.id}" },
                                modifier = Modifier.clickable {
                                    selectedFlights = if (flight.id in selectedFlights) {
                                        selectedFlights - flight.id
                                    } else {
                                        selectedFlights + flight.id
                                    }
                                })
                        }
                    }
                }

                // Loop toggle
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Loop", modifier = Modifier.weight(1f))
                    Switch(checked = loop, onCheckedChange = { loop = it })
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    onSave(Show(
                        id = initial?.id ?: -1,
                        name = name,
                        flightIds = selectedFlights.toList(),
                        loop = loop
                    ))
                },
                enabled = name.isNotBlank()
            ) { Text("Save") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        }
    )
}
