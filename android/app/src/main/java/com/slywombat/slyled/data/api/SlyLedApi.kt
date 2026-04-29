package com.slywombat.slyled.data.api

import com.slywombat.slyled.data.model.*
import kotlinx.serialization.json.JsonObject
import retrofit2.http.*

interface SlyLedApi {
    // Status
    @GET("status")
    suspend fun getStatus(): StatusResponse

    // Children
    @GET("api/children")
    suspend fun getChildren(): List<Child>

    @GET("api/children/discover")
    suspend fun discoverChildren(): List<Child>

    @POST("api/children")
    suspend fun addChild(@Body request: AddChildRequest): OkResponse

    @DELETE("api/children/{id}")
    suspend fun deleteChild(@Path("id") id: Int): OkResponse

    @POST("api/children/{id}/refresh")
    suspend fun refreshChild(@Path("id") id: Int): OkResponse

    @POST("api/children/{id}/reboot")
    suspend fun rebootChild(@Path("id") id: Int): OkResponse

    @POST("api/children/refresh-all")
    suspend fun refreshAllChildren(): OkResponse

    @GET("api/children/{id}/status")
    suspend fun getChildStatus(@Path("id") id: Int): ChildStatus

    // Layout
    @GET("api/layout")
    suspend fun getLayout(): Layout

    @POST("api/layout")
    suspend fun saveLayout(@Body layout: Layout): OkResponse

    // Settings
    @GET("api/settings")
    suspend fun getSettings(): Settings

    @POST("api/settings")
    suspend fun saveSettings(@Body body: Settings): OkResponse

    // Actions
    @GET("api/actions")
    suspend fun getActions(): List<Action>

    @POST("api/actions")
    suspend fun createAction(@Body action: Action): OkResponse

    @PUT("api/actions/{id}")
    suspend fun updateAction(@Path("id") id: Int, @Body action: Action): OkResponse

    @DELETE("api/actions/{id}")
    suspend fun deleteAction(@Path("id") id: Int): OkResponse

    // Config/Show export-import
    @GET("api/config/export")
    suspend fun exportConfig(): JsonObject

    @POST("api/config/import")
    suspend fun importConfig(@Body body: JsonObject): OkResponse

    @GET("api/show/export")
    suspend fun exportShow(): JsonObject

    @POST("api/show/import")
    suspend fun importShow(@Body body: JsonObject): OkResponse

    @POST("api/show/demo")
    suspend fun generateDemo(@Body body: Map<String, String> = emptyMap()): OkResponse

    // Factory reset
    @Headers("X-SlyLED-Confirm: true")
    @POST("api/reset")
    suspend fun factoryReset(): OkResponse

    // ── Stage ──────────────────────────────────────────────────────────
    @GET("api/stage")
    suspend fun getStage(): Stage

    @POST("api/stage")
    suspend fun saveStage(@Body stage: Stage): OkResponse

    // ── Fixtures ───────────────────────────────────────────────────────
    @GET("api/fixtures")
    suspend fun getFixtures(): List<Fixture>

    @POST("api/fixtures")
    suspend fun createFixture(@Body fixture: Fixture): OkResponse

    @PUT("api/fixtures/{id}")
    suspend fun updateFixture(@Path("id") id: Int, @Body fixture: Fixture): OkResponse

    @DELETE("api/fixtures/{id}")
    suspend fun deleteFixture(@Path("id") id: Int): OkResponse

    @PUT("api/fixtures/{id}/aim")
    suspend fun setAimPoint(@Path("id") id: Int, @Body body: Map<String, @JvmSuppressWildcards Any>): OkResponse

    @POST("api/fixtures/{id}/resolve")
    suspend fun resolveFixture(@Path("id") id: Int): Map<String, Any>

    // ── Fixture Controller mode (legacy) — send normalized pan/tilt + dimmer ──
    @POST("api/fixtures/{id}/dmx-test")
    suspend fun aimFixtureDirect(@Path("id") id: Int, @Body body: JsonObject): OkResponse

    // ── Mover Control (unified API) ──────────────────────────────────
    @POST("api/mover-control/claim")
    suspend fun moverClaim(@Body body: JsonObject): OkResponse

    @POST("api/mover-control/release")
    suspend fun moverRelease(@Body body: JsonObject): OkResponse

    @POST("api/mover-control/start")
    suspend fun moverStart(@Body body: JsonObject): OkResponse

