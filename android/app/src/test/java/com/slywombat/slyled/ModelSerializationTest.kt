package com.slywombat.slyled

import com.slywombat.slyled.data.model.*
import kotlinx.serialization.json.Json
import org.junit.Assert.*
import org.junit.Test

class ModelSerializationTest {
    private val json = Json { ignoreUnknownKeys = true; isLenient = true }

    @Test
    fun `deserialize Child with all fields`() {
        val input = """{"id":0,"ip":"192.168.10.219","hostname":"SLYC-1152","name":"My LED","desc":"Living room","sc":2,"strings":[{"leds":30,"mm":500,"type":0,"cdir":0,"cmm":0,"sdir":0,"folded":false}],"status":1,"seen":1711555200,"type":"slyled","fwVersion":"6.0.0","boardType":"ESP32"}"""
        val child = json.decodeFromString<Child>(input)
        assertEquals(0, child.id)
        assertEquals("SLYC-1152", child.hostname)
        assertEquals("My LED", child.name)
        assertEquals(2, child.sc)
        assertEquals(1, child.status)
        assertEquals(OnlineStatus.ONLINE, child.onlineStatus)
        assertEquals("6.0.0", child.fwVersion)
        assertEquals(1, child.strings.size)
        assertEquals(30, child.strings[0].leds)
    }

    @Test
    fun `deserialize Child without id (discovery endpoint)`() {
        val input = """{"ip":"192.168.10.50","hostname":"SLYC-ABCD","name":"","sc":1,"strings":[],"status":1}"""
        val child = json.decodeFromString<Child>(input)
        assertEquals(-1, child.id)  // default
        assertEquals("SLYC-ABCD", child.hostname)
    }

    @Test
    fun `deserialize Child with unknown fields`() {
        val input = """{"id":1,"ip":"10.0.0.1","hostname":"TEST","futureField":"ignored","status":0}"""
        val child = json.decodeFromString<Child>(input)
        assertEquals(1, child.id)
        assertEquals(OnlineStatus.OFFLINE, child.onlineStatus)
    }

    @Test
    fun `deserialize ChildStringConfig with SerialName mappings`() {
        val input = """{"leds":60,"mm":1000,"type":0,"cdir":1,"cmm":200,"sdir":2,"folded":true}"""
        val cfg = json.decodeFromString<ChildStringConfig>(input)
        assertEquals(60, cfg.leds)
        assertEquals(1000, cfg.lengthMm)
        assertEquals(1, cfg.cableDirection)
        assertEquals(200, cfg.cableLengthMm)
        assertEquals(2, cfg.stripDirection)
        assertTrue(cfg.folded)
    }

    @Test
    fun `deserialize Action with all fields`() {
        val input = """{"id":5,"name":"Red Solid","type":1,"scope":"performer","r":255,"g":0,"b":0}"""
        val action = json.decodeFromString<Action>(input)
        assertEquals(5, action.id)
        assertEquals("Red Solid", action.name)
        assertEquals(1, action.type)
        assertEquals(255, action.r)
    }

    @Test
    fun `deserialize Action with optional fields missing`() {
        val input = """{"id":0,"name":"Test","type":5}"""
        val action = json.decodeFromString<Action>(input)
        assertEquals(5, action.type)
        assertNull(action.speedMs)
        assertNull(action.paletteId)
        assertNull(action.wledFxOverride)
    }

    // Runner, RunnerSummary, Flight models removed — replaced by timeline system

    // Show model removed — shows replaced by timeline playlist system

    @Test
    fun `deserialize Layout`() {
        val input = """{"canvasW":10000,"canvasH":5000,"children":[{"id":0,"x":500,"y":300}]}"""
        val layout = json.decodeFromString<Layout>(input)
        assertEquals(10000, layout.canvasW)
        assertEquals(1, layout.children.size)
        assertEquals(500, layout.children[0].x)
    }

    @Test
    fun `deserialize Settings with all fields`() {
        val input = """{"name":"SlyLED","units":0,"canvasW":10000,"canvasH":5000,"darkMode":1,"runnerRunning":false,"activeRunner":-1,"activeShow":-1,"runnerElapsed":0,"runnerLoop":true,"globalBrightness":200,"logging":false}"""
        val settings = json.decodeFromString<Settings>(input)
        assertEquals("SlyLED", settings.name)
        assertEquals(200, settings.globalBrightness)
        assertTrue(settings.runnerLoop)
    }

