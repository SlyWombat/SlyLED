package com.slywombat.slyled.ui.screens.setup

import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.slywombat.slyled.data.model.Child
import com.slywombat.slyled.data.model.ChildStringConfig
import com.slywombat.slyled.data.model.DmxProfile
import com.slywombat.slyled.data.model.Fixture
import com.slywombat.slyled.data.model.OnlineStatus
import com.slywombat.slyled.ui.theme.CyanSecondary
import com.slywombat.slyled.ui.theme.GreenOnline
import com.slywombat.slyled.ui.theme.OrangeWled
import com.slywombat.slyled.ui.theme.RedError
import com.slywombat.slyled.viewmodel.SetupViewModel
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.intOrNull
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.clickable
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextDecoration

@Composable
fun SetupScreen(viewModel: SetupViewModel = hiltViewModel()) {
    // Reload on every screen visit
    LaunchedEffect(Unit) { viewModel.loadChildren() }

    val children by viewModel.children.collectAsState()
    val discovered by viewModel.discovered.collectAsState()
    val fixtures by viewModel.fixtures.collectAsState()
    val dmxProfiles by viewModel.dmxProfiles.collectAsState()
    val isDiscovering by viewModel.isDiscovering.collectAsState()
    val isAdding by viewModel.isAdding.collectAsState()
    val isRefreshing by viewModel.isRefreshing.collectAsState()

    var manualIp by remember { mutableStateOf("") }
    var showDiscovered by remember { mutableStateOf(false) }
    var detailChild by remember { mutableStateOf<Child?>(null) }
    var confirmRemoveId by remember { mutableStateOf<Int?>(null) }
    var confirmRebootId by remember { mutableStateOf<Int?>(null) }
    var showCreateFixture by remember { mutableStateOf(false) }
    var editFixture by remember { mutableStateOf<Fixture?>(null) }
    var confirmDeleteFixtureId by remember { mutableStateOf<Int?>(null) }
    var dmxTestFixture by remember { mutableStateOf<Fixture?>(null) }

    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(Unit) {
        viewModel.message.collectLatest { msg ->
            snackbarHostState.showSnackbar(msg)
        }
    }

    // Show discovered results when they arrive
    LaunchedEffect(discovered) {
        if (discovered.isNotEmpty()) showDiscovered = true
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) }
    ) { padding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 16.dp),
            contentPadding = PaddingValues(vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            // Discovery + manual add section
            item {
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(modifier = Modifier.padding(16.dp)) {
                        Text(
                            "Add Devices",
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.Bold
                        )
                        Spacer(Modifier.height(12.dp))

                        // Discover button
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            Button(
                                onClick = { viewModel.discover() },
                                enabled = !isDiscovering,
                                modifier = Modifier.weight(1f)
                            ) {
                                if (isDiscovering) {
                                    CircularProgressIndicator(
                                        modifier = Modifier.size(18.dp),
                                        strokeWidth = 2.dp,
                                        color = MaterialTheme.colorScheme.onPrimary
                                    )
                                    Spacer(Modifier.width(8.dp))
                                }
                                Text("Discover")
                            }
                            FilledTonalButton(
                                onClick = { viewModel.refreshAll() },
                                enabled = !isRefreshing
                            ) {
                                if (isRefreshing) {
                                    CircularProgressIndicator(
                                        modifier = Modifier.size(18.dp),
                                        strokeWidth = 2.dp
                                    )
                                    Spacer(Modifier.width(8.dp))
                                }
                                Text("Refresh All")
                            }
                        }
                        Spacer(Modifier.height(12.dp))

                        // Manual IP entry
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            OutlinedTextField(
                                value = manualIp,
                                onValueChange = { manualIp = it },
                                label = { Text("IP Address") },
                                placeholder = { Text("192.168.1.100") },
                                singleLine = true,
                                modifier = Modifier.weight(1f)
                            )
                            Button(
                                onClick = {
                                    if (manualIp.isNotBlank()) {
                                        viewModel.addChild(manualIp.trim())
                                        manualIp = ""
                                    }
                                },
                                enabled = !isAdding && manualIp.isNotBlank()
                            ) {
                                if (isAdding) {
                                    CircularProgressIndicator(
                                        modifier = Modifier.size(18.dp),
                                        strokeWidth = 2.dp,
                                        color = MaterialTheme.colorScheme.onPrimary
                                    )
                                } else {
                                    Text("Add")
                                }
                            }
                        }
                    }
                }
            }

            // Discovered devices (expandable)
            if (discovered.isNotEmpty()) {
                item {
                    Card(modifier = Modifier.fillMaxWidth()) {
                        Column(modifier = Modifier.padding(16.dp)) {
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text(
                                    "Discovered (${discovered.size})",
                                    style = MaterialTheme.typography.titleSmall,
                                    fontWeight = FontWeight.Bold
                                )
                                IconButton(onClick = { showDiscovered = !showDiscovered }) {
                                    Icon(
                                        if (showDiscovered) Icons.Default.ExpandLess
                                        else Icons.Default.ExpandMore,
                                        contentDescription = "Toggle"
                                    )
                                }
                            }
                            AnimatedVisibility(visible = showDiscovered) {
                                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                    discovered.forEach { child ->
                                        val discDisplayName = if (child.name.isNotBlank() && child.name != child.hostname) child.name else child.hostname
                                        Row(
                                            modifier = Modifier.fillMaxWidth(),
                                            horizontalArrangement = Arrangement.SpaceBetween,
                                            verticalAlignment = Alignment.CenterVertically
                                        ) {
                                            Column {
                                                Text(
                                                    discDisplayName,
                                                    style = MaterialTheme.typography.bodyMedium
                                                )
                                                if (child.name.isNotBlank() && child.name != child.hostname) {
                                                    Text(
                                                        child.hostname,
                                                        style = MaterialTheme.typography.bodySmall,
                                                        color = MaterialTheme.colorScheme.onSurfaceVariant
                                                    )
                                                }
                                                Text(
                                                    child.ip,
                                                    style = MaterialTheme.typography.bodySmall,
                                                    color = MaterialTheme.colorScheme.onSurfaceVariant
                                                )
                                            }
                                            FilledTonalButton(
                                                onClick = { viewModel.addChild(child.ip) },
                                                enabled = !isAdding
                                            ) {
                                                Text("Add")
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Split children into DMX bridges and LED devices
            val dmxBridges = children.filter { it.type == "dmx" || it.boardType == "giga-dmx" || it.boardType == "DMX Bridge" }
            val ledDevices = children.filter { it !in dmxBridges }

            // Hardware section (DMX bridges)
            if (dmxBridges.isNotEmpty()) {
                item {
                    Text(
                        "DMX Bridges (${dmxBridges.size})",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(top = 8.dp)
                    )
                }
                items(dmxBridges, key = { "hw-${it.id}" }) { child ->
                    SetupPerformerCard(
                        child = child,
                        onRefresh = { viewModel.refreshChild(child.id) },
                        onReboot = { confirmRebootId = child.id },
                        onRemove = { confirmRemoveId = child.id },
                        onDetails = { detailChild = child }
                    )
                }
            }

            // LED Devices section
            if (ledDevices.isNotEmpty()) {
                item {
                    Text(
                        "LED Devices (${ledDevices.size})",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(top = 8.dp)
                    )
                }
            }
            items(ledDevices, key = { "dev-${it.id}" }) { child ->
                SetupPerformerCard(
                    child = child,
                    onRefresh = { viewModel.refreshChild(child.id) },
                    onReboot = { confirmRebootId = child.id },
                    onRemove = { confirmRemoveId = child.id },
                    onDetails = { detailChild = child }
                )
            }

            // Empty state
            if (children.isEmpty()) {
                item {
                    Box(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 48.dp),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            "No devices registered — use Discover or add manually",
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            style = MaterialTheme.typography.bodyMedium
                        )
                    }
                }
            }

            // ── Fixtures section ──────────────────────────────────────────
            item {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(top = 16.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        "Fixtures (${fixtures.size})",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold
                    )
                    IconButton(onClick = { showCreateFixture = true }) {
                        Icon(Icons.Default.Add, contentDescription = "Add Fixture")
                    }
                }
            }

            if (fixtures.isEmpty()) {
                item {
                    Box(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 24.dp),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            "No fixtures — tap + to create one",
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            style = MaterialTheme.typography.bodyMedium
                        )
                    }
                }
            }

            items(fixtures, key = { "fix-${it.id}" }) { fixture ->
                FixtureCard(
                    fixture = fixture,
                    children = children,
                    onEdit = { editFixture = fixture },
                    onDelete = { confirmDeleteFixtureId = fixture.id },
                    onDmxTest = if (fixture.fixtureType == "dmx") {{ dmxTestFixture = fixture }} else null
                )
            }
        }
    }

    // Details dialog
    detailChild?.let { child ->
        ChildDetailsDialog(
            child = child,
            onDismiss = { detailChild = null }
        )
    }

    // Confirm remove dialog
    confirmRemoveId?.let { id ->
        val child = children.find { it.id == id }
        AlertDialog(
            onDismissRequest = { confirmRemoveId = null },
            title = { Text("Remove Device") },
            text = {
                val removeName = child?.let {
                    if (it.name.isNotBlank() && it.name != it.hostname) it.name else it.hostname
                } ?: "device #$id"
                Text("Remove $removeName? It will need to be re-added.")
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        viewModel.removeChild(id)
                        confirmRemoveId = null
                    },
                    colors = ButtonDefaults.textButtonColors(contentColor = RedError)
                ) {
                    Text("Remove")
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmRemoveId = null }) {
                    Text("Cancel")
                }
            }
        )
    }

    // Confirm reboot dialog
    confirmRebootId?.let { id ->
        val child = children.find { it.id == id }
        AlertDialog(
            onDismissRequest = { confirmRebootId = null },
            title = { Text("Reboot Device") },
            text = {
                val rebootName = child?.let {
                    if (it.name.isNotBlank() && it.name != it.hostname) it.name else it.hostname
                } ?: "device #$id"
                Text("Reboot $rebootName? It will be offline briefly.")
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        viewModel.rebootChild(id)
                        confirmRebootId = null
                    },
                    colors = ButtonDefaults.textButtonColors(contentColor = OrangeWled)
                ) {
                    Text("Reboot")
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmRebootId = null }) {
                    Text("Cancel")
                }
            }
        )
    }

    // Confirm delete fixture dialog
    confirmDeleteFixtureId?.let { id ->
        val fixture = fixtures.find { it.id == id }
        AlertDialog(
            onDismissRequest = { confirmDeleteFixtureId = null },
            title = { Text("Delete Fixture") },
            text = {
                Text("Delete \"${fixture?.name ?: "fixture #$id"}\"? This cannot be undone.")
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        viewModel.deleteFixture(id)
                        confirmDeleteFixtureId = null
                    },
                    colors = ButtonDefaults.textButtonColors(contentColor = RedError)
                ) { Text("Delete") }
            },
            dismissButton = {
                TextButton(onClick = { confirmDeleteFixtureId = null }) { Text("Cancel") }
            }
        )
    }

    // DMX channel test dialog
    dmxTestFixture?.let { fixture ->
        DmxChannelTestDialog(
            fixture = fixture,
            viewModel = viewModel,
            onDismiss = { dmxTestFixture = null }
        )
    }

    // Create fixture dialog
    if (showCreateFixture) {
        FixtureFormDialog(
            title = "Create Fixture",
            fixture = null,
            children = children,
            dmxProfiles = dmxProfiles,
            viewModel = viewModel,
            onDismiss = { showCreateFixture = false },
            onConfirm = { fixture ->
                viewModel.createFixture(fixture)
                showCreateFixture = false
            }
        )
    }

    // Edit fixture dialog
    editFixture?.let { fixture ->
        FixtureFormDialog(
            title = "Edit Fixture",
            fixture = fixture,
            children = children,
            dmxProfiles = dmxProfiles,
            viewModel = viewModel,
            onDismiss = { editFixture = null },
            onConfirm = { updated ->
                viewModel.updateFixture(fixture.id, updated)
                editFixture = null
            }
        )
    }
}

