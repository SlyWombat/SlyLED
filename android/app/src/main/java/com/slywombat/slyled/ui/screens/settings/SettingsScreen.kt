package com.slywombat.slyled.ui.screens.settings

import android.content.Intent
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.slywombat.slyled.data.model.DmxProfile
import com.slywombat.slyled.ui.theme.RedError
import com.slywombat.slyled.viewmodel.SettingsViewModel
import kotlinx.coroutines.flow.collectLatest
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    onDisconnect: () -> Unit = {},
    viewModel: SettingsViewModel = hiltViewModel()
) {
    val settings by viewModel.settings.collectAsState()
    val isSaving by viewModel.isSaving.collectAsState()
    val context = LocalContext.current

    var name by remember(settings.name) { mutableStateOf(settings.name) }
    var units by remember(settings.units) { mutableIntStateOf(settings.units) }
    var canvasW by remember(settings.canvasW) { mutableStateOf(settings.canvasW.toString()) }
    var canvasH by remember(settings.canvasH) { mutableStateOf(settings.canvasH.toString()) }
    var darkMode by remember(settings.darkMode) { mutableIntStateOf(settings.darkMode) }
    var logging by remember(settings.logging) { mutableStateOf(settings.logging) }

    var showResetConfirm by remember { mutableStateOf(false) }
    var unitsExpanded by remember { mutableStateOf(false) }

    // File pickers for config/show import
    fun readFileAsString(uri: Uri): String? {
        return try {
            context.contentResolver.openInputStream(uri)?.bufferedReader()?.readText()
        } catch (_: Exception) { null }
    }
    val configPicker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        uri?.let { readFileAsString(it)?.let { json -> viewModel.importConfig(json) } }
    }
    val showPicker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        uri?.let { readFileAsString(it)?.let { json -> viewModel.importShow(json) } }
    }

    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(Unit) {
        viewModel.message.collectLatest { msg ->
            snackbarHostState.showSnackbar(msg)
        }
    }

    // Handle exported JSON via share intent
    LaunchedEffect(Unit) {
        viewModel.exportedJson.collectLatest { json ->
            val sendIntent = Intent().apply {
                action = Intent.ACTION_SEND
                putExtra(Intent.EXTRA_TEXT, json)
                type = "application/json"
            }
            context.startActivity(Intent.createChooser(sendIntent, "Export"))
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 16.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Spacer(Modifier.height(4.dp))

            // App Settings Card
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        "App Settings",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold
                    )
                    Spacer(Modifier.height(12.dp))

                    OutlinedTextField(
                        value = name,
                        onValueChange = { name = it },
                        label = { Text("System Name") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                    Spacer(Modifier.height(8.dp))

                    // Units dropdown
                    ExposedDropdownMenuBox(
                        expanded = unitsExpanded,
                        onExpandedChange = { unitsExpanded = it }
                    ) {
                        OutlinedTextField(
                            value = if (units == 0) "Metric (mm)" else "Imperial (in)",
                            onValueChange = {},
                            readOnly = true,
                            label = { Text("Units") },
                            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = unitsExpanded) },
                            modifier = Modifier.fillMaxWidth().menuAnchor()
                        )
                        ExposedDropdownMenu(
                            expanded = unitsExpanded,
                            onDismissRequest = { unitsExpanded = false }
                        ) {
                            DropdownMenuItem(
                                text = { Text("Metric (mm)") },
                                onClick = { units = 0; unitsExpanded = false }
                            )
                            DropdownMenuItem(
                                text = { Text("Imperial (in)") },
                                onClick = { units = 1; unitsExpanded = false }
                            )
                        }
                    }
                    Spacer(Modifier.height(8.dp))

                    // Stage dimensions
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        OutlinedTextField(
                            value = canvasW,
                            onValueChange = { canvasW = it.filter { c -> c.isDigit() } },
                            label = { Text("Stage W") },
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                            singleLine = true,
                            modifier = Modifier.weight(1f)
                        )
                        OutlinedTextField(
                            value = canvasH,
                            onValueChange = { canvasH = it.filter { c -> c.isDigit() } },
                            label = { Text("Stage H") },
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                            singleLine = true,
                            modifier = Modifier.weight(1f)
                        )
                    }
                    Spacer(Modifier.height(12.dp))

                    // Dark mode switch
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text("Dark Mode", style = MaterialTheme.typography.bodyMedium)
                        Switch(
                            checked = darkMode == 1,
                            onCheckedChange = { darkMode = if (it) 1 else 0 }
                        )
                    }

                    // Logging switch
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text("Logging", style = MaterialTheme.typography.bodyMedium)
                        Switch(
                            checked = logging,
                            onCheckedChange = { logging = it }
                        )
                    }
                    Spacer(Modifier.height(8.dp))

                    Button(
                        onClick = {
                            viewModel.saveSettings(
                                name = name,
                                units = units,
                                canvasW = canvasW.toIntOrNull() ?: 10000,
                                canvasH = canvasH.toIntOrNull() ?: 5000,
                                darkMode = darkMode,
                                logging = logging
                            )
                        },
                        enabled = !isSaving,
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        if (isSaving) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(18.dp),
                                strokeWidth = 2.dp,
                                color = MaterialTheme.colorScheme.onPrimary
                            )
                            Spacer(Modifier.width(8.dp))
                        }
                        Text("Save Settings")
                    }
                }
            }

            // Config Save/Load Card
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        "Configuration",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold
                    )
                    Spacer(Modifier.height(12.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        OutlinedButton(
                            onClick = { viewModel.exportConfig() },
                            modifier = Modifier.weight(1f)
                        ) {
                            Icon(Icons.Default.Upload, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("Save Config")
                        }
                        OutlinedButton(
                            onClick = { configPicker.launch("application/json") },
                            modifier = Modifier.weight(1f)
                        ) {
                            Icon(Icons.Default.Download, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("Load Config")
                        }
                    }
                }
            }

            // Show Save/Load Card
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        "Show Data",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold
                    )
                    Spacer(Modifier.height(12.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        OutlinedButton(
                            onClick = { viewModel.exportShow() },
                            modifier = Modifier.weight(1f)
                        ) {
                            Icon(Icons.Default.Upload, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("Save Show")
                        }
                        OutlinedButton(
                            onClick = { showPicker.launch("application/json") },
                            modifier = Modifier.weight(1f)
                        ) {
                            Icon(Icons.Default.Download, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("Load Show")
                        }
                    }
                    Spacer(Modifier.height(8.dp))
                    FilledTonalButton(
                        onClick = { viewModel.generateDemo() },
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Icon(Icons.Default.AutoAwesome, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(4.dp))
                        Text("Generate Demo Show")
                    }
                }
            }

            // DMX Control Card
            DmxControlSection(viewModel = viewModel)

            // Factory Reset Card
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(
                    containerColor = RedError.copy(alpha = 0.08f)
                )
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        "Danger Zone",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold,
                        color = RedError
                    )
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Factory reset deletes all performers, actions, runners, flights, and shows from the server.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Spacer(Modifier.height(12.dp))
                    OutlinedButton(
                        onClick = { showResetConfirm = true },
                        modifier = Modifier.fillMaxWidth(),
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = RedError)
                    ) {
                        Icon(Icons.Default.DeleteForever, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(4.dp))
                        Text("Factory Reset")
                    }
                }
            }

            // Disconnect button
            Button(
                onClick = onDisconnect,
                colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error),
                modifier = Modifier.fillMaxWidth()
            ) {
                Icon(Icons.Default.LinkOff, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(4.dp))
                Text("Disconnect")
            }

            Spacer(Modifier.height(16.dp))
        }
    }

    // Factory Reset confirmation dialog
    if (showResetConfirm) {
        AlertDialog(
            onDismissRequest = { showResetConfirm = false },
            title = { Text("Factory Reset") },
            text = { Text("This will permanently delete all data on the server. Are you sure?") },
            confirmButton = {
                TextButton(
                    onClick = {
                        viewModel.factoryReset()
                        showResetConfirm = false
                    },
                    colors = ButtonDefaults.textButtonColors(contentColor = RedError)
                ) {
                    Text("Reset Everything")
                }
            },
            dismissButton = {
                TextButton(onClick = { showResetConfirm = false }) {
                    Text("Cancel")
                }
            }
        )
    }
}

