// fixture_shortcuts.js — profile-driven UI shortcut resolver for non-mover
// DMX fixtures (bubble machines, hazers, RGB pars, etc.) on the SPA's
// Fixtures tab and the Android Control / Fixtures page. #888.
//
// Resolves a profile's channels into an ordered list of UI shortcut
// descriptors. Three resolution passes per channel:
//
//   1. explicit `channel.shortcut` field (profile-author opt-in, the
//      stable contract)
//   2. capability-type heuristic (e.g. ShutterStrobe → strobe-momentary)
//   3. name-match fallback (channel name contains "bubble"/"haze"/...)
//
// The Kotlin twin at android/.../shortcuts/FixtureShortcuts.kt mirrors
// this resolver byte-for-byte. The shared JSON test corpus at
// tests/fixtures/shortcut_corpus.json exercises both implementations.

const SHORTCUT_IDS = Object.freeze([
  "bubble-toggle",
  "haze-segmented",
  "fan-segmented",
  "color-swatch",
  "uv-toggle",
  "strobe-momentary",
  "clean-mode",
]);

// Each entry describes how to render the shortcut and what DMX value(s)
// to write on each tap. `writes` is an array of {channelType, value}
// pairs the renderer applies via /api/fixtures/<fid>/dmx-test.
//
// `value` is either a number (single tap) or an object keyed by the
// segmented control's label ("LOW" / "MED" / etc.). For toggles, value
// is {on: <byte>, off: <byte>}.
const SHORTCUT_SPECS = Object.freeze({
  "bubble-toggle": {
    icon: "🫧",
    label: "BUBBLES",
    ui: "toggle",
    channelType: "dimmer",
    onValue: 255,
    offValue: 0,
  },
  "haze-segmented": {
    icon: "💨",
    label: "HAZE",
    ui: "segmented",
    channelType: "dimmer",
    segments: [
      { label: "OFF", value: 0 },
      { label: "LOW", value: 64 },
      { label: "MED", value: 128 },
      { label: "HIGH", value: 220 },
    ],
  },
  "fan-segmented": {
    icon: "🌀",
    label: "FAN",
    ui: "segmented",
    channelType: "speed",
    segments: [
      { label: "SLOW", value: 64 },
      { label: "MED", value: 160 },
      { label: "FAST", value: 255 },
    ],
  },
  "color-swatch": {
    icon: "💡",
    label: "COLOUR",
    ui: "color-swatch",
    channelTypes: ["red", "green", "blue"], // optional: "uv" if present
    swatches: [
      { label: "Red",     rgb: [255,   0,   0] },
      { label: "Orange",  rgb: [255, 128,   0] },
      { label: "Yellow",  rgb: [255, 255,   0] },
      { label: "Green",   rgb: [  0, 255,   0] },
      { label: "Cyan",    rgb: [  0, 255, 255] },
      { label: "Blue",    rgb: [  0,   0, 255] },
      { label: "Magenta", rgb: [255,   0, 255] },
      { label: "White",   rgb: [255, 255, 255] },
      { label: "Off",     rgb: [  0,   0,   0] },
    ],
  },
  "uv-toggle": {
    icon: "🟣",
    label: "UV",
    ui: "toggle",
    channelType: "uv",
    onValue: 255,
    offValue: 0,
  },
  "strobe-momentary": {
    icon: "⚡",
    label: "STROBE",
    ui: "momentary",
    channelType: "strobe",
    // Capability lookup at render time picks the ShutterStrobe Strobe
    // range. Renderer writes range midpoint on press, Open value on
    // release.
  },
  "clean-mode": {
    icon: "🧼",
    label: "CLEAN",
    ui: "long-press",
    channelType: "reset",
    onValue: 255,
    offValue: 0,
    confirmHoldMs: 1500,
  },
});

// Name-match fallback dictionary. Keys are substring patterns (lowercase
// channel.name); values are shortcut ids. First match wins; ordering
// matters (more-specific first).
const NAME_HEURISTIC = Object.freeze([
  ["bubble",            "bubble-toggle"],
  ["haze",              "haze-segmented"],
  ["fog",               "haze-segmented"],
  ["fan",               "fan-segmented"],
  ["blower",            "fan-segmented"],
  ["clean",             "clean-mode"],
]);

/**
 * Resolve a single channel to a shortcut id, or null if no shortcut
 * applies. Three-pass resolver: explicit → capability-type → name.
 *
 * @param {object} channel — one entry from profile.channels
 * @returns {string|null} a SHORTCUT_IDS member, or null
 */