    @Test
    fun `deserialize Settings with missing optional fields`() {
        val input = """{"name":"Test","units":1}"""
        val settings = json.decodeFromString<Settings>(input)
        assertEquals("Test", settings.name)
        assertEquals(1, settings.units)
        assertNull(settings.globalBrightness)
    }

    @Test
    fun `deserialize StatusResponse`() {
        val input = """{"role":"parent","hostname":"WIN-PC","version":"6.0.0"}"""
        val status = json.decodeFromString<StatusResponse>(input)
        assertEquals("parent", status.role)
        assertEquals("6.0.0", status.version)
    }

    @Test
    fun `deserialize OkResponse variants`() {
        val simple = json.decodeFromString<OkResponse>("""{"ok":true}""")
        assertTrue(simple.ok)

        val withId = json.decodeFromString<OkResponse>("""{"ok":true,"id":5}""")
        assertEquals(5, withId.id)

        val withError = json.decodeFromString<OkResponse>("""{"ok":false,"err":"not found"}""")
        assertFalse(withError.ok)
        assertEquals("not found", withError.err)

        val withCounts = json.decodeFromString<OkResponse>("""{"ok":true,"added":3,"updated":1,"skipped":0}""")
        assertEquals(3, withCounts.added)

        val withWarning = json.decodeFromString<OkResponse>("""{"ok":true,"actions":8,"runners":1,"warning":"orphans"}""")
        assertEquals(8, withWarning.actions)
        assertEquals("orphans", withWarning.warning)
    }

    // LiveEvent model removed — replaced by fixtures/live polling

    @Test
    fun `deserialize ChildStatus`() {
        val input = """{"ok":true,"activeAction":5,"runnerActive":true,"currentStep":2,"wifiRssi":-65,"uptimeS":3600}"""
        val status = json.decodeFromString<ChildStatus>(input)
        assertTrue(status.ok)
        assertEquals(5, status.activeAction)
        assertTrue(status.runnerActive!!)
    }

    @Test
    fun `deserialize child list (array)`() {
        val input = """[{"id":0,"ip":"10.0.0.1","hostname":"A","status":1},{"id":1,"ip":"10.0.0.2","hostname":"B","status":0}]"""
        val children = json.decodeFromString<List<Child>>(input)
        assertEquals(2, children.size)
        assertEquals(OnlineStatus.ONLINE, children[0].onlineStatus)
        assertEquals(OnlineStatus.OFFLINE, children[1].onlineStatus)
    }

    @Test
    fun `serialize Settings for save`() {
        val settings = Settings(name = "Test", units = 1, canvasW = 8000, canvasH = 4000, darkMode = 0, logging = true)
        val output = json.encodeToString(Settings.serializer(), settings)
        assertTrue(output.contains("\"name\":\"Test\""))
        assertTrue(output.contains("\"logging\":true"))
    }

    @Test
    fun `serialize AddChildRequest`() {
        val req = AddChildRequest(ip = "192.168.1.50")
        val output = json.encodeToString(AddChildRequest.serializer(), req)
        assertTrue(output.contains("\"ip\":\"192.168.1.50\""))
    }

    @Test
    fun `serialize Action for create`() {
        val action = Action(name = "Blue Solid", type = 1, r = 0, g = 0, b = 255, scope = "performer")
        val output = json.encodeToString(Action.serializer(), action)
        assertTrue(output.contains("\"name\":\"Blue Solid\""))
        assertTrue(output.contains("\"b\":255"))
    }

    // ── #912 StageObject source provenance ────────────────────────────

    @Test
    fun `deserialize StageObject without source (old server)`() {
        val input = """{"id":2,"name":"Wall","objectType":"wall","mobility":"static","color":"#334155","opacity":30,"transform":{"pos":[100.0,200.0,0.0],"rot":[0,0,0],"scale":[2000.0,1500.0,100.0]}}"""
        val obj = json.decodeFromString<StageObject>(input)
        assertEquals(2, obj.id)
        assertNull(obj.source)
        assertFalse(obj.temporal)
    }

    @Test
    fun `deserialize radar-sourced person from captured server JSON`() {
        // Shape mirrors parent_server._radar_person_create (#910/#912):
        // float ttl, _temporal/_expiresAt underscore keys, source stamp.
        val input = """{"id":7,"name":"Person (radar) 7","objectType":"person","mobility":"moving","_temporal":true,"ttl":2.0,"_expiresAt":1767000000.5,"color":"#f472b6","opacity":40,"transform":{"pos":[1200.0,2400.0,850.0],"rot":[0,0,0],"scale":[500.0,1700.0,500.0]},"source":{"type":"radar","fixtureId":3,"node":"MMW-A1B2"}}"""
        val obj = json.decodeFromString<StageObject>(input)
        assertEquals("person", obj.objectType)
        assertTrue(obj.temporal)
        assertEquals(2.0, obj.ttl, 0.0)
        assertEquals("radar", obj.source?.type)
        assertEquals(3, obj.source?.fixtureId)
        assertEquals("MMW-A1B2", obj.source?.node)
        assertNull(obj.source?.cameraId)
    }

