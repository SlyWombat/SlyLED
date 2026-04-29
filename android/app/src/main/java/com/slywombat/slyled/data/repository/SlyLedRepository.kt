package com.slywombat.slyled.data.repository

import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import com.slywombat.slyled.data.api.SlyLedApi
import com.slywombat.slyled.data.model.*
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class SlyLedRepository @Inject constructor(
    private val okHttpClient: OkHttpClient,
    private val deviceIdentity: DeviceIdentity? = null
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
    suspend fun saveStage(w: Double, h: Double, d: Double) =
        requireApi().saveStage(Stage(w = w, h = h, d = d))

    // Fixtures
    suspend fun getFixtures() = requireApi().getFixtures()
    suspend fun createFixture(fixture: Fixture) = requireApi().createFixture(fixture)
    suspend fun updateFixture(id: Int, fixture: Fixture) = requireApi().updateFixture(id, fixture)
    suspend fun deleteFixture(id: Int) = requireApi().deleteFixture(id)
    suspend fun setAimPoint(id: Int, aimPoint: List<Double>) =
        requireApi().setAimPoint(id, mapOf("aimPoint" to aimPoint))

    // Fixture Controller mode (legacy) — send normalized 0-1 pan/tilt
    suspend fun aimFixtureDirect(id: Int, panNorm: Float, tiltNorm: Float): OkResponse {
        val body = buildJsonObject {
            put("pan", JsonPrimitive(panNorm.toDouble()))
            put("tilt", JsonPrimitive(tiltNorm.toDouble()))
        }
        return requireApi().aimFixtureDirect(id, body)
    }

    // Fixture Controller mode (legacy) — send color/dimmer/strobe channels (0-1 normalized)
    suspend fun setFixtureOutput(id: Int, dimmer: Float, red: Float, green: Float,
                                  blue: Float, white: Float, strobe: Float): OkResponse {
        val body = buildJsonObject {
            put("dimmer", JsonPrimitive(dimmer.toDouble()))
            put("red", JsonPrimitive(red.toDouble()))
            put("green", JsonPrimitive(green.toDouble()))
            put("blue", JsonPrimitive(blue.toDouble()))
            put("white", JsonPrimitive(white.toDouble()))
            put("strobe", JsonPrimitive(strobe.toDouble()))
        }
        return requireApi().aimFixtureDirect(id, body)
    }

    // ── Mover Control (unified API) ────────────────────────────────────

    private fun requireIdentity(): DeviceIdentity =
        deviceIdentity ?: throw IllegalStateException("DeviceIdentity not available")

    suspend fun moverClaim(moverId: Int): OkResponse {
        val id = requireIdentity()
        val body = buildJsonObject {
            put("moverId", moverId)
            put("deviceId", id.deviceId)
            put("deviceName", id.deviceName)
            put("deviceType", "android")
        }
        return requireApi().moverClaim(body)
    }

    suspend fun moverRelease(moverId: Int): OkResponse {
        val id = requireIdentity()
        val body = buildJsonObject {
            put("moverId", moverId)
            put("deviceId", id.deviceId)
        }
        return requireApi().moverRelease(body)
    }

    suspend fun moverStart(moverId: Int): OkResponse {
        val id = requireIdentity()
        val body = buildJsonObject {
            put("moverId", moverId)
            put("deviceId", id.deviceId)
        }
        return requireApi().moverStart(body)
    }

    suspend fun moverCalibrateStart(moverId: Int, roll: Float, pitch: Float, yaw: Float): OkResponse {
        val id = requireIdentity()
        val body = buildJsonObject {
            put("moverId", moverId)
            put("deviceId", id.deviceId)
            put("roll", roll.toDouble())
            put("pitch", pitch.toDouble())
            put("yaw", yaw.toDouble())
        }
        return requireApi().moverCalibrateStart(body)
    }

    suspend fun moverCalibrateEnd(moverId: Int, roll: Float, pitch: Float, yaw: Float): OkResponse {
        val id = requireIdentity()
        val body = buildJsonObject {
            put("moverId", moverId)
            put("deviceId", id.deviceId)
            put("roll", roll.toDouble())
            put("pitch", pitch.toDouble())
            put("yaw", yaw.toDouble())
        }
        return requireApi().moverCalibrateEnd(body)
    }

    suspend fun moverOrient(moverId: Int, roll: Float, pitch: Float, yaw: Float,
                             quat: FloatArray? = null): OkResponse {
        val id = requireIdentity()
        val body = buildJsonObject {
            put("moverId", moverId)
            put("deviceId", id.deviceId)
            // #492 — include the phone's hostname so the server can
            // upgrade the auto-registered remote from GUID → model name.
            put("deviceName", id.deviceName)
            if (quat != null && quat.size == 4) {
                putJsonArray("quat") {
                    quat.forEach { add(JsonPrimitive(it.toDouble())) }
                }
            } else {
                put("roll", roll.toDouble())
                put("pitch", pitch.toDouble())
                put("yaw", yaw.toDouble())
            }
        }
        return requireApi().moverOrient(body)
    }

    suspend fun moverFlash(moverId: Int, on: Boolean): OkResponse {
        val id = requireIdentity()
        val body = buildJsonObject {
            put("moverId", moverId)
            put("deviceId", id.deviceId)
            put("on", on)
        }
        return requireApi().moverFlash(body)
    }

    suspend fun moverSmoothing(moverId: Int, smoothing: Float): OkResponse {
        val id = requireIdentity()
        val body = buildJsonObject {
            put("moverId", moverId)
            put("deviceId", id.deviceId)
            put("smoothing", smoothing.toDouble())
        }
        return requireApi().moverSmoothing(body)
    }

    suspend fun moverColor(moverId: Int, r: Int, g: Int, b: Int, dimmer: Int? = null): OkResponse {
        val id = requireIdentity()
        val body = buildJsonObject {
            put("moverId", moverId)
            put("deviceId", id.deviceId)
            put("r", r)
            put("g", g)
            put("b", b)
            if (dimmer != null) put("dimmer", dimmer)
        }
        return requireApi().moverColor(body)
    }

    // #479 — Mover-Control live status (claims + engine health). Polled
    // by ControlViewModel while a controller-mode session is active.
    suspend fun getMoverControlStatus() = requireApi().getMoverControlStatus()

    // Fixtures Live
    suspend fun getFixturesLive() = requireApi().getFixturesLive()

    // Show Playback
    suspend fun getShowStatus() = requireApi().getShowStatus()
    suspend fun getShowPlaylist() = requireApi().getShowPlaylist()
    suspend fun startShow() = requireApi().startShow()
    suspend fun stopShow() = requireApi().stopShow()

    // Cameras
    suspend fun getCameras() = requireApi().getCameras()
    suspend fun registerCamera(ip: String, name: String? = null): OkResponse {
        val body = mutableMapOf<String, Any>("ip" to ip)
        if (!name.isNullOrBlank()) body["name"] = name
        return requireApi().registerCamera(body)
    }
    suspend fun unregisterCamera(id: Int) = requireApi().unregisterCamera(id)
    suspend fun getCameraStatus(id: Int) = requireApi().getCameraStatus(id)
    suspend fun startTracking(id: Int) = requireApi().startTracking(id)
    suspend fun stopTracking(id: Int) = requireApi().stopTracking(id)
    suspend fun getCameraSshSettings() = requireApi().getCameraSshSettings()
    suspend fun saveCameraSshSettings(body: Map<String, Any>) = requireApi().saveCameraSshSettings(body)
    suspend fun scanNetwork() = requireApi().scanNetwork()
    suspend fun scanNetworkResults() = requireApi().scanNetworkResults()
    suspend fun deployCameraServer(ip: String) = requireApi().deployCameraServer(mapOf("ip" to ip))
    suspend fun deployStatus() = requireApi().deployStatus()

    // DMX Profiles & Control
    suspend fun getDmxProfiles(category: String? = null) = requireApi().getDmxProfiles(category)
    suspend fun getDmxStatus(): DmxStatus = DmxStatus.fromJson(requireApi().getDmxStatus())
    suspend fun startDmx(body: JsonObject) = requireApi().startDmx(body)
    suspend fun stopDmx() = requireApi().stopDmx()
    suspend fun dmxBlackout() = requireApi().dmxBlackout()
    suspend fun getDmxFixtureChannels(id: Int) = requireApi().getDmxFixtureChannels(id)
    suspend fun testDmxFixture(id: Int, body: JsonObject) = requireApi().testDmxFixture(id, body)
    suspend fun getDmxSettings() = requireApi().getDmxSettings()
    suspend fun saveDmxSettings(body: JsonObject) = requireApi().saveDmxSettings(body)

    // Stage Objects
    suspend fun getObjects() = requireApi().getObjects()
    suspend fun updateObject(id: Int, posX: Int, posY: Int, scaleW: Int, scaleH: Int, opacity: Int) {
        // Server has no PUT for objects — delete and recreate
        val old = getObjects().find { it.id == id } ?: return
        requireApi().deleteObject(id)
        requireApi().createObject(StageObject(
            name = old.name, objectType = old.objectType, mobility = old.mobility,
            color = old.color, opacity = opacity,
            transform = ObjectTransform(
                pos = listOf(posX.toDouble(), posY.toDouble(), 0.0),
                rot = listOf(0.0, 0.0, 0.0),
                scale = listOf(scaleW.toDouble(), scaleH.toDouble(), 100.0))
        ))
    }
    suspend fun deleteObject(id: Int) = requireApi().deleteObject(id)

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
