package com.slywombat.slyled.data.repository

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

private val Context.dataStore by preferencesDataStore(name = "slyled_prefs")

@Singleton
class ServerPreferences @Inject constructor(
    @ApplicationContext private val context: Context
) {
    private val HOST_KEY = stringPreferencesKey("server_host")
    private val PORT_KEY = intPreferencesKey("server_port")

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

    suspend fun clear() {
        context.dataStore.edit { it.clear() }
    }
}
