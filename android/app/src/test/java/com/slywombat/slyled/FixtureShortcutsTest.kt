package com.slywombat.slyled

// #906 — executable parity gate for the Kotlin fixture-shortcut resolver.
//
// Consumes the shared corpus at tests/fixtures/shortcut_corpus/ (repo
// root) — the same JSON profiles + expected.json that gate the JS
// reference implementation (tests/test_fixture_shortcuts.py via Node)
// and the Swift twin (ios/SlyLEDTests/FixtureShortcutsTests.swift).
// expected.json is generated FROM the JS resolver; if this test fails,
// fix FixtureShortcuts.kt to match the JS, never the corpus.
//
// The corpus is located by walking up from the gradle working directory
// (android/app) to the repo root, so `gradlew test` needs no resource-
// copy step and the corpus stays single-sourced.

import com.slywombat.slyled.ui.screens.control.shortcuts.ShortcutUi
import com.slywombat.slyled.ui.screens.control.shortcuts.resolveShortcutsForProfile
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.double
import kotlinx.serialization.json.longOrNull
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File

class FixtureShortcutsTest {

    private fun findCorpusDir(): File {
        var dir: File? = File(System.getProperty("user.dir")).absoluteFile
        var hops = 0
        while (dir != null && hops < 8) {
            val candidate = File(dir, "tests/fixtures/shortcut_corpus")
            if (candidate.isDirectory) return candidate
            dir = dir.parentFile
            hops++
        }
        throw AssertionError(
            "tests/fixtures/shortcut_corpus not found above " +
                System.getProperty("user.dir")
        )
    }

    /** JsonElement → plain Kotlin (Map/List/Int/Double/String/Boolean/null),
     *  the shape resolveShortcutsForProfile consumes. */
    private fun JsonElement.toPlain(): Any? = when (this) {
        is JsonNull -> null
        is JsonPrimitive -> when {
            isString -> content
            booleanOrNull != null -> booleanOrNull
            longOrNull != null -> {
                val l = longOrNull!!
                if (l in Int.MIN_VALUE..Int.MAX_VALUE) l.toInt() else l
            }
            else -> double
        }
        is JsonArray -> map { it.toPlain() }
        is JsonObject -> entries.associate { (k, v) -> k to v.toPlain() }
    }

    private fun uiTag(ui: ShortcutUi): String = when (ui) {
        ShortcutUi.TOGGLE -> "toggle"
        ShortcutUi.SEGMENTED -> "segmented"
        ShortcutUi.COLOR_SWATCH -> "color-swatch"
        ShortcutUi.MOMENTARY -> "momentary"
        ShortcutUi.LONG_PRESS -> "long-press"
    }

    @Test
    fun `kotlin resolver matches shared corpus expected output`() {
        val corpusDir = findCorpusDir()
        val expectedRoot = Json.parseToJsonElement(
            File(corpusDir, "expected.json").readText()
        ) as JsonObject

        var checked = 0
        for ((slug, wantEl) in expectedRoot) {
            if (slug == "_doc") continue
            val profileFile = File(corpusDir, "$slug.json")
            assertTrue("profile JSON missing: $profileFile", profileFile.isFile)

            @Suppress("UNCHECKED_CAST")
            val profile = Json.parseToJsonElement(profileFile.readText())
                .toPlain() as Map<String, Any?>
            // No channel_map supplied — exercises the same rebuild-from-
            // channels fallback the JS reference applies (first type wins).
            val got = resolveShortcutsForProfile(profile)
            checked++

            val want = wantEl as JsonObject

            @Suppress("UNCHECKED_CAST")
            val wantIds = (want["shortcutIds"] as JsonArray)
                .map { (it.toPlain() as String) }
            assertEquals("$slug: shortcut id order", wantIds, got.map { it.id.tag })

            @Suppress("UNCHECKED_CAST")
            val wantShortcuts = (want["shortcuts"] as JsonArray)
                .map { it.toPlain() as Map<String, Any?> }
            for ((ws, gs) in wantShortcuts.zip(got)) {
                val id = ws["id"] as String
                assertEquals("$slug/$id: id", id, gs.id.tag)
                assertEquals("$slug/$id: ui", ws["ui"], uiTag(gs.ui))
                if (ws.containsKey("channelOffset")) {
                    assertEquals(
                        "$slug/$id: channelOffset",
                        ws["channelOffset"], gs.channelOffset
                    )
                }
                if (ws.containsKey("anchorOffset")) {
                    assertEquals(
                        "$slug/$id: anchorOffset",
                        ws["anchorOffset"], gs.anchorOffset
                    )
                }
            }
        }
        if (checked < 4) {
            fail("expected at least 4 corpus profiles, checked $checked")
        }
    }
}
