package com.slywombat.slyled.data.repository

import android.content.Context
import android.os.Build
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Stable device identity for mover-control claim/release.
 * Generates a UUID on first launch, persists in SharedPreferences.
 */
@Singleton
class DeviceIdentity @Inject constructor(
    @ApplicationContext private val context: Context
) {
    /** Stable UUID — generated once, persisted across launches. */
    val deviceId: String by lazy {
        val prefs = context.getSharedPreferences("slyled_device", Context.MODE_PRIVATE)
        prefs.getString("device_id", null) ?: run {
            val id = UUID.randomUUID().toString()
            prefs.edit().putString("device_id", id).apply()
            id
        }
    }

    /** Human-readable device name (e.g., "Pixel 8 Pro"). */
    val deviceName: String = Build.MODEL
}
