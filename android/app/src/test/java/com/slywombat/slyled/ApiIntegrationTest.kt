package com.slywombat.slyled

import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import com.slywombat.slyled.data.api.SlyLedApi
import com.slywombat.slyled.data.model.*
import com.slywombat.slyled.data.repository.SlyLedRepository
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

class ApiIntegrationTest {
    private lateinit var server: MockWebServer
    private lateinit var api: SlyLedApi
    private lateinit var repository: SlyLedRepository
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

        repository = SlyLedRepository(client)
    }

    @After
    fun teardown() {
        server.shutdown()
    }

    // ── Status ────────────────────────────────────────────────────

    @Test
    fun `GET status returns parent info`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"role":"parent","hostname":"WIN-PC","version":"6.0.0"}"""))
        val status = api.getStatus()
        assertEquals("parent", status.role)
        assertEquals("6.0.0", status.version)
        assertEquals("/status", server.takeRequest().path)
    }

    // ── Children ──────────────────────────────────────────────────

    @Test
    fun `GET children returns list`() = runBlocking {
        server.enqueue(MockResponse().setBody("""[
            {"id":0,"ip":"192.168.10.219","hostname":"SLYC-1152","name":"Room1","sc":2,"strings":[],"status":1,"type":"slyled","fwVersion":"6.0.0"},
            {"id":1,"ip":"192.168.10.223","hostname":"SLYC-6992","name":"Room2","sc":1,"strings":[],"status":0,"type":"slyled"}
        ]"""))
        val children = api.getChildren()
        assertEquals(2, children.size)
        assertEquals("Room1", children[0].name)
        assertEquals(OnlineStatus.OFFLINE, children[1].onlineStatus)
    }

    @Test
    fun `GET discover returns children without id`() = runBlocking {
        server.enqueue(MockResponse().setBody("""[
            {"ip":"192.168.10.50","hostname":"SLYC-NEW","name":"","sc":1,"strings":[],"status":1}
        ]"""))
        val discovered = api.discoverChildren()
        assertEquals(1, discovered.size)
        assertEquals(-1, discovered[0].id)
        assertEquals("SLYC-NEW", discovered[0].hostname)
    }

    @Test
    fun `POST add child`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"ok":true,"id":5,"type":"slyled"}"""))
        val resp = api.addChild(AddChildRequest("192.168.1.50"))
        assertTrue(resp.ok)
        assertEquals(5, resp.id)
        val req = server.takeRequest()
        assertEquals("POST", req.method)
        assertTrue(req.body.readUtf8().contains("192.168.1.50"))
    }

    @Test
    fun `DELETE child`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"ok":true}"""))
        val resp = api.deleteChild(3)
        assertTrue(resp.ok)
        val req = server.takeRequest()
        assertEquals("DELETE", req.method)
        assertEquals("/api/children/3", req.path)
    }

    // ── Settings ──────────────────────────────────────────────────

    @Test
    fun `GET settings`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"name":"SlyLED","units":0,"canvasW":10000,"canvasH":5000,"darkMode":1,"runnerRunning":false,"activeRunner":-1,"runnerLoop":true,"logging":false}"""))
        val settings = api.getSettings()
        assertEquals("SlyLED", settings.name)
        assertTrue(settings.runnerLoop)
    }

    @Test
    fun `POST save settings with typed object`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"ok":true}"""))
        val settings = Settings(name = "Test", units = 1, canvasW = 8000, canvasH = 4000, darkMode = 0)
        val resp = api.saveSettings(settings)
        assertTrue(resp.ok)
        val body = server.takeRequest().body.readUtf8()
        assertTrue(body.contains("\"name\":\"Test\""))
        assertTrue(body.contains("\"units\":1"))
    }

    // ── Actions ───────────────────────────────────────────────────

    @Test
    fun `GET actions`() = runBlocking {
        server.enqueue(MockResponse().setBody("""[{"id":0,"name":"Red","type":1,"r":255,"g":0,"b":0},{"id":1,"name":"Rainbow","type":5}]"""))
        val actions = api.getActions()
        assertEquals(2, actions.size)
        assertEquals(255, actions[0].r)
    }

    @Test
    fun `POST create action`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"ok":true,"id":10}"""))
        val action = Action(name = "Test", type = 3, r = 100, g = 200, b = 50)
        val resp = api.createAction(action)
        assertTrue(resp.ok)
        assertEquals(10, resp.id)
    }

    @Test
    fun `DELETE action`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"ok":true}"""))
        val resp = api.deleteAction(5)
        assertTrue(resp.ok)
        assertEquals("/api/actions/5", server.takeRequest().path)
    }

    // Runners, Flights, and Shows removed — replaced by timeline system

    // ── Config/Show export-import ─────────────────────────────────

    @Test
    fun `POST generate demo show`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"ok":true,"actions":8,"runners":1,"flights":1,"shows":1}"""))
        val resp = api.generateDemo()
        assertTrue(resp.ok)
        assertEquals(8, resp.actions)
    }

    @Test
    fun `POST factory reset`() = runBlocking {
        server.enqueue(MockResponse().setBody("""{"ok":true}"""))
        val resp = api.factoryReset()
        assertTrue(resp.ok)
        assertEquals("/api/reset", server.takeRequest().path)
    }

    // ── Repository connect/disconnect ─────────────────────────────

    @Test
    fun `repository connect and disconnect`() = runBlocking {
        val host = server.hostName
        val port = server.port
        server.enqueue(MockResponse().setBody("""{"role":"parent","hostname":"TEST","version":"6.0.0"}"""))

        assertFalse(repository.isConnected)
        repository.connect(host, port)
        assertTrue(repository.isConnected)
        assertEquals("http://$host:$port/", repository.baseUrl)

        repository.disconnect()
        assertFalse(repository.isConnected)
        assertNull(repository.baseUrl)
    }

    // ── Error handling ────────────────────────────────────────────

    @Test
    fun `404 response throws`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(404).setBody("Not Found"))
        try {
            api.getShowStatus()
            fail("Expected exception")
        } catch (e: Exception) {
            // Expected — Retrofit throws on non-2xx
        }
    }

    @Test
    fun `500 response throws`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(500).setBody("""{"ok":false,"err":"internal error"}"""))
        try {
            api.getChildren()
            fail("Expected exception")
        } catch (e: Exception) {
            // Expected
        }
    }
}
