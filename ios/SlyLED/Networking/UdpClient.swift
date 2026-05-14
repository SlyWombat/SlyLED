// UdpClient — Network.framework UDP sender for AUTOBRI_PUSH (cmd 0x6D,
// port 4211). 11-byte PDU per CLAUDE.md UDP table:
//   magic   uint16 LE  = 0x534C
//   version uint8      = 5
//   cmd     uint8      = 0x6D
//   epoch   uint32 LE  (monotonic ms since session start)
//   master  uint8      (0..255)
//   flags   uint8      (bit0=beat-pulse, bit1=mic-gated)
//   seq     uint8      (per-session counter, wraps)
//
// Fire-and-forget; failures are logged at .error and dropped (the next
// hop fires ~50 ms later). NWConnection is recreated whenever the host
// changes.

import Foundation
import Network
import os

@MainActor
final class UdpClient {
    static let shared = UdpClient()
    private let log = OSLog(subsystem: "ca.electricrv.slyled", category: "udp")

    private var connection: NWConnection?
    private var currentHost: String = ""
    private var currentPort: NWEndpoint.Port = 4211
    private var seq: UInt8 = 0
    private let sessionEpochStart = Date()

    func configure(host: String, port: UInt16 = 4211) {
        let np = NWEndpoint.Port(integerLiteral: port)
        if host == currentHost && np == currentPort && connection != nil { return }
        currentHost = host
        currentPort = np
        connection?.cancel()
        guard !host.isEmpty else { connection = nil; return }
        let params: NWParameters = .udp
        connection = NWConnection(host: NWEndpoint.Host(host), port: np, using: params)
        connection?.stateUpdateHandler = { [weak self] state in
            guard let self else { return }
            if case .failed(let err) = state {
                os_log("UDP connection failed: %{public}@",
                       log: self.log, type: .error, String(describing: err))
            }
        }
        connection?.start(queue: .global(qos: .utility))
    }

    func tearDown() {
        connection?.cancel()
        connection = nil
        currentHost = ""
    }

    /// Send AUTOBRI_PUSH (cmd 0x6D) with the given master 0..255.
    /// `flags`: bit0 = beat-pulse-on, bit1 = mic-gated.
    func sendAutoBrightness(master: UInt8, beatPulse: Bool, micGated: Bool) {
        guard let conn = connection else { return }
        let epochMs = UInt32(truncatingIfNeeded:
            Int(Date().timeIntervalSince(sessionEpochStart) * 1000))
        var flagsByte: UInt8 = 0
        if beatPulse { flagsByte |= 0x01 }
        if micGated  { flagsByte |= 0x02 }
        seq = seq &+ 1

        var pdu = Data(capacity: 11)
        // magic 0x534C in little-endian → 0x4C, 0x53
        pdu.append(0x4C); pdu.append(0x53)
        pdu.append(0x05)              // version
        pdu.append(0x6D)              // cmd AUTOBRI_PUSH
        var epochLE = epochMs.littleEndian
        withUnsafeBytes(of: &epochLE) { pdu.append(contentsOf: $0) }
        pdu.append(master)
        pdu.append(flagsByte)
        pdu.append(seq)

        conn.send(content: pdu, completion: .contentProcessed { [weak self] err in
            if let err, let self {
                os_log("UDP send failed: %{public}@",
                       log: self.log, type: .error, String(describing: err))
            }
        })
    }
}