function resolveChannelShortcut(channel) {
  if (!channel || typeof channel !== "object") return null;

  // Pass 1 — explicit annotation.
  if (channel.shortcut && SHORTCUT_IDS.includes(channel.shortcut)) {
    return channel.shortcut;
  }

  // Pass 2 — capability-type heuristic. Only ShutterStrobe maps; the
  // others are too generic to auto-assign safely.
  const caps = Array.isArray(channel.capabilities) ? channel.capabilities : [];
  if (channel.type === "strobe") {
    const hasStrobeRange = caps.some(c => c.type === "ShutterStrobe"
                                          && c.shutterEffect === "Strobe");
    if (hasStrobeRange) return "strobe-momentary";
  }
  if (channel.type === "uv") return "uv-toggle";

  // Pass 3 — name-match fallback (only for dimmer-class and speed
  // channels; we don't want a "Strobe Macro" channel matching "fog
  // strobe" because we tested name in isolation).
  const eligibleTypes = new Set(["dimmer", "intensity", "speed", "reset"]);
  if (eligibleTypes.has(channel.type)) {
    const lname = (channel.name || "").toLowerCase();
    for (const [needle, sid] of NAME_HEURISTIC) {
      if (lname.includes(needle)) return sid;
    }
  }

  return null;
}

/**
 * Walk a profile and return the deduped, ordered list of shortcut
 * descriptors that should render on a Fixtures-page card.
 *
 * Color-swatch is special — it represents the whole RGB triad, not a
 * single channel; the renderer needs only one swatch shortcut per
 * fixture. Returned with `anchorOffset` (the red channel's offset)
 * plus a `channelOffsets` map {red, green, blue, uv?} for writes.
 *
 * @param {object} profile — full profile object (id, channels, channel_map)
 * @returns {Array<{id, icon, label, ui, channel, ...}>}
 */
function resolveShortcutsForProfile(profile) {
  if (!profile || !Array.isArray(profile.channels)) return [];

  const out = [];
  const seenSwatch = { added: false };

  for (const ch of profile.channels) {
    const sid = resolveChannelShortcut(ch);
    if (!sid) continue;
    const spec = SHORTCUT_SPECS[sid];
    if (!spec) continue;

    if (sid === "color-swatch") {
      // Only emit one swatch entry per fixture, anchored at this
      // channel offset. The renderer pulls green/blue/uv from the
      // channel_map at render time.
      if (seenSwatch.added) continue;
      const cm = profile.channel_map || {};
      if (!("red" in cm && "green" in cm && "blue" in cm)) continue;
      seenSwatch.added = true;
      out.push({
        id: sid,
        ...spec,
        anchorOffset: ch.offset,
        channelOffsets: {
          red:   cm.red,
          green: cm.green,
          blue:  cm.blue,
          uv:    cm.uv,  // may be undefined; renderer handles
        },
      });
      continue;
    }

    out.push({
      id: sid,
      ...spec,
      channelOffset: ch.offset,
      channelName: ch.name,
      capabilities: ch.capabilities || [],
    });
  }

  // Stable order independent of channel order, for predictable UI:
  const ORDER = [
    "bubble-toggle", "haze-segmented", "fan-segmented",
    "color-swatch",  "uv-toggle",      "strobe-momentary",
    "clean-mode",
  ];
  out.sort((a, b) => ORDER.indexOf(a.id) - ORDER.indexOf(b.id));
  return out;
}

/**
 * Strobe-momentary press value — midpoint of the channel's
 * `ShutterStrobe/Strobe` capability range. Mirrors Kotlin's
 * `strobeMomentaryValue` in FixtureShortcuts.kt. Returns null when
 * no Strobe range is declared (renderer should hide the shortcut).
 */
function strobeMomentaryValue(shortcut) {
  for (const cap of (shortcut.capabilities || [])) {
    if (cap.type === "ShutterStrobe" && cap.shutterEffect === "Strobe") {
      const rng = cap.range || [];
      if (rng.length !== 2) continue;
      return Math.floor((rng[0] + rng[1]) / 2);
    }
  }
  return null;
}

/**
 * Strobe-momentary release value — midpoint of the channel's
 * `ShutterStrobe/Open` capability range, or 0 when none declared.
 */
function strobeOpenValue(shortcut) {
  for (const cap of (shortcut.capabilities || [])) {
    if (cap.type === "ShutterStrobe" && cap.shutterEffect === "Open") {
      const rng = cap.range || [];
      if (rng.length !== 2) continue;
      return Math.floor((rng[0] + rng[1]) / 2);
    }
  }
  return 0;
}

// Export for ES module + plain-script usage.
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    SHORTCUT_IDS, SHORTCUT_SPECS, resolveChannelShortcut, resolveShortcutsForProfile,
    strobeMomentaryValue, strobeOpenValue,
  };
}
if (typeof window !== "undefined") {
  window.FixtureShortcuts = {
    SHORTCUT_IDS, SHORTCUT_SPECS, resolveChannelShortcut, resolveShortcutsForProfile,
    strobeMomentaryValue, strobeOpenValue,
  };
}
