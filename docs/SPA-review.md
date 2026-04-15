# SlyLED Code Review
**Model:** gemini-robotics-er-1.6-preview  
**Date:** 2026-04-15 10:48  
**Files reviewed:** 1  
**Groups:** spa

---

## Group: spa (part 1/3) — desktop/shared/spa/index.html [part 1]

This is a comprehensive review of the `desktop/shared/spa/index.html` file from the SlyLED lighting control system.

---

### desktop/shared/spa/index.html

#### 1. Bugs
*   **`_dashAttach3d` Duplicate Attachment:** The function `_dashAttach3d` calls `el.appendChild(_s3d.renderer.domElement)` without checking if the element is already a child. While `appendChild` moves an existing node, repeated calls trigger unnecessary layout recalculations.
*   **`_peRecalcOffsets` Logic:** The function assumes 16-bit channels always occupy two contiguous slots starting from the current offset. If a user manually defines offsets in a non-linear fashion in the JSON, this function will overwrite them, potentially breaking existing DMX patches.
*   **`_rotToAim` Inversion:** 
    ```javascript
    return[pos[0]+Math.sin(pr)*Math.cos(tr)*dist, pos[1]+Math.cos(pr)*Math.cos(tr)*dist, pos[2]-Math.sin(tr)*dist];
    ```
    In the coordinate system defined (`Z` is height), `pos[2] - Math.sin(tr)*dist` means a positive tilt (`tr`) results in the beam pointing **down** (decreasing Z). In standard theatrical/DMX conventions, positive tilt usually moves the beam up from the horizon.
*   **`_clearTabTimers` Race Condition:** The function clears several intervals but doesn't set the variables back to `null` in all code paths (e.g., `dashRunnerTimer` is cleared but `_rtTimer` check is separate). If `showTab` is called rapidly, multiple intervals could theoretically be spawned if the API responses return out of order.

#### 2. Security Issues
*   **XSS via `innerHTML`:** There is widespread use of `innerHTML` to render fixture names, hostnames, and IP addresses (e.g., in `_renderSetup`, `_afOflSearch`, and `_renderGyroDash`). While `escapeHtml` is defined, it is inconsistently applied. A malicious fixture node broadcasting a crafted hostname (e.g., `<img src=x onerror=alert(1)>`) could execute arbitrary JS in the orchestrator UI.
*   **Unvalidated `slug` in `_commDownload`:** The `slug` is taken directly from the UI and passed to `ra('POST','/api/dmx-profiles/community/download',{slug:slug},...)`. If the backend doesn't strictly validate this, it could lead to path traversal or command injection on the server side.
*   **Insecure Password Handling:** `fw-pass` and `cam-ssh-pass` are stored in the DOM. While they are `type='password'`, they are easily accessible via the console or browser extensions.

#### 3. Robustness
*   **Missing XHR Error States:** The `ra` function handles `onerror` and `ontimeout` by calling `callback(null)`. However, many callers (like `loadDash` or `_peSave`) do not explicitly check for `null` or handle the UI state when the server is unreachable, leading to "zombie" loading indicators.
*   **Three.js Initialization:** `s3dInit` checks for `THREE === 'undefined'` but doesn't handle WebGL context loss. If the computer sleeps or the GPU driver resets, the 3D viewport will go black and stay black until a full refresh.
*   **`_modalStack` Corruption:** If a user manages to trigger a modal while another is mid-animation or if `_pushModal` is called when the modal is already hidden, the stack can become desynchronized, making the "Close" button non-functional.

#### 4. Performance
*   **DOM Thrashing in `refreshLiveGrid`:** Rebuilding the entire grid (`grid.innerHTML = h`) when the fixture count changes is expensive. In large setups (100+ fixtures), this will cause noticeable UI stutter every second.
*   **MutationObserver Overhead:** 
    ```javascript
    var _tipObs=new MutationObserver(function(muts){...});
    _tipObs.observe(document.body,{childList:true,subtree:true});
    ```
    Observing the entire `body` with `subtree: true` for every single DOM change to apply tooltips is highly inefficient, especially during high-frequency DMX monitor updates or 3D rendering.
