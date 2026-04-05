package com.slywombat.slyled.data.repository

import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import com.slywombat.slyled.data.api.SlyLedApi
import com.slywombat.slyled.data.model.*
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class SlyLedRepository @Inject constructor(
    private val okHttpClient: OkHttpClient
) {
    private val json = Json { ignoreUnknownKeys = true; isLenient = true }
    private var api: SlyLedApi? = null
    var baseUrl: String? = null
        private set

    val isConnected: Boolean get() = api != null

    fun connect(host: String, port: Int): SlyLedApi {
        val url = "http://$host:$port/"
        baseUrl = url
        val retrofit = Retrofit.Builder()
            .baseUrl(url)
            .client(okHttpClient)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
        api = retrofit.create(SlyLedApi::class.java)
        return api!!
    }

    fun disconnect() {
        api = null
        baseUrl = null
    }

    private fun requireApi(): SlyLedApi = api ?: throw IllegalStateException("Not connected")

    // Polling flows
    fun childrenFlow(intervalMs: Long = 5000): Flow<Result<List<Child>>> = flow {
        while (true) {
            emit(runCatching { requireApi().getChildren() })
            delay(intervalMs)
        }
    }

    fun settingsFlow(intervalMs: Long = 3000): Flow<Result<Settings>> = flow {
        while (true) {
            emit(runCatching { requireApi().getSettings() })
            delay(intervalMs)
        }
    }

    // Status
    suspend fun getStatus() = requireApi().getStatus()

    // Children
    suspend fun getChildren() = requireApi().getChildren()
    suspend fun discoverChildren() = requireApi().discoverChildren()
    suspend fun addChild(ip: String) = requireApi().addChild(AddChildRequest(ip))
    suspend fun deleteChild(id: Int) = requireApi().deleteChild(id)
    suspend fun refreshChild(id: Int) = requireApi().refreshChild(id)
    suspend fun rebootChild(id: Int) = requireApi().rebootChild(id)
    suspend fun refreshAllChildren() = requireApi().refreshAllChildren()
    suspend fun getChildStatus(id: Int) = requireApi().getChildStatus(id)

    // Layout
    suspend fun getLayout() = requireApi().getLayout()
    suspend fun saveLayout(layout: Layout) = requireApi().saveLayout(layout)

    // Settings
    suspend fun getSettings() = requireApi().getSettings()
    suspend fun saveSettings(settings: Settings) = requireApi().saveSettings(settings)

    // Actions
    suspend fun getActions() = requireApi().getActions()
    suspend fun createAction(action: Action) = requireApi().createAction(action)
    suspend fun updateAction(id: Int, action: Action) = requireApi().updateAction(id, action)
    suspend fun deleteAction(id: Int) = requireApi().deleteAction(id)

    // Config/Show
    suspend fun exportConfig() = requireApi().exportConfig()
    suspend fun importConfig(data: JsonObject) = requireApi().importConfig(data)
    suspend fun exportShow() = requireApi().exportShow()
    suspend fun importShow(data: JsonObject) = requireApi().importShow(data)
    suspend fun generateDemo() = requireApi().generateDemo()

    // Timelines
    suspend fun getTimelines() = requireApi().getTimelines()
    suspend fun getTimeline(id: Int) = requireApi().getTimeline(id)
    suspend fun createTimeline(timeline: Timeline) = requireApi().createTimeline(timeline)
    suspend fun deleteTimeline(id: Int) = requireApi().deleteTimeline(id)
    suspend fun bakeTimeline(id: Int) = requireApi().bakeTimeline(id)
    suspend fun getBakeStatus(id: Int) = requireApi().getBakeStatus(id)
    suspend fun syncBaked(id: Int) = requireApi().syncBaked(id)
    suspend fun getSyncStatus(id: Int) = requireApi().getSyncStatus(id)
    suspend fun startTimeline(id: Int) = requireApi().startTimeline(id)
    suspend fun stopTimeline(id: Int) = requireApi().stopTimeline(id)
    suspend fun getTimelineStatus(id: Int) = requireApi().getTimelineStatus(id)
    suspend fun getBakePreview(id: Int) = requireApi().getBakePreview(id)

    // Presets
    suspend fun getShowPresets() = requireApi().getShowPresets()
    suspend fun loadPreset(body: Map<String, String>) = requireApi().loadPreset(body)

    // Stage
    suspend fun getStage() = requireApi().getStage()

    // Fixtures
    suspend fun getFixtures() = requireApi().getFixtures()
    suspend fun createFixture(fixture: Fixture) = requireApi().createFixture(fixture)
    suspend fun updateFixture(id: Int, fixture: Fixture) = requireApi().updateFixture(id, fixture)
    suspend fun deleteFixture(id: Int) = requireApi().deleteFixture(id)

    // DMX Profiles & Control
    suspend fun getDmxProfiles(category: String? = null) = requireApi().getDmxProfiles(category)
    suspend fun getDmxStatus() = requireApi().getDmxStatus()
    suspend fun startDmx(body: JsonObject) = requireApi().startDmx(body)
    suspend fun stopDmx() = requireApi().stopDmx()
    suspend fun dmxBlackout() = requireApi().dmxBlackout()
    suspend fun getDmxFixtureChannels(id: Int) = requireApi().getDmxFixtureChannels(id)
    suspend fun testDmxFixture(id: Int, body: JsonObject) = requireApi().testDmxFixture(id, body)
    suspend fun getDmxSettings() = requireApi().getDmxSettings()
    suspend fun saveDmxSettings(body: JsonObject) = requireApi().saveDmxSettings(body)

    // Surfaces
    suspend fun getSurfaces() = requireApi().getSurfaces()
    suspend fun updateSurface(id: Int, posX: Int, posY: Int, scaleW: Int, scaleH: Int, opacity: Int) {
        // Server has no PUT for surfaces — delete and recreate
        val old = getSurfaces().find { it.id == id } ?: return
        requireApi().deleteSurface(id)
        requireApi().createSurface(Surface(
            name = old.name, surfaceType = old.surfaceType, color = old.color, opacity = opacity,
            transform = SurfaceTransform(
                pos = listOf(posX.toDouble(), posY.toDouble(), 0.0),
                rot = listOf(0.0, 0.0, 0.0),
                scale = listOf(scaleW.toDouble(), scaleH.toDouble(), 100.0))
        ))
    }
    suspend fun deleteSurface(id: Int) = requireApi().deleteSurface(id)

    // OFL
    suspend fun oflSearch(query: String) = requireApi().oflSearch(query)
    suspend fun oflImport(manufacturer: String, fixture: String) =
        requireApi().oflImport(mapOf("manufacturer" to manufacturer, "fixture" to fixture))
    suspend fun getDmxPatch() = requireApi().getDmxPatch()
    // Spatial Effects
    suspend fun getSpatialEffects() = requireApi().getSpatialEffects()

    // Timeline update
    suspend fun updateTimeline(id: Int, timeline: Timeline) = requireApi().updateTimeline(id, timeline)

    // Migration
    suspend fun migrateLayout() = requireApi().migrateLayout()

    // Reset
    suspend fun factoryReset() = requireApi().factoryReset()
}
