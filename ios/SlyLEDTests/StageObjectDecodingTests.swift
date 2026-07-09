// StageObjectDecodingTests — B5/#912 wire-shape gate for the /api/objects
// Codable models (ios/SlyLED/Networking/Models.swift). The captured JSON
// mirrors parent_server.py exactly: _radar_person_create (radar persons,
// float ttl, underscore _temporal/_expiresAt keys, source stamp) and
// api_objects_temporal_create (camera persons carry cameraId, NOT
// fixtureId). Android's twin lives in ModelSerializationTest.kt.

import XCTest
@testable import SlyLED

final class StageObjectDecodingTests: XCTestCase {

    func testRadarSourcedPersonDecodesFromServerShape() throws {
        let json = """
        {"id":7,"name":"Person (radar) 7","objectType":"person","mobility":"moving",\
        "_temporal":true,"ttl":2.0,"_expiresAt":1767000000.5,"color":"#f472b6",\
        "opacity":40,"transform":{"pos":[1200.0,2400.0,850.0],"rot":[0,0,0],\
        "scale":[500.0,1700.0,500.0]},"source":{"type":"radar","fixtureId":3,"node":"MMW-A1B2"}}
        """
        let obj = try JSONDecoder().decode(StageObject.self, from: Data(json.utf8))
        XCTAssertEqual(obj.objectType, "person")
        XCTAssertEqual(obj.temporal, true)
        XCTAssertTrue(obj.isTracked)
        XCTAssertTrue(obj.isRadarSourced)
        XCTAssertEqual(obj.ttl, 2.0)
        XCTAssertEqual(obj.source?.fixtureId, 3)
        XCTAssertEqual(obj.source?.node, "MMW-A1B2")
        XCTAssertNil(obj.source?.cameraId)
        XCTAssertEqual(obj.transform?.pos, [1200.0, 2400.0, 850.0])
    }

    func testCameraSourcedPersonCarriesCameraId() throws {
        let json = """
        {"id":9,"name":"Person 9","objectType":"person","mobility":"moving",\
        "_temporal":true,"ttl":5,"color":"#f472b6","opacity":40,\
        "transform":{"pos":[0,0,850.0],"rot":[0,0,0],"scale":[400.0,1700.0,400.0]},\
        "source":{"type":"camera","cameraId":4}}
        """
        let obj = try JSONDecoder().decode(StageObject.self, from: Data(json.utf8))
        XCTAssertFalse(obj.isRadarSourced)
        XCTAssertTrue(obj.isTracked)
        XCTAssertEqual(obj.source?.cameraId, 4)
        XCTAssertNil(obj.source?.fixtureId)
        XCTAssertEqual(obj.ttl, 5.0)  // integer ttl still decodes
    }

    func testStaticObjectWithoutSourceDecodes() throws {
        // Old-server / operator-placed shape — no source, no _temporal.
        let json = """
        {"id":2,"name":"Wall","objectType":"wall","mobility":"static",\
        "color":"#334155","opacity":30,"transform":{"pos":[100.0,200.0,0.0],\
        "rot":[0,0,0],"scale":[2000.0,1500.0,100.0]},"stageLocked":false}
        """
        let obj = try JSONDecoder().decode(StageObject.self, from: Data(json.utf8))
        XCTAssertNil(obj.source)
        XCTAssertFalse(obj.isTracked)
        XCTAssertFalse(obj.isRadarSourced)
    }

    func testChildRadarNodeDetection() throws {
        // Radar nodes have no HTTP /status, so type stays "slyled" and
        // boardType stays blank — the MMW- hostname prefix is the signal.
        let radar = try JSONDecoder().decode(Child.self, from: Data("""
        {"id":4,"ip":"10.0.0.9","hostname":"MMW-A1B2","sc":0,"strings":[],"status":1,"type":"slyled"}
        """.utf8))
        XCTAssertTrue(radar.isRadarNode)

        var led = try JSONDecoder().decode(Child.self, from: Data("""
        {"id":5,"ip":"10.0.0.10","hostname":"SLYC-1152","sc":1,"strings":[],"status":1,"type":"slyled"}
        """.utf8))
        XCTAssertFalse(led.isRadarNode)
        // Future server stamping the registry board id also matches.
        led.boardType = "mmwave"
        XCTAssertTrue(led.isRadarNode)
    }
}
