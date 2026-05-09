package com.slywombat.slyled.data.repository

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
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

/**
 * #804 — Auto Brightness operator-tunable settings, persisted across
 * app restarts. Mirrors the EnvelopeFollower defaults so loading
 * before any user interaction is a no-op.
 */
data class AutoBrightnessPrefs(
    val enabled: Boolean,
    val sensitivity: Float,
    val floor: Float,
    val ceiling: Float,
    val attackMs: Float,
    val releaseMs: Float,
    // #820 — semantic audio source. See com.slywombat.slyled.audio.AudioSourceKind.
    // Persisted as enum name string. Old `audioSource` int pref (now
    // unused) is left in DataStore for one release in case a downgrade
    // installation needs to read it; new code only writes / reads the
    // string key.
    val audioSourceKind: com.slywombat.slyled.audio.AudioSourceKind =
        com.slywombat.slyled.audio.AudioSourceKind.DEFAULT,
)

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

    // #804 — Auto Brightness persisted tunables.
    private val AB_ENABLED_KEY = booleanPreferencesKey("auto_brightness_enabled")
    private val AB_SENSITIVITY_KEY = floatPreferencesKey("auto_brightness_sensitivity")
    private val AB_FLOOR_KEY = floatPreferencesKey("auto_brightness_floor")
    private val AB_CEILING_KEY = floatPreferencesKey("auto_brightness_ceiling")
    private val AB_ATTACK_KEY = floatPreferencesKey("auto_brightness_attack_ms")
    private val AB_RELEASE_KEY = floatPreferencesKey("auto_brightness_release_ms")
    // #820 — semantic audio source kind, persisted as enum name string.
    private val AB_AUDIO_SOURCE_KIND_KEY = stringPreferencesKey("auto_brightness_audio_source_kind")

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

    // #804 — Auto Brightness persisted prefs. Defaults mirror
    // EnvelopeFollower's in-code defaults so a fresh-install operator
    // sees the same baseline as before persistence existed.
    suspend fun saveAutoBrightness(prefs: AutoBrightnessPrefs) {
        context.dataStore.edit { p ->
            p[AB_ENABLED_KEY] = prefs.enabled
            p[AB_SENSITIVITY_KEY] = prefs.sensitivity
            p[AB_FLOOR_KEY] = prefs.floor
            p[AB_CEILING_KEY] = prefs.ceiling
            p[AB_ATTACK_KEY] = prefs.attackMs
            p[AB_RELEASE_KEY] = prefs.releaseMs
            p[AB_AUDIO_SOURCE_KIND_KEY] = prefs.audioSourceKind.name
        }
    }

    suspend fun loadAutoBrightness(): AutoBrightnessPrefs {
        val p = context.dataStore.data.first()
        return AutoBrightnessPrefs(
            enabled = p[AB_ENABLED_KEY] ?: false,
            sensitivity = p[AB_SENSITIVITY_KEY] ?: 1.5f,
            floor = p[AB_FLOOR_KEY] ?: 0.05f,
            ceiling = p[AB_CEILING_KEY] ?: 1.0f,
            attackMs = p[AB_ATTACK_KEY] ?: 8f,
            releaseMs = p[AB_RELEASE_KEY] ?: 220f,
            audioSourceKind = com.slywombat.slyled.audio.AudioSourceKind
                .fromName(p[AB_AUDIO_SOURCE_KIND_KEY]),
        )
    }
}
