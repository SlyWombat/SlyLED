package com.slywombat.slyled.ui.screens.connection

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.common.InputImage
import com.slywombat.slyled.ui.theme.CyanSecondary
import com.slywombat.slyled.viewmodel.ConnectionViewModel
import java.util.concurrent.Executors

@Composable
fun ConnectionScreen(viewModel: ConnectionViewModel) {
    val savedHost by viewModel.savedHost.collectAsState()
    val savedPort by viewModel.savedPort.collectAsState()
    var host by remember { mutableStateOf("") }
    var port by remember { mutableStateOf("8080") }
    var showScanner by remember { mutableStateOf(false) }

    // Pre-fill from saved connection
    LaunchedEffect(savedHost, savedPort) {
        if (savedHost.isNotEmpty() && host.isEmpty()) {
            host = savedHost
            port = savedPort.toString()
        }
    }

    val connState by viewModel.state.collectAsState()
    val isConnecting = connState == ConnectionViewModel.State.CONNECTING
    val snackbarHostState = remember { SnackbarHostState() }

    // Collect one-shot error events
    LaunchedEffect(Unit) {
        viewModel.errorEvent.collect { msg ->
            snackbarHostState.showSnackbar(
                message = msg,
                duration = SnackbarDuration.Long,
                withDismissAction = true
            )
        }
    }

    fun doConnect() {
        val h = host.trim()
        val p = port.trim().toIntOrNull() ?: 8080
        if (h.isEmpty()) return
        viewModel.connect(h, p)
    }

    fun onQrScanned(url: String) {
        val stripped = url.removePrefix("slyled://").removePrefix("SLYLED://")
        val parts = stripped.split(":")
        if (parts.isNotEmpty()) {
            host = parts[0]
            port = if (parts.size > 1) parts[1] else "8080"
            showScanner = false
            viewModel.connect(host.trim(), port.trim().toIntOrNull() ?: 8080)
        }
    }

    if (showScanner) {
        QrScannerScreen(
            onScanned = { onQrScanned(it) },
            onDismiss = { showScanner = false }
        )
        return
    }

    Scaffold(snackbarHost = { SnackbarHost(snackbarHostState) }) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(24.dp)
                .verticalScroll(rememberScrollState()),
            contentAlignment = Alignment.Center
        ) {
            Card(
                modifier = Modifier.fillMaxWidth().widthIn(max = 400.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
            ) {
                Column(
                    modifier = Modifier.padding(24.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(16.dp)
                ) {
                    Text(
                        "SlyLED",
                        style = MaterialTheme.typography.headlineMedium,
                        fontWeight = FontWeight.Bold,
                        color = CyanSecondary
                    )
                    Text(
                        "Connect to Orchestrator",
                        style = MaterialTheme.typography.bodyLarge,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )

                    Spacer(modifier = Modifier.height(8.dp))

                    OutlinedButton(
                        onClick = { showScanner = true },
                        enabled = !isConnecting,
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Icon(Icons.Default.QrCodeScanner, contentDescription = null)
                        Spacer(Modifier.width(8.dp))
                        Text("Scan QR Code")
                    }

                    HorizontalDivider()

                    Text(
                        "Manual Connection",
                        style = MaterialTheme.typography.labelLarge,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )

                    OutlinedTextField(
                        value = host,
                        onValueChange = { host = it },
                        label = { Text("Server IP") },
                        placeholder = { Text("192.168.1.100") },
                        singleLine = true,
                        enabled = !isConnecting,
                        keyboardOptions = KeyboardOptions(
                            keyboardType = KeyboardType.Uri,
                            imeAction = ImeAction.Next
                        ),
                        modifier = Modifier.fillMaxWidth()
                    )

                    OutlinedTextField(
                        value = port,
                        onValueChange = { port = it },
                        label = { Text("Port") },
                        placeholder = { Text("8080") },
                        singleLine = true,
                        enabled = !isConnecting,
                        keyboardOptions = KeyboardOptions(
                            keyboardType = KeyboardType.Number,
                            imeAction = ImeAction.Done
                        ),
                        keyboardActions = KeyboardActions(onDone = { doConnect() }),
                        modifier = Modifier.fillMaxWidth()
                    )

                    Button(
                        onClick = { doConnect() },
                        enabled = !isConnecting && host.isNotBlank(),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        if (isConnecting) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(20.dp),
                                strokeWidth = 2.dp,
                                color = MaterialTheme.colorScheme.onPrimary
                            )
                            Spacer(Modifier.width(8.dp))
                            Text("Connecting...")
                        } else {
                            Icon(Icons.Default.Link, contentDescription = null)
                            Spacer(Modifier.width(8.dp))
                            Text("Connect")
                        }
                    }

                    if (isConnecting) {
                        Text(
                            "Trying ${host.trim()}:${port.trim()}...",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
            }
        }
    }
}

@Composable
fun QrScannerScreen(onScanned: (String) -> Unit, onDismiss: () -> Unit) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    var hasCameraPermission by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
                    PackageManager.PERMISSION_GRANTED
        )
    }
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted -> hasCameraPermission = granted }

    LaunchedEffect(Unit) {
        if (!hasCameraPermission) {
            permissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    Box(modifier = Modifier.fillMaxSize()) {
        if (hasCameraPermission) {
            val scanned = remember { mutableStateOf(false) }
            val analysisExecutor = remember { Executors.newSingleThreadExecutor() }
            DisposableEffect(Unit) { onDispose { analysisExecutor.shutdown() } }

            AndroidView(
                factory = { ctx ->
                    val previewView = PreviewView(ctx)
                    val cameraProviderFuture = ProcessCameraProvider.getInstance(ctx)
                    cameraProviderFuture.addListener({
                        val cameraProvider = cameraProviderFuture.get()
                        val preview = Preview.Builder().build().also {
                            it.surfaceProvider = previewView.surfaceProvider
                        }

                        val analyzer = ImageAnalysis.Builder()
                            .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                            .build()
                            .also { analysis ->
                                analysis.setAnalyzer(analysisExecutor) { imageProxy ->
                                    @androidx.camera.core.ExperimentalGetImage
                                    val mediaImage = imageProxy.image
                                    if (mediaImage != null && !scanned.value) {
                                        val image = InputImage.fromMediaImage(
                                            mediaImage, imageProxy.imageInfo.rotationDegrees
                                        )
                                        val scanner = BarcodeScanning.getClient()
                                        scanner.process(image)
                                            .addOnSuccessListener { barcodes ->
                                                for (barcode in barcodes) {
                                                    val raw = barcode.rawValue ?: continue
                                                    if (raw.startsWith("slyled://", ignoreCase = true)) {
                                                        scanned.value = true
                                                        onScanned(raw)
                                                        return@addOnSuccessListener
                                                    }
                                                }
                                            }
                                            .addOnCompleteListener { imageProxy.close() }
                                    } else {
                                        imageProxy.close()
                                    }
                                }
                            }

                        cameraProvider.unbindAll()
                        cameraProvider.bindToLifecycle(
                            lifecycleOwner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analyzer
                        )
                    }, ContextCompat.getMainExecutor(ctx))
                    previewView
                },
                modifier = Modifier.fillMaxSize()
            )

            Column(
                modifier = Modifier.fillMaxSize().padding(16.dp),
                verticalArrangement = Arrangement.SpaceBetween
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        "Point at SlyLED QR code",
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.onBackground
                    )
                    IconButton(onClick = onDismiss) {
                        Icon(Icons.Default.Close, contentDescription = "Close",
                            tint = MaterialTheme.colorScheme.onBackground)
                    }
                }
                Spacer(Modifier.weight(1f))
            }
        } else {
            Column(
                modifier = Modifier.fillMaxSize().padding(32.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center
            ) {
                Text("Camera permission required to scan QR codes")
                Spacer(Modifier.height(16.dp))
                Button(onClick = onDismiss) { Text("Go Back") }
            }
        }
    }
}