    @POST("api/mover-control/calibrate-start")
    suspend fun moverCalibrateStart(@Body body: JsonObject): OkResponse

    @POST("api/mover-control/calibrate-end")
    suspend fun moverCalibrateEnd(@Body body: JsonObject): OkResponse

    @POST("api/mover-control/orient")
    suspend fun moverOrient(@Body body: JsonObject): OkResponse

    @POST("api/mover-control/color")
    suspend fun moverColor(@Body body: JsonObject): OkResponse

    @POST("api/mover-control/flash")
    suspend fun moverFlash(@Body body: JsonObject): OkResponse

    @POST("api/mover-control/smoothing")
    suspend fun moverSmoothing(@Body body: JsonObject): OkResponse

    // #479 — live status card poll.
    @GET("api/mover-control/status")
    suspend fun getMoverControlStatus(): MoverControlStatus

    // #427 — pointer mode aims by stage XYZ (mm). Body: {targetX,targetY,targetZ}.
    // Server routes through SMART model when present, returns 400
    // {err:"Fixture not calibrated"} when world-XYZ aim isn't supported yet.
    @POST("api/calibration/mover/{id}/aim")
    suspend fun moverAim(@Path("id") id: Int, @Body body: JsonObject): OkResponse

    // #427 — gate pointer mode on the SMART capability flag (#738).
    @GET("api/calibration/mover/{id}/status")
    suspend fun getMoverCalibrationStatus(@Path("id") id: Int): MoverCalibrationStatus

    // ── Fixtures Live ────────────────────────────────────────────────
    @GET("api/fixtures/live")
    suspend fun getFixturesLive(): Map<String, kotlinx.serialization.json.JsonElement>

    // ── Show Playback ─────────────────────────────────────────────────
    @GET("api/show/status")
    suspend fun getShowStatus(): ShowStatus

    @GET("api/show/playlist")
    suspend fun getShowPlaylist(): ShowPlaylist

    @POST("api/show/start")
    suspend fun startShow(): OkResponse

    @POST("api/show/stop")
    suspend fun stopShow(): OkResponse

    // ── Cameras ───────────────────────────────────────────────────────
    @GET("api/cameras")
    suspend fun getCameras(): List<Fixture>

    @POST("api/cameras")
    suspend fun registerCamera(@Body body: Map<String, @JvmSuppressWildcards Any>): OkResponse

    @DELETE("api/cameras/{id}")
    suspend fun unregisterCamera(@Path("id") id: Int): OkResponse

    @GET("api/cameras/discover")
    suspend fun discoverCameras(): JsonObject

    @GET("api/cameras/discover/results")
    suspend fun discoverCamerasResults(): List<JsonObject>

    @GET("api/cameras/{id}/status")
    suspend fun getCameraStatus(@Path("id") id: Int): CameraStatus

    @POST("api/cameras/{id}/track/start")
    suspend fun startTracking(@Path("id") id: Int, @Body body: JsonObject = JsonObject(emptyMap())): OkResponse

    @POST("api/cameras/{id}/track/stop")
    suspend fun stopTracking(@Path("id") id: Int): OkResponse

    @GET("api/cameras/ssh")
    suspend fun getCameraSshSettings(): JsonObject

    @POST("api/cameras/ssh")
    suspend fun saveCameraSshSettings(@Body body: Map<String, @JvmSuppressWildcards Any>): OkResponse

    @GET("api/cameras/scan-network")
    suspend fun scanNetwork(): JsonObject

    @GET("api/cameras/scan-network/results")
    suspend fun scanNetworkResults(): List<JsonObject>

    @POST("api/cameras/deploy")
    suspend fun deployCameraServer(@Body body: Map<String, @JvmSuppressWildcards Any>): OkResponse

    @GET("api/cameras/deploy/status")
    suspend fun deployStatus(): JsonObject

    // ── DMX Profiles & Control ────────────────────────────────────────
    @GET("/api/dmx-profiles")
    suspend fun getDmxProfiles(@Query("category") category: String? = null): List<DmxProfile>

    @GET("/api/dmx-profiles/{id}")
    suspend fun getDmxProfile(@Path("id") id: String): DmxProfile

    @GET("/api/dmx/status")
    suspend fun getDmxStatus(): JsonObject

    @POST("/api/dmx/start")
    suspend fun startDmx(@Body body: JsonObject): OkResponse