*   **Redundant API Calls:** `showTab` calls `loadSettings()` and `loadDmxSettings()` every time the settings tab is clicked, even if no data has changed.

#### 5. Code Quality
*   **Monolithic File:** At over 1,500 lines, this file mixes CSS, HTML, and complex business logic. It is difficult to maintain.
*   **Global Namespace Pollution:** Variables like `ld`, `phW`, `drag`, and `units` are in the global scope. This increases the risk of name collisions with third-party libraries (Three.js, OrbitControls).
*   **Inconsistent Naming:** Mix of camelCase (`showTab`) and underscore-prefixed internal functions (`_peSave`).

#### 6. Universal Coordinate Correctness
*   **Y/Z Ambiguity:** 
    *   `_s3dPos` maps Stage `Y` (depth) to 3D `Z`.
    *   `_s3dToMm` maps 3D `Z` back to Stage `Y`.
    *   However, `_s3dDir` uses `dirs=[new THREE.Vector3(1,0,0),new THREE.Vector3(0,1,0),...]` where index 1 (North) is `+Y` in 3D. In the `_s3dPos` mapping, `+Y` in 3D is Stage `Z` (Height). This means "North" points **Up** instead of **Forward** into the stage depth.
*   **Inverted Fixtures:** The `mountedInverted` flag is collected in `saveFixture` but is not consistently applied in the `_s3dAnimate` beam calculation logic. The beam will likely point the wrong way in the 3D preview for truss-mounted fixtures.

---

### Summary and Prioritised Issues

The SlyLED SPA is a feature-rich interface, but it suffers from typical "growing pains" of a single-file application. The most critical issues relate to coordinate mapping inconsistencies which will cause moving heads to point in the wrong direction in real space compared to the 3D preview.

#### Prioritised Fixes

| ID | Priority | Category | Issue |
|:---|:---|:---|:---|
| **P1** | **Must Fix** | **Coordinates** | **North/Up Confusion:** Fix `_s3dDir` to ensure North points to Stage Depth (+Y/3D Z) rather than Stage Height (+Z/3D Y). |
| **P2** | **Must Fix** | **Security** | **XSS Audit:** Replace `innerHTML` with `textContent` for all fixture-provided strings (names, IPs, hostnames). |
| **P3** | **Should Fix** | **Bugs** | **Tilt Inversion:** Correct `_rotToAim` to ensure positive tilt values point the beam upwards in the 3D coordinate space. |
| **P4** | **Should Fix** | **Performance** | **MutationObserver:** Scope the tooltip observer to specific containers or use event delegation for tooltips instead. |
| **P5** | **Nice to Have** | **Quality** | **Refactor:** Split the JS logic into separate modules (e.g., `ui.js`, `three-scene.js`, `api.js`). |

---

## Group: spa (part 2/3) — desktop/shared/spa/index.html [part 2]

This review covers the provided JavaScript source for the SlyLED lighting control system frontend. The system appears to be a complex single-page application (SPA) managing 3D spatial environments, DMX protocols, and timeline-based sequencing.

---

### 1. File: `slyled_frontend.js` (Partial)

