package com.slywombat.slyled.data.repository

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.floatPreferencesKey
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

private val Context.dataStore by preferencesDataStore(name = "slyled_prefs")

/**
 * #427 — operator's stage position in mm, used by Pointer mode to
 * intersect the phone's aim ray with the floor.
 */
data class UserPosition(val xMm: Float, val yMm: Float, val zMm: Float)

@Singleton
class ServerPreferences @Inject constructor(
    @ApplicationContext private val context: Context
) {
    private val HOST_KEY = stringPreferencesKey("server_host")
    private val PORT_KEY = intPreferencesKey("server_port")

    // #427 — pointer-mode operator position (stage mm).
    private val USER_X_KEY = floatPreferencesKey("user_pos_x_mm")
    private val USER_Y_KEY = floatPreferencesKey("user_pos_y_mm")
    private val USER_Z_KEY = floatPreferencesKey("user_pos_z_mm")

    suspend fun save(host: String, port: Int) {
        context.dataStore.edit { prefs ->
            prefs[HOST_KEY] = host
            prefs[PORT_KEY] = port
        }
    }

    suspend fun load(): Pair<String, Int>? {
        val prefs = context.dataStore.data.first()
        val host = prefs[HOST_KEY] ?: return null
        val port = prefs[PORT_KEY] ?: 8080
        return Pair(host, port)
    }

    suspend fun saveUserPosition(pos: UserPosition) {
        context.dataStore.edit { prefs ->
            prefs[USER_X_KEY] = pos.xMm
            prefs[USER_Y_KEY] = pos.yMm
            prefs[USER_Z_KEY] = pos.zMm
        }
    }

    /** Returns the saved operator position, or a default of stage centre at
     *  ~standing-eye height (1700 mm) when nothing has been saved yet. The
     *  default X/Y assume the operator stands near the centre of a 4 m × 4 m
     *  stage; user can dial it in once and forget. */
    suspend fun loadUserPosition(): UserPosition {
        val prefs = context.dataStore.data.first()
        return UserPosition(
            xMm = prefs[USER_X_KEY] ?: 2000f,
            yMm = prefs[USER_Y_KEY] ?: 2000f,
            zMm = prefs[USER_Z_KEY] ?: 1700f,
        )
    }

    suspend fun clear() {
        context.dataStore.edit { it.clear() }
    }
}
