package com.slywombat.slyled

import com.slywombat.slyled.ui.screens.control.computeFloorIntersection
import org.junit.Assert.*
import org.junit.Test

/**
 * #427 — phone aim → floor intersection (z = 0 plane). The overlay
 * computes this every sensor tick so the bug surface here is small but
 * load-bearing — getting the sign wrong sends the beam to the ceiling.
 */
class PointerMathTest {

    @Test
    fun `straight down lands directly under the operator`() {
        // Operator at (1000, 2000, 1700) aiming straight down.
        val target = computeFloorIntersection(
            ux = 1000f, uy = 2000f, uz = 1700f,
            fx = 0f, fy = 0f, fz = -1f
        )
        assertNotNull("straight down must intersect the floor", target)
        assertEquals(1000f, target!!.first, 0.01f)
        assertEquals(2000f, target.second, 0.01f)
    }

    @Test
    fun `45 degrees forward and down hits at ground distance equal to height`() {
        // 1700 mm tall operator, aiming forward-and-down at 45°: the
        // ray should land 1700 mm in front along +Y stage.
        val target = computeFloorIntersection(
            ux = 0f, uy = 0f, uz = 1700f,
            fx = 0f, fy = 0.7071f, fz = -0.7071f
        )
        assertNotNull(target)
        assertEquals(0f, target!!.first, 0.01f)
        assertEquals(1700f, target.second, 1f)
    }

    @Test
    fun `aiming level is rejected — no intersection`() {
        val target = computeFloorIntersection(
            ux = 0f, uy = 0f, uz = 1700f,
            fx = 0f, fy = 1f, fz = 0f
        )
        assertNull("level aim must not intersect the floor", target)
    }

    @Test
    fun `aiming up is rejected`() {
        val target = computeFloorIntersection(
            ux = 0f, uy = 0f, uz = 1700f,
            fx = 0f, fy = 0.5f, fz = 0.5f
        )
        assertNull("upward aim must not intersect the floor", target)
    }

    @Test
    fun `slight downward dip below threshold is rejected`() {
        // fz = -0.04 is "almost level" — ignore so we don't shoot
        // the beam wildly far away from a noisy sensor reading.
        val target = computeFloorIntersection(
            ux = 0f, uy = 0f, uz = 1700f,
            fx = 0f, fy = 0.999f, fz = -0.04f
        )
        assertNull("near-horizontal aim must be rejected (>= -0.05 fz threshold)", target)
    }

    @Test
    fun `forward-down aim translates from operator origin correctly`() {
        // Operator is offset to (3000, 1500, 1800) and aims down at 60°.
        // tan(30°) ≈ 0.577 → ground distance from operator = 1800 * tan(30°)
        // forward in stage frame is +X (1, 0, 0) tipped down at 60° below
        // horizontal: (cos(-60°), 0, sin(-60°)) = (0.5, 0, -0.866).
        val target = computeFloorIntersection(
            ux = 3000f, uy = 1500f, uz = 1800f,
            fx = 0.5f, fy = 0f, fz = -0.866f
        )
        assertNotNull(target)
        // t = 1800 / 0.866 ≈ 2078; X = 3000 + 2078*0.5 = 4039
        assertEquals(4039f, target!!.first, 5f)
        assertEquals(1500f, target.second, 0.01f)
    }
}
