package com.slywombat.slyled.audio

import android.app.Activity
import android.content.Context
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext

/**
 * #820 — composable wrapper that registers the screen-capture consent
 * launcher and converts the result into a [MediaProjection] suitable for
 * `AudioPlaybackCaptureConfiguration`.
 *
 * Usage from a screen that has the Audio Sources picker:
 *
 * ```kotlin
 * val requestPlaybackConsent = rememberPlaybackCaptureLauncher { mp ->
 *     viewModel.setAutoBrightnessMediaProjection(mp)
 * }
 * // …
 * onSelect = { kind ->
 *     viewModel.configureAutoBrightness(audioSourceKind = kind)
 *     if (kind == AudioSourceKind.PLAYBACK_CAPTURE) requestPlaybackConsent()
 * }
 * ```
 *
 * The returned function fires the system screen-recording / audio-capture
 * dialog. On grant, [onMediaProjection] receives the constructed
 * MediaProjection. On denial, it's invoked with `null`.
 */
@Composable
fun rememberPlaybackCaptureLauncher(
    onMediaProjection: (MediaProjection?) -> Unit,
): () -> Unit {
    val context = LocalContext.current
    val mpm = remember(context) {
        context.getSystemService(Context.MEDIA_PROJECTION_SERVICE)
            as? MediaProjectionManager
    }
    val launcher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        val mp = if (result.resultCode == Activity.RESULT_OK
            && result.data != null && mpm != null) {
            try { mpm.getMediaProjection(result.resultCode, result.data!!) }
            catch (_: Exception) { null }
        } else null
        onMediaProjection(mp)
    }
    return remember(mpm, launcher) {
        {
            val intent = mpm?.createScreenCaptureIntent()
            if (intent != null) launcher.launch(intent)
            else onMediaProjection(null)
        }
    }
}