#### **1. Bugs — Logic & State**
*   **Timeline Drift:** In `tlTogglePreview`, the playhead increments by `_tlPlayT += 0.1` every `100ms`. JavaScript's `setInterval` is not guaranteed to fire precisely. Over a 60-second timeline, the visual playhead will drift significantly from actual time.
*   **Division by Zero:** In `_emuPixel`, multiple effects (Rainbow, Chase, Comet) use `var spd=p.speedMs||...; if(spd<1)spd=1;`. However, if `p.speedMs` is explicitly `0`, it defaults to the fallback. If the fallback is also missing or `0`, and the check fails, division by zero occurs.
*   **Race Condition in Cache:** `emuLoadStage` and `loadLayout` both check `if(!window._profileCache)`. If both are triggered near-simultaneously (common on page load), multiple identical API requests to `/api/dmx-profiles` will be dispatched.
*   **Bake Polling Leak:** In `tlBake`, if the user closes the modal while baking, the `poll` interval continues to run in the background indefinitely until `s.done` or `s.error` is returned, even if the user navigates away.

#### **2. Security Issues — Injection & Validation**
*   **Widespread XSS Vulnerabilities:** The code uses `.innerHTML` extensively to render data from the API (e.g., `fixName`, `t.name`, `a.name`, `s.status`). While `escapeHtml` is used in some places, it is missing in critical areas like `_renderSyncPerformers`, `_manCalRenderMarkers`, and `_calWizStep1`. A malicious fixture name or IP string could execute arbitrary JS in the context of the control dashboard.
*   **Unvalidated Input:** `parseInt(document.getElementById(...).value)` is used frequently without checking for `NaN` before sending to the API (e.g., `tlAddTrackConfirm`). While the backend *should* validate this, the frontend may send corrupt payloads.
*   **Sensitive Data in DOM:** `_manCalRenderJog` puts raw DMX channel offsets into element IDs (`mcj-ch-'+c.offset`). While not a direct exploit, it exposes internal memory/addressing structures to the DOM unnecessarily.

#### **3. Robustness — Edge Cases**
*   **Missing Error Handling:** The `ra` (requestAsync) wrapper is used everywhere, but the callbacks often assume `r` is valid: `function(r){ closeModal(); if(r.ok) ... }`. If the server returns a 500 error or the network times out, `r` might be null/undefined, causing a crash.
*   **Blocking UI:** The use of `confirm()` and `prompt()` (e.g., `newTimeline`, `delSpatialFx`) blocks the main thread. In a real-time lighting system, this can pause the `requestAnimationFrame` loop, causing the 3D preview to freeze.
*   **Z-Index/Modal Stacking:** The modal logic resets `innerHTML` constantly. If a sub-wizard (like `_calWizardShow`) is called, it overwrites previous modal content without a stack mechanism, making "Back" buttons difficult to implement reliably.

#### **4. Performance — Inefficient Patterns**
*   **DOM Thrashing:** `renderTimeline` rebuilds the entire timeline DOM from strings on every update. For long shows with many tracks, this causes significant layout jitter and CPU spikes.
*   **O(N^2) Lookups:** Inside `renderTimeline`, there is a `_fixtures.forEach` loop inside a `tracks.forEach` loop. As fixture counts grow, UI responsiveness will degrade.
*   **Redundant 3D Rebuilds:** `s3dLoadChildren()` is called frequently. If this involves disposing and re-creating Three.js geometries (as seen in `_emu3dClearNodes`), it will cause noticeable frame drops during layout adjustments.

#### **5. Universal Coordinate Correctness**
*   **Y/Z Inversion:** The code notes: `Stage→Three.js: X→X, Z(height)→Y, Y(depth)→Z`.
    *   In `_pointCloudRender`: `positions[i*3+1]=pts[i][2]/1000;` (Z to Y).
    *   In `_emuPixel` (Fire effect): It uses `di` (index) and `e` (time) but ignores the physical `Y` depth for spatial effects.
    *   **Potential Bug:** In `autoArrangeDmx`, `f.y = backY` and `f.z = topZ`. If `backY` is depth and `topZ` is height, this is correct for the internal model, but `_rotToAim` must strictly follow the same mapping or movers will point at the ceiling when told to point at the floor.
*   **Inverted Fixtures:** `f.mountedInverted` is checked in `applyNodePos` but doesn't seem to be consistently applied to the `_emu3d` beam cone logic. If a fixture is inverted, the `tilt` value in the preview should likely be mirrored, or the cone will point the wrong way in the emulator compared to the real world.

