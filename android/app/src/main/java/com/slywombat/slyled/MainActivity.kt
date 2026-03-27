package com.slywombat.slyled

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import com.slywombat.slyled.ui.navigation.SlyLedNavHost
import com.slywombat.slyled.ui.theme.SlyLedTheme
import com.slywombat.slyled.viewmodel.ConnectionViewModel
import dagger.hilt.android.AndroidEntryPoint

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    private val connectionVm: ConnectionViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            SlyLedTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    SlyLedNavHost(connectionVm = connectionVm)
                }
            }
        }
    }
}
