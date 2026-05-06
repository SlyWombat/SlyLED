package com.slywombat.slyled.data.repository

/**
 * #824 follow-up — Android-vs-orchestrator compatibility.
 *
 * Pre-fix the version banner did a string-equality check, so any
 * independent bump on either side (the whole point of the decoupled
 * tracks) lit a "mismatched versions — some features may not work"
 * warning. That alarmist behaviour trained the operator to ignore the
 * banner, defeating its purpose: surfacing real, half-shipped releases.
 *
 * Now the banner only fires when the orchestrator is older than the
 * earliest version this APK can actually talk to — driven by known
 * breaking points, not arbitrary version-string drift.
 *
 * ## How to add a new breaking point
 *
 * When a future change ships that requires a specific orchestrator
 * version (e.g. a new endpoint the APK now relies on, or a wire-protocol
 * change), bump [MIN_ORCHESTRATOR_VERSION] to the version that introduced
 * the orchestrator side of that change. The banner will then warn
 * operators running stale orchestrators that they need to update.
 *
 * The reverse direction (orchestrator requires APK ≥ X) is handled
 * server-side via /api/status response field; not implemented yet because
 * we have no example of an orchestrator-side change that breaks an old
 * APK without graceful degradation.
 */
object Compatibility {
    /**
     * Earliest orchestrator version this APK is known to work with.
     *
     * Bump this only when:
     * - the APK calls a NEW orchestrator endpoint that an older server
     *   doesn't have (would 404 — like the SMART /status route deletion
     *   that broke the Pointer button), OR
     * - the wire format of an existing endpoint the APK uses changed in
     *   a not-back-compat way.
     *
     * Cosmetic / additive server changes do NOT bump this.
     *
     * Current floor: 1.7.0 (April 2026 reset baseline). The decoupling
     * shipped in #824 didn't introduce a new contract gap, so the
     * original v1.7.0 reset version is still safe.
     */
    const val MIN_ORCHESTRATOR_VERSION = "1.7.0"

    /**
     * Compare two semver-ish strings ("1.7.0", "1.7.65", "1.10.2").
     *
     * Returns: negative if [a] < [b], 0 if equal, positive if [a] > [b].
     * Non-numeric segments fall through to string compare so weird
     * pre-release tags don't crash the banner — they just report as
     * "different" and the floor check decides if that matters.
     */
    fun compareVersions(a: String, b: String): Int {
        val ap = a.split(".")
        val bp = b.split(".")
        val n = maxOf(ap.size, bp.size)
        for (i in 0 until n) {
            val ai = ap.getOrNull(i)?.toIntOrNull()
            val bi = bp.getOrNull(i)?.toIntOrNull()
            when {
                ai != null && bi != null -> {
                    if (ai != bi) return ai - bi
                }
                else -> {
                    // String compare on non-numeric tail; rare in practice.
                    val cmp = (ap.getOrNull(i) ?: "").compareTo(bp.getOrNull(i) ?: "")
                    if (cmp != 0) return cmp
                }
            }
        }
        return 0
    }

    /**
     * Banner trigger. True when the orchestrator is OLDER than this APK's
     * declared minimum. Empty/blank server version → false (we haven't
     * connected yet, so don't flag).
     */
    fun orchestratorBelowFloor(serverVersion: String): Boolean {
        if (serverVersion.isBlank()) return false
        return try {
            compareVersions(serverVersion, MIN_ORCHESTRATOR_VERSION) < 0
        } catch (_: Exception) {
            false
        }
    }
}
