/**
 * constants.js — Static lookup data and enums.
 * @module constants
 */

/** Action type display names — must match server-side _ACTION_NAMES. */
export const TYPE_NAMES = [
  'Blackout', 'Solid', 'Fade', 'Breathe', 'Chase', 'Rainbow', 'Fire',
  'Comet', 'Twinkle', 'Strobe', 'Color Wipe', 'Scanner', 'Sparkle',
  'Gradient', 'DMX Scene', 'Pan/Tilt Move', 'Gobo Select', 'Color Wheel',
  'Track',
];

/** Strip direction names. */
export const DIR_NAMES = ['Left→Right', 'Right→Left', 'Bottom→Up', 'Top→Down'];

/** Palette names. */
export const PAL_NAMES = ['Rainbow', 'Ocean', 'Lava', 'Forest', 'Party', 'Heat'];

/** Default LED strip colors for display. */
export const STR_COL = ['#22d3ee', '#a78bfa', '#f472b6', '#34d399', '#fb923c', '#f87171', '#facc15', '#818cf8'];

/** YOLO class names for tracking configuration. */
export const TRACK_CLASSES = [
  'person', 'bicycle', 'car', 'motorcycle', 'bus', 'truck',
  'cat', 'dog', 'horse', 'sheep', 'cow', 'bear',
  'backpack', 'umbrella', 'handbag', 'suitcase',
];
