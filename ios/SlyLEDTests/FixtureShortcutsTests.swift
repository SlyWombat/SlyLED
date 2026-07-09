// FixtureShortcutsTests — #906 executable parity gate for the Swift
// fixture-shortcut resolver (ios/SlyLED/UI/Components/FixtureShortcuts.swift).
//
// Consumes the shared corpus at tests/fixtures/shortcut_corpus/ (repo
// root), bundled into this test target as resources by project.yml.
// expected.json is generated FROM the JS reference implementation
// (desktop/shared/spa/js/fixture_shortcuts.js); if this test fails, fix
// FixtureShortcuts.swift to match the JS, never the corpus.
//
// The JS and Kotlin twins are gated by tests/test_fixture_shortcuts.py
// (Node) and android/app/src/test/.../FixtureShortcutsTest.kt (gradlew
// test) against the identical JSON.

import XCTest
@testable import SlyLED

final class FixtureShortcutsTests: XCTestCase {

    private func loadJSON(named name: String) throws -> Any {
        let bundle = Bundle(for: FixtureShortcutsTests.self)
        guard let url = bundle.url(forResource: name, withExtension: "json") else {
            XCTFail("corpus resource missing from test bundle: \(name).json — check the project.yml resources entry for ../tests/fixtures/shortcut_corpus")
            throw NSError(domain: "FixtureShortcutsTests", code: 1)
        }
        let data = try Data(contentsOf: url)
        return try JSONSerialization.jsonObject(with: data)
    }

    private func uiTag(_ ui: ShortcutUi) -> String {
        switch ui {
        case .toggle:      return "toggle"
        case .segmented:   return "segmented"
        case .colorSwatch: return "color-swatch"
        case .momentary:   return "momentary"
        case .longPress:   return "long-press"
        }
    }

    func testSwiftResolverMatchesSharedCorpus() throws {
        guard let expectedRoot = try loadJSON(named: "expected") as? [String: Any] else {
            XCTFail("expected.json did not decode to an object")
            return
        }

        var checked = 0
        for (slug, wantAny) in expectedRoot {
            if slug == "_doc" { continue }
            guard let want = wantAny as? [String: Any] else {
                XCTFail("\(slug): expected entry is not an object")
                continue
            }
            guard let profile = try loadJSON(named: slug) as? [String: Any] else {
                XCTFail("\(slug): profile JSON did not decode to an object")
                continue
            }

            // No channel_map supplied — exercises the same
            // rebuild-from-channels fallback the JS reference applies
            // (first occurrence of each type wins).
            let got = FixtureShortcuts.resolveShortcutsForProfile(profile)
            checked += 1

            let wantIds = (want["shortcutIds"] as? [String]) ?? []
            XCTAssertEqual(wantIds, got.map { $0.id.rawValue },
                           "\(slug): shortcut id order")

            let wantShortcuts = (want["shortcuts"] as? [[String: Any]]) ?? []
            XCTAssertEqual(wantShortcuts.count, got.count, "\(slug): shortcut count")
            for (ws, gs) in zip(wantShortcuts, got) {
                let id = (ws["id"] as? String) ?? "?"
                XCTAssertEqual(id, gs.id.rawValue, "\(slug): id at same index")
                XCTAssertEqual(ws["ui"] as? String, uiTag(gs.ui), "\(slug)/\(id): ui")
                if let co = ws["channelOffset"] as? Int {
                    XCTAssertEqual(co, gs.channelOffset, "\(slug)/\(id): channelOffset")
                }
                if let ao = ws["anchorOffset"] as? Int {
                    XCTAssertEqual(ao, gs.anchorOffset, "\(slug)/\(id): anchorOffset")
                }
            }
        }
        XCTAssertGreaterThanOrEqual(checked, 4,
            "corpus should contain at least 4 profiles (found \(checked))")
    }
}
