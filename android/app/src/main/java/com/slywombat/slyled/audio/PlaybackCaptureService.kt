package com.slywombat.slyled.audio

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat

/**
 * #820 — Foreground service that holds the MediaProjection alive for the
 * duration of an AudioPlaybackCapture session.
 *
 * Android 14 (API 34) requires a foreground service of type `mediaProjection`
 * to be running BEFORE `AudioPlaybackCaptureConfiguration` can call
 * `MediaProjection.createAudioRecord` — without it, the AudioRecord build
 * throws SecurityException. Earlier versions tolerated capture without a
 * service but the documented path is to always start one, so we do.
 *
 * Lifecycle:
 *   • Started by [start] before MediaProjection is constructed.
 *   • Holds a "recording audio" notification (which carries the system
 *     audio-capture indicator chip) for the full capture duration.
 *   • Stopped by [stop] when the AudioRecord is torn down (in
 *     `MicAutoBrightness.stop()`).
 *
 * The service intentionally does NO work in onStartCommand beyond
 * `startForeground` — MediaProjection lifecycle is owned by
 * `MicAutoBrightness`; this service is just the OS-required handle.
 */
class PlaybackCaptureService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        ensureChannel()
        val notif = buildNotification()
        if (Build.VERSION.SDK_INT >= 30) {
            startForeground(
                NOTIF_ID, notif,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION,
            )
        } else {
            startForeground(NOTIF_ID, notif)
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        super.onDestroy()
        if (Build.VERSION.SDK_INT >= 24) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < 26) return
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (nm.getNotificationChannel(CHANNEL_ID) != null) return
        val ch = NotificationChannel(
            CHANNEL_ID,
            "Auto Brightness — Playback Capture",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "Required while capturing audio playback for Auto Brightness"
            setShowBadge(false)
        }
        nm.createNotificationChannel(ch)
    }

    private fun buildNotification(): Notification {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Auto Brightness")
            .setContentText("Capturing playback audio for brightness")
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setOngoing(true)
            .setSilent(true)
            .build()
    }

    companion object {
        private const val CHANNEL_ID = "auto_brightness_playback"
        private const val NOTIF_ID = 0xAB1

        /** Start the foreground service. Idempotent. */
        fun start(context: Context) {
            val intent = Intent(context, PlaybackCaptureService::class.java)
            if (Build.VERSION.SDK_INT >= 26) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        /** Stop the foreground service. Safe to call when not running. */
        fun stop(context: Context) {
            try {
                context.stopService(Intent(context, PlaybackCaptureService::class.java))
            } catch (_: Exception) {}
        }
    }
}