@Composable
private fun SetupPerformerCard(
    child: Child,
    onRefresh: () -> Unit,
    onReboot: () -> Unit,
    onRemove: () -> Unit,
    onDetails: () -> Unit
) {
    val isOnline = child.onlineStatus == OnlineStatus.ONLINE

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    val displayName = if (child.name.isNotBlank() && child.name != child.hostname) child.name else child.hostname
                    Text(
                        displayName,
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold
                    )
                    if (child.name.isNotBlank() && child.name != child.hostname) {
                        Text(
                            child.hostname,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    SetupBoardBadge(type = child.type)
                    SuggestionChip(
                        onClick = {},
                        label = {
                            Text(
                                if (isOnline) "Online" else "Offline",
                                style = MaterialTheme.typography.labelSmall
                            )
                        },
                        colors = SuggestionChipDefaults.suggestionChipColors(
                            containerColor = if (isOnline) GreenOnline.copy(alpha = 0.15f)
                            else RedError.copy(alpha = 0.15f),
                            labelColor = if (isOnline) GreenOnline else RedError
                        ),
                        border = null
                    )
                }
            }
            Spacer(Modifier.height(4.dp))
            val context = LocalContext.current
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(
                    child.ip,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.primary,
                    textDecoration = TextDecoration.Underline,
                    modifier = Modifier.clickable {
                        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("http://${child.ip}")))
                    }
                )
                if (child.fwVersion != null) {
                    Text(
                        "v${child.fwVersion}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
            Spacer(Modifier.height(8.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.End
            ) {
                TextButton(onClick = onDetails) { Text("Details") }
                TextButton(onClick = onRefresh) { Text("Refresh") }
                TextButton(
                    onClick = onReboot,
                    colors = ButtonDefaults.textButtonColors(contentColor = OrangeWled)
                ) { Text("Reboot") }
                TextButton(
                    onClick = onRemove,
                    colors = ButtonDefaults.textButtonColors(contentColor = RedError)
                ) { Text("Remove") }
            }
        }
    }
}

