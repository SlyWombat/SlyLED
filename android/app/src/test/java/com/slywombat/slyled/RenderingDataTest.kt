package com.slywombat.slyled

import com.slywombat.slyled.data.model.*
import kotlinx.serialization.json.Json
import org.junit.Assert.*
import org.junit.Test

/**
 * Validates that all data models used by rendering code (LayoutScreen,
 * RuntimeScreen ShowEmulatorCanvas) deserialize correctly from server JSON.
 *
 * These tests catch the class of bugs where the server returns floats but
 * the Kotlin model expects ints, or field names don't match.
 */
class RenderingDataTest {
    private val json = Json { ignoreUnknownKeys = true; isLenient = true }

    // ── Fixture with aimPoint (float values from server) ────────────────

    @Test
    fun `deserialize Fixture with float aimPoint`() {
        val input = """{"id":5,"name":"Moving Head","fixtureType":"dmx","type":"point",
            "dmxUniverse":1,"dmxStartAddr":1,"dmxChannelCount":16,
            "aimPoint":[5000.0,2000.0,5000.0],"x":2000,"y":5000,"z":0,"positioned":true}"""
        val f = json.decodeFromString<Fixture>(input)
        assertEquals(5, f.id)
        assertEquals("dmx", f.fixtureType)
        assertNotNull(f.aimPoint)
        assertEquals(5000.0, f.aimPoint!![0], 0.01)
        assertEquals(2000.0, f.aimPoint!![1], 0.01)  // Y = height, used for 2D canvas Y
        assertEquals(5000.0, f.aimPoint!![2], 0.01)
        assertTrue(f.positioned)
    }

    @Test
    fun `deserialize Fixture with int aimPoint`() {
        val input = """{"id":3,"name":"Par","fixtureType":"dmx","type":"point",
            "aimPoint":[5000,0,5000],"x":100,"y":200,"positioned":true}"""
        val f = json.decodeFromString<Fixture>(input)
        assertEquals(5000.0, f.aimPoint!![0], 0.01)
        assertEquals(0.0, f.aimPoint!![1], 0.01)
    }

    @Test
    fun `deserialize Fixture without aimPoint (LED)`() {
        val input = """{"id":0,"name":"LED Strip","fixtureType":"led","type":"linear",
            "childId":1,"strings":[{"leds":60,"mm":3000,"sdir":0}],"positioned":true,"x":1000,"y":4500}"""
        val f = json.decodeFromString<Fixture>(input)
        assertNull(f.aimPoint)
        assertEquals("led", f.fixtureType)
        assertEquals(1, f.strings.size)
        assertEquals(60, f.strings[0].leds)
    }

    @Test
    fun `deserialize Fixture DMX with default aimPoint`() {
        val input = """{"id":4,"name":"Dimmer","fixtureType":"dmx","type":"point",
            "dmxUniverse":2,"dmxStartAddr":1,"dmxChannelCount":1,
            "aimPoint":[0,-1000,0]}"""
        val f = json.decodeFromString<Fixture>(input)
        assertEquals(-1000.0, f.aimPoint!![1], 0.01)
    }

    // ── FixtureString with server-merged child fields ───────────────────

    @Test
    fun `deserialize FixtureString with all child fields`() {
        val input = """{"leds":60,"mm":3000,"sdir":0,"type":0,"cdir":1,"cmm":200,"folded":false}"""
        val s = json.decodeFromString<FixtureString>(input)
        assertEquals(60, s.leds)
        assertEquals(3000, s.mm)
        assertEquals(0, s.sdir)
        assertEquals(0, s.type)
        assertEquals(1, s.cdir)
        assertEquals(200, s.cmm)
        assertFalse(s.folded)
    }

    @Test
    fun `deserialize FixtureString minimal (from fixture creation)`() {
        val input = """{"leds":30,"mm":1500,"sdir":2}"""
        val s = json.decodeFromString<FixtureString>(input)
        assertEquals(30, s.leds)
        assertEquals(1500, s.mm)
        assertEquals(2, s.sdir)
    }

    @Test
    fun `FixtureString sdir values map to directions`() {
        // 0=East (right), 1=North (up), 2=West (left), 3=South (down)
        for (dir in 0..3) {
            val input = """{"leds":10,"mm":500,"sdir":$dir}"""
            val s = json.decodeFromString<FixtureString>(input)
            assertEquals(dir, s.sdir)
        }
    }

    // ── SurfaceTransform with float values ──────────────────────────────

