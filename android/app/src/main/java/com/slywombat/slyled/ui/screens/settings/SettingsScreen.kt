package com.slywombat.slyled.ui.screens.settings

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
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
import com.slywombat.slyled.BuildConfig
import com.slywombat.slyled.audio.MicAutoBrightness
import com.slywombat.slyled.data.model.DmxProfile
import com.slywombat.slyled.ui.theme.GreenOnline
import com.slywombat.slyled.ui.theme.RedError
import com.slywombat.slyled.viewmodel.SettingsViewModel
import kotlin.math.pow
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
    // Stage dimensions in cm (canvasW/H are mm, divide by 10 for cm display)
    var stageWcm by remember(settings.canvasW) { mutableStateOf((settings.canvasW / 10).toString()) }
    var stageHcm by remember(settings.canvasH) { mutableStateOf((settings.canvasH / 10).toString()) }
    var stageDcm by remember { mutableStateOf("150") }

    // #649 — prefer manual stage when stageBoundsManual=true; fall back to
    // server's auto-derived values when manual is unset. Reading raw w/h/d
    // can yield the auto bounds because the server overwrites _stage when
    // manual is off — so always pick the right source explicitly.
    LaunchedEffect(Unit) {
        try {
            val stage = viewModel.getStage()
            if (stage != null) {
                val w = if (stage.stageBoundsManual || stage.auto == null) stage.w else stage.auto.w
                val h = if (stage.stageBoundsManual || stage.auto == null) stage.h else stage.auto.h
                val d = if (stage.stageBoundsManual || stage.auto == null) stage.d else stage.auto.d
                stageWcm = (w * 100).toInt().toString()
                stageHcm = (h * 100).toInt().toString()
                stageDcm = (d * 100).toInt().toString()
            }
        } catch (_: Exception) {}
    }
    var darkMode by remember(settings.darkMode) { mutableIntStateOf(settings.darkMode) }
    var logging by remember(settings.logging) { mutableStateOf(settings.logging) }

    var showResetConfirm by remember { mutableStateOf(false) }
    var unitsExpanded by remember { mutableStateOf(false) }
    // #826 — empirical aim-axis calibration wizard state.
    var showAimWizard by remember { mutableStateOf(false) }

    // File picker for show import (config import removed in #649 —
    // operator app does not edit project state).
    fun readFileAsString(uri: Uri): String? {
        return try {
            context.contentResolver.openInputStream(uri)?.bufferedReader()?.readText()
        } catch (_: Exception) { null }
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

                    // Stage dimensions (cm) — read-only on the operator app
                    // (#649): editing the stage belongs in the desktop SPA.
                    Text("Stage Dimensions (${if (units == 0) "cm" else "approx cm"})",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        OutlinedTextField(
                            value = stageWcm,
                            onValueChange = {},
                            readOnly = true,
                            label = { Text("W") },
                            singleLine = true,
                            modifier = Modifier.weight(1f)
                        )
                        OutlinedTextField(
                            value = stageHcm,
                            onValueChange = {},
                            readOnly = true,
                            label = { Text("H") },
                            singleLine = true,
                            modifier = Modifier.weight(1f)
                        )
                        OutlinedTextField(
                            value = stageDcm,
                            onValueChange = {},
                            readOnly = true,
                            label = { Text("D") },
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
                            // #649 — operator app does not edit stage; only
                            // local-preference fields go through saveSettings.
                            viewModel.saveSettings(
                                name = name,
                                units = units,
                                canvasW = settings.canvasW,
                                canvasH = settings.canvasH,
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

            // #649 — Save/Load Config removed: editing the project belongs
            // on the desktop SPA. Show import/export stays for live ops.

            // #804 — Auto Brightness section.
            AutoBrightnessSection(viewModel = viewModel, settings = settings)

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

            // #826 — Aim Calibration entry. Opens the empirical aim-axis
            // wizard so the operator measures the phone's pointer axes
            // for THIS grip instead of the server guessing from
            // Surface.ROTATION_*. Run once per device or grip change.
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        "Aim Calibration",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold,
                    )
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Three quick gestures so the head tracks your phone's actual pointer " +
                            "direction. Run once per device, or any time you change grip.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(12.dp))
                    OutlinedButton(
                        onClick = { showAimWizard = true },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("Run Aim Calibration Wizard")
                    }
                }
            }

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
                        "Factory reset deletes all devices, fixtures, actions, timelines, and effects from the server.",
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

            // #824 — version footer. App and orchestrator track independently
            // (decoupled in build_release.ps1), so the two version strings
            // will diverge naturally on any release that touches only one
            // side. Pre-fix the banner fired on ANY difference, training
            // the operator to ignore it; now it fires only when the
            // orchestrator is below the APK's declared minimum (see
            // Compatibility.MIN_ORCHESTRATOR_VERSION) — i.e. on a real
            // known-incompatibility, not arbitrary drift.
            val appVersion = BuildConfig.VERSION_NAME
            val orchVersion by viewModel.serverVersion.collectAsState()
            val versionStr = "App v$appVersion (${BuildConfig.VERSION_CODE}) " +
                              "/ Orchestrator v${if (orchVersion.isEmpty()) "?" else orchVersion}"
            val orchTooOld = com.slywombat.slyled.data.repository.Compatibility
                .orchestratorBelowFloor(orchVersion)
            if (orchTooOld) {
                Spacer(Modifier.height(8.dp))
                Surface(
                    color = MaterialTheme.colorScheme.errorContainer,
                    contentColor = MaterialTheme.colorScheme.onErrorContainer,
                    shape = MaterialTheme.shapes.small,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Row(modifier = Modifier.padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Default.Warning, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Text(
                            text = "Orchestrator v$orchVersion is older than this APK's " +
                                    "minimum (${com.slywombat.slyled.data.repository.Compatibility.MIN_ORCHESTRATOR_VERSION}). " +
                                    "Update the orchestrator before running live.",
                            style = MaterialTheme.typography.bodySmall
                        )
                    }
                }
            }
            Column(modifier = Modifier.fillMaxWidth().padding(top = 8.dp)) {
                Text(
                    text = "App:           v$appVersion (${BuildConfig.VERSION_CODE})",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    text = "Orchestrator:  v${if (orchVersion.isEmpty()) "?" else orchVersion}",
                    style = MaterialTheme.typography.bodySmall,
                    color = if (orchTooOld) MaterialTheme.colorScheme.error
                             else MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.clickable {
                        val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                        cm.setPrimaryClip(ClipData.newPlainText("SlyLED version", versionStr))
                        Toast.makeText(context, "Copied $versionStr", Toast.LENGTH_SHORT).show()
                    }
                )
            }

            Spacer(Modifier.height(16.dp))
        }
    }

    // #826 — Aim Calibration wizard dialog.
    if (showAimWizard) {
        AimWizardDialog(
            onDismiss = { showAimWizard = false },
            submitWizard = viewModel::submitAimWizard,
        )
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

    // Extract status fields from DmxStatus
    val running = dmxStatus?.running ?: false
    val universes = dmxStatus?.universes ?: 0
    val statusFrameRate = dmxStatus?.fps ?: 40
    val nodes: Int? = null  // Not in DmxStatus yet

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

// #804 — Auto Brightness Settings card. Mirrors the Stage modal toggle and
// adds the full tunable surface: mode (LiteRT greyed = coming-soon, DSP
// fallback active), mic source picker, sensitivity, floor/ceiling,
// attack/release, live envelope meter, manual brightness fallback slider,
// and the RECORD_AUDIO permission gate.
@Composable
private fun AutoBrightnessSection(
    viewModel: SettingsViewModel,
    settings: com.slywombat.slyled.data.model.Settings,
) {
    val enabled by viewModel.autoBrightnessEnabled.collectAsState()
    val state by viewModel.autoBrightnessState.collectAsState()
    val envelope by viewModel.autoBrightnessEnvelope.collectAsState()

    // #804 — read tunables from StateFlows so the panel always shows the
    // current value (including persisted load + edits made on the Stage
    // modal). Slider drags push back through configureAutoBrightness().
    val sens by viewModel.autoBrightnessSensitivityFlow.collectAsState()
    val fl by viewModel.autoBrightnessFloorFlow.collectAsState()
    val ce by viewModel.autoBrightnessCeilingFlow.collectAsState()
    val atk by viewModel.autoBrightnessAttackMsFlow.collectAsState()
    val rel by viewModel.autoBrightnessReleaseMsFlow.collectAsState()

    val manualBrightness = settings.globalBrightness ?: 255
    var manualSlider by remember(manualBrightness) { mutableFloatStateOf(manualBrightness.toFloat()) }
    var modeLite by remember { mutableStateOf(false) }  // LiteRT greyed for v1

    val micPermLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted -> if (granted) viewModel.setAutoBrightnessEnabled(true) }

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                "Auto Brightness",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.height(12.dp))

            // Enable + state badge
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text("Enable", style = MaterialTheme.typography.bodyMedium)
                    val (lab, col) = autoBrightnessSettingsLabel(enabled, state)
                    Text(lab, style = MaterialTheme.typography.labelSmall, color = col)
                }
                Switch(
                    checked = enabled,
                    onCheckedChange = { want ->
                        if (want && !viewModel.autoBrightnessHasPermission()) {
                            micPermLauncher.launch(Manifest.permission.RECORD_AUDIO)
                        } else {
                            viewModel.setAutoBrightnessEnabled(want)
                        }
                    },
                )
            }
            Spacer(Modifier.height(8.dp))

            // Mode segment — LiteRT disabled in v1 (no model bundled).
            Text(
                "Mode",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
                SegmentedButton(
                    selected = modeLite,
                    onClick = { /* coming soon */ },
                    enabled = false,
                    shape = SegmentedButtonDefaults.itemShape(index = 0, count = 2),
                ) { Text("LiteRT (soon)") }
                SegmentedButton(
                    selected = !modeLite,
                    onClick = { modeLite = false },
                    shape = SegmentedButtonDefaults.itemShape(index = 1, count = 2),
                ) { Text("DSP fallback") }
            }
            Spacer(Modifier.height(8.dp))

            // #820 — Audio Sources picker (replaces the prior six-AudioSource-
            // constant dropdown). Four semantic sources operators actually
            // think in: Microphone / Playback Capture / Remote Submix /
            // USB Audio. State-aware hints surface device-specific
            // unavailability (no USB, Remote Submix denied, MediaProjection
            // consent pending). Switching restarts capture transparently
            // — preview mode keeps the meter live while Auto Brightness
            // is off so the operator can audition each source.
            val rawPeak by viewModel.autoBrightnessRawPeak.collectAsState()
            val audioKind by viewModel.autoBrightnessAudioSourceKindFlow.collectAsState()
            // #820 — Playback Capture consent launcher; same surface as
            // the Stage modal. Fires when the operator selects
            // PLAYBACK_CAPTURE so the system shows the screen-recording
            // dialog. On grant the VM gets a MediaProjection and the
            // capture pipeline restarts under the playback configuration.
            val requestPlaybackConsent =
                com.slywombat.slyled.audio.rememberPlaybackCaptureLauncher { mp ->
                    viewModel.setAutoBrightnessMediaProjection(mp)
                }
            // Auto-start preview on this tab whenever permission is held
            // and Auto Brightness is off; live capture is unaffected.
            val abEnabled = enabled
            val hasPerm = remember(state) { viewModel.autoBrightnessHasPermission() }
            DisposableEffect(hasPerm, abEnabled) {
                if (hasPerm && !abEnabled) viewModel.startAutoBrightnessPreview()
                onDispose { viewModel.stopAutoBrightnessPreview() }
            }
            Text(
                "Audio Sources",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            val srcOptions = listOf(
                com.slywombat.slyled.audio.AudioSourceKind.MICROPHONE to "Microphone",
                com.slywombat.slyled.audio.AudioSourceKind.PLAYBACK_CAPTURE to
                    "Playback Capture (loopback)",
                com.slywombat.slyled.audio.AudioSourceKind.REMOTE_SUBMIX to "Remote Submix",
                com.slywombat.slyled.audio.AudioSourceKind.USB_AUDIO to "USB Audio",
            )
            srcOptions.forEach { (kind, label) ->
                Column {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable {
                                viewModel.configureAutoBrightness(audioSourceKind = kind)
                                if (kind == com.slywombat.slyled.audio.AudioSourceKind.PLAYBACK_CAPTURE) {
                                    requestPlaybackConsent()
                                }
                            }
                            .padding(vertical = 2.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        RadioButton(
                            selected = audioKind == kind,
                            onClick = {
                                viewModel.configureAutoBrightness(audioSourceKind = kind)
                                if (kind == com.slywombat.slyled.audio.AudioSourceKind.PLAYBACK_CAPTURE) {
                                    requestPlaybackConsent()
                                }
                            },
                        )
                        Spacer(Modifier.width(8.dp))
                        Text(label, style = MaterialTheme.typography.bodyMedium)
                    }
                    if (audioKind == kind) {
                        // (hint, isError). Informational hints render in
                        // the muted on-surface variant; failure hints
                        // render in error red so the operator notices.
                        val (hint, isError) = when {
                            kind == com.slywombat.slyled.audio.AudioSourceKind.PLAYBACK_CAPTURE
                                && state == MicAutoBrightness.Mode.NeedsPlaybackConsent ->
                                    "Consent declined or pending. Tap Playback Capture again to retry." to true
                            kind == com.slywombat.slyled.audio.AudioSourceKind.PLAYBACK_CAPTURE ->
                                    ("Captures audio from apps playing through the device speakers " +
                                    "(Spotify, YouTube, etc.). Android shares the screen-recording " +
                                    "consent dialog for this — SlyLED only reads the audio stream, " +
                                    "not screen content.") to false
                            kind == com.slywombat.slyled.audio.AudioSourceKind.REMOTE_SUBMIX
                                && state == MicAutoBrightness.Mode.RemoteSubmixDenied ->
                                    "Remote Submix isn't available on this device — denied by the OS." to true
                            kind == com.slywombat.slyled.audio.AudioSourceKind.USB_AUDIO
                                && state == MicAutoBrightness.Mode.NoUsbDevice ->
                                    "No USB input device detected. Plug in a USB mic or audio interface." to true
                            else -> null to false
                        }
                        if (hint != null) {
                            Text(
                                hint,
                                style = MaterialTheme.typography.bodySmall,
                                color = if (isError) RedError
                                        else MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(start = 32.dp, top = 2.dp),
                            )
                        }
                    }
                }
            }
            Spacer(Modifier.height(12.dp))

            // #820 — Raw audio input meter (pre-follower). sqrt scale so
            // typical music peaks of 0.05–0.15 are visible (the linear
            // mapping was unreadable; pre-fix the operator only saw the
            // leading colour pip change red→green and reported "the
            // meter does not move"). Numeric readout to the right gives
            // the underlying value for diagnostics.
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    "Raw audio input (peak)",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    "%.4f".format(rawPeak),
                    style = MaterialTheme.typography.labelSmall,
                    color = if (rawPeak > 0.0005f) GreenOnline else RedError,
                )
            }
            // Cube-root scaling — typical post-AGC room peak of 0.005–0.05
            // maps to 17–37 % bar fill, visibly moving without
            // saturating on louder content.
            LinearProgressIndicator(
                progress = { rawPeak.coerceIn(0f, 1f).toDouble().pow(1.0 / 3.0).toFloat() },
                modifier = Modifier.fillMaxWidth().height(10.dp),
                color = if (rawPeak > 0.0005f) GreenOnline else RedError,
                trackColor = MaterialTheme.colorScheme.outlineVariant,
            )
            Spacer(Modifier.height(8.dp))

            // Live envelope meter (post-follower). Cube-root scaled so
            // the post-LPF RMS values (typically 0.005–0.05 for music)
            // are visible — pre-fix the linear bar showed sub-1 % fill
            // and read as "doing nothing" even with audio playing.
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    "Envelope (after sensitivity / floor / ceiling)",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    "%.4f".format(envelope),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            LinearProgressIndicator(
                progress = { envelope.coerceIn(0f, 1f).toDouble().pow(1.0 / 3.0).toFloat() },
                modifier = Modifier.fillMaxWidth().height(10.dp),
                color = if (state == MicAutoBrightness.Mode.Clipping) RedError else GreenOnline,
                trackColor = MaterialTheme.colorScheme.outlineVariant,
            )
            Spacer(Modifier.height(8.dp))

            // #820 — Brightness output bar. The operator-meaningful
            // signal: floor + (ceiling-floor) * max(envelope, beatPulse).
            // With floor=0.4, the bar baselines at 40 % and swings up
            // to 100 % on each beat. Linear scale (the bar IS the
            // brightness; it should match what the lights are doing 1:1).
            val master by viewModel.autoBrightnessMaster.collectAsState()
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    "Brightness output (sent to lights)",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    "%d / 255".format((master * 255f).toInt()),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            LinearProgressIndicator(
                progress = { master.coerceIn(0f, 1f) },
                modifier = Modifier.fillMaxWidth().height(14.dp),
                color = androidx.compose.ui.graphics.Color(0xFFFB923C), // amber
                trackColor = MaterialTheme.colorScheme.outlineVariant,
            )
            Spacer(Modifier.height(8.dp))

            // Beat indicator (pulsing dot + persistent BPM readout).
            val beatPulse by viewModel.autoBrightnessBeatPulse.collectAsState()
            val bpm by viewModel.autoBrightnessBpm.collectAsState()
            val beatCount by viewModel.autoBrightnessBeatCount.collectAsState()
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                androidx.compose.foundation.Canvas(modifier = Modifier.size(36.dp)) {
                    val r = 6f + 28f * beatPulse.coerceIn(0f, 1f)
                    drawCircle(
                        color = androidx.compose.ui.graphics.Color(0xFF334155),
                        radius = size.minDimension / 2f,
                        style = androidx.compose.ui.graphics.drawscope.Stroke(width = 1.5f),
                    )
                    drawCircle(
                        color = androidx.compose.ui.graphics.Color(0xFFFB7185),
                        radius = r.coerceAtMost(size.minDimension / 2f),
                        alpha = 0.4f + 0.6f * beatPulse.coerceIn(0f, 1f),
                    )
                }
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        "Beat",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text(
                        if (bpm > 0f) "${bpm.toInt()} BPM" else "— BPM (waiting for beats)",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
                Text(
                    "$beatCount beats",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.height(12.dp))

            // Tunables — single source of truth is the StateFlow on the
            // viewmodel; the slider's onChange writes back through
            // configureAutoBrightness which mutates the flow + persists.
            SettingsLabelledSlider("Sensitivity", sens, 0.5f..4f) {
                viewModel.configureAutoBrightness(sensitivity = it)
            }
            SettingsLabelledSlider("Floor", fl, 0f..1f) {
                viewModel.configureAutoBrightness(floor = it.coerceAtMost(ce))
            }
            SettingsLabelledSlider("Ceiling", ce, 0f..1f) {
                viewModel.configureAutoBrightness(ceiling = it.coerceAtLeast(fl))
            }
            SettingsLabelledSlider("Attack (ms)", atk, 1f..200f) {
                viewModel.configureAutoBrightness(attackMs = it)
            }
            SettingsLabelledSlider("Release (ms)", rel, 20f..2000f) {
                viewModel.configureAutoBrightness(releaseMs = it)
            }

            Spacer(Modifier.height(12.dp))
            Text(
                "Manual Brightness (used when Auto is off)",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                Slider(
                    value = manualSlider,
                    onValueChange = { manualSlider = it },
                    onValueChangeFinished = { viewModel.setManualBrightness(manualSlider.toInt()) },
                    valueRange = 0f..255f,
                    enabled = !enabled,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    "${manualSlider.toInt()}",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.padding(start = 8.dp).width(40.dp),
                )
            }

            if (state == MicAutoBrightness.Mode.PermissionDenied) {
                Spacer(Modifier.height(8.dp))
                Text(
                    "Mic permission denied. Tap Enable to retry.",
                    style = MaterialTheme.typography.bodySmall,
                    color = RedError,
                )
            }
        }
    }
}

@Composable
private fun autoBrightnessSettingsLabel(
    enabled: Boolean,
    state: MicAutoBrightness.Mode,
): Pair<String, androidx.compose.ui.graphics.Color> = when {
    !enabled && state == MicAutoBrightness.Mode.PermissionDenied ->
        "Tap to grant mic permission" to androidx.compose.ui.graphics.Color(0xFFF59E0B)
    !enabled -> "Off" to MaterialTheme.colorScheme.onSurfaceVariant
    state == MicAutoBrightness.Mode.NoMic -> "No mic available" to RedError
    state == MicAutoBrightness.Mode.Clipping -> "Clipping — reduce sensitivity" to androidx.compose.ui.graphics.Color(0xFFF59E0B)
    state == MicAutoBrightness.Mode.Listening -> "Listening" to GreenOnline
    else -> "Idle" to MaterialTheme.colorScheme.onSurfaceVariant
}

@Composable
private fun SettingsLabelledSlider(
    label: String,
    value: Float,
    range: ClosedFloatingPointRange<Float>,
    onChange: (Float) -> Unit,
) {
    Column(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(
                label,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                "%.2f".format(value),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurface,
            )
        }
        Slider(value = value, onValueChange = onChange, valueRange = range)
    }
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