@Composable
private fun SetupBoardBadge(type: String) {
    val (label, color) = when (type.lowercase()) {
        "esp32" -> "ESP32" to CyanSecondary
        "d1mini", "d1_mini" -> "D1 Mini" to OrangeWled
        "giga" -> "Giga" to androidx.compose.ui.graphics.Color(0xFFa78bfa)
        "giga-dmx", "dmx-bridge", "dmx" -> "DMX Bridge" to androidx.compose.ui.graphics.Color(0xFF7c3aed)
        "wled" -> "WLED" to OrangeWled
        else -> "SlyLED" to MaterialTheme.colorScheme.primary
    }
    SuggestionChip(
        onClick = {},
        label = { Text(label, style = MaterialTheme.typography.labelSmall) },
        colors = SuggestionChipDefaults.suggestionChipColors(
            containerColor = color.copy(alpha = 0.15f),
            labelColor = color
        ),
        border = null
    )
}

@Composable
private fun ChildDetailsDialog(child: Child, onDismiss: () -> Unit) {
    val ledTypeNames = listOf("WS2812B", "WS2811", "SK6812", "APA102")
    val dirNames = listOf("Normal", "Reversed")

    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            val detailTitle = if (child.name.isNotBlank() && child.name != child.hostname) child.name else child.hostname
            Text("$detailTitle Details")
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (child.name.isNotEmpty()) DetailRow("Name", child.name)
                if (child.desc.isNotEmpty()) DetailRow("Description", child.desc)
                // Clickable IP to open child config in browser
                val context = LocalContext.current
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text("IP", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(
                        child.ip,
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.primary,
                        textDecoration = TextDecoration.Underline,
                        modifier = Modifier.clickable {
                            context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("http://${child.ip}")))
                        }
                    )
                }
                DetailRow("Type", child.type)
                if (child.fwVersion != null) DetailRow("Firmware", "v${child.fwVersion}")
                DetailRow("Strings", "${child.sc}")

                if (child.strings.isNotEmpty()) {
                    HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))
                    child.strings.forEachIndexed { idx, cfg ->
                        StringConfigSection(index = idx, config = cfg, ledTypeNames = ledTypeNames, dirNames = dirNames)
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text("Close") }
        }
    )
}