    @Test
    fun `deserialize SurfaceTransform with float pos and scale`() {
        val input = """{"pos":[5000.0,2500.0,0.0],"rot":[0.0,0.0,0.0],"scale":[10000.0,5000.0,100.0]}"""
        val t = json.decodeFromString<SurfaceTransform>(input)
        assertEquals(5000.0, t.pos[0], 0.01)
        assertEquals(2500.0, t.pos[1], 0.01)
        assertEquals(10000.0, t.scale[0], 0.01)
        assertEquals(5000.0, t.scale[1], 0.01)
    }

    @Test
    fun `deserialize SurfaceTransform with int values`() {
        val input = """{"pos":[0,0,0],"rot":[0,0,0],"scale":[2000,1500,100]}"""
        val t = json.decodeFromString<SurfaceTransform>(input)
        assertEquals(0.0, t.pos[0], 0.01)
        assertEquals(2000.0, t.scale[0], 0.01)
    }

    @Test
    fun `deserialize Surface with full transform`() {
        val input = """{"id":0,"name":"Back Wall","surfaceType":"wall","color":"#1e293b",
            "opacity":30,"transform":{"pos":[0,0,0],"rot":[0,0,0],"scale":[10000,5000,100]}}"""
        val s = json.decodeFromString<Surface>(input)
        assertEquals("Back Wall", s.name)
        assertEquals("#1e293b", s.color)
        assertEquals(30, s.opacity)
        assertEquals(10000.0, s.transform.scale[0], 0.01)
    }

    // ── Layout with fixtures (merged strings + positions) ───────────────

    @Test
    fun `deserialize Layout with positioned fixtures`() {
        val input = """{"canvasW":10000,"canvasH":5000,"children":[{"id":0,"x":1000,"y":4500,"z":0}],
            "fixtures":[{"id":0,"name":"LED Strip","fixtureType":"led","type":"linear",
            "strings":[{"leds":60,"mm":3000,"sdir":0}],"x":1000,"y":4500,"z":0,"positioned":true}]}"""
        val lay = json.decodeFromString<Layout>(input)
        assertEquals(10000, lay.canvasW)
        assertEquals(5000, lay.canvasH)
        assertEquals(1, lay.fixtures.size)
        assertTrue(lay.fixtures[0].positioned)
        assertEquals(1000, lay.fixtures[0].x)
        assertEquals(60, lay.fixtures[0].strings[0].leds)
    }

    @Test
    fun `deserialize Layout with DMX and LED fixtures`() {
        val input = """{"canvasW":8000,"canvasH":3000,"children":[],"fixtures":[
            {"id":0,"name":"LED","fixtureType":"led","strings":[{"leds":30,"mm":1500,"sdir":1}],
             "x":1000,"y":2000,"positioned":true},
            {"id":1,"name":"MH","fixtureType":"dmx","dmxUniverse":1,"dmxStartAddr":1,
             "aimPoint":[4000.0,1500.0,0.0],"x":4000,"y":3000,"positioned":true}
        ]}"""
        val lay = json.decodeFromString<Layout>(input)
        assertEquals(2, lay.fixtures.size)
        val led = lay.fixtures[0]
        val dmx = lay.fixtures[1]
        assertEquals("led", led.fixtureType)
        assertEquals("dmx", dmx.fixtureType)
        assertNotNull(dmx.aimPoint)
        assertEquals(4000.0, dmx.aimPoint!![0], 0.01)
        assertEquals(1500.0, dmx.aimPoint!![1], 0.01)  // Y for 2D canvas
    }

    // ── Stage dimensions ────────────────────────────────────────────────

    @Test
    fun `Stage dimensions match canvas convention`() {
        // canvasW = stage.w * 1000, canvasH = stage.h * 1000
        val stage = json.decodeFromString<Stage>("""{"w":10.0,"h":5.0,"d":10.0}""")
        assertEquals(10000, (stage.w * 1000).toInt())  // canvasW
        assertEquals(5000, (stage.h * 1000).toInt())   // canvasH — NOT stage.d
    }

    @Test
    fun `Small stage dimensions`() {
        val stage = json.decodeFromString<Stage>("""{"w":2.0,"h":1.0,"d":3.0}""")
        assertEquals(2000, (stage.w * 1000).toInt())
        assertEquals(1000, (stage.h * 1000).toInt())
    }

    // ── Beam cone direction convention ──────────────────────────────────

    @Test
    fun `aimPoint index 0 is X and index 1 is Y for 2D canvas`() {
        // Convention: 2D layout is front view
        // aimPoint[0] = X (horizontal position on canvas)
        // aimPoint[1] = Y (vertical position on canvas, inverted)
        // aimPoint[2] = Z (depth, only for 3D view)
        val input = """{"id":0,"fixtureType":"dmx","type":"point","aimPoint":[8000,1000,5000]}"""
        val f = json.decodeFromString<Fixture>(input)
        val canvasW = 10000; val canvasH = 5000
        val xPct = f.aimPoint!![0] / canvasW  // 0.8 = 80% across
        val yPct = f.aimPoint!![1] / canvasH  // 0.2 = 20% up from bottom
        assertEquals(0.8, xPct, 0.01)
        assertEquals(0.2, yPct, 0.01)
        // aimPoint[2] is Z=depth, NOT used for 2D canvas Y
        val zPct = f.aimPoint!![2] / canvasH  // 1.0 — would be WRONG for canvas Y
        assertEquals(1.0, zPct, 0.01)  // This proves [2] != canvas Y
    }

