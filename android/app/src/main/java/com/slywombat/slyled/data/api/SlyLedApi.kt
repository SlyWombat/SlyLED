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
    suspend fun saveSettings(@Body body: Map<String, @JvmSuppressWildcards Any>): OkResponse

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
    @POST("api/reset")
    suspend fun factoryReset(): OkResponse
}
