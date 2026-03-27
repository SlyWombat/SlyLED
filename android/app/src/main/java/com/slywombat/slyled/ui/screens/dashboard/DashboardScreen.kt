package com.slywombat.slyled.ui.screens.dashboard

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.slywombat.slyled.data.model.OnlineStatus
import com.slywombat.slyled.viewmodel.DashboardViewModel

@Composable
fun DashboardScreen(viewModel: DashboardViewModel = hiltViewModel()) {
    val children by viewModel.children.collectAsState()
    val settings by viewModel.settings.collectAsState()
    val networkError by viewModel.networkError.collectAsState()

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        if (networkError) {
            Card(
                modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)
            ) {
                Text(
                    "Unable to reach server",
                    modifier = Modifier.padding(12.dp),
                    color = MaterialTheme.colorScheme.onErrorContainer,
                    style = MaterialTheme.typography.labelLarge
                )
            }
        }

        val online = children.count { it.onlineStatus == OnlineStatus.ONLINE }
        Text(
            "$online / ${children.size} performers online",
            style = MaterialTheme.typography.titleMedium
        )
        Spacer(Modifier.height(8.dp))

        if (settings.runnerRunning) {
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(modifier = Modifier.padding(12.dp)) {
                    Text("Runner active", style = MaterialTheme.typography.labelLarge)
                    LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(top = 8.dp))
                }
            }
            Spacer(Modifier.height(12.dp))
        }

        if (children.isEmpty()) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text("No performers registered", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        } else {
            children.forEach { child ->
                Card(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                    Row(
                        modifier = Modifier.padding(12.dp).fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column {
                            Text(child.hostname, style = MaterialTheme.typography.bodyMedium)
                            Text(child.ip, style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        SuggestionChip(
                            onClick = {},
                            label = { Text(if (child.onlineStatus == OnlineStatus.ONLINE) "Online" else "Offline") },
                            colors = SuggestionChipDefaults.suggestionChipColors(
                                containerColor = if (child.onlineStatus == OnlineStatus.ONLINE)
                                    MaterialTheme.colorScheme.primaryContainer
                                else MaterialTheme.colorScheme.surfaceVariant
                            )
                        )
                    }
                }
            }
        }
    }
}