    // ── Preview data format ─────────────────────────────────────────────

    @Test
    fun `deserialize BakeStatus`() {
        val input = """{"running":false,"done":true,"status":"complete","progress":100.0}"""
        val bs = json.decodeFromString<BakeStatus>(input)
        assertTrue(bs.done)
        assertEquals(100.0, bs.progress, 0.01)
    }

    @Test
    fun `deserialize SyncStatus`() {
        val input = """{"done":true,"allReady":true,"readyCount":2,"totalPerformers":2}"""
        val ss = json.decodeFromString<SyncStatus>(input)
        assertTrue(ss.done)
        assertTrue(ss.allReady)
    }

    @Test
    fun `deserialize TimelineStatus`() {
        val input = """{"id":0,"running":true,"elapsed":15,"durationS":30,"loop":false}"""
        val ts = json.decodeFromString<TimelineStatus>(input)
        assertTrue(ts.running)
        assertEquals(15, ts.elapsed)
        assertEquals(30, ts.durationS)
    }

    @Test
    fun `deserialize DmxProfile with beamWidth`() {
        val input = """{"id":"generic-moving-head-16bit","name":"Moving Head 16-bit",
            "manufacturer":"Generic","category":"moving-head","channelCount":16,
            "beamWidth":15,"panRange":540,"tiltRange":270,"builtin":true}"""
        val p = json.decodeFromString<DmxProfile>(input)
        assertEquals(15, p.beamWidth)
        assertEquals(540, p.panRange)
    }

    // ── Settings for emulator polling ───────────────────────────────────

    @Test
    fun `deserialize Settings with timeline fields`() {
        val input = """{"name":"SlyLED","runnerRunning":true,"activeTimeline":3,"runnerStartEpoch":1700000000}"""
        val s = json.decodeFromString<Settings>(input)
        assertTrue(s.runnerRunning)
        assertEquals(3, s.activeTimeline)
        assertEquals(1700000000L, s.runnerStartEpoch)
    }

    @Test
    fun `deserialize Settings with null timeline fields`() {
        val input = """{"name":"SlyLED","runnerRunning":false}"""
        val s = json.decodeFromString<Settings>(input)
        assertFalse(s.runnerRunning)
        assertNull(s.activeTimeline)
        assertNull(s.runnerStartEpoch)
    }

    // ── DMX Bridge detection ────────────────────────────────────────

    @Test
    fun `deserialize Child with DMX bridge type and boardType`() {
        val input = """{"id":3,"ip":"192.168.10.219","hostname":"SLYC-1152",
            "name":"SLYC-1152","type":"dmx","boardType":"giga-dmx",
            "sc":1,"strings":[{"leds":30,"mm":500,"sdir":0}],"status":1}"""
        val child = json.decodeFromString<Child>(input)
        assertEquals("dmx", child.type)
        assertEquals("giga-dmx", child.boardType)
        // This child should be identified as a DMX bridge, not an LED performer
        val isDmxBridge = child.type == "dmx" || child.boardType == "giga-dmx"
        assertTrue(isDmxBridge)
    }

    @Test
    fun `DMX bridge should NOT be treated as LED performer`() {
        val bridge = json.decodeFromString<Child>(
            """{"id":1,"type":"dmx","boardType":"giga-dmx","sc":1,
            "strings":[{"leds":30,"mm":500,"sdir":0}]}""")
        val ledPerformer = json.decodeFromString<Child>(
            """{"id":2,"type":"slyled","boardType":"ESP32","sc":2,
            "strings":[{"leds":150,"mm":1000,"sdir":0},{"leds":150,"mm":1000,"sdir":2}]}""")

        // Bridge has type=dmx even though it has strings with LEDs
        assertEquals("dmx", bridge.type)
        assertEquals("slyled", ledPerformer.type)

        // Filter logic should separate them
        val allChildren = listOf(bridge, ledPerformer)
        val bridges = allChildren.filter { it.type == "dmx" || it.boardType == "giga-dmx" }
        val performers = allChildren.filter { it !in bridges }
        assertEquals(1, bridges.size)
        assertEquals(1, performers.size)
        assertEquals("dmx", bridges[0].type)
        assertEquals("slyled", performers[0].type)
    }
}