// ── DMX Control Section ────────────────────────────────────────────────

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DmxControlSection(viewModel: SettingsViewModel) {
    val dmxStatus by viewModel.dmxStatus.collectAsState()
    val dmxSettings by viewModel.dmxSettings.collectAsState()
    var showProfileDialog by remember { mutableStateOf(false) }

    // Extract status fields from JsonObject
    val running = dmxStatus?.get("running")?.jsonPrimitive?.booleanOrNull ?: false
    val universes = dmxStatus?.get("universes")?.jsonPrimitive?.intOrNull ?: 0
    val statusFrameRate = dmxStatus?.get("frameRate")?.jsonPrimitive?.intOrNull ?: 40
    val nodes = dmxStatus?.get("nodes")?.jsonPrimitive?.intOrNull

    // Local state initialized from settings
    val settingsProtocol = dmxSettings?.get("protocol")?.jsonPrimitive?.contentOrNull ?: "artnet"
    val settingsFrameRate = dmxSettings?.get("frameRate")?.jsonPrimitive?.intOrNull ?: 40
    val settingsBindIp = dmxSettings?.get("bindIp")?.jsonPrimitive?.contentOrNull ?: "0.0.0.0"
    val settingsSacnPriority = dmxSettings?.get("sacnPriority")?.jsonPrimitive?.intOrNull ?: 100
    val settingsSacnSourceName = dmxSettings?.get("sacnSourceName")?.jsonPrimitive?.contentOrNull ?: ""
    val settingsUnicastTargets = dmxSettings?.get("unicastTargets")?.jsonObject
    val initialUnicastText = settingsUnicastTargets?.entries?.joinToString("\n") { "${it.key}:${it.value.jsonPrimitive.content}" } ?: ""

    var selectedProtocol by remember(settingsProtocol) { mutableStateOf(settingsProtocol) }
    var frameRateText by remember(settingsFrameRate) { mutableStateOf(settingsFrameRate.toString()) }
    var bindIp by remember(settingsBindIp) { mutableStateOf(settingsBindIp) }
    var sacnPriority by remember(settingsSacnPriority) { mutableStateOf(settingsSacnPriority.toFloat()) }
    var sacnSourceName by remember(settingsSacnSourceName) { mutableStateOf(settingsSacnSourceName) }
    var unicastTargetsText by remember(initialUnicastText) { mutableStateOf(initialUnicastText) }

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                "DMX Control",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold
            )
            Spacer(Modifier.height(12.dp))

            // Protocol selector
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text("Protocol:", style = MaterialTheme.typography.bodyMedium)
                SingleChoiceSegmentedButtonRow(modifier = Modifier.weight(1f)) {
                    SegmentedButton(
                        selected = selectedProtocol == "artnet",
                        onClick = { selectedProtocol = "artnet" },
                        shape = SegmentedButtonDefaults.itemShape(index = 0, count = 2)
                    ) { Text("Art-Net") }
                    SegmentedButton(
                        selected = selectedProtocol == "sacn",
                        onClick = { selectedProtocol = "sacn" },
                        shape = SegmentedButtonDefaults.itemShape(index = 1, count = 2)
                    ) { Text("sACN") }
                }
            }
            Spacer(Modifier.height(8.dp))

            // Frame Rate
            OutlinedTextField(
                value = frameRateText,
                onValueChange = { frameRateText = it.filter { c -> c.isDigit() } },
                label = { Text("Frame Rate (1-44 Hz)") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                singleLine = true,
                modifier = Modifier.fillMaxWidth()
            )
            Spacer(Modifier.height(8.dp))

            // Bind IP
            OutlinedTextField(
                value = bindIp,
                onValueChange = { bindIp = it },
                label = { Text("Bind IP") },
                placeholder = { Text("0.0.0.0") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth()
            )
            Spacer(Modifier.height(8.dp))

            // sACN-specific fields
            if (selectedProtocol == "sacn") {
                // sACN Priority slider
                Column(modifier = Modifier.fillMaxWidth()) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text("sACN Priority", style = MaterialTheme.typography.bodyMedium)
                        Text("${sacnPriority.toInt()}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Slider(
                        value = sacnPriority,
                        onValueChange = { sacnPriority = it },
                        valueRange = 0f..200f,
                        steps = 0,
                        modifier = Modifier.fillMaxWidth()
                    )
                }
                Spacer(Modifier.height(8.dp))

                // sACN Source Name
                OutlinedTextField(
                    value = sacnSourceName,
                    onValueChange = { sacnSourceName = it },
                    label = { Text("sACN Source Name") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
                Spacer(Modifier.height(8.dp))
            }

            // Art-Net unicast targets
            if (selectedProtocol == "artnet") {
                OutlinedTextField(
                    value = unicastTargetsText,
                    onValueChange = { unicastTargetsText = it },
                    label = { Text("Unicast Targets") },
                    placeholder = { Text("1:192.168.1.100") },
                    supportingText = { Text("One per line: universe:ip") },
                    minLines = 2,
                    maxLines = 4,
                    modifier = Modifier.fillMaxWidth()
                )
                Spacer(Modifier.height(8.dp))
            }

            // Save Settings button
            Button(
                onClick = {
                    val targets = mutableMapOf<String, String>()
                    unicastTargetsText.lines().filter { it.contains(":") }.forEach { line ->
                        val parts = line.split(":", limit = 2)
                        if (parts.size == 2) {
                            targets[parts[0].trim()] = parts[1].trim()
                        }
                    }
                    viewModel.saveDmxSettings(
                        protocol = selectedProtocol,
                        frameRate = (frameRateText.toIntOrNull() ?: 40).coerceIn(1, 44),
                        bindIp = bindIp.ifBlank { "0.0.0.0" },
                        sacnPriority = sacnPriority.toInt(),
                        sacnSourceName = sacnSourceName,
                        unicastTargets = targets
                    )
                },
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Save DMX Settings")
            }
            Spacer(Modifier.height(12.dp))

            // Status display
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                AssistChip(
                    onClick = { viewModel.loadDmxStatus() },
                    label = { Text(if (running) "Running" else "Stopped") },
                    leadingIcon = {
                        Icon(
                            if (running) Icons.Default.PlayArrow else Icons.Default.Stop,
                            contentDescription = null,
                            modifier = Modifier.size(18.dp)
                        )
                    },
                    colors = AssistChipDefaults.assistChipColors(
                        containerColor = if (running)
                            MaterialTheme.colorScheme.primaryContainer
                        else
                            MaterialTheme.colorScheme.surfaceVariant
                    )
                )
                AssistChip(
                    onClick = {},
                    label = { Text("$universes univ") }
                )
                AssistChip(
                    onClick = {},
                    label = { Text("$statusFrameRate Hz") }
                )
                if (selectedProtocol == "artnet" && nodes != null) {
                    AssistChip(
                        onClick = {},
                        label = { Text("$nodes nodes") }
                    )
                }
            }
            Spacer(Modifier.height(12.dp))

            // Control buttons
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Button(
                    onClick = { viewModel.startDmx(selectedProtocol) },
                    enabled = !running,
                    modifier = Modifier.weight(1f)
                ) {
                    Icon(Icons.Default.PlayArrow, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("Start")
                }
                Button(
                    onClick = { viewModel.stopDmx() },
                    enabled = running,
                    modifier = Modifier.weight(1f)
                ) {
                    Icon(Icons.Default.Stop, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("Stop")
                }
                OutlinedButton(
                    onClick = { viewModel.dmxBlackout() },
                    modifier = Modifier.weight(1f)
                ) {
                    Icon(Icons.Default.DarkMode, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("Blackout")
                }
            }
            Spacer(Modifier.height(12.dp))

            // Browse Profiles button
            FilledTonalButton(
                onClick = {
                    viewModel.loadDmxProfiles()
                    showProfileDialog = true
                },
                modifier = Modifier.fillMaxWidth()
            ) {
                Icon(Icons.Default.Lightbulb, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(4.dp))
                Text("Browse Fixture Profiles")
            }
        }
    }

    if (showProfileDialog) {
        DmxProfileBrowserDialog(
            viewModel = viewModel,
            onDismiss = { showProfileDialog = false }
        )
    }
}

// ── DMX Profile Browser Dialog ─────────────────────────────────────────

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DmxProfileBrowserDialog(
    viewModel: SettingsViewModel,
    onDismiss: () -> Unit
) {
    val profiles by viewModel.dmxProfiles.collectAsState()
    var selectedCategory by remember { mutableStateOf<String?>(null) }
    var categoryExpanded by remember { mutableStateOf(false) }

    val categories = listOf("All", "par", "wash", "spot", "bar", "moving", "strobe", "laser", "fog", "other")
    val filteredProfiles = if (selectedCategory == null) profiles
        else profiles.filter { it.category == selectedCategory }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Fixture Profiles") },
        text = {
            Column(modifier = Modifier.fillMaxWidth()) {
                // Category filter
                ExposedDropdownMenuBox(
                    expanded = categoryExpanded,
                    onExpandedChange = { categoryExpanded = it }
                ) {
                    OutlinedTextField(
                        value = selectedCategory?.replaceFirstChar { it.uppercase() } ?: "All",
                        onValueChange = {},
                        readOnly = true,
                        label = { Text("Category") },
                        trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = categoryExpanded) },
                        modifier = Modifier.fillMaxWidth().menuAnchor()
                    )
                    ExposedDropdownMenu(
                        expanded = categoryExpanded,
                        onDismissRequest = { categoryExpanded = false }
                    ) {
                        categories.forEach { cat ->
                            DropdownMenuItem(
                                text = { Text(cat.replaceFirstChar { it.uppercase() }) },
                                onClick = {
                                    selectedCategory = if (cat == "All") null else cat
                                    viewModel.loadDmxProfiles(selectedCategory)
                                    categoryExpanded = false
                                }
                            )
                        }
                    }
                }
                Spacer(Modifier.height(8.dp))

                // Profile list
                if (filteredProfiles.isEmpty()) {
                    Text(
                        "No profiles found",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(vertical = 16.dp)
                    )
                } else {
                    LazyColumn(
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(max = 400.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp)
                    ) {
                        items(filteredProfiles, key = { it.id }) { profile ->
                            DmxProfileRow(profile)
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) {
                Text("Close")
            }
        }
    )
}

@Composable
private fun DmxProfileRow(profile: DmxProfile) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f)
        )
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    profile.name,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium
                )
                Text(
                    profile.manufacturer,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
            Row(
                horizontalArrangement = Arrangement.spacedBy(4.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                AssistChip(
                    onClick = {},
                    label = { Text(profile.category) },
                    modifier = Modifier.height(24.dp)
                )
                Text(
                    "${profile.channelCount}ch",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                if (profile.beamWidth > 0) {
                    Text(
                        "${profile.beamWidth}\u00B0",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
        }
    }
}
