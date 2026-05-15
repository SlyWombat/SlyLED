package com.slywombat.slyled

import android.content.Context
import android.os.Build
import android.util.Log
import java.io.File
import java.io.PrintWriter
import java.io.StringWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Global crash + diagnostic capture. #888 follow-up — operator reported
 * "the app crashes a lot" with no way to see what. This installs a
 * last-resort uncaught-exception handler that writes a full report to
 * the app's external files dir BEFORE the process dies, so a crash
 * leaves a retrievable artifact instead of a vanished process.
 *
 * Retrieval (no root needed — external-files-dir is app-private but
 * adb-readable and visible to the system Files app):
 *   adb pull /sdcard/Android/data/com.slywombat.slyled/files/crashes
 *
 * The reports are also re-POSTed to the orchestrator on next launch
 * (see [uploadPending]) so a field crash surfaces server-side without
 * the operator hand-fishing files off the phone.
 */
object CrashReporter {

    private const val TAG = "SlyLEDCrash"
    private const val DIR = "crashes"
    private const val MAX_KEEP = 20

    /** Install the handler. Call once from Application.onCreate(). */
    fun install(context: Context) {
        val appContext = context.applicationContext
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            try {
                val report = buildReport(appContext, thread, throwable)
                writeReport(appContext, report)
                // Mirror to logcat so `adb logcat` catches it too.
                Log.e(TAG, "uncaught exception on ${thread.name}\n$report")
            } catch (e: Throwable) {
                // A crash handler must never throw — swallow and fall
                // through to the platform handler regardless.
                Log.e(TAG, "crash reporter failed: ${e.message}")
            }
            // Re-delegate so the OS still shows its dialog / kills the
            // process cleanly. Without this the app would hang on a
            // dead main thread.
            previous?.uncaughtException(thread, throwable)
        }
    }

    private fun buildReport(
        context: Context, thread: Thread, throwable: Throwable,
    ): String {
        val sw = StringWriter()
        throwable.printStackTrace(PrintWriter(sw))
        val ver = try {
            val pi = context.packageManager.getPackageInfo(context.packageName, 0)
            "${pi.versionName} (${pi.longVersionCode})"
        } catch (_: Exception) {
            "unknown"
        }
        val ts = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date())
        return buildString {
            appendLine("=== SlyLED Android crash ===")
            appendLine("time:    $ts")
            appendLine("app:     $ver")
            appendLine("device:  ${Build.MANUFACTURER} ${Build.MODEL}")
            appendLine("android: ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})")
            appendLine("thread:  ${thread.name}")
            appendLine()
            append(sw.toString())
        }
    }

    private fun crashDir(context: Context): File =
        File(context.getExternalFilesDir(null), DIR).apply { mkdirs() }

    private fun writeReport(context: Context, report: String) {
        val dir = crashDir(context)
        val stamp = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date())
        File(dir, "crash-$stamp.txt").writeText(report)
        // Ring-buffer: keep only the newest MAX_KEEP reports.
        dir.listFiles { f -> f.name.startsWith("crash-") }
            ?.sortedByDescending { it.lastModified() }
            ?.drop(MAX_KEEP)
            ?.forEach { it.delete() }
    }

    /** Reports waiting to be uploaded, oldest first. */
    fun pendingReports(context: Context): List<File> =
        crashDir(context)
            .listFiles { f -> f.name.startsWith("crash-") }
            ?.sortedBy { it.lastModified() }
            ?: emptyList()

    /** Delete a report once it has been successfully uploaded. */
    fun markUploaded(file: File) {
        try { file.delete() } catch (_: Exception) {}
    }

    /**
     * POST every pending report to the orchestrator's
     * `/api/android/crash` endpoint, deleting each on a successful
     * upload. Best-effort: a failed upload leaves the file for the
     * next attempt. Call from a background thread after a connection
     * is established. [baseUrl] is the orchestrator root, e.g.
     * "http://10.0.2.2:9000/".
     */
    fun uploadPending(context: Context, baseUrl: String) {
        val pending = pendingReports(context)
        if (pending.isEmpty()) return
        val endpoint = baseUrl.trimEnd('/') + "/api/android/crash"
        for (file in pending) {
            try {
                val body = file.readText().toByteArray(Charsets.UTF_8)
                val conn = (java.net.URL(endpoint).openConnection()
                        as java.net.HttpURLConnection).apply {
                    requestMethod = "POST"
                    connectTimeout = 5000
                    readTimeout = 5000
                    doOutput = true
                    setRequestProperty("Content-Type", "text/plain; charset=utf-8")
                }
                conn.outputStream.use { it.write(body) }
                val code = conn.responseCode
                conn.disconnect()
                if (code in 200..299) {
                    markUploaded(file)
                    Log.i(TAG, "uploaded crash report ${file.name}")
                } else {
                    Log.w(TAG, "crash upload ${file.name} → HTTP $code (keeping)")
                }
            } catch (e: Exception) {
                Log.w(TAG, "crash upload failed for ${file.name}: ${e.message}")
            }
        }
    }

    /**
     * Record a non-fatal diagnostic (a caught-but-notable exception).
     * Same file format as a crash so the operator sees one stream.
     * Use for the silent `catch` arms that previously swallowed errors.
     */
    fun logNonFatal(context: Context, tag: String, throwable: Throwable) {
        try {
            val sw = StringWriter()
            throwable.printStackTrace(PrintWriter(sw))
            val ts = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date())
            val report = "=== SlyLED non-fatal ($tag) ===\ntime: $ts\n\n$sw"
            val dir = crashDir(context)
            val stamp = SimpleDateFormat("yyyyMMdd-HHmmss-SSS", Locale.US).format(Date())
            File(dir, "crash-nonfatal-$stamp.txt").writeText(report)
            Log.w(TAG, "non-fatal [$tag]: ${throwable.message}", throwable)
        } catch (_: Throwable) {
        }
    }
}
