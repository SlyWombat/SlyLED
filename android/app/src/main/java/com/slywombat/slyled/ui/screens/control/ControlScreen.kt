package com.slywombat.slyled.ui.screens.control

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.slywombat.slyled.data.model.Fixture
import com.slywombat.slyled.ui.screens.control.overlays.FixtureSheet
import com.slywombat.slyled.ui.screens.control.overlays.TakeoverSheet
import com.slywombat.slyled.ui.screens.control.pages.FixturesPage
import com.slywombat.slyled.ui.screens.control.pages.GrabPage
import com.slywombat.slyled.ui.screens.control.pages.MasterPage
import com.slywombat.slyled.ui.screens.control.pages.ShowsPage
import com.slywombat.slyled.viewmodel.ControlViewModel
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject

/**
 * Control tab — rebuilt for #888.
 *
 * Persistent NowPlayingAnchor over a 4-page HorizontalPager:
 *   Master (default cold-start) · Grab · Fixtures · Shows
 * Bottom-bar nav stays Stage / Control / Status (per Navigation.kt);
 * Settings is the top-right gear in MainScaffold's TopAppBar.
 * Long-press logo blackout is wired in MainScaffold.
 */
private enum class ControlPage(val label: String) {
    MASTER("Master"),
    GRAB("Grab"),
    FIXTURES("Fixtures"),
    SHOWS("Shows"),
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ControlScreen(viewModel: ControlViewModel = hiltViewModel()) {
    LaunchedEffect(Unit) { viewModel.load() }

    val isRunning = viewModel.settings.collectAsState().value.runnerRunning
    val timelineStatus by viewModel.timelineStatus.collectAsState()
    val showStatus by viewModel.showStatus.collectAsState()
    val timelines by viewModel.timelines.collectAsState()
    val fixtures by viewModel.fixtures.collectAsState()
    val message by viewModel.message.collectAsState()
    val controllerFixtureId by viewModel.controllerFixtureId.collectAsState()
    val controllerReady by viewModel.controllerReady.collectAsState()
    val controllerConnected by viewModel.controllerConnected.collectAsState()
    val controllerStatus by viewModel.controllerStatus.collectAsState()
    val engineRunning by viewModel.engineRunning.collectAsState()

    // Snackbar for ControlViewModel messages.
    val snackbarHostState = remember { SnackbarHostState() }
    LaunchedEffect(message) {
        message?.let {
            snackbarHostState.showSnackbar(it)
            viewModel.clearMessage()
        }
    }

    // FixtureSheet (More controls →) state.
    var openSheet by remember { mutableStateOf<Pair<Fixture, JsonObject>?>(null) }
    // Takeover state — stub; wired here so the GrabPage tap can route through
    // ControllerMode and only show on a real conflict. v1: not auto-shown.
    var takeover by remember { mutableStateOf<Pair<String, String>?>(null) }

    val pagerState = rememberPagerState(
        initialPage = ControlPage.MASTER.ordinal,
        pageCount = { ControlPage.entries.size },
    )
    val scope = rememberCoroutineScope()

    Scaffold(snackbarHost = { SnackbarHost(snackbarHostState) }) { padding ->
        Column(modifier = Modifier.fillMaxSize().padding(padding)) {
            // Persistent NowPlaying anchor.
            NowPlayingAnchor(
                isRunning = isRunning,
                timelineStatus = timelineStatus,
                showStatus = showStatus,
                timelines = timelines,
                onStop = { viewModel.stopShow() },
                onNext = { viewModel.nextShow() },
            )

            // Segmented header tied to pager.
            SecondaryTabRow(
                selectedTabIndex = pagerState.currentPage,
                modifier = Modifier.fillMaxWidth(),
            ) {
                ControlPage.entries.forEachIndexed { index, page ->
                    Tab(
                        selected = pagerState.currentPage == index,
                        onClick = {
                            scope.launch { pagerState.animateScrollToPage(index) }
                        },
                        text = { Text(page.label) },
                    )
                }
            }

            HorizontalPager(
                state = pagerState,
                modifier = Modifier.fillMaxSize(),
            ) { pageIdx ->
                when (ControlPage.entries[pageIdx]) {
                    ControlPage.MASTER -> MasterPage()
                    ControlPage.GRAB -> GrabPage(onGrab = { fid ->
                        viewModel.enterControllerMode(fid)
                    })
                    ControlPage.FIXTURES -> FixturesPage(
                        onOpenSheet = { fix, prof -> openSheet = fix to prof },
                    )
                    ControlPage.SHOWS -> ShowsPage()
                }
            }
        }
    }

    // Full-profile sheet for non-mover DMX fixtures.
    openSheet?.let { (fix, prof) ->
        FixtureSheet(
            fixture = fix,
            profile = prof,
            onDismiss = { openSheet = null },
            onWrite = { offset, byte ->
                viewModel.channelWrite(fix.id, mapOf(offset to byte))
            },
        )
    }

    // Conflict takeover sheet — manually triggered for now; auto-wired
    // once GrabPage detects the held-by-other claim state in v2.
    takeover?.let { (name, heldBy) ->
        TakeoverSheet(
            fixtureName = name,
            heldBy = heldBy,
            onConfirm = { takeover = null },
            onCancel = { takeover = null },
        )
    }

    // Existing ControllerModeOverlay — unchanged from prior version.
    val controllerFix = controllerFixtureId?.let { id -> fixtures.find { it.id == id } }
    if (controllerFix != null && controllerReady) {
        ControllerModeOverlay(
            fixtureName = controllerFix.name.ifBlank { "Fixture ${controllerFix.id}" },
            connected = controllerConnected,
            statusClaim = controllerStatus,
            engineRunning = engineRunning,
            onOrient = { roll, pitch, yaw, quat ->
                viewModel.sendOrientation(controllerFix.id, roll, pitch, yaw, quat)
            },
            onPublishGrip = { rotation ->
                viewModel.publishGripFromSurfaceRotation(rotation)
            },
            onCalibrateStart = { roll, pitch, yaw ->
                viewModel.calibrateStart(controllerFix.id, roll, pitch, yaw)
            },
            onCalibrateEnd = { roll, pitch, yaw, quat ->
                viewModel.calibrateEnd(controllerFix.id, roll, pitch, yaw, quat)
            },
            onColorChange = { r, g, b, dimmer ->
                viewModel.setMoverColor(controllerFix.id, r, g, b, dimmer)
            },
            onFlash = { on -> viewModel.setFlash(controllerFix.id, on) },
            onDismiss = { viewModel.exitControllerMode() },
        )
    }
}