---

### **Overall Summary**

The SlyLED frontend is a feature-rich interface, but it suffers from "jQuery-style" state management in a modern context. The heavy reliance on `innerHTML` and global state (`_curTl`, `_fixtures`) makes it prone to side effects and security vulnerabilities.

#### **Prioritised List of Issues**

| ID | Priority | Category | Issue |
|:---|:---|:---|:---|
| **1** | **P1** | **Security** | **XSS via innerHTML:** Fixture names, timeline names, and status messages are not consistently escaped. |
| **2** | **P1** | **Bugs** | **Timeline Drift:** Playhead logic in `tlTogglePreview` is decoupled from the system clock. |
| **3** | **P2** | **Robustness** | **Callback Safety:** API callbacks lack null-checks for response objects, leading to UI crashes on network error. |
| **4** | **P2** | **Performance** | **DOM Reconstruction:** Rebuilding the entire timeline and fixture list on every change is inefficient. |
| **5** | **P2** | **Coord Correctness** | **Inversion Logic:** Ensure `mountedInverted` flag is mathematically applied to the Three.js `ConeGeometry` rotation. |
| **6** | **P3** | **Code Quality** | **Global Namespace:** Excessive use of `_` prefixed global variables; should be encapsulated in a state manager or class. |
| **7** | **P3** | **Robustness** | **Interval Leaks:** Polling intervals (bake, sync, scan) are not consistently cleared on modal close. |

**Recommendation:**
The immediate priority is to implement a consistent `escapeHtml` policy or move to `textContent` for all UI labels to prevent XSS. Secondly, the timeline playhead should use `performance.now()` to calculate elapsed time rather than assuming `setInterval` accuracy.

---

## Group: spa (part 3/3) — desktop/shared/spa/index.html [part 3]

This review covers the provided JavaScript source code for the **SlyLED Lighting Control System** frontend. The code primarily handles the DMX fixture wizard, configuration import/export, and project file management using the File System Access API.

---

### 1. SlyLED Frontend Logic (JavaScript)

