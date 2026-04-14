package com.slywombat.slyled.ui.navigation

import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.*
import com.slywombat.slyled.ui.screens.connection.ConnectionScreen
import com.slywombat.slyled.ui.screens.livestage.LiveStageScreen
import com.slywombat.slyled.ui.screens.control.ControlScreen
import com.slywombat.slyled.ui.screens.status.StatusScreen
import com.slywombat.slyled.ui.screens.settings.SettingsScreen
import com.slywombat.slyled.viewmodel.ConnectionViewModel

enum class Tab(val route: String, val label: String, val icon: ImageVector) {
    STAGE("stage", "Stage", Icons.Default.Visibility),
    CONTROL("control", "Control", Icons.Default.PlayCircle),
    STATUS("status", "Status", Icons.Default.DeviceHub),
}

@Composable
fun SlyLedNavHost(connectionVm: ConnectionViewModel) {
    val rootNav = rememberNavController()
    val connState by connectionVm.state.collectAsState()
    val isConnected = connState == ConnectionViewModel.State.CONNECTED

    LaunchedEffect(isConnected) {
        val current = rootNav.currentDestination?.route
        if (isConnected && current != "main") {
            rootNav.navigate("main") {
                popUpTo("connection") { inclusive = true }
            }
        } else if (!isConnected && current != "connection") {
            rootNav.navigate("connection") {
                popUpTo("main") { inclusive = true }
            }
        }
    }

    NavHost(
        navController = rootNav,
        startDestination = if (isConnected) "main" else "connection"
    ) {
        composable("connection") {
            ConnectionScreen(viewModel = connectionVm)
        }
        composable("main") {
            MainScaffold(
                connectionVm = connectionVm,
                onDisconnect = { connectionVm.disconnect() }
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScaffold(connectionVm: ConnectionViewModel, onDisconnect: () -> Unit) {
    val navController = rememberNavController()
    val navBackStackEntry by navController.currentBackStackEntryAsState()
    val currentDestination = navBackStackEntry?.destination
    val serverInfo by connectionVm.serverInfo.collectAsState()
    val isOnSettings = currentDestination?.route == "settings"

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        "SlyLED",
                        style = MaterialTheme.typography.titleMedium
                    )
                },
                actions = {
                    if (serverInfo.isNotEmpty()) {
                        Text(
                            serverInfo,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                    IconButton(onClick = {
                        if (isOnSettings) {
                            navController.popBackStack()
                        } else {
                            navController.navigate("settings") {
                                launchSingleTop = true
                            }
                        }
                    }) {
                        Icon(
                            Icons.Default.Settings,
                            contentDescription = "Settings",
                            tint = if (isOnSettings)
                                MaterialTheme.colorScheme.primary
                            else
                                MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface
                )
            )
        },
        bottomBar = {
            NavigationBar {
                Tab.entries.forEach { tab ->
                    NavigationBarItem(
                        icon = { Icon(tab.icon, contentDescription = tab.label) },
                        label = { Text(tab.label, style = MaterialTheme.typography.labelSmall) },
                        selected = currentDestination?.hierarchy?.any { it.route == tab.route } == true,
                        onClick = {
                            // Auto-close settings if open
                            if (isOnSettings) {
                                navController.popBackStack("settings", inclusive = true)
                            }
                            navController.navigate(tab.route) {
                                popUpTo(navController.graph.findStartDestination().id) { saveState = true }
                                launchSingleTop = true
                                restoreState = true
                            }
                        }
                    )
                }
            }
        }
    ) { innerPadding ->
        NavHost(
            navController = navController,
            startDestination = Tab.STAGE.route,
            modifier = Modifier.padding(innerPadding)
        ) {
            composable(Tab.STAGE.route) { LiveStageScreen() }
            composable(Tab.CONTROL.route) { ControlScreen() }
            composable(Tab.STATUS.route) { StatusScreen() }
            composable("settings") { SettingsScreen(onDisconnect = onDisconnect) }
        }
    }
}
