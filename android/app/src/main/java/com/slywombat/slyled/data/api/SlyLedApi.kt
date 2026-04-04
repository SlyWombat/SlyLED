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

    // Immediate action
    @POST("api/action")
    suspend fun sendAction(@Body body: Map<String, @JvmSuppressWildcards Any>): OkResponse

    @POST("api/action/stop")
    suspend fun stopAction(@Body body: Map<String, String> = emptyMap()): OkResponse

    // Runners
    @GET("api/runners")
    suspend fun getRunners(): List<RunnerSummary>

    @GET("api/runners/{id}")
    suspend fun getRunner(@Path("id") id: Int): Runner

    @POST("api/runners")
    suspend fun createRunner(@Body request: CreateRunnerRequest): OkResponse

    @PUT("api/runners/{id}")
    suspend fun updateRunner(@Path("id") id: Int, @Body runner: Runner): OkResponse

    @DELETE("api/runners/{id}")
    suspend fun deleteRunner(@Path("id") id: Int): OkResponse

    @POST("api/runners/{id}/compute")
    suspend fun computeRunner(@Path("id") id: Int): OkResponse

    @POST("api/runners/{id}/sync")
    suspend fun syncRunner(@Path("id") id: Int): OkResponse

    @POST("api/runners/{id}/start")
    suspend fun startRunner(@Path("id") id: Int): OkResponse

    @POST("api/runners/stop")
    suspend fun stopRunners(): OkResponse

    @GET("api/runners/live")
    suspend fun getLiveEvents(): List<LiveEvent>

    // Flights
    @GET("api/flights")
    suspend fun getFlights(): List<Flight>

    @POST("api/flights")
    suspend fun createFlight(@Body flight: Flight): OkResponse

    @PUT("api/flights/{id}")
    suspend fun updateFlight(@Path("id") id: Int, @Body flight: Flight): OkResponse

    @DELETE("api/flights/{id}")
    suspend fun deleteFlight(@Path("id") id: Int): OkResponse

    // Shows
    @GET("api/shows")
    suspend fun getShows(): List<Show>

    @POST("api/shows")
    suspend fun createShow(@Body show: Show): OkResponse

    @PUT("api/shows/{id}")
    suspend fun updateShow(@Path("id") id: Int, @Body show: Show): OkResponse

    @DELETE("api/shows/{id}")
    suspend fun deleteShow(@Path("id") id: Int): OkResponse

    @POST("api/shows/{id}/start")
    suspend fun startShow(@Path("id") id: Int): OkResponse

    @POST("api/shows/stop")
    suspend fun stopShows(): OkResponse

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

    @POST("api/fixtures/{id}/resolve")
    suspend fun resolveFixture(@Path("id") id: Int): Map<String, Any>

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

    // ── Surfaces ───────────────────────────────────────────────────────
    @GET("api/surfaces")
    suspend fun getSurfaces(): List<Surface>

    @POST("api/surfaces")
    suspend fun createSurface(@Body surface: Surface): OkResponse

    @DELETE("api/surfaces/{id}")
    suspend fun deleteSurface(@Path("id") id: Int): OkResponse

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
