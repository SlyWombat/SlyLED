package com.slywombat.slyled.ui.screens.setup

import androidx.compose.runtime.LaunchedEffect
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
import com.slywombat.slyled.data.model.OnlineStatus
import com.slywombat.slyled.ui.theme.CyanSecondary
import com.slywombat.slyled.ui.theme.GreenOnline
import com.slywombat.slyled.ui.theme.OrangeWled
import com.slywombat.slyled.ui.theme.RedError
import com.slywombat.slyled.viewmodel.SetupViewModel
import kotlinx.coroutines.flow.collectLatest

@Composable
fun SetupScreen(viewModel: SetupViewModel = hiltViewModel()) {
    // Reload on every screen visit
    LaunchedEffect(Unit) { viewModel.loadChildren() }

    val children by viewModel.children.collectAsState()
    val discovered by viewModel.discovered.collectAsState()
    val isDiscovering by viewModel.isDiscovering.collectAsState()
    val isAdding by viewModel.isAdding.collectAsState()
    val isRefreshing by viewModel.isRefreshing.collectAsState()

    var manualIp by remember { mutableStateOf("") }
    var showDiscovered by remember { mutableStateOf(false) }
    var detailChild by remember { mutableStateOf<Child?>(null) }
    var confirmRemoveId by remember { mutableStateOf<Int?>(null) }
    var confirmRebootId by remember { mutableStateOf<Int?>(null) }

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
                            "Add Performers",
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

            // Discovered performers (expandable)
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
                                        Row(
                                            modifier = Modifier.fillMaxWidth(),
                                            horizontalArrangement = Arrangement.SpaceBetween,
                                            verticalAlignment = Alignment.CenterVertically
                                        ) {
                                            Column {
                                                Text(
                                                    child.hostname,
                                                    style = MaterialTheme.typography.bodyMedium
                                                )
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

            // Registered performers header
            if (children.isNotEmpty()) {
                item {
                    Text(
                        "Registered Performers (${children.size})",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(top = 8.dp)
                    )
                }
            }

            // Performer list
            items(children, key = { it.id }) { child ->
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
                            "No performers registered — use Discover or add manually",
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            style = MaterialTheme.typography.bodyMedium
                        )
                    }
                }
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
            title = { Text("Remove Performer") },
            text = { Text("Remove ${child?.hostname ?: "performer #$id"}? It will need to be re-added.") },
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
            title = { Text("Reboot Performer") },
            text = { Text("Reboot ${child?.hostname ?: "performer #$id"}? It will be offline briefly.") },
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
                    Text(
                        child.hostname,
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold
                    )
                    if (child.name.isNotEmpty() && child.name != child.hostname) {
                        Text(
                            child.name,
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
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(
                    child.ip,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
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
            Text("${child.hostname} Details")
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (child.name.isNotEmpty()) DetailRow("Name", child.name)
                if (child.desc.isNotEmpty()) DetailRow("Description", child.desc)
                DetailRow("IP", child.ip)
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