#### **Bugs**
*   **Race Condition in Conflict Checker:** In `_wizStep2`, `checkConflict()` is bound to the `change` event of `uniEl` and `addrEl`. If a user types quickly or toggles values, multiple `ra('GET','/api/dmx/patch',...)` requests are fired. Since these are asynchronous, a slower, older request might return after a newer one, overwriting the conflict status with stale data.
*   **DMX Address Boundary Logic:** In `_wizStep2`, `var endAddr = addr + w.channels - 1;`. While correct for the end address, there is no validation that `endAddr` stays $\le 512$. A fixture starting at 510 with 10 channels will "successfully" show no conflicts but is invalid in a standard DMX universe.
*   **Recent Files "Dead End":** `_fmOpenRecent` only triggers an `alert` telling the user to manually open the file. While noted as a browser limitation (FS handles don't persist easily without IndexedDB storage), it is functionally a "broken" feature for a user expecting a "Recent" list to actually open the file.
*   **Integer Parsing Defaults:** `parseInt(document.getElementById('wiz-uni').value)||1`. If a user intentionally enters `0` (valid for some ArtNet/DMX systems starting at Universe 0), the logic forces it to `1`.

#### **Security Issues**
*   **XSS via `onclick` Construction:** In `_wizSearch`, the `selectFn` is built as a string: 
    `selectFn='_wizSelectLocal(\''+escapeHtml(f.id)+'\',...'`. 
    Even with `escapeHtml`, if the `f.id` contains specific combinations of quotes or if `escapeHtml` is not robust against all contexts, this is a vector for DOM-based XSS. Using `addEventListener` or passing data-attributes is safer than injecting executable strings into `onclick`.
*   **Unvalidated JSON Import:** `importConfig` and `_fmImportJson` perform `JSON.parse` inside a `try-catch`, but the subsequent validation is shallow (only checking `data.type`). A maliciously crafted JSON file could potentially exploit logic further down the chain if it expects specific array lengths or object types that aren't verified.

#### **Robustness**
*   **Brittle Focus Logic:** `setTimeout(function(){var q=document.getElementById('wiz-q');if(q)q.focus();},100);` is used to focus the search input. If the modal animation takes longer than 100ms or the DOM hasn't finished rendering, the focus will fail.
*   **Silent Failures:** Many `ra` (Request Async) calls have an `else` block that updates `hs.textContent` (status bar), but some (like `_wizSelectCommunity`) only update a small span inside the modal. If the network fails, the user might be stuck in a "Downloading..." state without a clear way to retry other than closing the modal.
*   **File Handle Desync:** `_projFileHandle` is global. If a user "Saves As" to a file, then manually deletes that file on their OS, subsequent calls to `_fmSave()` will fail. There is no `try-catch` around `handle.createWritable()` to recover gracefully and force a new "Save As".

#### **Performance**
*   **DOM Thrashing:** `_wizBrowseAll` clears and rebuilds the entire profile list every time. If the library grows to hundreds of profiles, the `profiles.forEach` loop creating large strings of HTML to inject via `.innerHTML` will cause noticeable UI jank.
*   **Redundant API Calls:** `checkConflict` in `_wizStep2` fetches the *entire* patch from `/api/dmx/patch` every time the user changes the address. This should be fetched once when Step 2 opens and cached locally for the duration of the wizard.

#### **Code Quality**
*   **Global Namespace Pollution:** `window._wiz` is used as a global state container. This is prone to collisions and makes debugging state transitions difficult.
*   **Hardcoded Styles:** The code is heavily reliant on inline styles (`style="background:#1e293b;..."`). This makes it nearly impossible to implement themes or maintain consistent spacing without editing logic files.
*   **Terse Naming:** Variables like `ra`, `h`, `el`, `r`, `p`, and `f` are used throughout. While common in minified code, in source code, it hinders readability for new maintainers.

#### **Universal Coordinate Correctness**
*   **Coordinate Inversion Risk:** In `_wizCreate`, `type: w.geom` is passed to the API. The wizard does not appear to ask for fixture orientation (Invert Pan/Tilt) or physical mounting position (Floor vs. Ceiling). In point-cloud based systems, if the "Real Space" coordinates aren't established during this wizard, moving heads will likely point in the opposite direction of the virtual beam until manually calibrated elsewhere.

---

### Overall Summary

The SlyLED frontend is a functional, lightweight SPA-style interface. It effectively uses modern browser APIs (File System Access) to provide a "desktop-like" experience. However, the reliance on string-concatenated HTML and inline event handlers introduces security risks and makes the code difficult to scale.

#### **Prioritised Issues to Fix**

| ID | Priority | Category | Issue |
|:---|:---|:---|:---|
| **1** | **P1** | **Security** | Refactor `selectFn` in `_wizSearch` to avoid building executable strings. Use `data-` attributes and a single event listener. |
| **2** | **P1** | **Bugs** | Add debouncing to `checkConflict` and validate that `startAddr + channelCount` does not exceed 512. |
| **3** | **P2** | **Robustness** | Implement proper error handling for `showSaveFilePicker`. If the handle is stale, catch the error and trigger `_fmSaveAs`. |
| **4** | **P2** | **Performance** | Cache the DMX patch data in `_wizStep2` instead of re-requesting the full list on every keystroke. |
| **5** | **P2** | **Coordinate** | Add a "Mounting Orientation" toggle in `_wizStep3` to ensure `w.geom` correctly aligns with the point cloud space. |
| **6** | **P3** | **Code Quality** | Move inline CSS to a dedicated stylesheet and use classes to improve readability and maintainability. |