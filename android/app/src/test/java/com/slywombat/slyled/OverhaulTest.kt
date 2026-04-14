package com.slywombat.slyled

import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import com.slywombat.slyled.data.api.SlyLedApi
import com.slywombat.slyled.data.model.*
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import retrofit2.Retrofit
import java.util.concurrent.TimeUnit

/**
 * Tests for the Android overhaul (#393) — new API endpoints, models, and screen data flows.
 * Uses MockWebServer to verify all new REST calls match the expected paths and payloads.
 */
class OverhaulTest {
    private lateinit var server: MockWebServer
    private lateinit var api: SlyLedApi
    private val json = Json { ignoreUnknownKeys = true; isLenient = true }

    @Before
    fun setup() {
        server = MockWebServer()
        server.start()
        val client = OkHttpClient.Builder()
            .connectTimeout(1, TimeUnit.SECONDS)
            .readTimeout(1, TimeUnit.SECONDS)
            .build()
        api = Retrofit.Builder()
            .baseUrl(server.url("/"))
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(SlyLedApi::class.java)
    }

    @After
    fun teardown() { server.shutdown() }

    // ── New models ────────────────────────────────────────────────

    @Test
    fun `deserialize CameraStatus with all fields`() {
        val input = """{"id":10,"name":"Stage Left Cam","cameraIp":"192.168.10.235","online":true,"fwVersion":"1.1.0","hostname":"orangepi","tracking":true,"trackClasses":["person","chair"]}"""
        val cam = json.decodeFromString<CameraStatus>(input)
        assertEquals(10, cam.id)
        assertEquals("Stage Left Cam", cam.name)
        assertEquals("192.168.10.235", cam.cameraIp)
        assertTrue(cam.online)
        assertTrue(cam.tracking)
        assertEquals(2, cam.trackClasses.size)
        assertEquals("person", cam.trackClasses[0])
        assertEquals("chair", cam.trackClasses[1])
    }

    @Test
    fun `deserialize CameraStatus with defaults`() {
        val input = """{"id":5}"""
        val cam = json.decodeFromString<CameraStatus>(input)
        assertEquals(5, cam.id)
        assertEquals("", cam.name)
        assertFalse(cam.online)
        assertFalse(cam.tracking)
        assertEquals(listOf("person"), cam.trackClasses)
    }

    @Test
    fun `deserialize ShowStatus running`() {
        val input = """{"running":true,"currentTimeline":3,"currentIndex":1,"totalTimelines":5,"elapsed":42.5,"duration":120.0,"loop":true}"""
        val st = json.decodeFromString<ShowStatus>(input)
        assertTrue(st.running)
        assertEquals(3, st.currentTimeline)
        assertEquals(1, st.currentIndex)
        assertEquals(5, st.totalTimelines)
        assertEquals(42.5, st.elapsed, 0.01)
        assertEquals(120.0, st.duration, 0.01)
        assertTrue(st.loop)
    }

    @Test
    fun `deserialize ShowStatus stopped defaults`() {
        val input = """{"running":false}"""
        val st = json.decodeFromString<ShowStatus>(input)
        assertFalse(st.running)
        assertNull(st.currentTimeline)
        assertEquals(0, st.currentIndex)
        assertEquals(0.0, st.elapsed, 0.01)
    }

    @Test
    fun `deserialize ShowPlaylist`() {
        val input = """{"order":[2,0,1,3],"loop":true}"""
        val pl = json.decodeFromString<ShowPlaylist>(input)
        assertEquals(listOf(2, 0, 1, 3), pl.order)
        assertTrue(pl.loop)
    }

    @Test
    fun `deserialize ShowPlaylist empty`() {
        val input = """{"order":[],"loop":false}"""
        val pl = json.decodeFromString<ShowPlaylist>(input)
        assertTrue(pl.order.isEmpty())
        assertFalse(pl.loop)
    }