@Composable
private fun DetailRow(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(
            label,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        Text(
            value,
            style = MaterialTheme.typography.bodySmall,
            fontWeight = FontWeight.Medium
        )
    }
}

@Composable
private fun StringConfigSection(
    index: Int,
    config: ChildStringConfig,
    ledTypeNames: List<String>,
    dirNames: List<String>
) {
    Column {
        Text(
            "String ${index + 1}",
            style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.primary
        )
        Spacer(Modifier.height(2.dp))
        DetailRow("LED Count", "${config.leds}")
        if (config.lengthMm > 0) DetailRow("Length", "${config.lengthMm} mm")
        val typeIdx = config.type
        DetailRow("Type", if (typeIdx in ledTypeNames.indices) ledTypeNames[typeIdx] else "Unknown ($typeIdx)")
        val dirIdx = config.stripDirection
        DetailRow("Direction", if (dirIdx in dirNames.indices) dirNames[dirIdx] else "Unknown ($dirIdx)")
        if (config.folded) DetailRow("Folded", "Yes")
    }
}

// ── Fixture Card ──────────────────────────────────────────────────────
@Composable
private fun FixtureCard(
    fixture: Fixture,
    children: List<Child>,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
    onDmxTest: (() -> Unit)? = null
) {
    val childName = fixture.childId?.let { cid ->
        children.find { it.id == cid }?.let { c ->
            if (c.name.isNotBlank() && c.name != c.hostname) c.name else c.hostname
        }
    }

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        fixture.name.ifBlank { "Fixture #${fixture.id}" },
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold
                    )
                    if (childName != null) {
                        Text(
                            childName,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    FixtureTypeBadge(fixture.type)
                    FixtureModeBadge(fixture.fixtureType)
                }
            }
            // DMX details row
            if (fixture.fixtureType == "dmx") {
                Spacer(Modifier.height(4.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    fixture.dmxUniverse?.let {
                        Text("U:$it", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    fixture.dmxStartAddr?.let {
                        Text("Addr:$it", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    fixture.dmxChannelCount?.let {
                        Text("Ch:$it", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            Spacer(Modifier.height(8.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.End
            ) {
                TextButton(onClick = onEdit) { Text("Edit") }
                if (onDmxTest != null) {
                    TextButton(
                        onClick = onDmxTest,
                        colors = ButtonDefaults.textButtonColors(
                            contentColor = androidx.compose.ui.graphics.Color(0xFF7c3aed))
                    ) { Text("Details") }
                }
                TextButton(
                    onClick = onDelete,
                    colors = ButtonDefaults.textButtonColors(contentColor = RedError)
                ) { Text("Delete") }
            }
        }
    }
}

@Composable
private fun FixtureTypeBadge(type: String) {
    val label = when (type.lowercase()) {
        "linear" -> "Linear"
        "point" -> "Point"
        "surface" -> "Surface"
        "group" -> "Group"
        else -> type.replaceFirstChar { it.uppercase() }
    }
    SuggestionChip(
        onClick = {},
        label = { Text(label, style = MaterialTheme.typography.labelSmall) },
        colors = SuggestionChipDefaults.suggestionChipColors(
            containerColor = MaterialTheme.colorScheme.secondaryContainer,
            labelColor = MaterialTheme.colorScheme.onSecondaryContainer
        ),
        border = null
    )
}

@Composable
private fun FixtureModeBadge(fixtureType: String) {
    val isDmx = fixtureType == "dmx"
    val label = if (isDmx) "DMX" else "LED"
    val color = if (isDmx) OrangeWled else GreenOnline
    SuggestionChip(
        onClick = {},
        label = { Text(label, style = MaterialTheme.typography.labelSmall) },
        colors = SuggestionChipDefaults.suggestionChipColors(
            containerColor = color.copy(alpha = 0.15f),
            labelColor = color
        ),
        border = null
    )
}

// ── Fixture Form Dialog (Create / Edit) ───────────────────────────────
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun FixtureFormDialog(
    title: String,
    fixture: Fixture?,
    children: List<Child>,
    dmxProfiles: List<DmxProfile>,
    viewModel: SetupViewModel,
    onDismiss: () -> Unit,
    onConfirm: (Fixture) -> Unit
) {
    var name by remember { mutableStateOf(fixture?.name ?: "") }
    var type by remember { mutableStateOf(fixture?.type ?: "linear") }
    var fixtureType by remember { mutableStateOf(fixture?.fixtureType ?: "led") }
    var selectedChildId by remember { mutableStateOf(fixture?.childId) }
    var dmxUniverse by remember { mutableStateOf(fixture?.dmxUniverse?.toString() ?: "1") }
    var dmxStartAddr by remember { mutableStateOf(fixture?.dmxStartAddr?.toString() ?: "1") }
    var dmxChannelCount by remember { mutableStateOf(fixture?.dmxChannelCount?.toString() ?: "") }
    var selectedProfileId by remember { mutableStateOf(fixture?.dmxProfileId ?: "") }

    // Test channels state
    var testChannelsData by remember { mutableStateOf<JsonObject?>(null) }
    var testChannelValues by remember { mutableStateOf<Map<Int, Int>>(emptyMap()) }
    var isLoadingChannels by remember { mutableStateOf(false) }
    val coroutineScope = rememberCoroutineScope()

    val typeOptions = listOf("linear", "point", "surface", "group")
    var typeExpanded by remember { mutableStateOf(false) }
    var childExpanded by remember { mutableStateOf(false) }
    var profileExpanded by remember { mutableStateOf(false) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            Column(
                modifier = Modifier.fillMaxWidth(),
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
                        value = type.replaceFirstChar { it.uppercase() },
                        onValueChange = {},
                        readOnly = true,
                        label = { Text("Type") },
                        trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = typeExpanded) },
                        modifier = Modifier.fillMaxWidth().menuAnchor()
                    )
                    ExposedDropdownMenu(
                        expanded = typeExpanded,
                        onDismissRequest = { typeExpanded = false }
                    ) {
                        typeOptions.forEach { opt ->
                            DropdownMenuItem(
                                text = { Text(opt.replaceFirstChar { it.uppercase() }) },
                                onClick = { type = opt; typeExpanded = false }
                            )
                        }
                    }
                }

                // Fixture type toggle (LED / DMX)
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Text("Mode:", style = MaterialTheme.typography.bodyMedium)
                    FilterChip(
                        selected = fixtureType == "led",
                        onClick = { fixtureType = "led" },
                        label = { Text("LED") }
                    )
                    FilterChip(
                        selected = fixtureType == "dmx",
                        onClick = { fixtureType = "dmx" },
                        label = { Text("DMX") }
                    )
                }

                // Child dropdown
                if (fixtureType == "led") {
                    ExposedDropdownMenuBox(
                        expanded = childExpanded,
                        onExpandedChange = { childExpanded = it }
                    ) {
                        val childLabel = selectedChildId?.let { cid ->
                            children.find { it.id == cid }?.let { c ->
                                if (c.name.isNotBlank() && c.name != c.hostname) c.name else c.hostname
                            } ?: "Child #$cid"
                        } ?: "None"
                        OutlinedTextField(
                            value = childLabel,
                            onValueChange = {},
                            readOnly = true,
                            label = { Text("LED Device") },
                            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = childExpanded) },
                            modifier = Modifier.fillMaxWidth().menuAnchor()
                        )
                        ExposedDropdownMenu(
                            expanded = childExpanded,
                            onDismissRequest = { childExpanded = false }
                        ) {
                            DropdownMenuItem(
                                text = { Text("None") },
                                onClick = { selectedChildId = null; childExpanded = false }
                            )
                            children.forEach { child ->
                                val cName = if (child.name.isNotBlank() && child.name != child.hostname) child.name else child.hostname
                                DropdownMenuItem(
                                    text = { Text("$cName (${child.ip})") },
                                    onClick = { selectedChildId = child.id; childExpanded = false }
                                )
                            }
                        }
                    }
                }

                // DMX fields
                if (fixtureType == "dmx") {
                    OutlinedTextField(
                        value = dmxUniverse,
                        onValueChange = { dmxUniverse = it.filter { c -> c.isDigit() } },
                        label = { Text("Universe") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                    OutlinedTextField(
                        value = dmxStartAddr,
                        onValueChange = { dmxStartAddr = it.filter { c -> c.isDigit() } },
                        label = { Text("Start Address") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                    OutlinedTextField(
                        value = dmxChannelCount,
                        onValueChange = { dmxChannelCount = it.filter { c -> c.isDigit() } },
                        label = { Text("Channel Count") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )

                    // Profile dropdown
                    ExposedDropdownMenuBox(
                        expanded = profileExpanded,
                        onExpandedChange = { profileExpanded = it }
                    ) {
                        val profileLabel = dmxProfiles.find { it.id == selectedProfileId }?.name ?: "None"
                        OutlinedTextField(
                            value = profileLabel,
                            onValueChange = {},
                            readOnly = true,
                            label = { Text("DMX Profile") },
                            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = profileExpanded) },
                            modifier = Modifier.fillMaxWidth().menuAnchor()
                        )
                        ExposedDropdownMenu(
                            expanded = profileExpanded,
                            onDismissRequest = { profileExpanded = false }
                        ) {
                            DropdownMenuItem(
                                text = { Text("None") },
                                onClick = { selectedProfileId = ""; profileExpanded = false }
                            )
                            dmxProfiles.forEach { profile ->
                                DropdownMenuItem(
                                    text = { Text("${profile.name} (${profile.channelCount}ch)") },
                                    onClick = {
                                        selectedProfileId = profile.id
                                        if (dmxChannelCount.isBlank() || dmxChannelCount == "0") {
                                            dmxChannelCount = profile.channelCount.toString()
                                        }
                                        profileExpanded = false
                                    }
                                )
                            }
                        }
                    }

                    // Test Channels section (only for existing DMX fixtures)
                    if (fixture != null && fixture.id > 0) {
                        HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))
                        Text(
                            "Test Channels",
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.Bold
                        )

                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            FilledTonalButton(
                                onClick = {
                                    isLoadingChannels = true
                                    coroutineScope.launch {
                                        val data = viewModel.loadFixtureChannels(fixture.id)
                                        testChannelsData = data
                                        // Initialize slider values from response
                                        if (data != null) {
                                            val channels = data["channels"]?.jsonArray
                                            val values = mutableMapOf<Int, Int>()
                                            channels?.forEach { ch ->
                                                val obj = ch.jsonObject
                                                val offset = obj["offset"]?.jsonPrimitive?.intOrNull ?: 0
                                                val value = obj["value"]?.jsonPrimitive?.intOrNull ?: 0
                                                values[offset] = value
                                            }
                                            testChannelValues = values
                                        }
                                        isLoadingChannels = false
                                    }
                                },
                                enabled = !isLoadingChannels,
                                modifier = Modifier.weight(1f)
                            ) {
                                if (isLoadingChannels) {
                                    CircularProgressIndicator(
                                        modifier = Modifier.size(18.dp),
                                        strokeWidth = 2.dp
                                    )
                                    Spacer(Modifier.width(4.dp))
                                }
                                Text("Load Channels")
                            }
                            OutlinedButton(
                                onClick = {
                                    // Send blackout (all channels to 0)
                                    val channels = testChannelsData?.get("channels")?.jsonArray
                                    if (channels != null) {
                                        val zeroed = mutableMapOf<Int, Int>()
                                        channels.forEach { ch ->
                                            val offset = ch.jsonObject["offset"]?.jsonPrimitive?.intOrNull ?: 0
                                            zeroed[offset] = 0
                                        }
                                        testChannelValues = zeroed
                                        coroutineScope.launch {
                                            zeroed.forEach { (offset, _) ->
                                                viewModel.testFixtureChannel(fixture.id, offset, 0)
                                            }
                                        }
                                    }
                                },
                                enabled = testChannelsData != null
                            ) {
                                Text("Blackout")
                            }
                        }

                        // Channel sliders
                        testChannelsData?.get("channels")?.jsonArray?.forEach { ch ->
                            val obj = ch.jsonObject
                            val offset = obj["offset"]?.jsonPrimitive?.intOrNull ?: 0
                            val chName = obj["name"]?.jsonPrimitive?.content ?: "Ch $offset"
                            val currentValue = testChannelValues[offset] ?: 0

                            Column(modifier = Modifier.fillMaxWidth()) {
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween
                                ) {
                                    Text(
                                        chName,
                                        style = MaterialTheme.typography.bodySmall
                                    )
                                    Text(
                                        "$currentValue",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant
                                    )
                                }
                                Slider(
                                    value = currentValue.toFloat(),
                                    onValueChange = { newVal ->
                                        val intVal = newVal.toInt()
                                        testChannelValues = testChannelValues.toMutableMap().apply { put(offset, intVal) }
                                    },
                                    onValueChangeFinished = {
                                        val finalVal = testChannelValues[offset] ?: 0
                                        coroutineScope.launch {
                                            viewModel.testFixtureChannel(fixture.id, offset, finalVal)
                                        }
                                    },
                                    valueRange = 0f..255f,
                                    steps = 0,
                                    modifier = Modifier.fillMaxWidth()
                                )
                            }
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    val result = Fixture(
                        id = fixture?.id ?: 0,
                        name = name.trim(),
                        type = type,
                        fixtureType = fixtureType,
                        childId = if (fixtureType == "led") selectedChildId else null,
                        dmxUniverse = if (fixtureType == "dmx") dmxUniverse.toIntOrNull() else null,
                        dmxStartAddr = if (fixtureType == "dmx") dmxStartAddr.toIntOrNull() else null,
                        dmxChannelCount = if (fixtureType == "dmx") dmxChannelCount.toIntOrNull() else null,
                        dmxProfileId = if (fixtureType == "dmx" && selectedProfileId.isNotBlank()) selectedProfileId else null,
                        strings = fixture?.strings ?: emptyList(),
                        rotation = fixture?.rotation ?: listOf(0.0, 0.0, 0.0),
                        aoeRadius = fixture?.aoeRadius ?: 1000
                    )
                    onConfirm(result)
                },
                enabled = name.isNotBlank()
            ) { Text(if (fixture == null) "Create" else "Save") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        }
    )
}

// ── DMX Channel Test Dialog ──────────────────────────────────────────
@Composable
private fun DmxChannelTestDialog(
    fixture: Fixture,
    viewModel: SetupViewModel,
    onDismiss: () -> Unit
) {
    var channels by remember { mutableStateOf<List<Map<String, Any>>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(fixture.id) {
        try {
            val data = viewModel.loadFixtureChannels(fixture.id)
            if (data != null) {
                val chArray = data["channels"]?.jsonArray ?: return@LaunchedEffect
                channels = chArray.map { ch ->
                    val obj = ch.jsonObject
                    mapOf(
                        "offset" to (obj["offset"]?.jsonPrimitive?.intOrNull ?: 0),
                        "name" to (obj["name"]?.jsonPrimitive?.content ?: "Ch"),
                        "type" to (obj["type"]?.jsonPrimitive?.content ?: "dimmer"),
                        "value" to (obj["value"]?.jsonPrimitive?.intOrNull ?: 0)
                    )
                }
            }
        } catch (_: Exception) {}
        loading = false
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("${fixture.name} - Channel Test") },
        text = {
            Column(modifier = Modifier.fillMaxWidth()) {
                Text(
                    "U${fixture.dmxUniverse ?: "?"} @ ${fixture.dmxStartAddr ?: "?"} | ${fixture.dmxChannelCount ?: "?"} channels",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                Spacer(Modifier.height(8.dp))
                if (loading) {
                    CircularProgressIndicator(modifier = Modifier.size(24.dp))
                } else if (channels.isEmpty()) {
                    Text("No channels", color = MaterialTheme.colorScheme.onSurfaceVariant)
                } else {
                    // Channel sliders
                    Column(
                        modifier = Modifier.heightIn(max = 300.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp)
                    ) {
                        channels.forEachIndexed { idx, ch ->
                            val offset = ch["offset"] as Int
                            val name = ch["name"] as String
                            var value by remember { mutableIntStateOf(ch["value"] as Int) }
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                Text(name, modifier = Modifier.width(70.dp),
                                    style = MaterialTheme.typography.bodySmall,
                                    maxLines = 1)
                                Slider(
                                    value = value.toFloat(),
                                    onValueChange = { value = it.toInt() },
                                    onValueChangeFinished = {
                                        scope.launch { viewModel.testFixtureChannel(fixture.id, offset, value) }
                                    },
                                    valueRange = 0f..255f,
                                    modifier = Modifier.weight(1f)
                                )
                                Text("$value", modifier = Modifier.width(30.dp),
                                    style = MaterialTheme.typography.bodySmall)
                            }
                        }
                    }
                    Spacer(Modifier.height(8.dp))
                    // Quick buttons
                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        val quickColors = listOf("White" to Triple(255, 255, 255), "Red" to Triple(255, 0, 0),
                            "Green" to Triple(0, 255, 0), "Blue" to Triple(0, 0, 255), "Off" to Triple(0, 0, 0))
                        quickColors.forEach { (label, rgb) ->
                            FilledTonalButton(
                                onClick = {
                                    scope.launch {
                                        channels.forEach { ch ->
                                            val off = ch["offset"] as Int
                                            val type = ch["type"] as String
                                            val v = when (type) {
                                                "red" -> rgb.first; "green" -> rgb.second; "blue" -> rgb.third
                                                "dimmer" -> if (rgb.first + rgb.second + rgb.third > 0) 255 else 0
                                                else -> 0
                                            }
                                            viewModel.testFixtureChannel(fixture.id, off, v)
                                        }
                                    }
                                },
                                modifier = Modifier.weight(1f),
                                contentPadding = PaddingValues(4.dp)
                            ) { Text(label, style = MaterialTheme.typography.labelSmall) }
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text("Close") }
        }
    )
}
