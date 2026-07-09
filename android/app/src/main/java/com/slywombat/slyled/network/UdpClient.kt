package com.slywombat.slyled.network

import android.os.SystemClock
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.nio.ByteBuffer
import java.nio.ByteOrder
import javax.inject.Inject
import javax.inject.Singleton

/**
 * #861 — minimal `DatagramSocket` wrapper for the UDP-push fast paths
 * (Android Auto Brightness today; future operator-driven channels can
 * register here too).
 *
 * Replaces the prior HTTP `POST /api/brightness` fast path which
 * suffered TCP retransmit / connection-pool churn at audio rate
 * (live-test 2026-05-08: zero POSTs landed in 3.5 min of music despite
 * UI showing master bouncing 150–255). UDP fire-and-forget mirrors the
 * existing gyro telemetry pattern on `:4210`.
 *
 * Header layout matches CLAUDE.md UDP protocol v5:
 *   magic(uint16 LE = 0x534C) version(uint8 = 5) cmd(uint8) epoch(uint32 LE)
 *
 * The socket is created lazily on first send and reused. `close()`
 * releases it; the next send re-opens. Safe to call from the main
 * thread: `sendAutoBrightnessPush` dispatches onto `Dispatchers.IO`
 * via a private SupervisorJob scope (see `sendScope` below) — required
 * because Android forbids network IO on the UI thread and the
 * `MicAutoBrightness` master callback fires on `Dispatchers.Main`.
 */
@Singleton
class UdpClient @Inject constructor() {

    companion object {
        private const val TAG = "UdpClient"
        private const val MAGIC: Short = 0x534C
        private const val PROTOCOL_VERSION: Byte = 5
        // #862 — AUTOBRI_PUSH lives on its own UDP port, not the
        // firmware-shared 4210, because Windows kernel port
        // reservations occasionally refuse 4210 even with no visible
        // holder (HNS / Hyper-V dynamic pool) — observed during
        // 2026-05-08 live test. The orchestrator binds a second UDP
        // listener on this port; firmware paths (PING/PONG, gyro)
        // stay on 4210.
        private const val DEFAULT_PORT = 4211

        // CMDs from `desktop/shared/parent_server.py` /
        // `main/Protocol.h`. Keep these in sync — the Android side
        // doesn't import a shared header.
        const val CMD_AUTOBRI_PUSH: Byte = 0x6D
    }

    @Volatile private var socket: DatagramSocket? = null
    private val sendLock = Any()

    // #906 — session-relative monotonic epoch, matching iOS UdpClient.swift
    // (`sessionEpochStart = Date()` at init; epoch = ms since session start).
    // elapsedRealtime() is monotonic across sleep, immune to wall-clock
    // changes. The orchestrator currently ignores the header epoch for
    // fire-and-forget pushes, but both phone platforms now stamp the same
    // semantics so the field is usable for latency diagnostics.
    private val sessionEpochStartMs = SystemClock.elapsedRealtime()

    // #861 follow-up — `MicAutoBrightness` invokes its master callback on
    // `Dispatchers.Main` (line 370), so calling this from `LiveStageViewModel`
    // would `DatagramSocket.send` on the UI thread → `NetworkOnMainThreadException`,
    // which we silently swallowed in `sendAutoBrightnessPush`'s catch arm
    // (the operator-reported failure mode: "861 doesn't work, no packets land").
    // We dispatch the actual send onto IO via this lightweight scope so the
    // callback returns immediately and the kernel buffer absorbs the write
    // off-thread.
    private val sendScope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    private fun ensureSocket(): DatagramSocket {
        var s = socket
        if (s == null || s.isClosed) {
            synchronized(sendLock) {
                s = socket
                if (s == null || s!!.isClosed) {
                    s = DatagramSocket().apply { reuseAddress = true }
                    socket = s
                }
            }
        }
        return s!!
    }

    /** Build the standard 8-byte protocol header.
     *  `epoch` — #906: monotonic ms since session start (uint32, wraps),
     *  aligned with iOS UdpClient.swift's sendAutoBrightness. */
    private fun header(cmd: Byte, epoch: Int): ByteArray {
        val buf = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
        buf.putShort(MAGIC)
        buf.put(PROTOCOL_VERSION)
        buf.put(cmd)
        buf.putInt(epoch)
        return buf.array()
    }

    /**
     * Send a CMD_AUTOBRI_PUSH packet (#861). 11 bytes total:
     *   header(8) + payload(3) = master(1) flags(1) seq(1)
     *
     * Exceptions are logged at WARN; not propagated. The audio
     * envelope follower fires every ~50 ms — propagating a transient
     * IO error would tear down the whole capture loop. Drop-and-
     * continue matches the operator's expectation for fire-and-forget
     * audio-rate telemetry.
     */
    fun sendAutoBrightnessPush(
        host: String,
        master: Int,
        flags: Int,
        seq: Int,
        port: Int = DEFAULT_PORT,
    ) {
        sendScope.launch {
            try {
                val payload = ByteBuffer.allocate(3).order(ByteOrder.LITTLE_ENDIAN).apply {
                    put((master and 0xFF).toByte())
                    put((flags and 0xFF).toByte())
                    put((seq and 0xFF).toByte())
                }.array()
                // #906 — stamp session-relative monotonic ms, same
                // semantics as iOS (wire format unchanged: uint32 LE).
                val epochMs =
                    (SystemClock.elapsedRealtime() - sessionEpochStartMs).toInt()
                val pkt = header(CMD_AUTOBRI_PUSH, epochMs) + payload
                val addr = InetAddress.getByName(host)
                val datagram = DatagramPacket(pkt, pkt.size, addr, port)
                ensureSocket().send(datagram)
            } catch (e: Exception) {
                // Per-call rate-limited at INFO would also be reasonable;
                // WARN here so a misconfigured host shows up in logcat
                // immediately. The auto-brightness loop suppresses
                // exceptions so a single failure doesn't stop the stream.
                Log.w(TAG, "AUTOBRI_PUSH send failed host=$host master=$master: ${e.message}")
            }
        }
    }

    /**
     * Release the underlying socket. Safe to call repeatedly. Next
     * `sendAutoBrightnessPush` reopens lazily.
     */
    fun close() {
        synchronized(sendLock) {
            try { socket?.close() } catch (_: Exception) {}
            socket = null
        }
    }
}