    @Test
    fun `deserialize DmxStatus`() {
        val input = """{"running":true,"universes":4,"fps":40}"""
        val st = json.decodeFromString<DmxStatus>(input)
        assertTrue(st.running)
        assertEquals(4, st.universes)
        assertEquals(40, st.fps)
    }

    @Test
    fun `deserialize DmxStatus stopped`() {
        val input = """{"running":false,"universes":0}"""
        val st = json.decodeFromString<DmxStatus>(input)
        assertFalse(st.running)
        assertEquals(0, st.universes)
    }

    // ── Camera tracking API ──────────────────────────────────────

    @Test
    fun `GET cameras returns fixture list`() = runBlocking {
        server.enqueue(MockResponse().setBody("""[
            {"id":10,"name":"Cam1","type":"point","fixtureType":"camera","cameraIp":"192.168.10.235"},
            {"id":11,"name":"Cam2","type":"point","fixtureType":"camera","cameraIp":"192.168.10.109"}
        ]"""))
        val cams = api.getCameras()
        assertEquals(2, cams.size)
        assertEquals("Cam1", cams[0].name)
        assertEquals("camera", cams[0].fixtureType)
        assertEquals("/api/cameras", server.takeRequest().path)
    }

    @Test
    fun `GET camera status by id`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"id":10,"name":"Stage Left Cam","cameraIp":"192.168.10.235","online":true,"tracking":true,"trackClasses":["person","chair"]}"""))
        val cam = api.getCameraStatus(10)
        assertEquals(10, cam.id)
        assertTrue(cam.online)
        assertTrue(cam.tracking)
        assertEquals("/api/cameras/10/status", server.takeRequest().path)
    }

    @Test
    fun `POST start tracking`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"ok":true}"""))
        val resp = api.startTracking(10)
        assertTrue(resp.ok)
        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/api/cameras/10/track/start", req.path)
    }

    @Test
    fun `POST stop tracking`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"ok":true}"""))
        val resp = api.stopTracking(10)
        assertTrue(resp.ok)
        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/api/cameras/10/track/stop", req.path)
    }

    // ── Show playback API ────────────────────────────────────────

    @Test
    fun `GET show status`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"running":true,"currentTimeline":2,"currentIndex":0,"totalTimelines":3,"elapsed":15.2,"duration":60.0,"loop":false}"""))
        val st = api.getShowStatus()
        assertTrue(st.running)
        assertEquals(2, st.currentTimeline)
        assertEquals(15.2, st.elapsed, 0.01)
        assertEquals("/api/show/status", server.takeRequest().path)
    }

    @Test
    fun `GET show playlist`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"order":[1,0,2],"loop":true}"""))
        val pl = api.getShowPlaylist()
        assertEquals(3, pl.order.size)
        assertTrue(pl.loop)
        assertEquals("/api/show/playlist", server.takeRequest().path)
    }

    @Test
    fun `POST show start`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"ok":true}"""))
        val resp = api.startShow()
        assertTrue(resp.ok)
        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/api/show/start", req.path)
    }

    @Test
    fun `POST show stop`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"ok":true}"""))
        val resp = api.stopShow()
        assertTrue(resp.ok)
        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/api/show/stop", req.path)
    }

    // ── Live fixture data API ────────────────────────────────────

    @Test
    fun `GET fixtures live`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"10":{"r":255,"g":0,"b":128,"dimmer":200,"pan":90.5,"tilt":45.0},"11":{"r":0,"g":255,"b":0,"dimmer":255}}"""))
        val live = api.getFixturesLive()
        assertEquals(2, live.size)
        assertTrue(live.containsKey("10"))
        assertTrue(live.containsKey("11"))
        assertEquals("/api/fixtures/live", server.takeRequest().path)
    }

    // ── Objects (tracked + static) ───────────────────────────────

    @Test
    fun `GET objects includes temporal tracked objects`() = runBlocking {
        server.enqueue(MockResponse().setBody("""[
            {"id":1,"name":"Back Wall","objectType":"wall","mobility":"static","transform":{"pos":[5000,0,0],"rot":[0,0,0],"scale":[10000,2500,100]}},
            {"id":100,"name":"person","objectType":"person","mobility":"moving","_temporal":true,"_ttl":4,"color":"#f472b6","transform":{"pos":[3000,2000,0],"rot":[0,0,0],"scale":[400,1700,400]}}
        ]"""))
        val objs = api.getObjects()
        assertEquals(2, objs.size)
        // Static object
        assertEquals("Back Wall", objs[0].name)
        assertEquals("static", objs[0].mobility)
        // Temporal tracked object
        assertEquals("person", objs[1].name)
        assertEquals("moving", objs[1].mobility)
    }

    // ── DMX status ───────────────────────────────────────────────

    @Test
    fun `GET dmx status`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"running":true,"universes":2,"fps":40}"""))
        val st = api.getDmxStatus()
        assertTrue(st.running)
        assertEquals(2, st.universes)
        assertEquals("/api/dmx/status", server.takeRequest().path)
    }

    // ── Navigation structure ─────────────────────────────────────

    @Test
    fun `Tab enum has exactly 3 operator tabs`() {
        val tabs = com.slywombat.slyled.ui.navigation.Tab.entries
        assertEquals(3, tabs.size)
        assertEquals("stage", tabs[0].route)
        assertEquals("control", tabs[1].route)
        assertEquals("status", tabs[2].route)
    }

    @Test
    fun `Tab labels are user-friendly`() {
        val tabs = com.slywombat.slyled.ui.navigation.Tab.entries
        assertEquals("Stage", tabs[0].label)
        assertEquals("Control", tabs[1].label)
        assertEquals("Status", tabs[2].label)
    }

    @Test
    fun `No editing tabs remain`() {
        val routes = com.slywombat.slyled.ui.navigation.Tab.entries.map { it.route }
        assertFalse("layout should be removed", routes.contains("layout"))
        assertFalse("actions should be removed", routes.contains("actions"))
        assertFalse("setup should be removed", routes.contains("setup"))
        assertFalse("dashboard should be removed", routes.contains("dashboard"))
    }

    // ── Fixture model tracking fields ────────────────────────────

    @Test
    fun `Fixture with tracking config fields`() {
        val input = """{"id":10,"name":"Cam1","type":"point","fixtureType":"camera","trackClasses":["person","dog"],"trackFps":3,"trackThreshold":0.35,"trackTtl":8,"trackReidMm":600}"""
        val f = json.decodeFromString<Fixture>(input)
        assertEquals("camera", f.fixtureType)
        // Tracking config fields should deserialize without error
        assertEquals(10, f.id)
        assertEquals("Cam1", f.name)
    }

    // ── Settings with show state ─────────────────────────────────

    @Test
    fun `Settings with active show`() {
        val input = """{"name":"SlyLED","units":0,"canvasW":10000,"canvasH":5000,"darkMode":1,"runnerRunning":true,"activeRunner":0,"runnerLoop":true,"activeTimeline":2,"activeShow":1,"globalBrightness":200}"""
        val s = json.decodeFromString<Settings>(input)
        assertTrue(s.runnerRunning)
        assertEquals(2, s.activeTimeline)
        assertEquals(1, s.activeShow)
        assertEquals(200, s.globalBrightness)
    }

    @Test
    fun `Settings with no active show`() {
        val input = """{"name":"SlyLED","runnerRunning":false}"""
        val s = json.decodeFromString<Settings>(input)
        assertFalse(s.runnerRunning)
        assertNull(s.activeTimeline)
    }

    // ── Timeline list for Control screen ─────────────────────────

    @Test
    fun `GET timelines for picker`() = runBlocking {
        server.enqueue(MockResponse().setBody("""[
            {"id":0,"name":"Intro","durationS":30,"loop":false,"tracks":[]},
            {"id":1,"name":"Main Show","durationS":180,"loop":true,"tracks":[]}
        ]"""))
        val tls = api.getTimelines()
        assertEquals(2, tls.size)
        assertEquals("Intro", tls[0].name)
        assertEquals(180, tls[1].durationS)
        assertTrue(tls[1].loop)
    }

    // ── Error resilience ─────────────────────────────────────────

    @Test
    fun `camera tracking start on offline camera returns error`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(503).setBody("""{"ok":false,"err":"Camera offline"}"""))
        try {
            api.startTracking(99)
            fail("Expected exception for 503")
        } catch (e: Exception) {
            // Expected — Retrofit throws on non-2xx
        }
    }

    @Test
    fun `show start with no timelines returns error`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(400).setBody("""{"ok":false,"err":"No timelines"}"""))
        try {
            api.startShow()
            fail("Expected exception for 400")
        } catch (e: Exception) {
            // Expected
        }
    }

    @Test
    fun `fixtures live returns empty when no show`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{}"""))
        val live = api.getFixturesLive()
        assertTrue(live.isEmpty())
    }

    @Test
    fun `objects returns empty list`() = runBlocking {
        server.enqueue(MockResponse().setBody("""[]"""))
        val objs = api.getObjects()
        assertTrue(objs.isEmpty())
    }

    // ── Stage dimensions ─────────────────────────────────────────

    @Test
    fun `GET stage dimensions`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"w":10,"h":3,"d":8}"""))
        val stage = api.getStage()
        assertEquals(10.0, stage.w, 0.01)
        assertEquals(3.0, stage.h, 0.01)
        assertEquals(8.0, stage.d, 0.01)
        assertEquals("/api/stage", server.takeRequest().path)
    }

    // ── Pointer mode / fixture aim (#397) ────────────────────────

    @Test
    fun `POST aim fixture`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"ok":true}"""))
        val body = kotlinx.serialization.json.buildJsonObject {
            put("pan", kotlinx.serialization.json.JsonPrimitive(180.5f))
            put("tilt", kotlinx.serialization.json.JsonPrimitive(90.0f))
        }
        val resp = api.aimFixture(5, body)
        assertTrue(resp.ok)
        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertEquals("/api/calibration/mover/5/aim", req.path)
        val reqBody = req.body.readUtf8()
        assertTrue(reqBody.contains("180.5"))
        assertTrue(reqBody.contains("90"))
    }

    @Test
    fun `aim fixture on uncalibrated mover returns error`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(400).setBody("""{"ok":false,"err":"Not calibrated"}"""))
        try {
            val body = kotlinx.serialization.json.buildJsonObject {}
            api.aimFixture(99, body)
            fail("Expected exception for 400")
        } catch (e: Exception) {
            // Expected
        }
    }

    // ── Old screens removed (#399) ───────────────────────────────

    @Test
    fun `old screen files deleted`() {
        val oldScreens = listOf(
            "dashboard/DashboardScreen.kt",
            "setup/SetupScreen.kt",
            "layout/LayoutScreen.kt",
            "actions/ActionsScreen.kt",
            "runtime/RuntimeScreen.kt"
        )
        val basePath = java.io.File("src/main/java/com/slywombat/slyled/ui/screens")
        oldScreens.forEach { relPath ->
            val f = java.io.File(basePath, relPath)
            assertFalse("$relPath should be deleted", f.exists())
        }
    }

    @Test
    fun `old viewmodel files deleted`() {
        val oldVms = listOf(
            "DashboardViewModel.kt",
            "SetupViewModel.kt",
            "LayoutViewModel.kt",
            "ActionsViewModel.kt",
            "RuntimeViewModel.kt"
        )
        val basePath = java.io.File("src/main/java/com/slywombat/slyled/viewmodel")
        oldVms.forEach { name ->
            val f = java.io.File(basePath, name)
            assertFalse("$name should be deleted", f.exists())
        }
    }
}