    @Test
    fun `deserialize camera-sourced person carries cameraId not fixtureId`() {
        // Camera provenance stamp (#900): {"type":"camera","cameraId":N}.
        val input = """{"id":9,"name":"Person 9","objectType":"person","mobility":"moving","_temporal":true,"ttl":5,"color":"#f472b6","opacity":40,"transform":{"pos":[0,0,850.0],"rot":[0,0,0],"scale":[400.0,1700.0,400.0]},"source":{"type":"camera","cameraId":4}}"""
        val obj = json.decodeFromString<StageObject>(input)
        assertEquals("camera", obj.source?.type)
        assertEquals(4, obj.source?.cameraId)
        assertNull(obj.source?.fixtureId)
        assertEquals(5.0, obj.ttl, 0.0)  // integer ttl still parses
    }

    @Test
    fun `StageObject round-trips with and without source`() {
        val withSource = StageObject(
            id = 1, name = "P", objectType = "person", mobility = "moving",
            temporal = true, ttl = 2.0,
            source = ObjectSource(type = "radar", fixtureId = 3, node = "MMW-A1B2"),
        )
        val decodedWith = json.decodeFromString<StageObject>(
            json.encodeToString(StageObject.serializer(), withSource))
        assertEquals(withSource, decodedWith)

        val withoutSource = StageObject(id = 2, name = "Wall")
        val encoded = json.encodeToString(StageObject.serializer(), withoutSource)
        val decodedWithout = json.decodeFromString<StageObject>(encoded)
        assertEquals(withoutSource, decodedWithout)
        assertNull(decodedWithout.source)
    }

    @Test
    fun `Child isRadarNode from MMW hostname prefix`() {
        // Radar nodes have no HTTP /status, so type stays "slyled" and
        // boardType stays blank — the hostname prefix is the signal.
        val radar = json.decodeFromString<Child>(
            """{"id":4,"ip":"10.0.0.9","hostname":"MMW-A1B2","sc":0,"strings":[],"status":1,"type":"slyled"}""")
        assertTrue(radar.isRadarNode)
        val led = json.decodeFromString<Child>(
            """{"id":5,"ip":"10.0.0.10","hostname":"SLYC-1152","sc":1,"strings":[],"status":1,"type":"slyled"}""")
        assertFalse(led.isRadarNode)
        // Future server stamping the registry board id also matches.
        assertTrue(led.copy(boardType = "mmwave").isRadarNode)
        assertTrue(led.copy(type = "mmwave").isRadarNode)
    }

    @Test
    fun `deserialize radar Fixture descriptor`() {
        val input = """{"id":11,"name":"Stage radar","type":"point","fixtureType":"radar","radarNode":"MMW-A1B2","rangeMm":8000,"fovDeg":120.0,"radarEnabled":true,"x":500,"y":0,"z":1200}"""
        val f = json.decodeFromString<Fixture>(input)
        assertEquals("radar", f.fixtureType)
        assertEquals("MMW-A1B2", f.radarNode)
        assertEquals(8000, f.rangeMm)
        assertEquals(120.0, f.fovDeg!!, 0.0)
        assertEquals(true, f.radarEnabled)
    }

    @Test
    fun `ActionTypes constants`() {
        // #906 — 19 entries, one per wire type 0-18, matching
        // parent_server.py _ACTION_NAMES (the stale 14 predated the DMX +
        // Track types; cross-platform gate: tests/test_parity_action_names.py).
        assertEquals(19, ActionTypes.names.size)
        assertEquals("Blackout", ActionTypes.names[ActionTypes.BLACKOUT])
        assertEquals("Solid", ActionTypes.names[ActionTypes.SOLID])
        assertEquals("Gradient", ActionTypes.names[ActionTypes.GRADIENT])
        assertEquals("Color Wipe", ActionTypes.names[ActionTypes.WIPE])
        assertEquals("Color Wheel", ActionTypes.names[ActionTypes.DMX_COLOR_WHEEL])
        assertEquals("Track", ActionTypes.names[ActionTypes.TRACK])
        assertEquals(4, ActionTypes.directionNames.size)
        assertEquals(8, ActionTypes.paletteNames.size)
    }
}