    @POST("/api/dmx/stop")
    suspend fun stopDmx(@Body body: JsonObject = JsonObject(emptyMap())): OkResponse

    @POST("/api/dmx/blackout")
    suspend fun dmxBlackout(): OkResponse

    @GET("/api/dmx/fixture/{id}/channels")
    suspend fun getDmxFixtureChannels(@Path("id") id: Int): JsonObject

    @POST("/api/dmx/fixture/{id}/test")
    suspend fun testDmxFixture(@Path("id") id: Int, @Body body: JsonObject): OkResponse

    @GET("/api/dmx/settings")
    suspend fun getDmxSettings(): JsonObject

    @POST("/api/dmx/settings")
    suspend fun saveDmxSettings(@Body body: JsonObject): OkResponse

    // ── OFL Fixture Library ─────────────────────────────────────────────
    @GET("api/dmx-profiles/ofl/search")
    suspend fun oflSearch(@Query("q") query: String, @Query("limit") limit: Int = 30): List<JsonObject>

    @POST("api/dmx-profiles/ofl/import-by-id")
    suspend fun oflImport(@Body body: Map<String, String>): OkResponse

    @GET("api/dmx/patch")
    suspend fun getDmxPatch(): JsonObject

    // ── Stage Objects ─────────────────────────────────────────────────
    @GET("api/objects")
    suspend fun getObjects(): List<StageObject>

    @POST("api/objects")
    suspend fun createObject(@Body obj: StageObject): OkResponse

    @DELETE("api/objects/{id}")
    suspend fun deleteObject(@Path("id") id: Int): OkResponse

    // ── Spatial Effects ────────────────────────────────────────────────
    @GET("api/spatial-effects")
    suspend fun getSpatialEffects(): List<SpatialEffect>

    @POST("api/spatial-effects")
    suspend fun createSpatialEffect(@Body effect: SpatialEffect): OkResponse

    @PUT("api/spatial-effects/{id}")
    suspend fun updateSpatialEffect(@Path("id") id: Int, @Body effect: SpatialEffect): OkResponse

    @DELETE("api/spatial-effects/{id}")
    suspend fun deleteSpatialEffect(@Path("id") id: Int): OkResponse

    // ── Timelines ──────────────────────────────────────────────────────
    @GET("api/timelines")
    suspend fun getTimelines(): List<Timeline>

    @POST("api/timelines")
    suspend fun createTimeline(@Body timeline: Timeline): OkResponse

    @GET("api/timelines/{id}")
    suspend fun getTimeline(@Path("id") id: Int): Timeline

    @PUT("api/timelines/{id}")
    suspend fun updateTimeline(@Path("id") id: Int, @Body timeline: Timeline): OkResponse

    @DELETE("api/timelines/{id}")
    suspend fun deleteTimeline(@Path("id") id: Int): OkResponse

    // ── Bake & Sync ────────────────────────────────────────────────────
    @POST("api/timelines/{id}/bake")
    suspend fun bakeTimeline(@Path("id") id: Int): OkResponse

    @GET("api/timelines/{id}/baked/status")
    suspend fun getBakeStatus(@Path("id") id: Int): BakeStatus

    @POST("api/timelines/{id}/baked/sync")
    suspend fun syncBaked(@Path("id") id: Int): OkResponse

    @GET("api/timelines/{id}/sync/status")
    suspend fun getSyncStatus(@Path("id") id: Int): SyncStatus

    @POST("api/timelines/{id}/start")
    suspend fun startTimeline(@Path("id") id: Int): OkResponse

    @POST("api/timelines/{id}/stop")
    suspend fun stopTimeline(@Path("id") id: Int): OkResponse

    @GET("api/timelines/{id}/status")
    suspend fun getTimelineStatus(@Path("id") id: Int): TimelineStatus

    @GET("api/timelines/{id}/baked/preview")
    suspend fun getBakePreview(@Path("id") id: Int): Map<String, List<List<List<Int>>>>

    // ── Presets ────────────────────────────────────────────────────────
    @GET("api/show/presets")
    suspend fun getShowPresets(): List<ShowPreset>

    @POST("api/show/preset")
    suspend fun loadPreset(@Body body: Map<String, String>): OkResponse

    // ── Migration ──────────────────────────────────────────────────────
    @POST("api/migrate/layout")
    suspend fun migrateLayout(): OkResponse
}
