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

    @Test
    fun `deserialize RunnerSummary from list endpoint`() {
        val input = """{"id":0,"name":"Demo Runner","steps":8,"totalDurationS":55,"computed":false}"""
        val runner = json.decodeFromString<RunnerSummary>(input)
        assertEquals("Demo Runner", runner.name)
        assertEquals(8, runner.steps)
        assertEquals(55, runner.totalDurationS)
        assertFalse(runner.computed)
    }

    @Test
    fun `deserialize Runner with steps`() {
        val input = """{"id":0,"name":"Runner1","computed":true,"steps":[{"actionId":1,"durationS":5},{"actionId":2,"durationS":10}]}"""
        val runner = json.decodeFromString<Runner>(input)
        assertEquals(2, runner.steps.size)
        assertEquals(1, runner.steps[0].actionId)
        assertEquals(10, runner.steps[1].durationS)
    }

    @Test
    fun `deserialize Flight`() {
        val input = """{"id":0,"name":"Flight 1","performerIds":[0,1,2],"runnerId":0,"priority":1}"""
        val flight = json.decodeFromString<Flight>(input)
        assertEquals(3, flight.performerIds.size)
        assertEquals(0, flight.runnerId)
    }

    @Test
    fun `deserialize Flight with empty performerIds`() {
        val input = """{"id":0,"name":"Empty","performerIds":[],"runnerId":null,"priority":1}"""
        val flight = json.decodeFromString<Flight>(input)
        assertTrue(flight.performerIds.isEmpty())
        assertNull(flight.runnerId)
    }

    @Test
    fun `deserialize Show`() {
        val input = """{"id":0,"name":"Demo Show","flightIds":[0],"loop":true}"""
        val show = json.decodeFromString<Show>(input)
        assertEquals("Demo Show", show.name)
        assertEquals(1, show.flightIds.size)
        assertTrue(show.loop)
    }

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

    @Test
    fun `deserialize LiveEvent`() {
        val input = """{"ip":"192.168.10.219","actionType":5,"stepIndex":2,"totalSteps":8,"event":0,"age":1.5}"""
        val event = json.decodeFromString<LiveEvent>(input)
        assertEquals("192.168.10.219", event.ip)
        assertEquals(5, event.actionType)
        assertEquals(1.5, event.age!!, 0.001)
    }

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

    @Test
    fun `ActionTypes constants`() {
        assertEquals(14, ActionTypes.names.size)
        assertEquals("Blackout", ActionTypes.names[ActionTypes.BLACKOUT])
        assertEquals("Solid", ActionTypes.names[ActionTypes.SOLID])
        assertEquals("Gradient", ActionTypes.names[ActionTypes.GRADIENT])
        assertEquals(4, ActionTypes.directionNames.size)
        assertEquals(8, ActionTypes.paletteNames.size)
    }
}
